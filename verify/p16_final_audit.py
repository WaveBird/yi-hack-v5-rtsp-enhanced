#!/usr/bin/env python3
"""One-shot full frame-level audit for the P16 observation window.

Run ONLY after the observation window has closed. During the window use
p16_production_monitor.py (one ffprobe per minute); large ffprobe sweeps
during recording are a known diagnostic self-disturbance.

One ffprobe call per segment (JSON) yields both streams' PTS in a single
process, which halves the process count versus wallclock_signature_audit.py.

Usage:
  python3 tools/p16_final_audit.py START_UTC END_UTC [OUT.txt]
  START_UTC / END_UTC are "HH:MM" in the recordings' UTC day.
Example:
  python3 tools/p16_final_audit.py 11:18 11:58 \
      logs/p16-evidence/p16-final-audit.txt
"""
import collections
import datetime
import glob
import json
import os
import subprocess
import sys

ROOT = "/media/frigate/recordings"
CAM = "cam1"
DAY = "2026-11-08"


def hm(s):
    h, m = (int(x) for x in s.split(":"))
    return h * 60 + m


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_packets", "-show_streams",
         "-show_entries",
         "packet=stream_index,pts_time:stream=index,codec_type:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None
    idx = {}
    for s in d.get("streams", []):
        if s.get("codec_type") in ("video", "audio"):
            idx[s["index"]] = s["codec_type"]
    pts = {"video": [], "audio": []}
    for p in d.get("packets", []):
        t = idx.get(p.get("stream_index"))
        if t and p.get("pts_time") is not None:
            try:
                pts[t].append(float(p["pts_time"]))
            except (TypeError, ValueError):
                pass
    try:
        dur = float(d.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        dur = 0.0
    return pts["video"], pts["audio"], dur


def stats(v):
    if len(v) < 2:
        return 0, 0, 0.0, 0
    ds = [b - a for a, b in zip(v, v[1:]) if b >= a]
    burst = sum(1 for x in ds if x < 0.010)
    gaps = sum(1 for x in ds if x > 1.0)
    return burst, len(ds), (max(ds) if ds else 0.0), gaps


def main():
    lo, hi = hm(sys.argv[1]), hm(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else None
    files = []
    for h in ("%02d" % i for i in range(24)):
        files += glob.glob(os.path.join(ROOT, DAY, h, CAM, "*.mp4"))
    def key(f):
        hh = os.path.basename(os.path.dirname(f))
        mm, ss = os.path.basename(f).split(".")[0:2]
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    files = [f for f in files if lo * 60 <= key(f) < hi * 60]
    files.sort(key=key)

    lines = []
    def emit(s):
        print(s)
        lines.append(s)

    emit("P16 FINAL FRAME-LEVEL AUDIT")
    emit("window=%s..%s UTC on %s" % (sys.argv[1], sys.argv[2], DAY))
    emit("segments=%d" % len(files))

    per_min = collections.Counter()
    for f in files:
        per_min["%02d:%02d" % (key(f) // 3600, (key(f) % 3600) // 60)] += 1
    window = {}
    for m in range(lo + 1, hi):
        k = "%02d:%02d" % (m // 60, m % 60)
        window[k] = per_min.get(k, 0)
    missing = sorted(k for k, v in window.items() if v == 0)
    low = sorted((k, v) for k, v in window.items() if 0 < v < 4)
    emit("minutes_observed=%d zero_segment_minutes=%s low_minutes=%s" %
         (len(window), missing or "none", low or "none"))
    emit("segments_per_minute=" + " ".join("%s=%d" % (k, v)
                                          for k, v in sorted(window.items())))

    tot_v = tot_a = tot_burst = tot_ds = tot_gaps = 0
    worst = 0.0
    worst_file = ""
    amin = 10 ** 9
    amin_file = ""
    audio_low = []
    vmin = 10 ** 9
    ffprobe_errors = []
    prev_end = None
    cross_gaps = []
    for f in files:
        r = probe(f)
        if r is None:
            ffprobe_errors.append(os.path.basename(f))
            continue
        v, a, dur = r
        b, n, mx, g = stats(v)
        tot_v += len(v)
        tot_a += len(a)
        tot_burst += b
        tot_ds += n
        tot_gaps += g
        if mx > worst:
            worst, worst_file = mx, os.path.basename(f)
        if len(a) < amin:
            amin, amin_file = len(a), os.path.basename(f)
        if len(a) < 100:
            audio_low.append("%s(a=%d)" % (os.path.basename(f), len(a)))
        vmin = min(vmin, len(v))
        start = key(f)
        end = start + dur
        if prev_end is not None and start - prev_end > 0:
            cross_gaps.append((start - prev_end, os.path.basename(f)))
        prev_end = max(prev_end or 0, end)

    emit("video_frames=%d audio_frames=%d ffprobe_errors=%d" %
         (tot_v, tot_a, len(ffprobe_errors)))
    ratio = (100.0 * tot_burst / tot_ds) if tot_ds else 0.0
    emit("burst(<0.010s)=%d/%d (%.2f%%)" % (tot_burst, tot_ds, ratio))
    emit("intra_segment_gaps>1.0s=%d largest=%.3fs in %s" %
         (tot_gaps, worst, worst_file))
    emit("audio_frames_min=%d in %s  segments_with_a<100=%d %s" %
         (amin, amin_file, len(audio_low),
          (" ".join(audio_low[:8]) if audio_low else "none")))
    emit("video_frames_min=%d" % vmin)
    if cross_gaps:
        cg = max(cross_gaps)
        emit("largest_cross_segment_gap=%.3fs before %s "
             "(filename-epoch estimate, +/- a few seconds)" % (cg[0], cg[1]))
    ok = (not missing and not low and not ffprobe_errors and
          tot_gaps == 0 and ratio < 1.0 and amin >= 100 and vmin >= 150)
    emit("verdict=%s" % ("PASS" if ok else "CHECK"))
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        print("written to " + out)


if __name__ == "__main__":
    main()
