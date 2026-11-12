# prebuilt/

GPL 源码的构建产物与本项目自研二进制。**已入库**（2026-11-12 起，
均不含任何原厂专有代码）。

| 文件 | 来源 | 说明 |
|---|---|---|
| `h264grabber.v6` | 上游源码 + patch 013/016 构建 | 音频 FIFO 256KB（AAC-T 根因修复） |
| `h264grabber.p13` | 上游源码 + patch 013 构建 | 修复前版本（回滚用） |
| `rRTSPServer.hardened` | live555 + P1-P18b patch 构建 | 加固 RTSP 服务 |
| `rmm_venc_stub.so` | 本项目自研（LD_PRELOAD stub） | strncmp 劫持 + type 过滤 |

校验：

```bash
cd prebuilt && md5 -q <file>   # 对照 CHECKSUMS
```

**不入库**（专有，见 `../docs/LEGAL.md`）：`rmm.stock/patched`、`libhive_EQ.*`。
这些由 `../deploy/install.sh` 在**用户自己的相机**上现取现打（合规）。
