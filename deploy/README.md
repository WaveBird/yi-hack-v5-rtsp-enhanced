# 一键部署说明

## 快速开始

```bash
cd yi-hack-v5-rtsp-enhanced
./deploy/install.sh root@192.168.121.119
```

脚本自动完成：备份 → 部署二进制 → patch rmm/EQ → LD_PRELOAD → 自动 arm → 音频静音 → 重启。
重启后 arm_v4.sh 自动启用直连（约 30 秒内），无需人工干预。

## 验证

```bash
# stub 状态（应 enabled=1、frames 增长）
ssh root@192.168.121.119 'kill -34 $(pidof rmm); sleep 1; tail -1 /tmp/rmm_stub.log'

# Frigate
curl -s -u admin:Frigate@2026 http://192.168.121.205:8971/api/stats | \
  python3 -c "import json,sys; v=json.load(sys.stdin)['cameras']['cam1']; print(v.get('camera_fps'))"

# 音轨健康（全量扫描，在 frigate 容器内跑）
docker exec frigate python3 /config/docs/check_audio.py 2026-11-12
```

## 回滚

```bash
ssh root@192.168.121.119 'sh /tmp/sd/deploy-backup-<TS>/restore.sh'
```

## 二进制入库清单（合规）

| 文件 | 性质 | 入库 |
|---|---|---|
| `prebuilt/h264grabber.v6` | GPL 源码 + 016 patch 构建 | ✅ |
| `prebuilt/h264grabber.p13` | 修复前版本（回滚用） | ✅ |
| `prebuilt/rRTSPServer.hardened` | GPL 源码（P1-P18b）构建 | ✅ |
| `prebuilt/rmm_venc_stub.so` | 本项目自研 | ✅ |
| `prebuilt/rmm.stock.bin` 等 | **专有（Xiaomi）** | ❌ 由 install.sh 从用户相机现取现 patch |

见 `docs/LEGAL.md`：rmm/libhive_EQ 是原厂专有二进制，本项目只分发
"偏移量+修改字节"（install.sh 内的 printf 序列），不分发原始或修改后的二进制。

## 本仓库包含的 patch 链

| patch | 内容 |
|---|---|
| 001-015 | 视频链路加固（P1~P18b，见 docs/FINDINGS.md） |
| 016 | grabber 音频 FIFO 8KB→256KB（AAC-T 根因修复） |
| install.sh | rmm 4 调用点 patch（strncmp 劫持）+ P20 EQ patch + P19 静音 + 自动 arm |
