#!/bin/sh
# P17 candidate: both streams driven by CFR packet index (no silence padding).
DUR=120
URL=rtsp://192.0.2.20:8554/cam1
OUT=/tmp/case-p17/ab
mkdir -p "$OUT/J" "$OUT/K" "$OUT/L"
IN="-nostdin -hide_banner -loglevel warning -threads 2 -rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1"
MUX="-t $DUR -f segment -segment_time 10 -segment_format mp4 -reset_timestamps 1"
VSETS="-bsf:v setts=pts=N/(20*TB):dts=N/(20*TB)"
ASETS="-bsf:a setts=pts=N/(15.625*TB):dts=N/(15.625*TB)"

ffmpeg $IN -i "$URL" $MUX -c:v copy -af "asetpts=N/SR/TB" $VSETS -c:a aac \
    "$OUT/J/seg%03d.mp4" > "$OUT/J.log" 2>&1 &
PJ=$!
ffmpeg $IN -i "$URL" $MUX -c:v copy $VSETS $ASETS -c:a aac \
    "$OUT/K/seg%03d.mp4" > "$OUT/K.log" 2>&1 &
PK=$!
ffmpeg $IN -i "$URL" $MUX -c:v copy -af "aresample=async=100:first_pts=0" \
    $VSETS $ASETS -c:a aac "$OUT/L/seg%03d.mp4" > "$OUT/L.log" 2>&1 &
PL=$!
wait $PJ; echo "J rc=$?"
wait $PK; echo "K rc=$?"
wait $PL; echo "L rc=$?"
echo "done $(date -u '+%H:%M:%S UTC')"
