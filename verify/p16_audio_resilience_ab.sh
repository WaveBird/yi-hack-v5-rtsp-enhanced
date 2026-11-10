#!/bin/sh
# P16 same-window A/B/C: audio resilience under camera-side RTP loss.
#
# Background
# ----------
# P15 fixed video continuity with -bsf:v setts (20 fps CFR), but the
# production monitor showed the audio track degrading in a sawtooth:
# 164 -> 148 -> 109 -> ... -> 1 AAC frames per 10 s segment, resetting to
# 164 whenever Frigate's watchdog restarted ffmpeg. Camera logs correlate
# the degradation with P11 TCP output-queue overflow / P5 disconnect, and
# Frigate logs with "RTP: bad cseq" + AAC decode errors. So the camera TCP
# egress periodically loses RTP packets; the AAC *re-encode* path turns the
# lost samples into a permanently shortened audio timeline.
#
# Arms (all read the SAME go2rtc restream in the same window)
#   E = P15 current:              wallclock + setts(v) + asetpts(a) + aac
#   F = P16 candidate:            wallclock + setts(v)
#                                + aresample=async=1:first_pts=0,asetpts(a)
#                                + aac   (lost samples are padded with silence,
#                                        so the audio timeline stays continuous)
#   G = no audio re-encode:       wallclock + setts(v) + -c:a copy
#                                (control: are the audio RTP packets themselves
#                                 missing, or is it the decode/encode path?)
#
# go2rtc is a restreamer: extra local consumers share the single upstream
# connection to the camera, so this does not add camera-side clients.
#
# Usage: DUR=300 URL=rtsp://... sh tools/p16_audio_resilience_ab.sh
set -u

DUR=${DUR:-300}
URL=${URL:-rtsp://192.0.2.20:8554/cam1}
OUT=${OUT:-/tmp/case-p16/ab}
mkdir -p "$OUT/E" "$OUT/F" "$OUT/G"

IN="-nostdin -hide_banner -loglevel warning -threads 2 -rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1"
MUX="-t $DUR -f segment -segment_time 10 -segment_format mp4 -reset_timestamps 1"
SETS="-bsf:v setts=pts=N/(20*TB):dts=N/(20*TB)"

ffmpeg $IN -i "$URL" $MUX -c:v copy -af "asetpts=N/SR/TB" $SETS -c:a aac \
    "$OUT/E/seg%03d.mp4" > "$OUT/E.log" 2>&1 &
P_E=$!

ffmpeg $IN -i "$URL" $MUX -c:v copy \
    -af "aresample=async=1:first_pts=0,asetpts=N/SR/TB" $SETS -c:a aac \
    "$OUT/F/seg%03d.mp4" > "$OUT/F.log" 2>&1 &
P_F=$!

ffmpeg $IN -i "$URL" $MUX -c:v copy -c:a copy $SETS \
    "$OUT/G/seg%03d.mp4" > "$OUT/G.log" 2>&1 &
P_G=$!

wait $P_E; echo "E rc=$?"
wait $P_F; echo "F rc=$?"
wait $P_G; echo "G rc=$?"
echo "done $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
