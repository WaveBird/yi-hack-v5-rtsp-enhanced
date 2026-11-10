#!/bin/sh
# rollback.sh - restore a previous binary on the camera and restart the stream.
# Usage: ./scripts/rollback.sh [camera-ip] [backup-filename]
#   default backup: rRTSPServer.cooling.bak       (v0.1.0, patches 001-004)
#   stock binary:   ./scripts/rollback.sh 192.0.2.10 rRTSPServer.bak.20261107
# The backup is copied, not moved, so it stays available for repeated rollbacks.
set -e

IP="${1:-192.0.2.10}"
BAK="${2:-rRTSPServer.cooling.bak}"
BIN_DIR=/tmp/sd/yi-hack-v5/bin

echo ">>> rolling back to $BAK on $IP ..."
ssh "root@$IP" "cd $BIN_DIR && \
    [ -f '$BAK' ] || { echo 'backup not found: $BAK'; exit 1; }; \
    cp '$BAK' rRTSPServer; \
    sync; killall -q h264grabber 2>/dev/null; true"
# Kill the grabber (not just rRTSPServer) so wd_rtsp.sh::restart_grabber()
# rebuilds both together and the FIFO reader never joins mid-NALU.

echo ">>> waiting for wd_rtsp.sh to restart it ..."
sleep 20
ssh "root@$IP" "pidof rRTSPServer && echo ROLLBACK OK || echo 'not up yet - check /tmp/sd/wd_rtsp.log'"
