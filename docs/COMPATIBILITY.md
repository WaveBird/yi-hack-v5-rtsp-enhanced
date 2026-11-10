# 兼容性（COMPATIBILITY）

## 1. 已验证组合

| 项 | 值 |
|---|---|
| 机型 | **yi_home_1080p**（camera id `y20`） |
| SoC | Hi3518E，单核 ARM926EJ-S，ARMv5TE |
| 固件基线 | yi-hack-v5 **0.4.1**（rootfs + home 均为 0.4.1） |
| 相机侧二进制 | `rRTSPServer` v0.5.1（P18b）、`h264grabber` P9+P11b |
| 消费端 | Frigate + go2rtc **1.9.2**，`#timeout=4` |
| 网络 | 2.4GHz Wi-Fi，信号 −62~−70 dBm |
| 观测时长 | 累计 15 小时以上（含隔夜 10.5h） |

> **只有这一行是"已验证"。下面都是"理论上"。**

## 2. 理论兼容（未验证，需你反馈）

| 机型 | camera id | 状态 | 需要做什么 |
|---|---|---|---|
| Yi Home | `y18` | ⚠️ 未验证 | `h264grabber` 的缓冲区常量不同（`BUF_SIZE 648000`、表 150 条） |
| Yi Dome | `v201` | ⚠️ 未验证 | PTZ 相关代码路径不同 |
| Yi 1080p Dome | `h20` | ⚠️ 未验证 | 同上 + `/tmp/mmap.info` 偏移不同 |
| Yi 1080p Cloud Dome | `y19` | ⚠️ 未验证 | 同上 |
| Yi Outdoor | `h30` | ⚠️ 未验证 | `BUF_SIZE 1586752`，与 y20 同族 |
| Yi Home（非 1080p） | `y18` | ❌ 不建议 | 单核性能基线不同，补丁未经压测 |

验证方法：装 `safe` profile → 跑 `verify/verdict.sh --minutes 30` → 把输出提 issue。

## 3. 硬性要求

- **yi-hack-v5 必须是 0.4.1**。0.4.1 之前没有 `/tmp/sd/yi-hack-v5/` 这套目录约定，
  之后（如果有）不保证。
- 必须能 SSH 到相机（`SSHD=yes`）。
- `/tmp/sd` 必须是 **FAT32**（否则 yi-hack 的资源解压不出来）。
- 相机与消费端在同一内网（校时脚本依赖内网 HTTP `Date`）。

## 4. 已知限制

| 限制 | 说明 |
|---|---|
| 相机 Wi-Fi 停滞 4–15s | 未闭环，见 `FINDINGS.md` §4 |
| 单核 CPU 天花板 | `loadavg` 常态 4.7–8.1，已无优化空间（P19/P20 结论） |
| 无 RTC 电池 | 每次重启回默认时钟，依赖 `sync_clock.sh` |
| 固件升级会覆盖 | 所有改动在 `/tmp/sd/` 或 `/home/`，重刷固件后需重装 |
| 不支持 H.265 | 本机型硬件仅 H.264 |

## 5. 不支持

- 非 Hi3518 平台（MStar / GK7205 等）—— 见 [roleoroleo/yi-hack-MStar](https://github.com/roleoroleo/yi-hack-MStar)
- 未越狱的原厂固件
- 通过云端 P2P 的访问（本项目不碰云路径）
