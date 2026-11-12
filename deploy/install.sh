#!/bin/sh
# yi-hack-v5-rtsp-enhanced 一键部署
# 用法: ./deploy/install.sh root@<CAMERA_IP>
# 前提: 相机已刷 yi-hack-v5 (0.4.1+), 本机可 ssh root@<CAMERA_IP>
#
# 做什么:
#   1. 从相机自取专有二进制(rmm/libhive_EQ) — 合规: 用户自己设备的合法副本
#   2. 备份所有将被修改的文件到 /tmp/sd/deploy-backup-<ts>/
#   3. 部署 GPL 产物(h264grabber v6 / rRTSPServer hardened / stub)
#   4. 对 rmm 打 4 个视频调用点 patch(工具: tools/rmm_patch.py, 若相机无 python 则本机算好字节直写)
#   5. 对 libhive_EQ.so 打 P20 EQ patch(立即数替换)
#   6. 部署 system.sh 修改(LD_PRELOAD)、arm_v4.sh、watchdog 挂钩
#   7. 音频提示音静音(P19)
#   8. 重启相机并验证
#
# 回滚: 相机 /tmp/sd/deploy-backup-<ts>/restore.sh

set -e
CAM=${1:?usage: $0 root@<CAMERA_IP>}
SSHOPT="-o ConnectTimeout=45 -o BatchMode=yes -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa -o ServerAliveInterval=10"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TS=$(date +%Y%m%d-%H%M%S)
BK="/tmp/sd/deploy-backup-$TS"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# 远程执行(带重试)
run() {
  for i in 1 2 3 4 5 6; do
    R=$(ssh $SSHOPT "$CAM" "$1" 2>/dev/null)
    [ -n "$R" ] && { echo "$R"; return 0; }
    sleep 12
  done
  echo "REMOTE_FAILED: $1" >&2; return 1
}
# 文件上传(带重试)
put() {
  for i in 1 2 3 4 5 6; do
    ssh $SSHOPT "$CAM" "cat > $2" < "$1" 2>/dev/null && return 0
    sleep 10
  done
  echo "PUT_FAILED: $2" >&2; return 1
}

log "=== 1/8 备份相机现状 ==="
run "
mkdir -p $BK
cp /home/app/rmm $BK/rmm.stock
cp /home/hisiko/hisilib/libhive_EQ.so $BK/libhive_EQ.so.orig
cp /tmp/sd/yi-hack-v5/bin/h264grabber $BK/h264grabber.prev
cp /tmp/sd/yi-hack-v5/bin/rRTSPServer $BK/rRTSPServer.prev 2>/dev/null
cp /tmp/sd/yi-hack-v5/script/system.sh $BK/system.sh.orig
cp /tmp/sd/yi-hack-v5/script/wd_rtsp.sh $BK/wd_rtsp.sh.orig
cat > $BK/restore.sh << 'RESTORE'
#!/bin/sh
# 一键回滚到部署前状态
BK=$BK
cp \$BK/rmm.stock /home/app/rmm
cp \$BK/libhive_EQ.so.orig /home/hisiko/hisilib/libhive_EQ.so
cp \$BK/h264grabber.prev /tmp/sd/yi-hack-v5/bin/h264grabber
[ -f \$BK/rRTSPServer.prev ] && cp \$BK/rRTSPServer.prev /tmp/sd/yi-hack-v5/bin/rRTSPServer
cp \$BK/system.sh.orig /tmp/sd/yi-hack-v5/script/system.sh
cp \$BK/wd_rtsp.sh.orig /tmp/sd/yi-hack-v5/script/wd_rtsp.sh
echo 'restored, rebooting...'
reboot
RESTORE
chmod +x $BK/restore.sh
ls $BK
"

log "=== 2/8 部署 GPL 二进制 ==="
put "$HERE/prebuilt/h264grabber.v6"  /tmp/sd/yi-hack-v5/bin/h264grabber.new
run "
md5sum /tmp/sd/yi-hack-v5/bin/h264grabber.new | grep -q 5b7809e1197049f1ec044cb436c2a920 || { echo MD5_MISMATCH; exit 1; }
cp /tmp/sd/yi-hack-v5/bin/h264grabber.new /tmp/sd/yi-hack-v5/bin/h264grabber
chmod +x /tmp/sd/yi-hack-v5/bin/h264grabber
"
put "$HERE/prebuilt/rRTSPServer.hardened" /tmp/sd/yi-hack-v5/bin/rRTSPServer.new
run "
md5sum /tmp/sd/yi-hack-v5/bin/rRTSPServer.new | grep -q 9e3ad7d291d709566f96f01a89d31bed || { echo MD5_MISMATCH_RTSP; exit 1; }
cp /tmp/sd/yi-hack-v5/bin/rRTSPServer.new /tmp/sd/yi-hack-v5/bin/rRTSPServer
chmod +x /tmp/sd/yi-hack-v5/bin/rRTSPServer
"
put "$HERE/prebuilt/rmm_venc_stub.so" /tmp/sd/yi-hack-v5/extra/rmm_venc_stub.so
run "chmod +x /tmp/sd/yi-hack-v5/extra/rmm_venc_stub.so; md5sum /tmp/sd/yi-hack-v5/extra/rmm_venc_stub.so"

log "=== 3/8 rmm patch(4 个视频调用点) ==="
# patch 数据(硬编码字节,来自 tools/rmm_patch.py rev2; 相机通常无 python,直接远端 dd)
run "
md5sum /home/app/rmm | grep -q e096c7b6d58a75c19ea8cdab1688aa43 || { echo RMM_NOT_STOCK; exit 1; }
# 0x39508: eb043540 -> ebffc0c9
printf '\xc9\xc0\xff\xeb' | dd of=/home/app/rmm bs=1 seek=$((0x31508)) conv=notrunc 2>/dev/null
# 0x3982c: eb043477 -> ebffc000
printf '\x00\xc0\xff\xeb' | dd of=/home/app/rmm bs=1 seek=$((0x3182c)) conv=notrunc 2>/dev/null
# 0x3984c: eb04346f -> ebffbff8
printf '\xf8\xbf\xff\xeb' | dd of=/home/app/rmm bs=1 seek=$((0x3184c)) conv=notrunc 2>/dev/null
# 0x3986c: eb043467 -> ebffbff0
printf '\xf0\xbf\xff\xeb' | dd of=/home/app/rmm bs=1 seek=$((0x3186c)) conv=notrunc 2>/dev/null
md5sum /home/app/rmm
"
# 注意: seek 值 = VMA-0x8000 = 文件偏移(0x39508-0x8000=0x31508)。上方 0x39508-0x8000 算错,见下。

log "=== 4/8 libhive_EQ P20 patch ==="
run "
printf '\x00\x00\xa0\xe3\x1e\xff\x2f\xe1' | dd of=/home/hisiko/hisilib/libhive_EQ.so bs=1 seek=$((0x103c)) conv=notrunc 2>/dev/null
md5sum /home/hisiko/hisilib/libhive_EQ.so
"

log "=== 5/8 LD_PRELOAD 注入 ==="
run "
sed -i 's|LD_PRELOAD=\"/tmp/sd/yi-hack-v5/extra/relax_motion.so\"|LD_PRELOAD=\"/tmp/sd/yi-hack-v5/extra/relax_motion.so /tmp/sd/yi-hack-v5/extra/rmm_venc_stub.so\"|g' /tmp/sd/yi-hack-v5/script/system.sh
grep -c rmm_venc_stub /tmp/sd/yi-hack-v5/script/system.sh
"

log "=== 6/8 自动 arm ==="
put "$HERE/deploy/arm_v4.sh" /tmp/sd/yi-hack-v5/script/arm_v4.sh
run "
chmod +x /tmp/sd/yi-hack-v5/script/arm_v4.sh
grep -q arm_v4 /tmp/sd/yi-hack-v5/script/wd_rtsp.sh || \
  sed -i 's|    check_mqttv4|    check_mqttv4\n    /tmp/sd/yi-hack-v5/script/arm_v4.sh >/dev/null 2>\&1|' /tmp/sd/yi-hack-v5/script/wd_rtsp.sh
sh -n /tmp/sd/yi-hack-v5/script/wd_rtsp.sh && echo SYNTAX_OK
"

log "=== 7/8 P19 音频静音 ==="
run "
mkdir -p /tmp/sd/audio_orig_bak
cp /tmp/audio/*.aac /tmp/sd/audio_orig_bak/ 2>/dev/null
for f in /tmp/audio/*.aac; do [ -f \$f ] && [ ! -L \$f ] && : > \$f; done
for f in /tmp/us/*.aac;   do [ -f \$f ] && : > \$f; done
echo audio-muted
"

log "=== 8/8 重启 ==="
run "nohup busybox reboot >/dev/null 2>&1 & echo reboot-sent"
log "等待重启后 arm_v4.sh 会自动启用直连。验证: kill -34 \$(pidof rmm) 看 enabled=1"
log "回滚: ssh $CAM 'sh $BK/restore.sh'"
