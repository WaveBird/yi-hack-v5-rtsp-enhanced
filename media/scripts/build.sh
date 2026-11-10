#!/bin/sh
# build.sh - cross-compile hardened rRTSPServer. Runs INSIDE the build container
# with the project mounted at /build and the hisiv300 toolchain at
# /home/hisi-linux/arm-hisiv300-linux (see Dockerfile / README).
set -e

ln -sfn /home/hisi-linux/arm-hisiv300-linux /opt/arm-hisiv300-linux
export PATH=/opt/arm-hisiv300-linux/bin:$PATH
CROSS=arm-hisiv300-linux-uclibcgnueabi
CXX=${CROSS}-g++
CC=${CROSS}-gcc
STRIP=${CROSS}-strip

cd /build/live
COMMON="-I. -I./include -IUsageEnvironment/include -Igroupsock/include -IliveMedia/include -IBasicUsageEnvironment/include -O1 -ffunction-sections -fdata-sections -DSOCKLEN_T=socklen_t -D_LARGEFILE_SOURCE=1 -D_FILE_OFFSET_BITS=64 -DNO_OPENSSL=1 -DNEWLOCALE_NOT_USED -Wall -DBSD=1"
mkdir -p /tmp/objs && rm -f /tmp/objs/*.o

echo "[1/3] compiling live555 libraries ..."
n=0
for DIR in liveMedia UsageEnvironment groupsock BasicUsageEnvironment; do
    for F in $DIR/*.cpp; do
        case "$F" in *test*) continue ;; esac
        $CXX $COMMON -c "$F" -o "/tmp/objs/$(basename $F .cpp)_$DIR.o" || { echo "FAIL: $F"; exit 1; }
        n=$((n+1))
    done
done
echo "      $n translation units"

echo "[2/3] compiling C sources (inet.c, rtcp_from_spec.c) ..."
for F in groupsock/inet.c liveMedia/rtcp_from_spec.c; do
    $CC $COMMON -c "$F" -o "/tmp/objs/$(basename $F .c)_c.o" || { echo "FAIL: $F"; exit 1; }
done

echo "[3/3] compiling rRTSPServer application code and linking ..."
for F in src/*.cpp; do
    $CXX $COMMON -c "$F" -o "/tmp/objs/$(basename $F .cpp)_src.o" || { echo "FAIL: $F"; exit 1; }
done

$CXX -Wl,--gc-sections /tmp/objs/*.o -o /build/rRTSPServer
$STRIP /build/rRTSPServer
ls -l /build/rRTSPServer
echo "BUILD OK -> /build/rRTSPServer"
