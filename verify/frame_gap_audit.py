#!/usr/bin/env python3
"""Frame-level playback continuity auditor for Frigate recordings.

WHY THIS EXISTS (2026-11-08)
---------------------------
Segment filenames, the Frigate ``recordings`` SQLite table, segment counts,
go2rtc ``recv`` counters and Frigate "no frames" alerts are all WRITE-side
proxies.  A collapse can leave an 8-second hole *inside* an mp4 while every
one of those proxies still looks continuous.  The only reliable acceptance
test is a global wall-clock video-frame timeline built from ffprobe packet PTS.

With ``-use_wallclock_as_timestamps 1``, a segment named ``HH/MM.SS.mp4``
starts at wall clock HH:MM:SS and its PTS are seconds from that start, so::

    frame_wallclock_epoch = segment_start_epoch + pts_time

This tool enumerates the segments in a UTC window, extracts every video
packet PTS, stitches them into one timeline and reports gaps > threshold.

USAGE
-----
    python3 frame_gap_audit.py \
        --date 2026-11-08 --start 00:20:00 --end 00:40:00 \
        --camera cam1 \
        --root /media/frigate/recordings \
        --threshold 1.5 --out ../logs/p5-closure/frame-timeline.txt

Exit code is always 0; the report carries PASS/FAIL.  ``PASS`` means no
video-frame gap exceeded the threshold in the requested window.  A window
with zero frames is reported as NO-DATA, never silently as PASS.
"""

import argparse
import calendar
import datetime as dt
import os
import re
import subprocess
import sys

SEG_RE = re.compile(r"^(\d{2})\.(\d{2})\.mp4$")
UTC = dt.timezone.utc


def epoch(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def stamp(e):
    return dt.datetime.fromtimestamp(e, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def list_segments(root, date_s, camera, win_start, win_end):
    """Return [(seg_start_epoch, absolute_path)] for segments near the window."""
    y, mo, d = (int(x) for x in date_s.split("-"))
    h0 = int(dt.datetime.fromtimestamp(win_start, UTC).strftime("%H"))
    h1 = int(dt.datetime.fromtimestamp(win_end, UTC).strftime("%H"))
    hours = range(h0, h1 + 1)
    out = []
    for h in hours:
        hdir = os.path.join(root, date_s, "%02d" % h, camera)
        if not os.path.isdir(hdir):
            continue
        hour_base = epoch(y, mo, d, h, 0, 0)
        for name in os.listdir(hdir):
            m = SEG_RE.match(name)
            if not m:
                continue
            seg_start = hour_base + int(m.group(1)) * 60 + int(m.group(2))
            if win_start - 20 <= seg_start <= win_end + 20:
                out.append((seg_start, os.path.join(hdir, name)))
    return sorted(out)


def probe_pts(path):
    """Video packet PTS times in seconds, or None on ffprobe failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_packets", "-show_entries", "packet=pts_time",
        "-of", "csv=p=0", path,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=120)
    if p.returncode != 0:
        return None, p.stderr.strip()
    pts = []
    for line in p.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line or line == "N/A":
            continue
        try:
            pts.append(float(line))
        except ValueError:
            continue
    return pts, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="UTC date YYYY-MM-DD")
    ap.add_argument("--start", required=True, help="UTC start HH:MM:SS")
    ap.add_argument("--end", required=True, help="UTC end HH:MM:SS")
    ap.add_argument("--camera", default="cam1")
    ap.add_argument("--root", default="/media/frigate/recordings")
    ap.add_argument("--threshold", type=float, default=1.5,
                    help="gap in seconds considered a visible hole (default 1.5)")
    ap.add_argument("--out", default="", help="optional report path")
    args = ap.parse_args()

    y, mo, d = (int(x) for x in args.date.split("-"))
    def parse_hms(text):
        h, mi, s = (int(x) for x in text.split(":"))
        return epoch(y, mo, d, h, mi, s)
    win_start, win_end = parse_hms(args.start), parse_hms(args.end)
    if win_end <= win_start:
        win_end += 86400  # window crosses UTC midnight

    segs = list_segments(args.root, args.date, args.camera, win_start, win_end)
    lines = []
    lines.append("frame-level playback continuity audit")
    lines.append("window (UTC)     : %s .. %s (%ds)" %
                 (stamp(win_start), stamp(win_end), win_end - win_start))
    lines.append("camera / root    : %s / %s" % (args.camera, args.root))
    lines.append("gap threshold    : %.3fs" % args.threshold)
    lines.append("segments scanned : %d" % len(segs))
    lines.append("")

    all_frames = []   # (wallclock_epoch, segment_name)
    per_seg = []
    for seg_start, path in segs:
        pts, err = probe_pts(path)
        name = os.path.basename(path)
        if pts is None:
            per_seg.append((seg_start, name, 0, 0.0, "FFPROBE_ERROR: " + err))
            continue
        internal_max = 0.0
        for a, b in zip(pts, pts[1:]):
            if b - a > internal_max:
                internal_max = b - a
        per_seg.append((seg_start, name, len(pts), internal_max, "OK"))
        for t in pts:
            all_frames.append((seg_start + t, name))

    lines.append("per-segment detail (start UTC | name | video frames | max internal gap | status)")
    for seg_start, name, n, g, status in per_seg:
        lines.append("  %s | %s | %4d | %6.3fs | %s" %
                     (stamp(seg_start)[11:], name, n, g, status))
    lines.append("")

    gaps = []
    for (t0, n0), (t1, n1) in zip(all_frames, all_frames[1:]):
        gap = t1 - t0
        if gap > args.threshold:
            gaps.append((t0, t1, gap, n0, n1))

    lines.append("global video-frame timeline")
    lines.append("  frames in window     : %d" % len(all_frames))
    if all_frames:
        lines.append("  first / last frame   : %s / %s" %
                     (stamp(all_frames[0][0]), stamp(all_frames[-1][0])))
    lines.append("  gaps > %.3fs         : %d" % (args.threshold, len(gaps)))
    for t0, t1, gap, n0, n1 in gaps:
        lines.append("    %s -> %s  gap=%6.3fs  (%s -> %s)" %
                     (stamp(t0)[11:], stamp(t1)[11:], gap, n0, n1))
    if all_frames:
        biggest = max((b[0] - a[0] for a, b in zip(all_frames, all_frames[1:])),
                      default=0.0)
        lines.append("  largest frame gap    : %.3fs" % biggest)
        if gaps:
            verdict = "FAIL - playback is NOT frame-contiguous"
        else:
            verdict = "PASS - no frame gap above %.3fs" % args.threshold
    else:
        verdict = "NO-DATA - no video frames found; do not read as PASS"
    lines.append("")
    lines.append("CONCLUSION: " + verdict)
    lines.append("NOTE: this audits playback continuity, not recording writes. "
                 "Segment counts / the recordings table / go2rtc recv cannot "
                 "substitute for this check.")

    report = "\n".join(lines) + "\n"
    sys.stdout.write(report)
    if args.out:
        outdir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(outdir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        sys.stderr.write("report written to %s\n" % args.out)


if __name__ == "__main__":
    main()
