#!/usr/bin/env python3
"""Static vertical-streak (H264 concealment) audit for recorded segments.

Why a SECOND ruler exists
-------------------------
`concealment_audit.py` uses ffmpeg signalstats YDIF, a *frame-to-frame
difference*.  A longer P6 observation on 2026-11-08 showed it cannot be
used to claim "no green screen":

  * 22.47.mp4 logged 41 spikes, peak YDIF 29.25.  Visual inspection of the
    peak frame (09:22:56 CST) shows a perfectly clean picture - a person
    simply walked close to the lens.  In a room that is still for most of
    the segment (median YDIF 0.08), real motion blows past the 8x ratio.
  * 21.17.mp4 logged ZERO spikes, yet its frame at 5s (09:21:23 CST) has
    heavy grey/white vertical smear across the whole lower half.  Smear
    that persists for the entire segment is *static*, so a difference
    metric stays low and the tool is blind to it.
  * ffmpeg's own decoder diagnostics do not discriminate either.  Every
    10s segment, clean or smeared, reports exactly 8 "error while decoding
    MB", 7 "corrupt decoded frame", 8 "bytestream" lines.  That is the
    camera's background bitstream defect, not a per-segment signal.

This tool measures the *spatial* signature instead.  Decoder concealment
smear is a field of vertical stripes, so in the lower half of the frame the
mean horizontal gradient H is much larger than the mean vertical gradient V:

    streak  <=>  H / V >= 1.4        (lower half, scaled to 320x180 gray)

Calibration against visually inspected frames (2026-11-08, cam1):

    21.17.mp4 @5s    heavy static smear, 0 YDIF spikes  -> 1.839  STREAK
    18.17.mp4 @5s    heavy static smear                  -> 2.335  STREAK
    24.38.mp4 @2s    heavy static smear                  -> 1.780  STREAK
    22.47.mp4 @9s    clean, YDIF 29.25                  -> 0.983  clean
    24.08.mp4 @1.6s  clean, motion blur only            -> 1.055  clean
    06.07.mp4 @5s    clean                              -> 0.933  clean

Limitations: calibrated for this fixed indoor scene and for the
vertical-stripe form of concealment.  Genuine vertically striped objects
(curtains, blinds, pleated fabric) can raise H/V too, and smear of a
different shape (blocks, tiles) would not be detected.  Always confirm a
new scene with a visual spot-check.  This tool is a *sampling* audit: it
inspects --samples frames per segment, not every frame.

Usage:
    python3 streak_audit.py --from "2026-11-08 00:36:00" \
                            --to   "2026-11-08 01:05:00" \
                            [--out logs/streak-p5b.txt] [--samples 3]

Times are UTC (camera clock and the recording directory are UTC).
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys

W, H = 320, 180
FRAME_BYTES = W * H
LOWER = H // 2


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


def gradient_ratio(frame):
    """Mean |dI/dx| / mean |dI/dy| over the lower half of a gray frame."""
    hs = vs = hc = vc = 0
    for y in range(LOWER, H):
        base = y * W
        row = frame[base:base + W]
        for x in range(1, W):
            hs += abs(row[x] - row[x - 1])
            hc += 1
        if y > LOWER:
            prev = frame[base - W:base]
            for x in range(W):
                vs += abs(row[x] - prev[x])
                vc += 1
    if not hc or not vc:
        return 0.0
    return (hs / hc) / ((vs / vc) + 1e-6)


def sample_frames(path, count):
    """Decode `count` evenly spaced gray frames (one every ~3s) as raw bytes."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-i", path,
           "-vf", "scale=%d:%d,format=gray,fps=1/3" % (W, H),
           "-frames:v", str(count),
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        return []
    raw = p.stdout
    return [raw[i:i + FRAME_BYTES]
            for i in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/media/frigate/recordings")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--threshold", type=float, default=1.4,
                    help="lower-half H/V ratio classified as streak (default 1.4)")
    ap.add_argument("--samples", type=int, default=3,
                    help="frames sampled per segment (default 3, one every 3s)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    start, end = utc(args.start), utc(args.end)
    segs = segments_in_window(args.root, start, end)
    lines = [
        "window UTC            : %s .. %s" % (start, end),
        "segments in window    : %d" % len(segs),
        "samples per segment   : %d (one frame every ~3s)" % args.samples,
        "streak rule           : lower-half mean|dI/dx| / mean|dI/dy| >= %.2f" % args.threshold,
        "",
    ]

    total_frames = streak_frames = hit_segments = 0
    details = []
    per_minute = {}

    for seg_start, path in segs:
        frames = sample_frames(path, args.samples)
        if not frames:
            continue
        seg_hit = 0
        for i, frame in enumerate(frames):
            r = gradient_ratio(frame)
            total_frames += 1
            if r >= args.threshold:
                streak_frames += 1
                seg_hit += 1
                wall = seg_start + dt.timedelta(seconds=i * 3.0)
                per_minute[wall.strftime("%H:%M")] = per_minute.get(wall.strftime("%H:%M"), 0) + 1
                details.append("%s  %s  sample_t=%5.1f  H/V=%6.3f  STREAK" %
                               (wall.strftime("%Y-%m-%d %H:%M:%S"),
                                os.path.basename(path), i * 3.0, r))
        if seg_hit:
            hit_segments += 1

    lines.append("frames sampled        : %d" % total_frames)
    lines.append("streak frames         : %d" % streak_frames)
    lines.append("affected segments     : %d / %d" % (hit_segments, len(segs)))
    if total_frames:
        lines.append("streak frame rate     : %.2f%% of sampled frames" %
                     (100.0 * streak_frames / total_frames))
    if segs:
        lines.append("affected segment rate : %.2f%% of segments" %
                     (100.0 * hit_segments / len(segs)))
    lines.append("")
    lines.append("=== streak frames per minute (UTC) ===")
    lines.append(" ".join("%s=%d" % (k, per_minute[k]) for k in sorted(per_minute))
                 or "(none)")
    lines.append("")
    lines.append("=== streak frame details (UTC) ===")
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
