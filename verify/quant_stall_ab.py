#!/usr/bin/env python3
"""One-second quantitative A/B harness for the Yi camera RTSP collapse.

Starts two aligned 1-second samplers -- camera-side wlan0 TX byte counter and
consumer-side go2rtc producer `recv` byte counter -- then fires a controlled
RTSP stall (rtsp_stall_client.py: real PLAY, then deliberately stop reading a
socket with a tiny SO_RCVBUF). It writes raw logs, a merged per-second CSV and
a derived collapse/recovery window summary.

Usage:
    NAS_SUDO_PW='<sudo password>' python3 quant_stall_ab.py \
        --label C --stall 25 --tail 45 --out <case-rtsp>/logs/quant-C

No credential is stored in this file; it is read from the environment only.
Camera logs are UTC; all internal alignment uses Unix epoch seconds.
"""

import argparse
import calendar
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
STALL_CLIENT = HERE / "rtsp_stall_client.py"

# Site-specific values come from the environment; generic RFC 5737 / RFC 1918
# example addresses are only defaults so the script can be read and linted.
CAM_HOST = os.environ.get("CAM_HOST", "192.0.2.10")
NAS_HOST = os.environ.get("NAS_HOST", "192.0.2.20")
NAS_PORT = int(os.environ.get("NAS_SSH_PORT", "22"))
NAS_USER = os.environ.get("NAS_SSH_USER", "admin")
DOCKER = os.environ.get("DOCKER_BIN", "/usr/local/bin/docker")

SSH_BASE = [
    "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
]


def ssh_args(host, port, user, remote_cmd):
    return SSH_BASE + ["-p", str(port), "%s@%s" % (user, host), remote_cmd]


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def cam(remote_cmd, timeout=60, host=None):
    return run(ssh_args(host or CAM_HOST, 22, "root", remote_cmd), timeout)


def nas(remote_cmd, timeout=60):
    return ssh_args(NAS_HOST, NAS_PORT, NAS_USER, remote_cmd)


def nas_root_script(script, pw):
    """Wrap a shell script so it runs as root via sudo -S (password on stdin)."""
    return "echo %s | sudo -S sh -c %s 2>/dev/null" % (
        shlex.quote(pw), shlex.quote(script))


def parse_pairs(path):
    """Parse '<epoch> <counter>' lines into an epoch->value dict."""
    out = {}
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                out[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
    return out


def deltas(series):
    """Return epoch -> bytes/second, normalising for skipped seconds."""
    epochs = sorted(series)
    out = {}
    for prev, cur in zip(epochs, epochs[1:]):
        gap = cur - prev
        if gap <= 0 or gap > 5:
            continue
        d = series[cur] - series[prev]
        out[cur] = d // gap if d >= 0 else 0
    return out


def utc(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch))


def hardened_epochs(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) (.*)", line)
        if m:
            y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
            e = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
            out.append((e, m.group(7)))
    return out


def inject(logf, mode, hold, stall_secs, cam_host, pw):
    """Fire a controlled fault. Returns (start_epoch, end_epoch, detail).

    Every STOP/pause is released in a finally block so a failed harness can
    never leave the camera or Frigate frozen.
    """
    def mark(msg):
        logf.write("[%s e=%d] %s\n" %
                   (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    int(time.time()), msg))
        logf.flush()

    start = int(time.time())
    if mode == "stall":
        mark("inject=stall client, stop reading for %ds" % stall_secs)
        r = subprocess.run([sys.executable, str(STALL_CLIENT), cam_host, str(stall_secs)],
                           stdout=logf, stderr=subprocess.STDOUT,
                           timeout=stall_secs + 30)
        mark("stall client rc=%d" % r.returncode)
        return start, int(time.time()), "stall rc=%d" % r.returncode

    if mode == "grabber-stop":
        find = ('for p in $(pidof h264grabber); do '
                'if tr "\\0" " " < /proc/$p/cmdline | grep -q " -r high "; '
                'then echo $p; fi; done')
        rc, out, err = cam(find, host=cam_host)
        pids = [x for x in out.split() if x.isdigit()]
        if not pids:
            raise RuntimeError("no high h264grabber found: %s %s" % (out, err))
        pid = pids[0]
        mark("inject=grabber-stop pid=%s hold=%ds" % (pid, hold))
        rc, out, err = cam("kill -STOP %s" % pid, host=cam_host)
        if rc != 0:
            raise RuntimeError("SIGSTOP failed: %s" % err)
        try:
            time.sleep(hold)
        finally:
            cam("kill -CONT %s" % pid, host=cam_host)
            mark("released SIGCONT pid=%s" % pid)
        _, state, _ = cam("awk '/^State:/ {print $2}' /proc/%s/status" % pid,
                          host=cam_host)
        mark("grabber state after release=%s" % state.strip())
        return start, int(time.time()), "grabber-stop pid=%s hold=%ds" % (pid, hold)

    if mode == "pause-frigate":
        mark("inject=pause-frigate hold=%ds" % hold)
        cmd = "%s pause frigate" % DOCKER
        rc = subprocess.run(nas(nas_root_script(cmd, pw)),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=40).returncode
        if rc != 0:
            raise RuntimeError("docker pause failed rc=%d" % rc)
        try:
            time.sleep(hold)
        finally:
            subprocess.run(nas(nas_root_script("%s unpause frigate" % DOCKER, pw)),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=40)
            mark("released docker unpause frigate")
        return start, int(time.time()), "pause-frigate hold=%ds" % hold

    raise ValueError("unknown inject mode: %s" % mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="C")
    ap.add_argument("--stall", type=int, default=25)
    ap.add_argument("--tail", type=int, default=45)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cam", default=CAM_HOST)
    ap.add_argument("--inject", default="stall",
                    choices=["stall", "grabber-stop", "pause-frigate"])
    ap.add_argument("--hold", type=int, default=14,
                    help="seconds to hold SIGSTOP / docker pause")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pw = os.environ.get("NAS_SUDO_PW", "")
    if not pw:
        print("ERROR: NAS_SUDO_PW is required", file=sys.stderr)
        return 2
    if not STALL_CLIENT.exists():
        print("ERROR: missing %s" % STALL_CLIENT, file=sys.stderr)
        return 2

    inj_dur = args.stall if args.inject == "stall" else args.hold
    total = inj_dur + args.tail + 14

    cam_script = (
        'i=0; while [ $i -lt %d ]; do '
        'echo "$(date +%%s) $(awk "/wlan0:/ {print \\$10}" /proc/net/dev)"; '
        'i=$((i+1)); sleep 1; done' % total
    )
    nas_script = (
        'i=0; while [ $i -lt %d ]; do '
        'R=$(%s exec frigate curl -s -m 2 "http://127.0.0.1:1984/api/streams?src=cam1" '
        '| grep -o "\\\"recv\\\": [0-9]*" | awk "{print \\$2}"); '
        'echo "$(date +%%s) ${R:-NA}"; i=$((i+1)); sleep 1; done'
    ) % (total, DOCKER)

    tx_path = out / "camera_tx_1s.log"
    recv_path = out / "go2rtc_recv_1s.log"
    stall_path = out / "stall_client.log"

    print("[harness] starting 1s samplers, duration=%ds" % total, flush=True)
    with tx_path.open("w") as ftx, recv_path.open("w") as frc:
        p_cam = subprocess.Popen(ssh_args(args.cam, 22, "root", cam_script),
                                 stdout=ftx, stderr=subprocess.DEVNULL)
        p_nas = subprocess.Popen(nas(nas_root_script(nas_script, pw)),
                                 stdout=frc, stderr=subprocess.DEVNULL)

        time.sleep(4)  # let the first samples land before the event
        t_start = int(time.time())

        # Pre-event Frigate counters.
        pre_cmd = ('%s logs frigate 2>&1 | grep -cE '
                   '"No frames received|crashed unexpectedly|No new recording segments"' % DOCKER)
        _, pre_cnt, _ = run(nas(nas_root_script(pre_cmd, pw)), timeout=60)

        print("[harness] firing %s at e=%d (%s UTC)" %
              (args.inject, t_start, utc(t_start)), flush=True)
        with stall_path.open("w") as fs:
            fs.write("# label=%s inject=%s dur=%ds tail=%ds start_epoch=%d\n" %
                     (args.label, args.inject, inj_dur, args.tail, t_start))
            fs.flush()
            inj_start, inj_end, detail = inject(
                fs, args.inject, args.hold, args.stall, args.cam, pw)
            fs.write("# inject_start_epoch=%d inject_end_epoch=%d detail=%s\n" %
                     (inj_start, inj_end, detail))
        print("[harness] %s done (%s); observing %ds recovery tail" %
              (args.inject, detail, args.tail), flush=True)

        time.sleep(args.tail)
        for p in (p_cam, p_nas):
            try:
                p.wait(timeout=25)
            except subprocess.TimeoutExpired:
                p.terminate()

    # ---- collect post-event evidence -------------------------------------
    _, hardened, _ = cam("cat /tmp/rrtsp_hardened.log")
    (out / "rrtsp_hardened.log").write_text(hardened)
    _, wd, _ = cam("cat /tmp/sd/wd_stream.log")
    (out / "wd_stream.log").write_text(wd)
    _, pids, _ = cam('echo "rrtsp=$(pidof rRTSPServer) rmm=$(pidof rmm) uptime=$(cat /proc/uptime)"')
    (out / "camera_state.txt").write_text(pids)

    log_cmd = (
        '%s logs --since 10m frigate 2>&1 | '
        'grep -E "go2rtc|producer.go|No frames received|crashed unexpectedly|'
        'No new recording segments|restart" | tail -120' % DOCKER
    )
    _, glog, _ = run(nas(nas_root_script(log_cmd, pw)), timeout=90)
    (out / "frigate_go2rtc_10m.log").write_text(glog)

    post_cmd = ('%s logs frigate 2>&1 | grep -cE '
                '"No frames received|crashed unexpectedly|No new recording segments"' % DOCKER)
    _, post_cnt, _ = run(nas(nas_root_script(post_cmd, pw)), timeout=60)

    # ---- merged timeline --------------------------------------------------
    tx = parse_pairs(tx_path)
    rc = parse_pairs(recv_path)
    dtx, drc = deltas(tx), deltas(rc)
    epochs = sorted(set(list(dtx) + list(drc)))
    events = [e for e, msg in hardened_epochs(hardened) if e >= t_start - 5]

    csv_lines = ["epoch,utc,tx_Bps,go2rtc_recv_Bps,flag"]
    for e in epochs:
        t = dtx.get(e)
        r = drc.get(e)
        flags = []
        if t is not None and t < 15 * 1024:
            flags.append("TX_LOW")
        if t is not None and t < 5 * 1024:
            flags.append("TX_COLLAPSE")
        if r is not None and r < 5000:
            flags.append("RECV_STALL")
        if e in events:
            flags.append("TEARDOWN")
        csv_lines.append("%d,%s,%s,%s,%s" %
                         (e, utc(e), "" if t is None else t,
                          "" if r is None else r, "|".join(flags)))
    (out / "timeline.csv").write_text("\n".join(csv_lines) + "\n")

    # ---- derived windows --------------------------------------------------
    stall_text = stall_path.read_text(errors="replace")
    m = re.search(r"inject_start_epoch=(\d+)", stall_text)
    inj_start = int(m.group(1)) if m else t_start
    m = re.search(r"inject_end_epoch=(\d+)", stall_text)
    inj_end = int(m.group(1)) if m else inj_start + inj_dur
    m = re.search(r"PLAY ok.*e=(\d+)", stall_text)
    play_e = int(m.group(1)) if m else inj_start
    stall_end = inj_end

    teardown = [e for e in events if "tearing down" in
                dict((e, m) for e, m in hardened_epochs(hardened)).get(e, "")]
    tx_low = [e for e in sorted(dtx) if inj_start <= e <= stall_end + args.tail
              and dtx[e] < 15 * 1024]
    tx_recover = None
    if tx_low:
        first_low = tx_low[0]
        for e in sorted(dtx):
            if e > first_low and dtx[e] > 30 * 1024:
                tx_recover = e
                break
    recv_stall = [e for e in sorted(drc) if inj_start <= e <= stall_end + args.tail
                  and drc[e] < 5000]
    recv_recover = None
    if recv_stall:
        for e in sorted(drc):
            if e > recv_stall[0] and drc[e] > 20000:
                recv_recover = e
                break

    s = []
    s.append("=== quant_stall_ab label=%s inject=%s ===" % (args.label, args.inject))
    s.append("window: %s .. %s UTC (e=%d .. %d)" %
             (utc(t_start), utc(t_start + inj_dur + args.tail), t_start,
              t_start + inj_dur + args.tail))
    s.append("injection start         : %d (%s UTC)" % (inj_start, utc(inj_start)))
    s.append("PLAY/stop-reading epoch : %d (%s UTC)" % (play_e, utc(play_e)))
    s.append("injection released      : %d (%s UTC)" % (stall_end, utc(stall_end)))
    s.append("P4v2 teardown epochs    : %s" %
             (", ".join("%d(%s)" % (e, utc(e)) for e in teardown) or "none"))
    s.append("TX low(<15KB/s) first    : %s" %
             ("%d (%s UTC)" % (tx_low[0], utc(tx_low[0])) if tx_low else "none"))
    s.append("TX recover(>30KB/s)      : %s" %
             ("%d (%s UTC)" % (tx_recover, utc(tx_recover)) if tx_recover else "none"))
    s.append("go2rtc recv stall first  : %s" %
             ("%d (%s UTC)" % (recv_stall[0], utc(recv_stall[0])) if recv_stall else "none"))
    s.append("go2rtc recv resume       : %s" %
             ("%d (%s UTC)" % (recv_recover, utc(recv_recover)) if recv_recover else "none"))
    if tx_low and tx_recover:
        s.append("camera egress recovery window : %ds" % (tx_recover - tx_low[0]))
    if recv_stall and recv_recover:
        s.append("consumer recovery window      : %ds" % (recv_recover - recv_stall[0]))
    if teardown and recv_recover:
        s.append("teardown -> consumer resume   : %ds" % (recv_recover - teardown[0]))
    if recv_recover:
        s.append("release -> consumer resume    : %ds" % (recv_recover - stall_end))
        s.append("inject -> consumer resume     : %ds" % (recv_recover - inj_start))
    s.append("Frigate error counters        : before=%s after=%s" %
             (pre_cnt.strip(), post_cnt.strip()))
    s.append("camera state                  : %s" % pids.strip())
    summary = "\n".join(s) + "\n"
    (out / "summary.txt").write_text(summary)
    print(summary, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
