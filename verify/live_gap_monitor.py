#!/usr/bin/env python3
"""Watch new Frigate segments for INTERNAL >1s video-frame gaps.

When one is seen, snapshot the camera 1s probe log around that UTC minute so
the source-side ground truth can be aligned with the playback hole.
"""
import datetime as dt
import os
import subprocess
import sys
import time

ROOT = "/media/frigate/recordings"
CAM = "cam1"
OUT = "/tmp/case-p14/gap_events.txt"
THRESHOLD = 1.0
UTC = dt.timezone.utc


def probe(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_packets", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    if p.returncode != 0:
        return []
    out = []
    for ln in p.stdout.splitlines():
        ln = ln.strip().rstrip(",")
        if not ln or ln == "N/A":
            continue
        try:
            out.append(float(ln))
        except ValueError:
            pass
    return out


def now_utc():
    return dt.datetime.now(UTC)


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 7200
    end = time.time() + duration
    seen = set()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", buffering=1) as f:
        f.write("# monitor start %s duration=%ds\n" %
                (now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), duration))
        while time.time() < end:
            t = now_utc()
            # scan the last 6 minutes of segments
            checked = 0
            for back in range(0, 7):
                d = t - dt.timedelta(minutes=back)
                hdir = os.path.join(ROOT, d.strftime("%Y-%m-%d"),
                                    d.strftime("%H"), CAM)
                if not os.path.isdir(hdir):
                    continue
                for name in sorted(os.listdir(hdir)):
                    if not (name[:2] + name[3:5]).isdigit() or not name.endswith(".mp4"):
                        continue
                    path = os.path.join(hdir, name)
                    key = path
                    if key in seen:
                        continue
                    # only files finished / at least 20s old
                    try:
                        age = time.time() - os.path.getmtime(path)
                    except OSError:
                        continue
                    if age < 15:
                        continue
                    seen.add(key)
                    checked += 1
                    pts = probe(path)
                    if not pts:
                        continue
                    worst = 0.0
                    at = 0.0
                    for a, b in zip(pts, pts[1:]):
                        if b - a > worst:
                            worst, at = b - a, a
                    if worst > THRESHOLD:
                        # segment filename is HH.MM.SS.mp4 (UTC)
                        seg = (int(d.strftime("%H")) * 3600 +
                               int(name[:2]) * 60 + int(name[3:5]))
                        ev = "%s +%.3fs = %s" % (
                            d.strftime("%Y-%m-%d"),
                            seg + at, (d.strftime("%Y-%m-%d") + " %02d:%s:%06.3f" %
                                       (int(d.strftime("%H")), name[:2],
                                        float(name[3:5]) + at)))
                        line = "%s INTERNAL_GAP %.3fs in %s/%s/%s frames=%d\n" % (
                            now_utc().strftime("%H:%M:%S"), worst,
                            d.strftime("%Y-%m-%d"), d.strftime("%H"),
                            name, len(pts))
                        f.write(line)
                        f.flush()
                        print(line.strip(), flush=True)
                        with open("/tmp/case-p14/probe_at_%s.log" %
                                  now_utc().strftime("%H%M%S"), "w") as pl:
                            subprocess.run(
                                ["ssh", "-o", "BatchMode=yes",
                                 "-o", "StrictHostKeyChecking=no",
                                 "-o", "ConnectTimeout=8",
                                 "root@192.0.2.10",
                                 "tail -120 /tmp/cam_probe.log"],
                                stdout=pl, stderr=subprocess.DEVNULL,
                                timeout=30)
            print("%s scanned=%d\n" % (now_utc().strftime("%H:%M:%S"), checked),
                  flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
