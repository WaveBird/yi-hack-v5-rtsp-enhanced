#!/usr/bin/env python3
"""Frame-to-frame YDIF spike audit.

WARNING: THIS IS NOT A RELIABLE GREEN-SCREEN VERDICT
----------------------------------------------------
This tool correctly killed the "count green pixels" idea, but on 2026-11-08 a
longer P6 observation disproved YDIF as a verdict too. It fails both ways:

  * FALSE POSITIVES - 22.47.mp4 logged 41 spikes, peak YDIF 29.25, yet the
    peak frame is visually clean: a person walked close to the lens. In a room
    that is still for most of the segment (median 0.08), real motion exceeds
    the 8x ratio.
  * FALSE NEGATIVES - 21.17.mp4 logged ZERO spikes but its frame at 5s shows
    heavy grey/white vertical smear across the entire lower half. Smear that
    persists for a whole segment is static, so a difference metric stays low.

ffmpeg's decoder diagnostics (8 MB errors / 7 corrupt frames per 10s segment)
are equally constant and useless as a discriminator. An early report that
claimed "green screen no longer appears" from this tool was WITHDRAWN.

Use `streak_audit.py` (lower-half mean|dI/dx| / mean|dI/dy| >= 1.4) plus a
visual spot-check instead. This tool is kept only for reproducing the A/B
numbers and for documenting why the metric cannot be trusted. See
../logs/green-screen-20261108.md section 2.3.

Why this exists
---------------
The user-visible "bottom of the picture is green" is NOT pure green pixels in the
mp4: an H264 decoder that loses a reference frame fills the missing macroblocks
with uninitialised YUV, which the browser/WebRTC player renders as green while
ffmpeg writing a PNG gives grey/white vertical smear. A "count green pixels"
scan therefore returns 0 even on a visibly corrupted frame (verified: frame
08:20:10 CST has heavy lower-half smear but 0.00% green pixels).

The proxy used here is the frame-to-frame difference (ffmpeg signalstats YDIF).
A concealment frame that SUDDENLY replaces the lower half with stripes spikes
far above the segment's own baseline. Normal motion raises the median but not
the ratio, hence the relative test (valid only for sudden, short-lived smear
in a mostly static scene):

    spike  <=>  YDIF > 3.0  AND  YDIF > 8 * segment median(YDIF)

Validated against known frames:
    20.10.mp4  known heavy smear   -> 1 spike (YDIF 18.61, median 0.05)
    18.42.mp4  clean               -> 0 spikes (max 0.59)
    31.17.mp4  person moving       -> 0 spikes (max 4.24, median 1.09)
    54.22.mp4  mild smear          -> 1 spike (max 3.63, median 0.07)

The P5b / P6 numbers this tool produced are archived for reference only; they
are NOT evidence that the green screen was fixed. Real A/B: streak_audit.py.

Usage:
    python3 concealment_audit.py --from "2026-11-08 00:36:00" \
                                 --to   "2026-11-08 01:05:00" \
                                 [--out logs/concealment-p5b.txt]

Times are UTC (camera clock and the recording directory are UTC).
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys


def utc(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def segments_in_window(root, start, end):
    """Yield (segment_start_utc, path) for mp4 files overlapping [start, end]."""
    out = []
    day = start.date()
    for hour in range(24):
        hdir = os.path.join(root, day.strftime("%Y-%m-%d"), "%02d" % hour, "cam1")
        if not os.path.isdir(hdir):
            continue
        for name in sorted(os.listdir(hdir)):
            m = re.match(r"^(\d\d)\.(\d\d)\.mp4$", name)
            if not m:
                continue
            t = dt.datetime(day.year, day.month, day.day, hour,
                            int(m.group(1)), int(m.group(2)))
            if start - dt.timedelta(seconds=15) <= t <= end:
                out.append((t, os.path.join(hdir, name)))
    return out


def ydif_frames(path, tmpfile):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-i", path,
           "-vf", "scale=160:90,signalstats,"
                  "metadata=print:key=lavfi.signalstats.YDIF:file=" + tmpfile,
           "-f", "null", "-"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=90)
    frames = []
    t = 0.0
    for line in open(tmpfile, errors="replace"):
        mt = re.search(r"pts_time:([\d.]+)", line)
        if mt:
            t = float(mt.group(1))
            continue
        mv = re.search(r"YDIF=([\d.]+)", line)
        if mv:
            frames.append((t, float(mv.group(1))))
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/media/frigate/recordings")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--abs-threshold", type=float, default=3.0)
    ap.add_argument("--ratio", type=float, default=8.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    start, end = utc(args.start), utc(args.end)
    segs = segments_in_window(args.root, start, end)
    tmpfile = "/tmp/concealment-ydif.txt"
    lines = []
    total_frames = 0
    total_spikes = 0
    hit_segments = 0
    details = []

    lines.append("window UTC            : %s .. %s" % (start, end))
    lines.append("segments in window    : %d" % len(segs))
    lines.append("spike rule            : YDIF > %.1f AND YDIF > %.1f x segment median"
                 % (args.abs_threshold, args.ratio))
    lines.append("")

    for seg_start, path in segs:
        frames = ydif_frames(path, tmpfile)
        if not frames:
            continue
        vals = sorted(v for _, v in frames)
        med = vals[len(vals) // 2]
        thresh = max(args.abs_threshold, args.ratio * med)
        spikes = [(t, v) for t, v in frames if v > thresh]
        total_frames += len(frames)
        total_spikes += len(spikes)
        if spikes:
            hit_segments += 1
        for t, v in spikes:
            wall = seg_start + dt.timedelta(seconds=t)
            details.append("%s  %s  frame_t=%7.3f  YDIF=%7.2f  median=%6.2f" %
                           (wall.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                            os.path.basename(path), t, v, med))

    lines.append("frames decoded        : %d" % total_frames)
    lines.append("concealment spikes    : %d" % total_spikes)
    lines.append("affected segments     : %d / %d" % (hit_segments, len(segs)))
    if total_frames:
        lines.append("spike rate            : %.3f%% of frames" %
                     (100.0 * total_spikes / total_frames))
    lines.append("spikes per 10 min     : %.2f" %
                 (total_spikes / max((end - start).total_seconds() / 600.0, 1e-9)))
    lines.append("")
    lines.append("=== spike timestamps (UTC) ===")
    lines.extend(details if details else ["(none)"])

    report = "\n".join(lines) + "\n"
    sys.stdout.write(report)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("written: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
