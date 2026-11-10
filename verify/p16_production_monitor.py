#!/usr/bin/env python3
"""P16 low-overhead production acceptance monitor (40 min default).

Differences from p15_production_monitor.py
------------------------------------------
* The final report enumerates only FULLY ELAPSED minutes. p15's report
  mistakenly included the still-running current minute and reported it as a
  "low minute" (3 segments) even though a later pure-ls count showed 6.
* Frigate ERROR lines are bucketed by kind so we can distinguish harmless
  upstream "RTP: bad cseq" noise (camera-side P5 disconnect, which P16 does
  NOT claim to eliminate) from the things P16 must eliminate: AAC decode
  storms, Non-monotonous DTS, and watchdog restarts.
* Exactly one ffprobe per minute; full frame-level PTS audit is a separate
  one-shot run AFTER the window closes (large ffprobe sweeps during the window
  are known to be a diagnostic self-disturbance).

Usage: python3 tools/p16_production_monitor.py [SECONDS] [LOGFILE]
"""
import collections
import datetime
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = "/media/frigate/recordings"
CAM = "cam1"
API = "http://192.0.2.20:8971/api/logs/frigate"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/case-p16/p16_monitor.log"

start = time.time()
start_utc = datetime.datetime.now(datetime.timezone.utc)
seen = set()
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
)


def minute_dirs():
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    for h in ("%02d" % i for i in range(24)):
        p = os.path.join(ROOT, day, h, CAM)
        if os.path.isdir(p):
            yield h, p


def count_recent():
    out = {}
    for h, p in minute_dirs():
        c = collections.Counter()
        for f in os.listdir(p):
            if f.endswith(".mp4"):
                c[f[:2]] += 1
        for m, n in c.items():
            out["%s:%s" % (h, m)] = n
    return out


def probe_one():
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    files = []
    for h in ("%02d" % i for i in range(24)):
        files += glob.glob(os.path.join(ROOT, day, h, CAM, "*.mp4"))
    if not files:
        return None
    files.sort(key=os.path.getmtime)
    f = files[-2] if len(files) > 1 else files[0]
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=codec_type,nb_frames", "-of", "csv=p=0", f],
        capture_output=True, text=True, timeout=30)
    dur, v, a = 0.0, 0, 0
    for x in (y.strip() for y in r.stdout.splitlines() if y.strip()):
        p = x.split(",")
        if len(p) >= 2 and p[0] == "video":
            v = int(p[1])
        elif len(p) >= 2 and p[0] == "audio":
            a = int(p[1])
        else:
            try:
                dur = float(x)
            except ValueError:
                pass
    return dict(file=os.path.basename(f), dur=dur, v=v, a=a)


def frigate_logs():
    try:
        with urllib.request.urlopen(API, timeout=8) as r:
            return json.load(r).get("lines", [])
    except Exception:
        return []


os.makedirs(os.path.dirname(LOG), exist_ok=True)
with open(LOG, "w") as fp:
    def emit(s):
        line = "%s %s" % (datetime.datetime.now(
            datetime.timezone.utc).strftime("%H:%M:%S"), s)
        print(line, flush=True)
        fp.write(line + "\n")
        fp.flush()

    emit("P16 monitor start=%s duration=%ds" %
         (start_utc.strftime("%Y-%m-%d %H:%M:%S UTC"), DURATION))

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
            elapsed, ("%s dur=%.2fs v=%d a=%d" %
                      (s["file"], s["dur"], s["v"], s["a"])) if s else "n/a"))
        time.sleep(60)

    # ---- final report: only fully elapsed minutes ----
    emit("=" * 64)
    emit("P16 MONITOR FINAL REPORT")
    end_dt = datetime.datetime.now(datetime.timezone.utc)
    emit("window=%s .. %s UTC" % (start_utc.strftime("%H:%M:%S"),
                                  end_dt.strftime("%H:%M:%S")))
    start_min = start_utc.hour * 60 + start_utc.minute + 1
    end_min = end_dt.hour * 60 + end_dt.minute - 1   # exclude current minute
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
             "audio<100=%d" %
             (len(samples), min(s["dur"] for s in samples),
              max(s["dur"] for s in samples), min(s["v"] for s in samples),
              min(s["a"] for s in samples),
              sum(1 for s in samples if s["a"] < 100)))
    emit("frigate_error_lines=%d buckets=%s" %
         (len(error_lines), dict(buckets)))
    ok = (not missing and not low and samples and
          min(s["a"] for s in samples) >= 100 and
          min(s["v"] for s in samples) >= 150 and
          buckets["watchdog_restart"] == 0 and
          buckets["aac_decode"] == 0 and buckets["non_monotonic"] == 0)
    emit("verdict=%s" % ("PASS" if ok else "CHECK"))
