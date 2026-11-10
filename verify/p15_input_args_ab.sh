#!/bin/sh
# P15 same-window A/B/C/D for Frigate's record-role ffmpeg arguments.
#
# Context
# -------
# P14 proved that `-use_wallclock_as_timestamps 1` creates 1-2 s holes in the
# recorded H.264 frame timeline (sub-10 ms burst clusters + delayed socket
# reads). P14a removed it from both roles; P14b kept it only on `detect`.
# But the camera's AAC audio PTS is unreliable: with the flag removed, the
# record path degenerates into 1.5 s segments with a single audio frame and
# "Non-monotonous DTS" storms, and the Frigate watchdog restarts ffmpeg.
#
# Arms (all consume the same go2rtc restream in the same time window):
#   A = P14b production record args (no wallclock, no filters)  [control]
#   B = P13 original (wallclock only)                            [control]
#   C = wallclock + setts() CFR video PTS + asetpts() CFR audio PTS
#   D = no wallclock + asetpts() CFR audio PTS only
#
# Usage:  URL=rtsp://... DUR=120 sh tools/p15_input_args_ab.sh
# Output: /tmp/case-p15/ab/{A,B,C,D}/seg%03d.mp4 and {A,B,C,D}.log
set -u

URL=${URL:-rtsp://192.0.2.20:8554/cam1}
DUR=${DUR:-120}
OUT=${OUT:-/tmp/case-p15/ab}
mkdir -p "$OUT/A" "$OUT/B" "$OUT/C" "$OUT/D"

# Common input args: same TCP interleaved transport and demux timeout as Frigate.
IN="-nostdin -hide_banner -loglevel warning -threads 2 -rtsp_transport tcp -timeout 5000000"
# Common output args: same segment muxer as Frigate 0.14.1 record role.
MUX="-t $DUR -f segment -segment_time 10 -segment_format mp4 -reset_timestamps 1"
CODEC="-c:v copy -c:a aac"

ffmpeg $IN -i "$URL" $MUX $CODEC \
    "$OUT/A/seg%03d.mp4" > "$OUT/A.log" 2>&1 &
pA=$!
ffmpeg $IN -use_wallclock_as_timestamps 1 -i "$URL" $MUX $CODEC \
    "$OUT/B/seg%03d.mp4" > "$OUT/B.log" 2>&1 &
pB=$!
ffmpeg $IN -use_wallclock_as_timestamps 1 -i "$URL" $MUX \
    -bsf:v 'setts=pts=N/(20*TB):dts=N/(20*TB)' \
    -af 'asetpts=N/SR/TB' $CODEC \
    "$OUT/C/seg%03d.mp4" > "$OUT/C.log" 2>&1 &
pC=$!
ffmpeg $IN -i "$URL" $MUX \
    -af 'asetpts=N/SR/TB' $CODEC \
    "$OUT/D/seg%03d.mp4" > "$OUT/D.log" 2>&1 &
pD=$!

wait $pA $pB $pC $pD
echo "A=$pA B=$pB C=$pC D=$pD finished at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
for x in A B C D; do
    printf '%s: files=%s log_lines=%s\n' "$x" \
        "$(ls -1 "$OUT/$x"/*.mp4 2>/dev/null | wc -l | tr -d ' ')" \
        "$(wc -l < "$OUT/$x.log" | tr -d ' ')"
done
