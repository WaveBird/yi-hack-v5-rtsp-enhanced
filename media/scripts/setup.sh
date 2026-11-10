#!/bin/sh
# setup.sh - fetch upstream sources, assemble the build tree, apply patches.
# Run from the project root. Produces ./live/ ready for build.sh.
set -e

LIVE_VER=2023.01.19
YIHACK_VER=0.4.1
YIHACK_REPO=https://github.com/alienatedsec/yi-hack-v5

cd "$(dirname "$0")"

# 1. live555
if [ ! -d live/liveMedia ]; then
    echo "[1/3] fetching live555 ${LIVE_VER} ..."
    curl -fLo /tmp/live.tar.gz \
        "https://download.videolan.org/pub/live555/live.${LIVE_VER}.tar.gz"
    tar xzf /tmp/live.tar.gz
    mv live.${LIVE_VER} live
    rm -f /tmp/live.tar.gz
else
    echo "[1/3] live555 tree already present, skipping"
fi

# 2. yi-hack-v5 application sources
if [ ! -d yi-hack-v5 ]; then
    echo "[2/3] fetching yi-hack-v5 ${YIHACK_VER} ..."
    git clone --depth 1 --branch "${YIHACK_VER}" "${YIHACK_REPO}.git" yi-hack-v5
else
    echo "[2/3] yi-hack-v5 already present, skipping"
fi

# 3. assemble + patch
echo "[3/3] assembling and patching ..."
rm -rf live/src live/include
cp -r yi-hack-v5/src/rRTSPServer/src live/src
cp -r yi-hack-v5/src/rRTSPServer/include live/include
# v0.4.0: patch 013 targets the h264grabber source (P9/P11b). It is NOT part
# of the rRTSPServer application tree, so stage it separately as a flat file.
cp yi-hack-v5/src/h264grabber/h264grabber/h264grabber.c live/src/h264grabber.c
for P in patches/*.patch; do
    echo "    applying $(basename "$P")"
    patch -d live -p1 < "$P"
done

echo "done. Next: cross-compile with build.sh (see README)."
