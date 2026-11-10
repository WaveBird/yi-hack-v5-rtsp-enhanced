#!/usr/bin/env python3
"""Raw H264 bytestream capture + integrity audit for the Yi camera (case-rtsp).

Why this exists
---------------
Frigate recordings show a very stable ~7-8 "error while decoding MB ... bytestream"
per 10s segment, and each segment contains 6-7 I frames. A damaged I frame is
exactly what an H264 decoder renders as the green / smeared lower half the user
reported. Segment-level ffmpeg errors cannot tell us WHERE the corruption is
introduced: rmm / h264grabber / FIFO, rRTSPServer (RTP packetization), go2rtc,
or Frigate's mp4 muxing.

This tool opens its own RTSP/TCP interleaved session directly against the camera,
reassembles RTP into an Annex-B .h264 file, and audits NALU boundaries:

  * FU-A start without a matching end  -> a fragmented I frame was cut short
  * FU-A end without a start           -> we joined mid-frame
  * IDR count vs SPS/PPS count         -> new sessions / keyframe alignment
  * decoder MB errors on the raw file  -> corruption is camera-side, not
                                          go2rtc / Frigate muxing

It reads the socket as fast as possible (no deliberate stall), so it measures the
clean path. Compare its error rate with a Frigate segment from the same minute:
  - both bad  -> source (rmm / h264grabber / FIFO) or rRTSPServer packetization
  - raw clean -> corruption introduced downstream (go2rtc / Frigate)

Usage:
    python3 h264_capture_audit.py [--host IP] [--seconds 15] [--out FILE.h264]

Camera clock is UTC; this tool prints local time plus Unix epoch.
"""

import argparse
import collections
import re
import socket
import struct
import subprocess
import sys
import time


def stamp():
    return "%s e=%d" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                        int(time.time()))


class RTSPSession:
    def __init__(self, host, port, url):
        self.host, self.port, self.url = host, port, url
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect((host, port))
        self.buf = b""
        self.cseq = 0

    def _recv_response(self):
        while True:
            # Drop complete interleaved RTP frames that arrive before a response.
            while len(self.buf) >= 4 and self.buf[0] == 0x24:
                length = (self.buf[2] << 8) | self.buf[3]
                if len(self.buf) < 4 + length:
                    break
                self.buf = self.buf[4 + length:]
            idx = self.buf.find(b"\r\n\r\n")
            if idx >= 0:
                head = self.buf[:idx]
                body = self.buf[idx + 4:]
                self.buf = b""
                return head, body
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("connection closed waiting for RTSP response")
            self.buf += chunk

    def request(self, method, uri, extra=""):
        self.cseq += 1
        lines = ["%s %s RTSP/1.0" % (method, uri), "CSeq: %d" % self.cseq]
        if extra:
            lines.append(extra.rstrip("\r\n"))
        req = ("\r\n".join(lines) + "\r\n\r\n").encode()
        self.sock.sendall(req)
        head, body = self._recv_response()
        text = head.decode("utf-8", "replace")
        m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
        need = int(m.group(1)) if m else 0
        while len(body) < need:
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            body += chunk
        first = next((ln for ln in text.splitlines() if ln.startswith("RTSP/")),
                     text.splitlines()[0] if text.splitlines() else "")
        print("[%s] %-8s -> %s" % (stamp(), method, first), flush=True)
        if " 200 " not in first:
            raise RuntimeError("%s failed: %s" % (method, first))
        # 修复(2026-11-08)：RTSP 响应之后、同一个 TCP 段里可能已经带着
        # '$' 交错帧。原实现把 self.buf 清成 b"" 会把这些帧静默丢掉，
        # 让「重组失败 / IDR 偏短」的取证结果被工具自身污染。
        self.buf = body[need:]
        return text, body[:need]

    def play_tcp_interleaved(self):
        self.request("OPTIONS", self.url)
        _, sdp = self.request("DESCRIBE", self.url, "Accept: application/sdp")
        m = re.search(rb"a=control:(track[0-9]+)", sdp)
        control = m.group(1).decode("ascii", "replace").strip() if m else "track1"
        print("[%s] video control = %s" % (stamp(), control), flush=True)
        text, _ = self.request(
            "SETUP", self.url + "/" + control,
            "Transport: RTP/AVP/TCP;unicast;interleaved=0-1")
        m = re.search(r"Session:\s*([^;\r\n]+)", text, re.I)
        session = m.group(1).strip() if m else ""
        self.request("PLAY", self.url,
                     "Session: %s\r\nRange: npt=0.000-" % session)
        return session

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class H264Reassembler:
    START = b"\x00\x00\x00\x01"

    def __init__(self, fh):
        self.fh = fh
        self.nal = collections.Counter()
        self.fu_start = 0
        self.fu_end = 0
        self.fu_in_frame = False
        self.fu_broken = 0      # mid-stream FU-A discontinuity = real damage
        self.fu_boundary = 0    # capture started/ended inside a FU-A = artefact
        self.fu_seen = 0
        self.started = False  # 等到第一个 SPS 才开始落盘
        self._cur_start_offset = 0
        self.rtp_packets = 0
        self.rtcp_packets = 0
        self.bad_rtp = 0
        self.last_seq = None
        self.seq_gaps = 0
        self.missing_pkts = 0
        self.idr_sizes = []
        self._cur_type = None
        self._cur_len = 0

    def _finish_nalu(self):
        if self._cur_type == 5:
            self.idr_sizes.append(self._cur_len)
        self._cur_type = None
        self._cur_len = 0

    def drop_incomplete_nalu(self):
        """抓包窗口末尾停在一个未收尾的 FU-A 时，把该半个 NALU 从文件里裁掉。

        否则 ffmpeg 会在 EOF 处报 "error while decoding MB ..., bytestream -N"，
        看起来像真实花屏，其实只是观测窗口边界。"""
        try:
            self.fh.flush()
            self.fh.truncate(self._cur_start_offset)
        except (OSError, ValueError):
            pass
        self.fu_in_frame = False
        self._cur_type = None
        self._cur_len = 0

    def feed(self, pkt):
        # 修复(2026-11-08)：只接受视频 RTP。调用方现在按 '$' 通道过滤，
        # RTCP(channel 1) 不再进这里 —— RTCP 也是 version 2，混进来会被
        # 当成随机 NAL 类型写进裸流，凭空制造 ffmpeg 解码错误。
        if len(pkt) < 12 or (pkt[0] >> 6) != 2:
            self.bad_rtp += 1
            return
        self.rtp_packets += 1
        seq = struct.unpack(">H", pkt[2:4])[0]
        if self.last_seq is not None:
            expected = (self.last_seq + 1) & 0xFFFF
            if seq != expected:
                self.seq_gaps += 1
                self.missing_pkts += (seq - expected) & 0xFFFF
        self.last_seq = seq
        cc = pkt[0] & 0x0F
        off = 12 + 4 * cc
        if len(pkt) <= off:
            self.bad_rtp += 1
            return
        payload = pkt[off:]
        nal_type = payload[0] & 0x1F
        # 修复(2026-11-08)：抓包从 GOP 中间接入时，开头十几个 P slice 没有
        # SPS/PPS/IDR，ffmpeg 会报 "non-existing PPS 0 referenced / no frame!"，
        # 甚至伴随一次 concealment MB 错误 —— 这是观测窗口边界，不是码流损坏。
        # 等第一个 SPS 出现再落盘，输出的裸流才是自洽可解码的。
        if not self.started:
            fu_is_sps = (nal_type == 28 and len(payload) >= 2
                         and (payload[1] & 0x1F) == 7 and (payload[1] & 0x80))
            if nal_type != 7 and not fu_is_sps:
                return
            self.started = True
        if 1 <= nal_type <= 23:
            self._cur_start_offset = self.fh.tell()
            self.fh.write(self.START + payload)
            self.nal[nal_type] += 1
            self._cur_type, self._cur_len = nal_type, len(payload)
            self._finish_nalu()
        elif nal_type == 28 and len(payload) >= 2:  # FU-A
            # 修复(2026-11-08)：区分「抓包窗口边界」与「流中间真断」。
            # 抓取几乎必然从某个 FU-A 中途开始、也在某个 FU-A 中途结束，
            # 这类碎片缺失不是链路损坏；把它们计入 fu_broken 会让工具在
            # 完全健康的流上误报 "RTP reassembly is BROKEN"。
            self.fu_seen += 1
            fu = payload[1]
            start, end = bool(fu & 0x80), bool(fu & 0x40)
            if start:
                if self.fu_in_frame:
                    self.fu_broken += 1  # previous fragment never ended (mid-stream)
                    self._finish_nalu()
                self._cur_start_offset = self.fh.tell()
                self.fh.write(self.START)
                self.fh.write(bytes([(payload[0] & 0xE0) | (fu & 0x1F)]))
                self.nal[fu & 0x1F] += 1
                self.fu_start += 1
                self.fu_in_frame = True
                self._cur_type, self._cur_len = fu & 0x1F, 1
                self.fh.write(payload[2:])
                self._cur_len += len(payload) - 2
            elif self.fu_in_frame:
                self.fh.write(payload[2:])
                self._cur_len += len(payload) - 2
            else:
                # 没有 start、也不在任何 FU-A 里：只可能是「抓包起始处从
                # 碎片中间接入」。不要把这些孤立字节写进裸流，否则会伪造
                # 出损坏 NALU 和 ffmpeg 解码错误。
                if self.fu_seen == 1:
                    self.fu_boundary += 1
                else:
                    self.fu_broken += 1
            if end and self.fu_in_frame:
                self.fu_end += 1
                self.fu_in_frame = False
                self._finish_nalu()
        elif nal_type == 24:  # STAP-A
            i = 1
            while i + 2 <= len(payload):
                n = struct.unpack(">H", payload[i:i + 2])[0]
                i += 2
                if n == 0 or i + n > len(payload):
                    self.bad_rtp += 1
                    break
                self.fh.write(self.START + payload[i:i + n])
                self.nal[payload[i] & 0x1F] += 1
                i += n
        else:
            self.nal["other_%d" % nal_type] += 1


def decode_error_count(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", path, "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    lines = proc.stderr.splitlines()
    return (sum(1 for l in lines if b"error while decoding MB" in l),
            sum(1 for l in lines if b"corrupt decoded frame" in l),
            len(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.0.2.10")
    ap.add_argument("--port", type=int, default=554)
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--out", default="/tmp/cam-direct.h264")
    args = ap.parse_args()
    url = "rtsp://%s/ch0_0.h264" % args.host

    sess = RTSPSession(args.host, args.port, url)
    try:
        sess.play_tcp_interleaved()
        with open(args.out, "w+b") as fh:
            reasm = H264Reassembler(fh)
            print("[%s] capturing %.0fs of RTP/TCP interleaved data" %
                  (stamp(), args.seconds), flush=True)
            deadline = time.time() + args.seconds
            sess.sock.settimeout(3)
            while time.time() < deadline:
                try:
                    chunk = sess.sock.recv(262144)
                except socket.timeout:
                    continue
                if not chunk:
                    print("[%s] camera closed the connection" % stamp(), flush=True)
                    break
                sess.buf += chunk
                # Consume complete '$' frames; keep any partial tail.
                while len(sess.buf) >= 4 and sess.buf[0] == 0x24:
                    length = (sess.buf[2] << 8) | sess.buf[3]
                    if len(sess.buf) < 4 + length:
                        break
                    channel = sess.buf[1]
                    frame = sess.buf[4:4 + length]
                    sess.buf = sess.buf[4 + length:]
                    # SETUP 用的是 interleaved=0-1：0=RTP(视频)，1=RTCP。
                    # RTCP 混进重组会伪造出 type 2/3/4/17/23 之类不存在的 NALU。
                    if channel == 0:
                        reasm.feed(frame)
                    else:
                        reasm.rtcp_packets += 1
                # Any non-interleaved bytes (e.g. a late RTSP response) are dropped
                # up to the next '$' so they cannot desync the framing.
                if sess.buf and sess.buf[0] != 0x24:
                    nxt = sess.buf.find(b"\x24")
                    sess.buf = sess.buf[nxt:] if nxt >= 0 else b""
            if reasm.fu_in_frame:
                # 抓包结束时正好停在一个未收尾的 FU-A：窗口边界，不是断链。
                # 裁掉这半个 NALU，避免 EOF 处伪造出一次解码错误。
                reasm.fu_boundary += 1
                reasm.drop_incomplete_nalu()
    finally:
        sess.close()

    mb, corrupt, nlines = decode_error_count(args.out)
    print("\n=== capture summary ===", flush=True)
    print("output                : %s" % args.out, flush=True)
    print("RTP packets           : %d (bad headers %d, RTCP skipped %d)" %
          (reasm.rtp_packets, reasm.bad_rtp, reasm.rtcp_packets), flush=True)
    print("RTP seq gaps          : %d (missing packets %d)" %
          (reasm.seq_gaps, reasm.missing_pkts), flush=True)
    print("NALU types            : %s" % dict(reasm.nal), flush=True)
    print("IDR(5) / SPS(7) / PPS(8): %d / %d / %d" %
          (reasm.nal[5], reasm.nal[7], reasm.nal[8]), flush=True)
    if reasm.idr_sizes:
        short = sum(1 for s in reasm.idr_sizes if s < 50000)
        print("IDR size min/avg/max  : %d / %d / %d  (<50KB: %d/%d)" %
              (min(reasm.idr_sizes), sum(reasm.idr_sizes) // len(reasm.idr_sizes),
               max(reasm.idr_sizes), short, len(reasm.idr_sizes)), flush=True)
    print("FU-A start / end      : %d / %d" %
          (reasm.fu_start, reasm.fu_end), flush=True)
    print("FU-A mid-stream breaks: %d (capture-boundary artefacts %d)" %
          (reasm.fu_broken, reasm.fu_boundary), flush=True)
    print("decoder MB errors     : %d" % mb, flush=True)
    print("corrupt decoded frames: %d" % corrupt, flush=True)
    print("ffmpeg stderr lines   : %d" % nlines, flush=True)
    if reasm.fu_broken or reasm.seq_gaps or reasm.missing_pkts:
        print("CONCLUSION: RTP reassembly is BROKEN - fragmented NALUs are incomplete "
              "or RTP packets are missing", flush=True)
    elif mb:
        print("CONCLUSION: decoder MB errors present. NOTE: if the errors are the "
              "leading 'non-existing PPS 0 referenced' / 'no frame!' lines, they are "
              "the capture-start mid-GOP artefact (no SPS/PPS yet), not stream damage; "
              "confirm against a same-window mirror with idr_ab_audit.py.", flush=True)
    else:
        print("CONCLUSION: raw camera-side bytestream is clean; compare with Frigate "
              "segments from the same minute", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
