#!/usr/bin/env python3
"""Detect the PTS signature of ``-use_wallclock_as_timestamps 1``.

Why this exists
---------------
P13 left four 1.3-2.1 s "clean" holes in the recording frame timeline. They
were originally attributed to a camera-side (rmm / Wi-Fi) stall. P14 proved
that attribution wrong: the holes are created by Frigate's input flag
``-use_wallclock_as_timestamps 1``, which stamps *every RTP packet read from
one socket read()* with the wall clock instant of that read.

The fingerprint is a large fraction of consecutive video PTS intervals below
10 ms (a burst of packets all stamped almost identically), followed by a
1-2 s jump when a read() happens to be delayed. With normal RTP timestamps
(go2rtc already normalises the camera clock drift) the intervals are a flat
50 ms at 20 fps and there are no sub-10 ms intervals at all.

Usage
-----
    python3 wallclock_signature_audit.py DIR [DIR ...]
    python3 wallclock_signature_audit.py --threshold 0.010 /tmp/case-p14/ab/A

Only ffprobe is required. Read-only: never modifies the recordings.
"""
import argparse
import glob
import os
import subprocess
import sys


def pts(path, stream):
    """Return sorted packet PTS (seconds) for one stream."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_packets", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=90)
    out = []
    for ln in r.stdout.splitlines():
        ln = ln.strip().rstrip(",")
        if not ln or ln == "N/A":
            continue
        try:
            out.append(float(ln))
        except ValueError:
            pass
    return out


def audit_dir(path, threshold, show_segments):
    files = sorted(glob.glob(os.path.join(path, "*.mp4")))
    if not files:
        print("  (no mp4 files)")
        return None
    st = dict(segments=0, frames=0, audio=0, intervals=0, burst=0,
              gaps10=0, maxgap=0.0, worst="", ffprobe_errors=0,
              min_audio=10 ** 9)
    gap_list = []
    for f in files:
        try:
            v, a = pts(f, "v:0"), pts(f, "a:0")
        except Exception as e:
            st["ffprobe_errors"] += 1
            print("  ffprobe error: %s (%s)" % (os.path.basename(f), e))
            continue
        st["segments"] += 1
        st["frames"] += len(v)
        st["audio"] += len(a)
        st["min_audio"] = min(st["min_audio"], len(a))
        ds = [v[i + 1] - v[i] for i in range(len(v) - 1)]
        nburst = sum(1 for d in ds if 0 <= d < threshold)
        st["intervals"] += len(ds)
        st["burst"] += nburst
        seg_max = max(ds) if ds else 0.0
        for d in ds:
            if d > 1.0:
                st["gaps10"] += 1
                gap_list.append((os.path.basename(f), d))
            if d > st["maxgap"]:
                st["maxgap"], st["worst"] = d, os.path.basename(f)
        if show_segments:
            print("  %-16s v=%4d a=%4d burst=%4d/%-4d maxgap=%.3fs" %
                  (os.path.basename(f), len(v), len(a), nburst, len(ds),
                   seg_max))
    return st, gap_list


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", help="directories of mp4 segments")
    ap.add_argument("--threshold", type=float, default=0.010,
                    help="burst interval threshold in seconds (default 0.010)")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the per-directory summary")
    args = ap.parse_args()

    for d in args.dirs:
        print("=== %s ===" % d)
        res = audit_dir(d, args.threshold, not args.quiet)
        if not res:
            continue
        st, gaps = res
        ratio = 100.0 * st["burst"] / max(1, st["intervals"])
        print("  --- summary ---")
        print("  segments=%d frames=%d audio=%d ffprobe_errors=%d" %
              (st["segments"], st["frames"], st["audio"],
               st["ffprobe_errors"]))
        print("  burst(<%.3fs)=%d/%d (%.2f%%)" %
              (args.threshold, st["burst"], st["intervals"], ratio))
        print("  gaps>1.0s=%d  largest=%.3fs in %s" %
              (st["gaps10"], st["maxgap"], st["worst"]))
        print("  audio frames min per segment=%d" % st["min_audio"])
        print("  verdict: %s" %
              ("WALLCLOCK SIGNATURE PRESENT" if ratio > 1.0
               else "normal RTP timestamps (no wallclock stamping)"))
        if gaps:
            print("  gap detail: %s" % gaps[:20])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
