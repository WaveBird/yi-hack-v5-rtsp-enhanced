#!/usr/bin/env python3
"""Low-overhead P15 production acceptance monitor.

Why low overhead
----------------
Earlier full-window ffprobe sweeps overlapped with
``frigate.record.maintainer: Unable to keep up with recording segments in
cache`` and probably made the consumer stall worse. This monitor therefore
uses pure filename counting every minute and exactly ONE ffprobe call per
minute; the full frame-level audit is a separate one-shot run after the
window closes.

Tracks
------
* segments per wall-clock minute (detects whole-segment outages)
* duration / video frames / audio frames of one completed segment
  (detects the P14b "1.5 s / 1 audio frame" degeneration)
* Frigate ERROR / watchdog lines (detects muxer storms and ffmpeg restarts)

Usage: python3 tools/p15_production_monitor.py [SECONDS] [LOGFILE]
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
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/case-p15/p15_monitor.log"

start = time.time()
start_utc = datetime.datetime.now(datetime.timezone.utc)
seen_errors = set()
minute_counts = collections.Counter()
samples = []
errors = []


def minute_dirs():
    """Yield (HH:MM, path) for all minute directories of today, UTC."""
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
    """One ffprobe call on the newest *completed* segment."""
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
    vals = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    dur = 0.0
    v = a = 0
    for x in vals:
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


with open(LOG, "w") as fp:
    def emit(s):
        line = "%s %s" % (datetime.datetime.now(
            datetime.timezone.utc).strftime("%H:%M:%S"), s)
        print(line, flush=True)
        fp.write(line + "\n")
        fp.flush()

    emit("P15 monitor start=%s duration=%ds" %
         (start_utc.strftime("%Y-%m-%d %H:%M:%S UTC"), DURATION))
    while time.time() - start < DURATION:
        elapsed = int(time.time() - start)
        counts = count_recent()
        for k, v in counts.items():
            if v > minute_counts[k]:
                minute_counts[k] = v
        now = datetime.datetime.now(datetime.timezone.utc)
        cur = now.strftime("%H:%M")
        prev = (now - datetime.timedelta(minutes=1)).strftime("%H:%M")
        s = probe_one()
        if s:
            samples.append(s)
        for ln in frigate_logs():
            if "CPU detectors are not recommended" in ln:
                continue
            if (" ERROR " in ln or "watchdog" in ln.lower() or
                    "Unable to keep up" in ln or "Non-monotonous" in ln):
                key = ln[:120]
                if key not in seen_errors:
                    seen_errors.add(key)
                    errors.append(ln)
                    emit("FRIGATE " + ln[:220])
        emit("[t+%4ds] %s=%d %s=%d sample=%s" %
             (elapsed, prev, counts.get(prev, 0), cur, counts.get(cur, 0),
              ("%s dur=%.2fs v=%d a=%d" %
               (s["file"], s["dur"], s["v"], s["a"])) if s else "n/a"))
        time.sleep(60)

    # ---- final report ----
    emit("=" * 60)
    emit("P15 MONITOR FINAL REPORT")
    emit("window=%s .. %s" % (start_utc.strftime("%H:%M:%S"),
                              datetime.datetime.now(
                                  datetime.timezone.utc).strftime("%H:%M:%S")))
    # Enumerate EVERY minute explicitly: a minute with zero segments has no
    # files at all, so it never appears in minute_counts. Filtering the dict
    # alone would silently hide exactly the outage we are looking for.
    start_min = start_utc.hour * 60 + start_utc.minute
    end_dt = datetime.datetime.now(datetime.timezone.utc)
    end_min = end_dt.hour * 60 + end_dt.minute
    window = {}
    for m in range(start_min + 1, end_min + 1):
        k = "%02d:%02d" % (m // 60, m % 60)
        window[k] = minute_counts.get(k, 0)
    missing = sorted(k for k, v in window.items() if v == 0)
    low = sorted((k, v) for k, v in window.items() if 0 < v < 4)
    emit("minutes_observed=%d zero_segment_minutes=%s low_minutes=%s" %
         (len(window), missing or "none", low or "none"))
    if samples:
        emit("samples=%d dur_min=%.2fs dur_max=%.2fs audio_min=%d "
             "audio<10=%d video_min=%d" %
             (len(samples), min(s["dur"] for s in samples),
              max(s["dur"] for s in samples),
              min(s["a"] for s in samples),
              sum(1 for s in samples if s["a"] < 10),
              min(s["v"] for s in samples)))
    emit("frigate_error_lines=%d" % len(errors))
    emit("verdict=%s" % ("PASS" if not missing and not low and samples and
                         min(s["a"] for s in samples) >= 10 and
                         len(errors) == 0 else "CHECK"))
