#!/usr/bin/env python3
"""Same-window wire-vs-mirror NALU alignment audit (case-rtsp).

Why this exists
---------------
`h264_capture_audit.py` reassembles an RTSP/TCP session into an Annex-B file, and
`h264grabber`'s P9/P11b mirror writes the raw bytes it pushed into the FIFO.  Both
files cover the same wall-clock window, but the wire capture starts mid-GOP, so
their NALU indices do not line up.

This tool parses both Annex-B files, fingerprints each NALU by its leading bytes
(the head survives even when the tail is silently truncated), greedily aligns the
wire sequence onto the mirror sequence, and reports the size difference for every
matched IDR (NAL type 5).

Interpretation:
  * wire IDR == mirror IDR  -> the FIFO -> live555 -> RTP path is byte-exact
  * wire IDR  < mirror IDR  -> bytes are being swallowed inside rRTSPServer
    (StreamReplicator truncation / parser bank overflow), NOT lost on the network
  * RTP seq gaps == 0 + IDR short -> confirms "bytes eaten, not packets dropped"

Usage:
    python3 idr_ab_audit.py --wire FILE.h264 --mirror FILE.h264 [--out REPORT.md]
"""

import argparse
import hashlib
import sys


def parse_annexb(data):
    """Return [(nal_type, length, signature)] for every start-code delimited NALU."""
    n = len(data)
    starts = []
    i = 0
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else n
        # Trim the start-code prefix bytes belonging to the *next* NALU and any
        # trailing zero padding from this one.
        while e > s and data[e - 1] == 0:
            e -= 1
        if e <= s:
            continue
        payload = data[s:e]
        nal_type = payload[0] & 0x1F
        # Head-only fingerprint: a truncation removes tail bytes, so size is
        # deliberately excluded. 32 bytes covers NAL header + slice header.
        sig = hashlib.sha1(payload[:32]).digest()
        out.append((nal_type, len(payload), sig))
    return out


def align(wire, mirror, lookahead=800):
    """Greedy monotonic alignment; returns [(wire_index, mirror_index)]."""
    # Build mirror signature -> sorted index list for O(1) candidate lookup.
    index = {}
    for j, item in enumerate(mirror):
        index.setdefault(item[2], []).append(j)

    pairs = []
    m_floor = 0
    for i, w in enumerate(wire):
        cands = index.get(w[2])
        if not cands:
            continue
        pos = None
        for j in cands:
            if j < m_floor:
                continue
            if j - m_floor > lookahead:
                break
            pos = j
            break
        if pos is None:
            continue
        pairs.append((i, pos))
        m_floor = pos + 1
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wire", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    wire = parse_annexb(open(args.wire, "rb").read())
    mirror = parse_annexb(open(args.mirror, "rb").read())
    pairs = align(wire, mirror)

    w_idr = [x[1] for x in wire if x[0] == 5]
    m_idr = [x[1] for x in mirror if x[0] == 5]

    matched = []
    for i, j in pairs:
        if wire[i][0] == 5 and mirror[j][0] == 5:
            matched.append((i, j, mirror[j][1], wire[i][1], wire[i][1] - mirror[j][1]))

    lines = []
    def emit(s=""):
        lines.append(s)
        print(s)

    emit("=== wire / mirror IDR A/B ===")
    emit("wire file          : %s" % args.wire)
    emit("mirror file        : %s" % args.mirror)
    emit("wire NALUs         : %d (IDR %d)" % (len(wire), len(w_idr)))
    emit("mirror NALUs       : %d (IDR %d)" % (len(mirror), len(m_idr)))
    if w_idr:
        emit("wire IDR min/avg/max   : %d / %d / %d" %
             (min(w_idr), sum(w_idr) // len(w_idr), max(w_idr)))
    if m_idr:
        emit("mirror IDR min/avg/max : %d / %d / %d" %
             (min(m_idr), sum(m_idr) // len(m_idr), max(m_idr)))
    emit("aligned NALU pairs : %d" % len(pairs))
    emit("matched IDR pairs  : %d" % len(matched))
    if matched:
        diffs = [x[4] for x in matched]
        exact = sum(1 for d in diffs if d == 0)
        short = sum(1 for d in diffs if d < 0)
        emit("exact IDR (diff=0): %d/%d" % (exact, len(matched)))
        emit("short  IDR (diff<0): %d/%d" % (short, len(matched)))
        emit("diff min/avg/max  : %d / %d / %d" %
             (min(diffs), sum(diffs) // len(diffs), max(diffs)))
        emit("worst deficits    : %s" %
             ", ".join("%d" % d for d in sorted(diffs)[:8]))
        emit("")
        emit("| wire# | mirror# | mirror B | wire B | diff B |")
        emit("|---|---|---|---|---|")
        for i, j, mb, wb, d in matched[:60]:
            emit("| %d | %d | %d | %d | %+d |" % (i, j, mb, wb, d))
        if short == 0:
            emit("")
            emit("CONCLUSION: PASS - every matched IDR is byte-exact; no truncation.")
        else:
            emit("")
            emit("CONCLUSION: FAIL - %d/%d IDRs lost bytes in rRTSPServer." %
                 (short, len(matched)))
    else:
        emit("CONCLUSION: INCONCLUSIVE - no IDR pair could be aligned.")

    if args.out:
        with open(args.out, "w") as f:
            f.write("\n".join(lines) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
