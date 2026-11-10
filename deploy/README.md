# deploy/ — 幂等部署

## 原则

1. **覆盖前必备份**：`<file>.bak-<version>-<timestamp>`
2. **部署后必校验**：MD5 与 `prebuilt/CHECKSUMS` 比对
3. **一条命令回滚**
4. **支持 profile**：`safe`（默认）/ `tuned` / `zerocopy`

## 用法

```bash
./install.sh root@<CAMERA_IP>                    # safe
./install.sh root@<CAMERA_IP> --profile tuned
./rollback.sh root@<CAMERA_IP>
./rollback.sh root@<CAMERA_IP> --to 0.5.0        # 回到指定版本
```

## 部署的是什么

| 目标路径 | 内容 |
|---|---|
| `/tmp/sd/yi-hack-v5/bin/rRTSPServer` | 加固版 |
| `/tmp/sd/yi-hack-v5/bin/h264grabber` | 加固版（**必须配套**） |

> ⚠️ 只重启 `rRTSPServer` 而不重启 `h264grabber`，FIFO 读端会从 NALU 中途接入，
> **明显放大花屏**。脚本会一起处理。

## 验收

部署脚本成功 ≠ 部署完成。必须跑：

```bash
../verify/verdict.sh --camera <CAMERA_IP> --minutes 30
```
