#!/usr/bin/env python3
"""Long-window frame-level acceptance audit after removing wallclock timestamps.

Same acceptance criteria as logs/p13-long-window-20261108.md, so the two
windows are directly comparable. Also tracks the sub-10ms burst-interval
ratio, which is the signature of -use_wallclock_as_timestamps 1.
"""
import calendar
import datetime as dt
import glob
import os
import subprocess
import sys
import time

ROOT = "/media/frigate/recordings"
CAM = "cam1"
REPORT = "/tmp/case-p14/p14_long_report.txt"
START_HMS = "08:46:00"
DURATION = 9000  # 2.5h, same as P13 baseline window
UTC = dt.timezone.utc


def epoch(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def pts(path, stream):
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


def main():
    h, mi, s = (int(x) for x in START_HMS.split(":"))
    start = epoch(2026, 11, 8, h, mi, s)
    end = start + DURATION
    seen = set()
    st = dict(segments=0, frames=0, intervals=0, sub10=0, audio=0,
              gaps10=0, gaps15=0, gaps05=0, maxgap=0.0, min_audio=10**9,
              ffprobe_errors=0, worst_seg="")
    gap_list = []
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)

    with open(REPORT, "w", buffering=1) as rep:
        rep.write("P14 long-window frame-level acceptance audit\n")
        rep.write("window UTC: %s .. %s (%ds)\n" %
                  (dt.datetime.fromtimestamp(start, UTC),
                   dt.datetime.fromtimestamp(end, UTC), DURATION))
        rep.write("change: Frigate ffmpeg.input_args drops "
                  "-use_wallclock_as_timestamps 1\n")
        rep.write("Frigate restarted 2026-11-08 08:45:48 UTC; "
                  "window starts after that.\n\n")

        last_write = 0
        while time.time() < end:
            for hh in ("08", "09", "10", "11"):
                hdir = os.path.join(ROOT, "2026-11-08", hh, CAM)
                if not os.path.isdir(hdir):
                    continue
                for path in sorted(glob.glob(hdir + "/*.mp4")):
                    if path in seen:
                        continue
                    try:
                        mt = os.path.getmtime(path)
                    except OSError:
                        continue
                    # skip files written before the change, or still being written
                    if mt < start + 20 or time.time() - mt < 15:
                        continue
                    seen.add(path)
                    name = os.path.basename(path)
                    try:
                        v, a = pts(path, "v:0"), pts(path, "a:0")
                    except Exception:
                        st["ffprobe_errors"] += 1
                        continue
                    st["segments"] += 1
                    st["frames"] += len(v)
                    st["audio"] += len(a)
                    st["min_audio"] = min(st["min_audio"], len(a))
                    ds = [v[i + 1] - v[i] for i in range(len(v) - 1)]
                    st["intervals"] += len(ds)
                    st["sub10"] += sum(1 for d in ds if d < 0.010)
                    for d in ds:
                        if d > 0.5:
                            st["gaps05"] += 1
                        if d > 1.0:
                            st["gaps10"] += 1
                            gap_list.append((name, d))
                        if d > 1.5:
                            st["gaps15"] += 1
                        if d > st["maxgap"]:
                            st["maxgap"], st["worst_seg"] = d, name

            if time.time() - last_write >= 300:
                last_write = time.time()
                el = int(time.time() - (end - DURATION))
                rep.write("[t+%4ds] seg=%3d frames=%6d sub10=%d/%d "
                          "maxgap=%.3fs >1.0s=%d >1.5s=%d audio_min=%d\n" %
                          (el, st["segments"], st["frames"], st["sub10"],
                           st["intervals"], st["maxgap"], st["gaps10"],
                           st["gaps15"], st["min_audio"]))
                rep.flush()
            time.sleep(30)

        ratio = 100.0 * st["sub10"] / max(1, st["intervals"])
        verdict = "PASS" if st["gaps10"] == 0 and st["ffprobe_errors"] == 0 else "FAIL"
        rep.write("\n=== FINAL ===\n")
        rep.write("segments=%d frames=%d ffprobe_errors=%d\n" %
                  (st["segments"], st["frames"], st["ffprobe_errors"]))
        rep.write("sub-10ms intervals=%d/%d (%.2f%%)\n" %
                  (st["sub10"], st["intervals"], ratio))
        rep.write("gaps >0.5s=%d  >1.0s=%d  >1.5s=%d\n" %
                  (st["gaps05"], st["gaps10"], st["gaps15"]))
        rep.write("largest internal gap=%.3fs in %s\n" %
                  (st["maxgap"], st["worst_seg"]))
        rep.write("audio frames total=%d min per segment=%d\n" %
                  (st["audio"], st["min_audio"]))
        rep.write("gaps>1.0s detail: %s\n" % gap_list[:20])
        rep.write("CONCLUSION: %s - %s\n" %
                  (verdict, "frame timeline contiguous" if verdict == "PASS"
                   else "short-second holes remain"))
    print(open(REPORT).read())


if __name__ == "__main__":
    main()
