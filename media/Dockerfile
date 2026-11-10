# Build image for the hardened rRTSPServer.
#
# The HiSilicon hisiv300 cross toolchain is NOT redistributable; place
# arm-hisiv300-linux.tar.bz2 (from Hi3518E SDK V1.0.3.0, osdrv/opensource/
# toolchain/arm-hisiv300-linux/) next to this Dockerfile before building.
#
#   docker build -t rrtsp-build .
#   docker run --rm -v "$PWD":/build rrtsp-build sh /build/scripts/build.sh
FROM ubuntu:16.04

ENV DEBIAN_FRONTEND=noninteractive

COPY arm-hisiv300-linux.tar.bz2 /tmp/

RUN set -e; \
    sed -i s/archive.ubuntu.com/old-releases.ubuntu.com/g /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends libc6-i386 lib32stdc++6 lib32z1 \
        build-essential curl xz-utils bzip2 ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /home/hisi-linux; \
    tar xjf /tmp/arm-hisiv300-linux.tar.bz2 -C /home/hisi-linux; \
    rm -f /tmp/arm-hisiv300-linux.tar.bz2; \
    /home/hisi-linux/arm-hisiv300-linux/bin/arm-hisiv300-linux-uclibcgnueabi-gcc --version
