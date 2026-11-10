#!/usr/bin/env python3
"""Controlled RTSP stall reproduction (case-rtsp, 2026-11-07).

Opens a real RTSP/TCP interleaved session against the Yi camera and then
deliberately stops reading the socket. SO_RCVBUF is deliberately tiny, so the
receive window closes within a few hundred milliseconds: the camera's live555
send() hits EAGAIN, the single-threaded event loop stalls, and the hardened
rRTSPServer P4v2 watchdog tears the session down.

Paired with a 1-second wlan0 tx sampler, this produces a reproducible,
sub-second measurement of the collapse -> teardown -> reconnect window.

Usage:
    python3 rtsp_stall_client.py [camera-ip] [stall-seconds]

Outputs timestamps in local time plus Unix epoch; the camera's own logs are UTC.
"""

import re
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.10"
STALL = int(sys.argv[2]) if len(sys.argv) > 2 else 25
PORT = 554
BASE = "rtsp://%s/ch0_0.h264" % HOST


def stamp():
    return "%s e=%d" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), int(time.time()))


sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# Tiny receive buffer -> TCP window closes almost immediately once we stop reading.
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
sock.settimeout(5)
sock.connect((HOST, PORT))

cseq = 0
rxbuf = b""


def recv_response():
    """Return (header_bytes, body_bytes), stripping RTSP/TCP interleaved frames.

    live555 may push '$<channel><len16><payload>' RTP packets before the PLAY
    response, so a naive read would mistake binary for the RTSP status line.
    """
    global rxbuf
    while True:
        # Consume complete interleaved frames sitting at the front of the buffer.
        while len(rxbuf) >= 4 and rxbuf[0] == 0x24:
            length = (rxbuf[2] << 8) | rxbuf[3]
            if len(rxbuf) < 4 + length:
                break
            rxbuf = rxbuf[4 + length:]
        idx = rxbuf.find(b"\r\n\r\n")
        if idx >= 0:
            head, body = rxbuf[:idx], rxbuf[idx + 4:]
            rxbuf = b""
            return head, body
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("connection closed while waiting for RTSP response")
        rxbuf += chunk


def rtsp(method, url, extra=""):
    global cseq
    cseq += 1
    req = "%s %s RTSP/1.0\r\nCSeq: %d\r\n%s\r\n" % (method, url, cseq, extra)
    if not extra:
        req = "%s %s RTSP/1.0\r\nCSeq: %d\r\n\r\n" % (method, url, cseq)
    sock.sendall(req.encode())
    head, body = recv_response()
    text = head.decode("utf-8", "replace")
    clen = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
    need = int(clen.group(1)) if clen else 0
    while len(body) < need:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.startswith("RTSP/")),
                 lines[0] if lines else "")
    print("[%s] %-8s -> %s" % (stamp(), method, first), flush=True)
    if " 200 " not in first:
        raise RuntimeError("%s failed: %s" % (method, first))
    return text, body[:need] if need else body


opt, _ = rtsp("OPTIONS", BASE)
desc, body = rtsp("DESCRIBE", BASE, "Accept: application/sdp\r\n")
# The session-level "a=control:*" comes first in the SDP; only track-level
# controls are valid SETUP targets. Take the first track (video on this camera).
control = "track1"
found = re.search(rb"a=control:(track[0-9]+)", body)
if found:
    control = found.group(1).decode("utf-8", "replace").strip()
print("[%s] video control = %s" % (stamp(), control), flush=True)

setup_url = BASE + "/" + control
setup, _ = rtsp(
    "SETUP", setup_url, "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
)
m = re.search(r"Session:\s*([^;\r\n]+)", setup, re.I)
session = m.group(1).strip() if m else ""
print("[%s] session = %s" % (stamp(), session), flush=True)

rtsp("PLAY", BASE, "Session: %s\r\nRange: npt=0.000-\r\n" % session)
print("[%s] PLAY ok; STOP READING socket for %ds" % (stamp(), STALL), flush=True)
time.sleep(STALL)
print("[%s] stall finished; closing socket" % stamp(), flush=True)
sock.close()
