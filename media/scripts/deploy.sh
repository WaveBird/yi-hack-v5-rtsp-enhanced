#!/bin/sh
# deploy.sh - install the hardened binary on the camera and restart the stream.
# Usage: ./scripts/deploy.sh [camera-ip]     (default 192.0.2.10)
# Camera SSH must be key-authenticated as root (standard on yi-hack-v5).
# Works on macOS (md5 -q) and Linux (md5sum); the camera runs minimal BusyBox.
set -e

IP="${1:-192.0.2.10}"
BIN="$(dirname "$0")/../prebuilt/rRTSPServer.hardened"
GRAB="$(dirname "$0")/../prebuilt/h264grabber.hardened"
BIN_DIR=/tmp/sd/yi-hack-v5/bin

# v0.4.0 ships TWO binaries. They must be deployed and restarted together:
# restarting only rRTSPServer makes the FIFO reader join the H264 stream
# mid-frame (missing a clean SPS/PPS/IDR) which visibly amplifies the smear.
[ -f "$BIN" ]  || { echo "binary not found: $BIN"; exit 1; }
[ -f "$GRAB" ] || { echo "binary not found: $GRAB"; exit 1; }

local_md5() {
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$1" | awk '{print $1}'
    elif command -v md5 >/dev/null 2>&1; then
        md5 -q "$1"
    else
        echo "no md5 tool available on this host" >&2
        exit 1
    fi
}

echo ">>> uploading both binaries to $IP ..."
for PAIR in "$BIN:rRTSPServer.hardened.new" "$GRAB:h264grabber.hardened.new"; do
    SRC=${PAIR%%:*}
    DST=${PAIR#*:}
    LOCAL_MD5=$(local_md5 "$SRC")
    cat "$SRC" | ssh "root@$IP" "cat > $BIN_DIR/$DST"
    # Minimal BusyBox builds may or may not expose md5sum as an applet.
    REMOTE_MD5=$(ssh "root@$IP" "md5sum $BIN_DIR/$DST 2>/dev/null || busybox md5sum $BIN_DIR/$DST 2>/dev/null" | awk '{print $1}')
    if [ -z "$REMOTE_MD5" ]; then
        echo "    WARNING: no md5 tool on camera; skipping checksum for $DST"
    elif [ "$LOCAL_MD5" != "$REMOTE_MD5" ]; then
        echo "MD5 MISMATCH for $DST ($LOCAL_MD5 != $REMOTE_MD5), aborting"
        exit 1
    else
        echo "    $(basename "$SRC") md5 ok: $REMOTE_MD5"
    fi
done

echo ">>> backing up current binaries and swapping both in ..."
ssh "root@$IP" "cd $BIN_DIR && \
    cp rRTSPServer rRTSPServer.predeploy.\$(date +%Y%m%d-%H%M%S) 2>/dev/null || true; \
    cp h264grabber h264grabber.predeploy.\$(date +%Y%m%d-%H%M%S) 2>/dev/null || true; \
    mv rRTSPServer.hardened.new rRTSPServer; \
    mv h264grabber.hardened.new h264grabber; \
    chmod +x rRTSPServer h264grabber; \
    sync; \
    killall -q h264grabber; true"
# wd_rtsp.sh::check_grabber() -> restart_grabber() kills BOTH and rebuilds BOTH.
echo ">>> waiting for wd_rtsp.sh::restart_grabber() to rebuild both ..."
sleep 20
ssh "root@$IP" 'pidof rRTSPServer >/dev/null && pidof h264grabber >/dev/null && echo DEPLOY OK || { echo "not up yet - check /tmp/sd/wd_rtsp.log"; exit 1; }'
