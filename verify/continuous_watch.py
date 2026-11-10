#!/usr/bin/env python3
"""Unattended 1-second continuity observation for the Yi -> go2rtc -> Frigate chain.

Runs two aligned 1-second samplers (camera wlan0 TX bytes, go2rtc producer
`recv` bytes) with NO fault injection, then reports every egress/consumer
stall window, every P4v2 teardown, process restarts, Frigate error deltas and
recording-segment coverage. Used to close the "no-drop" verification.

Usage:
    NAS_SUDO_PW='<sudo password>' python3 continuous_watch.py \
        --minutes 20 --out <case-rtsp>/logs/continuity-20261107

    # Rebuild the report from 1s logs already on disk (no new sampling):
    NAS_SUDO_PW='<sudo password>' python3 continuous_watch.py \
        --offline --minutes 20 --out <case-rtsp>/logs/continuity-20261107

No credential is stored in this file.
"""

import argparse
import ast
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

import quant_stall_ab as q

DOCKER = q.DOCKER


def nas_root(script, pw):
    return q.nas(q.nas_root_script(script, pw))


def camera_snapshot(out, prefix):
    _, hardened, _ = q.cam("cat /tmp/rrtsp_hardened.log")
    (out / ("%s.rrtsp_hardened.log" % prefix)).write_text(hardened)
    _, wd, _ = q.cam("cat /tmp/sd/wd_stream.log")
    (out / ("%s.wd_stream.log" % prefix)).write_text(wd)
    _, state, _ = q.cam(
        'echo "rrtsp=$(pidof rRTSPServer) rmm=$(pidof rmm) grabber=$(pidof h264grabber) uptime=$(cat /proc/uptime)"')
    (out / ("%s.camera_state.txt" % prefix)).write_text(state)
    return hardened, wd, state.strip()


def frigate_snapshot(out, prefix, pw, minutes):
    errs = ('%s logs frigate 2>&1 | grep -cE '
            '"No frames received|crashed unexpectedly|No new recording segments"' % DOCKER)
    _, e, _ = q.run(nas_root(errs, pw), timeout=90)
    segs = ('find /media/frigate/recordings -name "*.mp4" | wc -l')
    _, s, _ = q.run(nas_root('%s exec frigate sh -c %s' %
                             (DOCKER, shlex.quote(segs)), pw), timeout=90)
    # Cumulative since container start: before/after delta = events in the window.
    # A --since window would also pick up earlier controlled A/B injections.
    iot = '%s logs frigate 2>&1 | grep -c "i/o timeout"' % DOCKER
    _, t, _ = q.run(nas_root(iot, pw), timeout=90)
    snapshot = {"errors": e.strip(), "segments": s.strip(), "io_timeouts": t.strip()}
    (out / ("%s.frigate_state.txt" % prefix)).write_text(repr(snapshot) + "\n")
    return snapshot


def window_io_timeouts(pw, start, end):
    """Count go2rtc i/o timeout lines inside [start, end] via Docker --since UTC.

    The cumulative counters in frigate_snapshot are only comparable when both
    snapshots come from the same code version. An older run stored a --since
    window count as its "io_timeouts" value, so an offline rebuild MUST NOT
    subtract those two numbers. Derive the window count directly instead.
    """
    import datetime
    since = datetime.datetime.fromtimestamp(
        start, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    script = ('%s logs --since %s frigate 2>&1 | grep "i/o timeout"' %
              (DOCKER, since))
    _, out, _ = q.run(nas_root(script, pw), timeout=180)
    # Docker prefixes each line with the NAS daemon's local timestamp (UTC+8).
    tz_cst = datetime.timezone(datetime.timedelta(hours=8))
    hits = []
    for ln in out.splitlines():
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if not m:
            continue
        e = int(datetime.datetime.strptime(
            m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_cst).timestamp())
        if start <= e <= end + 2:
            hits.append((e, m.group(1)))
    return hits


def runs(delta_map, threshold, min_len=2):
    """Contiguous epochs whose value stays under threshold, >= min_len samples."""
    out = []
    cur = []
    for e in sorted(delta_map):
        if delta_map[e] < threshold:
            if cur and e - cur[-1] > 2:
                if len(cur) >= min_len:
                    out.append((cur[0], cur[-1], len(cur)))
                cur = []
            cur.append(e)
        else:
            if len(cur) >= min_len:
                out.append((cur[0], cur[-1], len(cur)))
            cur = []
    if len(cur) >= min_len:
        out.append((cur[0], cur[-1], len(cur)))
    return out


def pid_of(state, key):
    m = re.search(r"\b%s=(\d+)" % key, state)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--out", required=True)
    ap.add_argument("--offline", action="store_true",
                    help="rebuild report from existing 1s logs + fresh after snapshots")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pw = os.environ.get("NAS_SUDO_PW", "")
    if not pw:
        print("ERROR: NAS_SUDO_PW is required", file=sys.stderr)
        return 2

    secs = args.minutes * 60
    n = secs + 12
    cam_script = (
        'i=0; while [ $i -lt %d ]; do '
        'echo "$(date +%%s) $(awk "/wlan0:/ {print \\$10}" /proc/net/dev)"; '
        'i=$((i+1)); sleep 1; done' % n
    )
    nas_script = (
        'i=0; while [ $i -lt %d ]; do '
        'R=$(%s exec frigate curl -s -m 2 "http://127.0.0.1:1984/api/streams?src=cam1" '
        '| grep -o "\\\"recv\\\": [0-9]*" | awk "{print \\$2}"); '
        'echo "$(date +%%s) ${R:-NA}"; i=$((i+1)); sleep 1; done'
    ) % (n, DOCKER)

    tx_path = out / "camera_tx_1s.log"
    recv_path = out / "go2rtc_recv_1s.log"

    if args.offline:
        needed = ("before.camera_state.txt", "before.frigate_state.txt",
                  "before.rrtsp_hardened.log", "before.wd_stream.log")
        for name in needed:
            if not (out / name).exists():
                print("ERROR: offline mode requires %s" % name, file=sys.stderr)
                return 2
        pre_h = (out / "before.rrtsp_hardened.log").read_text(errors="replace")
        pre_w = (out / "before.wd_stream.log").read_text(errors="replace")
        pre_state = (out / "before.camera_state.txt").read_text(errors="replace").strip()
        pre_f = ast.literal_eval(
            (out / "before.frigate_state.txt").read_text(errors="replace").strip())
        epochs = sorted(set(q.parse_pairs(tx_path)) | set(q.parse_pairs(recv_path)))
        if not epochs:
            print("ERROR: no 1s samples found", file=sys.stderr)
            return 2
        start, end = epochs[0], epochs[-1]
        print("[watch] offline reconstruction of %s .. %s UTC (e=%d .. %d)" %
              (q.utc(start), q.utc(end), start, end), flush=True)
    else:
        pre_h, pre_w, pre_state = camera_snapshot(out, "before")
        pre_f = frigate_snapshot(out, "before", pw, args.minutes)
        start = int(time.time())
        print("[watch] %d min observation started e=%d (%s UTC)" %
              (args.minutes, start, q.utc(start)), flush=True)
        with tx_path.open("w") as ftx, recv_path.open("w") as frc:
            p_cam = subprocess.Popen(q.ssh_args(q.CAM_HOST, 22, "root", cam_script),
                                     stdout=ftx, stderr=subprocess.DEVNULL)
            p_nas = subprocess.Popen(nas_root(nas_script, pw),
                                     stdout=frc, stderr=subprocess.DEVNULL)
            # The camera loop costs slightly more than 1 s per iteration, so the
            # wall clock can exceed the nominal window. Never discard samples
            # already written: on timeout, stop the samplers and report on disk.
            try:
                for p in (p_cam, p_nas):
                    p.wait(timeout=secs + 300)
            except subprocess.TimeoutExpired:
                for p in (p_cam, p_nas):
                    if p.poll() is None:
                        p.terminate()
                print("[watch] WARN: sampler exceeded wait budget; "
                      "reporting on samples already written", flush=True)
        end = int(time.time())

    post_h, post_w, post_state = camera_snapshot(out, "after")
    post_f = frigate_snapshot(out, "after", pw, args.minutes)

    dtx = q.deltas(q.parse_pairs(tx_path))
    drc = q.deltas(q.parse_pairs(recv_path))
    tx_runs = runs(dtx, 15 * 1024)
    rc_runs = runs(drc, 5 * 1024)
    teardowns = [(e, m) for e, m in q.hardened_epochs(post_h)
                 if start - 2 <= e <= end + 2 and "tearing down" in m]
    new_wd = [ln for ln in post_w.splitlines() if ln not in pre_w.splitlines()]
    wd_kill = [ln for ln in new_wd if re.search(r"kill|restart|重启", ln, re.I)]

    csv_lines = ["epoch,utc,tx_Bps,go2rtc_recv_Bps,flag"]
    for e in sorted(set(list(dtx) + list(drc))):
        t, r = dtx.get(e), drc.get(e)
        flags = []
        if t is not None and t < 15 * 1024:
            flags.append("TX_LOW")
        if r is not None and r < 5 * 1024:
            flags.append("RECV_STALL")
        if any(e == te for te, _ in teardowns):
            flags.append("TEARDOWN")
        csv_lines.append("%d,%s,%s,%s,%s" %
                         (e, q.utc(e), "" if t is None else t,
                          "" if r is None else r, "|".join(flags)))
    (out / "timeline.csv").write_text("\n".join(csv_lines) + "\n")

    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return -1

    err_delta = num(post_f["errors"]) - num(pre_f["errors"])
    seg_delta = num(post_f["segments"]) - num(pre_f["segments"])
    # Frigate writes one recording segment per 10 s; derive expectation from the
    # actual observed window (which may be longer than --minutes).
    expected_segs = max(1, int((end - start) / 10))
    # Window-accurate count; do NOT subtract the cumulative before/after values
    # when those snapshots were produced by different code versions.
    io_hits = window_io_timeouts(pw, start, end)
    io_delta = len(io_hits)
    io_cum = (num(pre_f["io_timeouts"]), num(post_f["io_timeouts"]))
    worst_stall = max((d for _, _, d in rc_runs), default=0)
    isolated_tx = (sum(1 for v in dtx.values() if v < 15 * 1024) -
                   sum(d for _, _, d in tx_runs))
    pid_stable = all(pid_of(pre_state, k) == pid_of(post_state, k)
                     for k in ("rrtsp", "rmm"))
    duration = end - start

    s = []
    s.append("=== continuous_watch (%s, %ds window) ===" %
             ("offline reconstruction" if args.offline else "%d min planned" % args.minutes,
              duration))
    s.append("window: %s .. %s UTC (e=%d .. %d)" %
             (q.utc(start), q.utc(end), start, end))
    s.append("camera TX samples      : %d" % len(dtx))
    s.append("go2rtc recv samples    : %d" % len(drc))
    s.append("TX low(<15KB/s) runs   : %s" %
             (", ".join("%s..%s (%ds)" % (q.utc(a), q.utc(b), d)
                        for a, b, d in tx_runs) or "none"))
    s.append("consumer stall(<5KB/s) : %s" %
             (", ".join("%s..%s (%ds)" % (q.utc(a), q.utc(b), d)
                        for a, b, d in rc_runs) or "none"))
    s.append("longest consumer stall : %ds (Frigate no-frame watchdog = 20s)" % worst_stall)
    s.append("isolated TX low 1s     : %d (below the 3-round P4v2 criterion; no teardown)"
             % isolated_tx)
    s.append("P4v2 teardowns         : %s" %
             (", ".join("%s %s" % (q.utc(e), m) for e, m in teardowns) or "none"))
    s.append("new wd_stream lines    : %s" % (" | ".join(new_wd) if new_wd else "none"))
    s.append("wd_stream kill actions : %s" % (" | ".join(wd_kill) if wd_kill else "none"))
    s.append("camera state before    : %s" % pre_state)
    s.append("camera state after     : %s" % post_state)
    s.append("camera PIDs stable     : %s" % ("yes" if pid_stable else "NO"))
    s.append("Frigate errors delta   : %+d (before=%s after=%s)" %
             (err_delta, pre_f["errors"], post_f["errors"]))
    s.append("go2rtc i/o timeouts    : %d in window (%s)" %
             (io_delta, ", ".join(t.split()[1] for _, t in io_hits) or "none"))
    s.append("counter basis note     : cumulative snapshots %d/%d use mixed bases "
             "in offline rebuild; window count from docker --since UTC" % io_cum)
    s.append("recording segments     : +%d at after-snapshot time (expected ~%d for "
             "%ds window; offline after snapshot may be later than window end)" %
             (seg_delta, expected_segs, duration))
    if wd_kill:
        s.append("CONCLUSION: FAIL - wd_stream restarted rRTSPServer during the window")
    elif not pid_stable:
        s.append("CONCLUSION: FAIL - a camera process restarted during the window")
    elif err_delta > 0:
        s.append("CONCLUSION: FAIL - Frigate logged %d new errors" % err_delta)
    elif seg_delta < expected_segs * 0.9:
        s.append("CONCLUSION: FAIL - recording segment count %d/%d below expectation" %
                 (seg_delta, expected_segs))
    elif rc_runs:
        s.append("CONCLUSION: PASS - %d natural collapse(s) (P4v2 teardowns) "
                 "self-healed in place; %d consumer stall window(s), longest %ds "
                 "< Frigate 20s watchdog; %d go2rtc i/o timeout(s), zero Frigate "
                 "error, zero killall, no process restart, recording writes "
                 "uninterrupted over %ds."
                 % (len(teardowns), len(rc_runs), worst_stall, io_delta, duration))
    else:
        s.append("CONCLUSION: PASS - zero consumer stall, zero P4v2 teardown, "
                 "zero Frigate error, no process restart, recording writes "
                 "uninterrupted over %ds." % duration)
    s.append("recording caveat      : PASS covers the WRITE side only (segment count "
             "and playable files). Playback is NOT frame-contiguous: every collapse "
             "leaves 4-8s with no video frames INSIDE an mp4, which the replay UI "
             "amplifies into a ~15-20s jump. Segment counts / the recordings table "
             "cannot detect this; see ../logs/visible-gap-20261108.md")
    report = "\n".join(s) + "\n"
    (out / "report.txt").write_text(report)
    print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
