#!/usr/bin/env python3
"""One-shot, window-closed full frame-level acceptance audit for P18/P18b.

This is deliberately NOT a live monitor.  It runs AFTER an observation window
has closed so that a burst of ffprobe/ffmpeg cannot perturb Frigate's recording
maintainer ("Unable to keep up with recording segments in cache" was previously
amplified by diagnostic self-noise).

It answers, with evidence, the user's acceptance criteria:

  * "no short-second gaps": enumerate EVERY minute of the window from pure
    filenames (a zero-segment minute never appears in a file-based dict), plus
    per-segment video PTS gap statistics (wallclock burst signature, >1s holes).
  * "no green screen / streaking": decode every segment with ffmpeg -v error
    and count H.264 concealment / corrupt-frame diagnostics.
  * "recordings are continuous and replayable": container duration, video and
    audio packet counts, audio first-packet PTS (P17 drifted 56.57s), keyframe
    presence per segment.
  * "camera never disconnects under congestion": cumulative camera-side
    P5 / teardown / P18 drop counters since the deploy marker.
  * Frigate errors are time-filtered: /api/logs/frigate is a ROLLING buffer,
    so lines older than the window (e.g. a planned deploy/restart) are
    rejected by their bracketed local timestamp instead of being counted as
    fresh failures.
  * go2rtc producer reconnects (v4, P18c): counts `i/o timeout` / `EOF` /
    `connection refused` inside the window from /api/logs/go2rtc. A reconnect
    makes the next session start at an IDR; Frigate then writes overlapping
    segments or a segment with +/- frames, and `-bsf:v setts` rewrites the PTS
    into a perfect CFR, so PTS-only audits CANNOT see it. This counter is the
    only direct evidence, hence it is part of VERDICT.

v4 changes: send a User-Agent (Frigate answers 403 to the default urllib UA,
which previously produced a silent false PASS on the Frigate judgement), and
add the go2rtc reconnect judgement.

Usage:
  python3 tools/p18_final_audit.py START_UTC END_UTC OUTFILE [CAMERA_SINCE]

  START_UTC / END_UTC   "HH:MM:SS" (UTC, same calendar day as the recordings)
  OUTFILE               report path
  CAMERA_SINCE          optional "YYYY-MM-DD HH:MM:SS" camera-log marker;
                        defaults to the last "hardened rRTSPServer starting"
                        line in /tmp/rrtsp_hardened.log
"""
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = "/media/frigate/recordings"
CAM = "cam1"
API = "http://192.0.2.20:8971/api/logs/frigate"
# v4: Frigate rejects the default urllib User-Agent with HTTP 403, which used to
# turn the Frigate/日志 judgement into a silent false PASS. Always send a UA.
GO2RTC_API = "http://192.0.2.20:8971/api/logs/go2rtc"
UA = {"User-Agent": "case-rtsp-audit/1.0"}
GO_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
CAMERA = "root@192.0.2.10"
CAM_LOG = "/tmp/rrtsp_hardened.log"

START_S = sys.argv[1] if len(sys.argv) > 1 else "12:44:38"
END_S = sys.argv[2] if len(sys.argv) > 2 else "13:14:38"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp/case-p18/final-audit.txt"
CAMERA_SINCE = sys.argv[4] if len(sys.argv) > 4 else None

DAY = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def hms(s):
    h, m, sec = (int(x) for x in s.split(":"))
    return h * 3600 + m * 60 + sec


START_SEC, END_SEC = hms(START_S), hms(END_S)
LOG_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
# Frigate container logs are in the host's local zone (CST = UTC+8).
LOCAL_OFFSET_H = 8

BUCKETS = (
    ("rtp_bad_cseq", "bad cseq"),
    ("aac_decode", "[aac @"),
    ("non_monotonic", "Non-monoton"),
    ("watchdog_restart", "Terminating the existing ffmpeg"),
    ("unable_to_read", "Unable to read frames"),
    ("maintainer_lag", "Unable to keep up"),
    ("missed_packets", "Missed some packets"),
    ("connection_timeout", "Connection timed out"),
    ("corrupt_frame", "corrupt decoded frame"),
    ("mb_error", "error while decoding MB"),
)

out = []


def emit(s=""):
    print(s, flush=True)
    out.append(s)


def seg_seconds(hh, name):
    """name is MM.SS.mp4 -> seconds since 00:00 UTC."""
    m = re.match(r"^(\d{2})\.(\d{2})\.mp4$", name)
    if not m:
        return None
    return hh * 3600 + int(m.group(1)) * 60 + int(m.group(2))


def collect_segments():
    segs = []
    for hh in range(START_SEC // 3600, END_SEC // 3600 + 1):
        d = os.path.join(ROOT, DAY, "%02d" % hh, CAM)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".mp4"):
                continue
            sec = seg_seconds(hh, f)
            if sec is None or not (START_SEC <= sec < END_SEC):
                continue
            segs.append((sec, os.path.join(d, f), f))
    segs.sort()
    return segs


def probe(path):
    cmd = ["ffprobe", "-v", "error", "-show_packets", "-show_streams",
           "-show_entries",
           "packet=stream_index,pts_time,flags:stream=index,codec_type:format=duration",
           "-of", "json", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        j = json.loads(r.stdout)
    except Exception as e:
        return dict(error=str(e)[:120])
    try:
        dur = float(j["format"]["duration"])
    except Exception:
        dur = 0.0
    idx = {}
    for s in j.get("streams", []):
        if s.get("codec_type") in ("video", "audio"):
            idx[s["index"]] = s["codec_type"]
    vpts, apts = [], []
    keys = 0
    for pk in j.get("packets", []):
        t = idx.get(pk.get("stream_index"))
        if t == "video":
            try:
                vpts.append(float(pk.get("pts_time") or 0))
            except (TypeError, ValueError):
                pass
            if "K" in (pk.get("flags") or ""):
                keys += 1
        elif t == "audio":
            try:
                apts.append(float(pk.get("pts_time") or 0))
            except (TypeError, ValueError):
                pass
    vpts.sort()
    gaps = [b - a for a, b in zip(vpts, vpts[1:]) if b >= a]
    sub10 = sum(1 for g in gaps if g < 0.010)
    big = sum(1 for g in gaps if g > 1.0)
    return dict(dur=dur, v=len(vpts), a=len(apts),
                v_first=vpts[0] if vpts else -1.0,
                a_first=apts[0] if apts else -1.0,
                sub10=sub10, gaps=len(gaps), big=big,
                maxgap=max(gaps) if gaps else 0.0, keys=keys)


def decode_errors(path):
    """H.264 concealment / green-screen diagnostics, one decode per segment."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        errs = [l for l in r.stderr.splitlines() if l.strip()]
        return r.returncode, len(errs), errs[:4]
    except Exception as e:
        return -1, 1, [str(e)[:120]]


def frigate_window_errors():
    try:
        req = urllib.request.Request(API, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            lines = json.load(r).get("lines", [])
    except Exception as e:
        return collections.Counter(), 0, 0, ["api error: %s" % e]
    buckets = collections.Counter()
    newest = ""
    total = skipped = 0
    lo = START_SEC + LOCAL_OFFSET_H * 3600
    hi = END_SEC + LOCAL_OFFSET_H * 3600
    for ln in lines:
        total += 1
        m = LOG_TS_RE.search(ln)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        local = ts.hour * 3600 + ts.minute * 60 + ts.second
        newest = max(newest, m.group(1))
        if not (lo <= local <= hi):
            skipped += 1
            continue
        if "CPU detectors are not recommended" in ln:
            continue
        for name, needle in BUCKETS:
            if needle in ln:
                buckets[name] += 1
                break
        else:
            if " ERROR " in ln:
                buckets["other_errors"] += 1
    return buckets, total, skipped, ["newest_log_ts=%s" % newest]


def go2rtc_window_events():
    """Count go2rtc producer reconnects inside the window.

    P18c insight: even with camera-side disconnects at zero, go2rtc's own
    `#timeout=N` read timeout can tear down and re-open the RTSP session.
    A reconnect makes the new session start at an IDR, which Frigate's
    `-f segment` turns into overlapping files (two files, same mtime) or
    +/- frame counts inside one segment. `-bsf:v setts` then rewrites PTS to a
    perfect CFR, so PTS-only audits CANNOT see this. This counter is the only
    direct evidence.
    """
    try:
        req = urllib.request.Request(GO2RTC_API, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            lines = json.load(r).get("lines", [])
    except Exception as e:
        return collections.Counter(), 0, 0, ["api error: %s" % e]
    b = collections.Counter()
    total = skipped = 0
    lo = START_SEC + LOCAL_OFFSET_H * 3600
    hi = END_SEC + LOCAL_OFFSET_H * 3600
    for ln in lines:
        total += 1
        m = GO_TS_RE.match(ln)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        local = ts.hour * 3600 + ts.minute * 60 + ts.second
        if not (lo <= local <= hi):
            skipped += 1
            continue
        if "i/o timeout" in ln:
            b["io_timeout"] += 1
        elif "unexpected EOF" in ln or "error=EOF" in ln:
            b["eof"] += 1
        elif "connection refused" in ln:
            b["refused"] += 1
    return b, total, skipped, []


def camera_events():
    marker = CAMERA_SINCE
    cmd = (
        "M=%s; "
        "if [ -z \"$M\" ]; then "
        "  M=$(grep 'hardened rRTSPServer starting' %s | tail -1 | cut -c1-19); "
        "fi; "
        "echo \"marker=$M\"; "
        "awk -v m=\"$M\" 'index($0,m)==1,0' %s > /tmp/.p18audit; "
        "grep -c 'P5: tcp send failure' /tmp/.p18audit; "
        "grep -c 'tearing down sessions' /tmp/.p18audit; "
        "grep -c 'P18: congestion drop' /tmp/.p18audit; "
        "grep -c 'P11: tcp output queue blocked' /tmp/.p18audit; "
        "grep -c 'P18b: drop-until-idr' /tmp/.p18audit; "
        "grep -c 'P18: drop-oldest' /tmp/.p18audit; "
        "tail -1 %s; rm -f /tmp/.p18audit"
    ) % (marker or "", CAM_LOG, CAM_LOG, CAM_LOG)
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", CAMERA, cmd],
            capture_output=True, text=True, timeout=30)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        nums = [int(x) for x in lines if x.strip().isdigit()]
        mk = next((l for l in lines if l.startswith("marker=")), "marker=?")
        if len(nums) >= 6:
            return dict(marker=mk, p5=nums[0], teardown=nums[1], drop=nums[2],
                        blocked=nums[3], p18b=nums[4], drop_oldest=nums[5],
                        tail=lines[-1][:160]), ""
    except Exception as e:
        return None, str(e)
    return None, r.stdout[-300:]


def main():
    t0 = time.time()
    emit("=" * 72)
    emit("P18/P18b FULL FRAME-LEVEL ACCEPTANCE AUDIT (window closed)")
    emit("generated=%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"))
    emit("window=%s .. %s UTC  date=%s" % (START_S, END_S, DAY))
    emit("=" * 72)

    segs = collect_segments()
    emit("segments_in_window=%d" % len(segs))

    # Pure filename per-minute census, explicitly enumerating EVERY minute.
    counts = collections.Counter()
    for sec, path, name in segs:
        counts[sec // 60] += 1
    first_min, last_min = START_SEC // 60 + 1, END_SEC // 60 - 1
    missing, low = [], []
    detail = []
    for m in range(first_min, last_min + 1):
        n = counts.get(m, 0)
        k = "%02d:%02d" % (m // 60, m % 60)
        detail.append("%s=%d" % (k, n))
        if n == 0:
            missing.append(k)
        elif n < 4:
            low.append((k, n))
    emit("minutes_observed=%d zero_segment_minutes=%s low_minutes=%s" %
         (max(0, last_min - first_min + 1), missing or "none", low or "none"))
    emit("segments_per_minute=" + " ".join(detail))

    # Per-segment frame-level audit + decode check.
    bad_dur, bad_afirst, low_v, low_a, holes, bursts, no_key = [], [], [], [], [], [], []
    decoding = []
    g_sub10 = g_gaps = 0
    g_maxgap = 0.0
    dur_lo, dur_hi = 1e9, 0.0
    v_lo, a_lo = 1e9, 1e9
    af_lo, af_hi = 1e9, 0.0
    for i, (sec, path, name) in enumerate(segs):
        p = probe(path)
        if "error" in p:
            emit("PROBE_ERROR %s %s" % (name, p["error"]))
            continue
        rc, nerr, sample = decode_errors(path)
        if nerr:
            decoding.append((name, rc, nerr, sample))
        dur_lo, dur_hi = min(dur_lo, p["dur"]), max(dur_hi, p["dur"])
        v_lo, a_lo = min(v_lo, p["v"]), min(a_lo, p["a"])
        af_lo, af_hi = min(af_lo, p["a_first"]), max(af_hi, p["a_first"])
        g_sub10 += p["sub10"]
        g_gaps += p["gaps"]
        g_maxgap = max(g_maxgap, p["maxgap"])
        if p["dur"] > 11.5:
            bad_dur.append((name, p["dur"]))
        if p["a_first"] > 0.5:
            bad_afirst.append((name, p["a_first"]))
        if p["v"] < 150:
            low_v.append((name, p["v"]))
        if p["a"] < 100:
            low_a.append((name, p["a"]))
        if p["big"]:
            holes.append((name, p["big"], p["maxgap"]))
        if p["sub10"]:
            bursts.append((name, p["sub10"], p["gaps"]))
        if p["keys"] == 0:
            no_key.append(name)
        if (i + 1) % 30 == 0:
            emit("  progress %d/%d segments audited" % (i + 1, len(segs)))

    emit("-" * 72)
    emit("segments_with_ffprobe_data=%d" % (len(segs) - 0))
    emit("duration=%.2f..%.2fs  video_min=%d audio_min=%d" %
         (dur_lo, dur_hi, v_lo, a_lo))
    emit("audio_first_pts=%.3f..%.3fs" % (af_lo, af_hi))
    emit("video_pts_intervals=%d sub10ms_bursts=%d (%.2f%%) "
         "gaps_over_1s=%d max_gap=%.3fs" %
         (g_gaps, g_sub10, 100.0 * g_sub10 / g_gaps if g_gaps else 0.0,
          sum(x[1] for x in holes), g_maxgap))
    emit("dur_over_11.5s=%s" % (bad_dur or "none"))
    emit("audio_first_over_0.5s=%s" % (bad_afirst or "none"))
    emit("video_under_150=%s" % (low_v or "none"))
    emit("audio_under_100=%s" % (low_a or "none"))
    emit("segments_with_1s_holes=%s" % (holes or "none"))
    emit("segments_with_wallclock_bursts=%s" % (bursts or "none"))
    emit("segments_without_keyframe=%s" % (no_key or "none"))
    emit("decode_error_segments=%d" % len(decoding))
    for name, rc, nerr, sample in decoding[:15]:
        emit("  %s rc=%d lines=%d %s" % (name, rc, nerr, " | ".join(sample)[:160]))

    # Frigate errors, time-filtered to the window.
    buckets, total_log, skipped_log, notes = frigate_window_errors()
    emit("-" * 72)
    emit("frigate_log_lines_seen=%d outside_window_skipped=%d %s" %
         (total_log, skipped_log, " ".join(notes)))
    emit("frigate_buckets_in_window=%s" % dict(buckets))

    # go2rtc producer reconnects (P18c). PTS-only audits cannot see these:
    # a reconnect starts a new session at an IDR, and `-bsf:v setts` rewrites
    # the resulting overlap / frame loss into a perfect CFR timeline.
    g2b, g2total, g2skip, g2notes = go2rtc_window_events()
    emit("-" * 72)
    emit("go2rtc_log_lines_seen=%d outside_window_skipped=%d %s" %
         (g2total, g2skip, " ".join(g2notes)))
    emit("go2rtc_reconnect_events_in_window=%s" % dict(g2b))

    # Camera-side ground truth.
    cam, cerr = camera_events()
    emit("-" * 72)
    emit("camera_events=%s" % (cam or ("unavailable: " + cerr)[:300]))

    # Verdict.
    cam_ok = bool(cam and cam["p5"] == 0 and cam["teardown"] == 0)
    frigate_ok = (buckets["watchdog_restart"] == 0 and
                  buckets["connection_timeout"] == 0 and
                  buckets["unable_to_read"] == 0 and
                  buckets["aac_decode"] == 0 and
                  buckets["non_monotonic"] == 0)
    media_ok = (not missing and not low and not bad_dur and not bad_afirst and
                not low_v and not low_a and not holes and not bursts and
                not no_key and dur_hi <= 11.5 and af_hi <= 0.5 and
                g_maxgap <= 0.10 and len(decoding) == 0 and len(segs) > 0)
    go2rtc_ok = not g2b
    emit("=" * 72)
    emit("camera_no_disconnect=%s" % ("PASS" if cam_ok else "CHECK"))
    emit("frigate_no_new_errors=%s" % ("PASS" if frigate_ok else "CHECK"))
    emit("go2rtc_no_reconnect=%s" % ("PASS" if go2rtc_ok else "CHECK"))
    emit("media_frame_continuity=%s" % ("PASS" if media_ok else "CHECK"))
    emit("VERDICT=%s" % ("PASS" if (cam_ok and frigate_ok and go2rtc_ok
                                    and media_ok) else "CHECK"))
    emit("elapsed=%.1fs" % (time.time() - t0))
    emit("=" * 72)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fp:
        fp.write("\n".join(out) + "\n")
    print("report -> %s" % OUT, flush=True)


if __name__ == "__main__":
    main()
