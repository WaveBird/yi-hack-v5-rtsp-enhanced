#!/usr/bin/env python3
"""P18 low-overhead production acceptance monitor (40 min default).

P18 = camera-side "congestion drops frames, never disconnects":

  * liveMedia/RTPInterface.cpp  output queue 192KB -> 512KB, per-frame drain
    with frameOff, rrtspOutqDropOldest() drops the OLDEST WHOLE interleaved
    frames at a complete-NAL boundary (FU-A S bit aware) instead of
    shutdown(SHUT_RDWR). Only hard send() errors or 30s of ZERO progress
    still disconnect.
  * src/rRTSPServer.cpp         P4v2 false-positive guard: 15KB/s -> 1KB/s,
    3 rounds -> 15 rounds, 20s -> 60s teardown cooldown.

This monitor is deliberately low frequency to avoid diagnostic self-noise
(high-volume ffprobe was shown to amplify Frigate maintainer lag):

  * once per minute: pure filename counting for every minute of the window
    (a "zero segment minute" never appears in a file-based dict, so the
    final report explicitly enumerates every minute);
  * once per minute: exactly ONE ffprobe JSON call for container duration,
    video/audio frame counts and audio first-packet PTS (P17 drifted 56.57s);
  * once per 5 minutes: one SSH to the camera reading cumulative event
    counters, so the acceptance criterion "camera-side disconnects == 0"
    is measured directly rather than inferred from Frigate logs.

v2 fix: /api/logs/frigate is a rolling buffer.  The first poll replays
leftovers from any earlier event (e.g. the planned deploy/restart window at
20:42 CST vs monitor start 20:44 CST), which v1 counted as fresh failures and
forced verdict=CHECK.  v2 rejects lines whose bracketed local timestamp is
older than the monitor start and reports the skipped count explicitly.
v3 fix: refresh the filename census once more after the loop, so the final
minute is not reported as a false "low minute" from a partial snapshot.

Usage: python3 tools/p18_production_monitor.py [SECONDS] [LOGFILE]
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
CAMERA = "root@192.0.2.10"
CAM_LOG = "/tmp/rrtsp_hardened.log"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/case-p18/p18_monitor.log"

start = time.time()
start_utc = datetime.datetime.now(datetime.timezone.utc)
# Frigate container logs are stamped in the SAME local timezone as this host
# (CST / UTC+8), while the monitor's own emit() prefix is UTC.  Keep a naive
# local start so log-buffer replay from before the window can be rejected.
start_local = datetime.datetime.now()
seen = set()
historical_skipped = 0
minute_counts = collections.Counter()
samples = []
buckets = collections.Counter()
error_lines = []

BUCKETS = (
    ("rtp_bad_cseq", "bad cseq"),
    ("aac_decode", "[aac @"),
    ("non_monotonic", "Non-monoton"),
    ("watchdog_restart", "Terminating the existing ffmpeg"),
    ("unable_to_read", "Unable to read frames"),
    ("maintainer_lag", "Unable to keep up"),
    ("missed_packets", "Missed some packets"),
    ("connection_timeout", "Connection timed out"),
)


def hour_dirs():
    """Only the current and previous UTC hour -> no multi-thousand-file stat storm."""
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    for h in ((now.hour - 1) % 24, now.hour):
        p = os.path.join(ROOT, day, "%02d" % h, CAM)
        if os.path.isdir(p):
            yield "%02d" % h, p


def count_recent():
    out = {}
    for h, p in hour_dirs():
        c = collections.Counter()
        for f in os.listdir(p):
            if f.endswith(".mp4"):
                c[f[:2]] += 1
        for m, n in c.items():
            out["%s:%s" % (h, m)] = n
    return out


def probe_one():
    """One ffprobe JSON call on the second-newest finished segment."""
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    picked = None
    for h in (now.hour, (now.hour - 1) % 24):
        p = os.path.join(ROOT, day, "%02d" % h, CAM)
        if not os.path.isdir(p):
            continue
        fs = [f for f in os.listdir(p) if f.endswith(".mp4")]
        if len(fs) >= 2:
            fs.sort(key=lambda f: os.path.getmtime(os.path.join(p, f)))
            picked = os.path.join(p, fs[-2])
            break
    if not picked:
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_packets", "-show_streams",
         "-show_entries",
         "packet=stream_index,pts_time:stream=index,codec_type:format=duration",
         "-of", "json", picked],
        capture_output=True, text=True, timeout=40)
    try:
        j = json.loads(r.stdout)
    except Exception:
        return None
    try:
        dur = float(j["format"]["duration"])
    except Exception:
        dur = 0.0
    idx = {}
    for s in j.get("streams", []):
        if s.get("codec_type") in ("video", "audio"):
            idx[s["index"]] = s["codec_type"]
    cnt = {"video": 0, "audio": 0}
    first = {}
    for pk in j.get("packets", []):
        t = idx.get(pk.get("stream_index"))
        if not t:
            continue
        cnt[t] += 1
        if t not in first:
            try:
                first[t] = float(pk.get("pts_time") or 0)
            except (TypeError, ValueError):
                first[t] = -1.0
    return dict(file=os.path.basename(picked), dur=dur,
                v=cnt["video"], a=cnt["audio"],
                v_first=first.get("video", -1.0),
                a_first=first.get("audio", -1.0))


def camera_counts():
    """Cumulative camera-side event counters; differences are the P18 verdict."""
    cmd = (
        "grep -c 'P5: tcp send failure' %s; "
        "grep -c 'tearing down sessions' %s; "
        "grep -c 'P18: congestion drop' %s; "
        "grep -c 'P11: tcp output queue blocked' %s"
    ) % (CAM_LOG, CAM_LOG, CAM_LOG, CAM_LOG)
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", CAMERA, cmd],
            capture_output=True, text=True, timeout=25)
        nums = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
        if len(nums) >= 4:
            return dict(p5=nums[0], teardown=nums[1],
                        drop=nums[2], blocked=nums[3])
    except Exception:
        pass
    return None


def frigate_logs():
    try:
        with urllib.request.urlopen(API, timeout=8) as r:
            return json.load(r).get("lines", [])
    except Exception:
        return []


# /api/logs/frigate returns a rolling buffer of the last ~100 lines.  On the
# first poll it replays EVERYTHING left over from earlier events (including a
# planned deploy/restart window), which would otherwise be counted as fresh
# P18 failures and force verdict=CHECK.  Each line carries a bracketed local
# timestamp, e.g. "[2026-11-08 20:42:37]"; reject anything older than the
# monitor start.
LOG_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")


def log_ts(line):
    m = LOG_TS_RE.search(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


os.makedirs(os.path.dirname(LOG), exist_ok=True)
with open(LOG, "w") as fp:
    def emit(s):
        line = "%s %s" % (datetime.datetime.now(
            datetime.timezone.utc).strftime("%H:%M:%S"), s)
        print(line, flush=True)
        fp.write(line + "\n")
        fp.flush()

    emit("P18 monitor start=%s duration=%ds" %
         (start_utc.strftime("%Y-%m-%d %H:%M:%S UTC"), DURATION))
    base_cam = camera_counts()
    emit("camera baseline=%s" % (base_cam or "unavailable"))

    tick = 0
    while time.time() - start < DURATION:
        elapsed = int(time.time() - start)
        counts = count_recent()
        for k, v in counts.items():
            if v > minute_counts[k]:
                minute_counts[k] = v
        s = probe_one()
        if s:
            samples.append(s)
        for ln in frigate_logs():
            if "CPU detectors are not recommended" in ln:
                continue
            ts = log_ts(ln)
            if ts is not None and ts < start_local - datetime.timedelta(seconds=30):
                historical_skipped += 1
                continue
            if (" ERROR " in ln or "watchdog" in ln.lower() or
                    "Unable to keep up" in ln or "Non-monoton" in ln):
                key = ln[:120]
                if key in seen:
                    continue
                seen.add(key)
                error_lines.append(ln)
                for name, needle in BUCKETS:
                    if needle in ln:
                        buckets[name] += 1
                        break
                else:
                    buckets["other"] += 1
                emit("FRIGATE " + ln[:200])
        emit("[t+%4ds] sample=%s" % (
            elapsed, ("%s dur=%.2fs v=%d a=%d a_first=%.3fs" %
                      (s["file"], s["dur"], s["v"], s["a"], s["a_first"]))
                     if s else "n/a"))
        tick += 1
        if tick % 5 == 0:
            cc = camera_counts()
            if cc and base_cam:
                emit("CAMERA +%4ds p5=%+d teardown=%+d drop=%+d blocked=%+d" %
                     (elapsed, cc["p5"] - base_cam["p5"],
                      cc["teardown"] - base_cam["teardown"],
                      cc["drop"] - base_cam["drop"],
                      cc["blocked"] - base_cam["blocked"]))
            else:
                emit("CAMERA +%4ds counters unavailable" % elapsed)
        time.sleep(60)

    # v3: refresh the pure-filename census once more before reporting.
    # The last in-loop tick samples the current minute while it is still
    # running, so without this the final minute looks like a false "low"
    # minute (e.g. 13:13=3 instead of 6).
    for k, v in count_recent().items():
        if v > minute_counts[k]:
            minute_counts[k] = v

    emit("=" * 64)
    emit("P18 MONITOR FINAL REPORT")
    end_dt = datetime.datetime.now(datetime.timezone.utc)
    emit("window=%s .. %s UTC" % (start_utc.strftime("%H:%M:%S"),
                                  end_dt.strftime("%H:%M:%S")))
    start_min = start_utc.hour * 60 + start_utc.minute + 1
    end_min = end_dt.hour * 60 + end_dt.minute - 1
    window = {}
    for m in range(start_min, end_min + 1):
        k = "%02d:%02d" % (m // 60, m % 60)
        window[k] = minute_counts.get(k, 0)
    missing = sorted(k for k, v in window.items() if v == 0)
    low = sorted((k, v) for k, v in window.items() if 0 < v < 4)
    emit("minutes_observed=%d zero_segment_minutes=%s low_minutes=%s" %
         (len(window), missing or "none", low or "none"))
    emit("segments_per_minute=" + " ".join("%s=%d" % (k, v)
                                          for k, v in sorted(window.items())))
    if samples:
        emit("samples=%d dur=%.2f..%.2fs video_min=%d audio_min=%d "
             "audio<100=%d a_first=%.3f..%.3fs dur>11.5s=%d a_first>0.5s=%d" %
             (len(samples), min(s["dur"] for s in samples),
              max(s["dur"] for s in samples), min(s["v"] for s in samples),
              min(s["a"] for s in samples),
              sum(1 for s in samples if s["a"] < 100),
              min(s["a_first"] for s in samples),
              max(s["a_first"] for s in samples),
              sum(1 for s in samples if s["dur"] > 11.5),
              sum(1 for s in samples if s["a_first"] > 0.5)))
    emit("frigate_historical_lines_skipped=%d (pre-window log-buffer replay)" %
         historical_skipped)
    emit("frigate_error_lines_in_window=%d buckets=%s" %
         (len(error_lines), dict(buckets)))

    final_cam = camera_counts()
    cam_delta = None
    if final_cam and base_cam:
        cam_delta = {k: final_cam[k] - base_cam[k] for k in final_cam}
    emit("camera_baseline=%s" % base_cam)
    emit("camera_final=%s" % final_cam)
    emit("camera_delta=%s" % cam_delta)
    cam_ok = bool(cam_delta and cam_delta["p5"] == 0 and
                  cam_delta["teardown"] == 0)
    emit("camera_no_disconnect=%s" % ("PASS" if cam_ok else "CHECK"))

    ok = (not missing and not low and samples and cam_ok and
          min(s["a"] for s in samples) >= 100 and
          min(s["v"] for s in samples) >= 150 and
          max(s["dur"] for s in samples) <= 11.5 and
          max(s["a_first"] for s in samples) <= 0.5 and
          buckets["watchdog_restart"] == 0 and
          buckets["connection_timeout"] == 0 and
          buckets["aac_decode"] == 0 and buckets["non_monotonic"] == 0)
    emit("verdict=%s" % ("PASS" if ok else "CHECK"))
