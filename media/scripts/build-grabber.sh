#!/bin/sh
# build-grabber.sh - cross-compile the hardened h264grabber (P9/P11b).
# Runs INSIDE the build container with the project mounted at /build:
#   docker run --rm -v "$PWD":/build rrtsp-build sh /build/scripts/build-grabber.sh
#
# The upstream Makefile needs `make` inside the container, so compile the single
# translation unit directly. This is the exact command that produced
# prebuilt/h264grabber.hardened (MD5 8205cd1c0617f1fc389c85990039d704).
set -e

ln -sfn /home/hisi-linux/arm-hisiv300-linux /opt/arm-hisiv300-linux
export PATH=/opt/arm-hisiv300-linux/bin:$PATH
CROSS=arm-hisiv300-linux-uclibcgnueabi
CC=${CROSS}-gcc
STRIP=${CROSS}-strip

cd /build/live/src
$CC -c h264grabber.c -march=armv5te -mcpu=arm926ej-s -fPIC -O2 -Wall \
    -o /tmp/h264grabber.o
$CC /tmp/h264grabber.o -lpthread -march=armv5te -mcpu=arm926ej-s -fPIC -O2 -Wall \
    -o /build/h264grabber
$STRIP /build/h264grabber
ls -l /build/h264grabber
echo "GRABBER BUILD OK -> /build/h264grabber"
