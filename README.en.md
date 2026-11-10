# yi-hack-v5-rtsp-enhanced

**A hardened media layer + acceptance harness for Xiaomi Yi cameras (Hi3518E).**

Built on top of [yi-hack-v5](https://github.com/alienatedsec/yi-hack-v5) 0.4.1.
No firmware reflash. Every change is reversible in seconds.

> **Not "v6", and not a firmware.** The name carries the `yi-hack-v5` prefix on purpose:
> this is an overlay. `yi-hack-v5` is still the foundation — replace it and this project
> ceases to exist. Version numbering continues from the predecessor
> (`yi-hack-v5-rrtsp-hardened` 0.5.1 → this project 0.6.0) rather than jumping a generation.

## The problem

Yi cameras feeding Frigate/NVRs fail in ways that **look fine on every dashboard**:

| Symptom | What it actually is |
|---|---|
| Picture freezes to black, connection still "up" | `RTPInterface` detaches RTP channels but keeps the TCP socket |
| Segment count / filenames / `recordings` table all continuous | 4–8 s of **no video frames inside a single mp4** |
| Green screen / vertical streaks | IDR **silently truncated** between FIFO and StreamParser |
| Recordings filed under last week's date | Camera clock 7 days off, no outbound NTP available |
| Every segment's audio slightly shorter than video | Consumer-side `-af asetpts` compresses the timeline on every dropped frame |

Common thread: **any single metric looks healthy. Only a frame-level timeline shows the truth.**

## What you get

1. **Hardened media path** — 16 patches over `rRTSPServer` + `h264grabber`.
2. **A verification harness** — run it yourself, get `PASS` / `CHECK` / `FAIL`.

The second part is the point. **We have withdrawn more conclusions than we published** —
all recorded in [`docs/FINDINGS.md`](docs/FINDINGS.md) §6.

## Quick start

```bash
./media/scripts/setup.sh
docker build -t yihackv5rtsp-build media/
docker run --rm -v "$PWD":/build yihackv5rtsp-build sh /build/media/scripts/build.sh
./deploy/install.sh root@<CAMERA_IP>
./verify/verdict.sh --camera <CAMERA_IP> --minutes 30
```

## Scope & honesty

- Verified on **yi_home_1080p (y20) + yi-hack-v5 0.4.1 + go2rtc 1.9.2 only**.
- The camera's Wi-Fi egress still stalls for 4–15 s periodically.
  Confirmed **not** an RTSP-layer issue. Final fix: a wired camera.
- We do not redistribute vendor firmware or vendor binaries. See [`docs/LEGAL.md`](docs/LEGAL.md).

## Docs

[FINDINGS](docs/FINDINGS.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [MEASUREMENT](docs/MEASUREMENT.md) · [COMPATIBILITY](docs/COMPATIBILITY.md) · [LEGAL](docs/LEGAL.md) · [PLAN](docs/PLAN.md)

License: GPL-3.0.
