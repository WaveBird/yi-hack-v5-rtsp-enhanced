# yi-hack-v5-rtsp-enhanced

> **小蚁（Yi）相机的可靠性加固层** —— 让 RTSP 流不仅"能连"，而且"可证明没坏"。
>
> 建立在小蚁 yi-hack-v5 之上：不重刷固件、不改原厂分区表、每一个改动都可秒级回滚。

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Hi3518E%20%2F%20ARMv5-lightgrey.svg)]()
[![Baseline](https://img.shields.io/badge/yi--hack--v5-0.4.1-orange.svg)]()

---

## 这个项目解决什么问题

如果你正在用小蚁相机（Hi3518E 系列）接 Frigate / Home Assistant / 任何 NVR，你大概率遇到过这些"看着不像 bug 的 bug"：

| 你看到的现象 | 你以为是 | 实际是 |
|---|---|---|
| 画面还在，但突然全黑 | 相机死机重启了 | 控制面活着，数据面在 `RTPInterface` 里被"摘了通道留了 TCP" |
| 录像文件名、段数、`recordings` 表全连续 | 录像没问题 | mp4 内部有 **4~8 秒无视频帧的空洞**，跳帧被完美隐藏 |
| 花屏 / 竖向拉丝 | 网络丢包 | IDR 在 FIFO→StreamParser 层被**静默截断**，解码器靠 concealment 硬撑 |
| 时间戳错、录像归到上周的目录 | 相机坏了 | 相机时钟错 7 天，且**无法出网校时**，LIVE555 的 SDP `o=` 用的是相机时钟 |
| 每段音频都比视频短一点点 | 编码器问题 | 消费端 `-af asetpts` 按"实际送达样本数"重建时间轴，丢帧即永久压缩 |

这些问题的共同点：**单看任何一个指标都正常，只有把时间轴摊开到帧级才看得见**。

yi-hack-v5-rtsp-enhanced 提供两样东西：

1. **一套加固过的媒体链路**（16 个补丁 + 可选零拷贝改造）；
2. **一套能自己跑出 PASS/FAIL 的验收台架** —— 不需要相信任何人的结论，自己测。

> 第 2 条是这个项目和其它相机 hack 项目最大的区别。
> 我们**撤回过的结论比发布过的更多**，全部记录在 [`docs/FINDINGS.md`](docs/FINDINGS.md) §5。

---

## 快速开始

### 前置条件

- 一台已经跑着 [yi-hack-v5](https://github.com/alienatedsec/yi-hack-v5) **0.4.1** 的 Hi3518E 小蚁相机（已开 SSH）
- Docker（用于交叉编译，工具链不需要装进宿主机）
- 相机与 NVR 在同一内网

支持的机型与基线版本见 [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md)。

### 三条命令

```bash
# 1. 拉上游源码并打补丁（001–015）
./media/scripts/setup.sh

# 2. 交叉编译（Docker 内，产物落 prebuilt/）
docker build -t yihackv5rtsp-build media/
docker run --rm -v "$PWD":/build yihackv5rtsp-build sh /build/media/scripts/build.sh
docker run --rm -v "$PWD":/build yihackv5rtsp-build sh /build/media/scripts/build-grabber.sh

# 3. 部署到相机（自动备份 + MD5 校验）
./deploy/install.sh root@<CAMERA_IP>
```

### 装完必须验证

```bash
# 30 分钟窗口，帧级全量审计
./verify/verdict.sh --camera <CAMERA_IP> --minutes 30 --out audit.txt
```

输出三态：`PASS` / `CHECK` / `FAIL`。**没有 PASS 的部署不算完成。**

---

## 三层安全设计

这个项目要动一个正在生产的相机，所以：

| 原则 | 实现 |
|---|---|
| **不碰固件** | 只替换 `/tmp/sd/yi-hack-v5/bin/` 下的两个用户态二进制，不写 mtd |
| **每个文件可回滚** | `deploy/` 在覆盖前把原文件备份为 `<name>.bak-<version>`，`rollback.sh` 一键还原 |
| **改动分级可选** | `safe` / `tuned` / `zerocopy` 三档 profile，默认只装 `safe` |
| **不发布专有二进制** | 涉及 patch 原厂二进制的部分，只发**补丁脚本**，由用户对自己相机上的文件执行（见 [`docs/LEGAL.md`](docs/LEGAL.md)） |

### Profile 说明

| Profile | 内容 | 风险 | 适合 |
|---|---|---|---|
| `safe`（默认） | 补丁 001–015，纯 GPL 源码重建 | 极低 | 所有人 |
| `tuned` | + P20 `HI_EQ_Process` 空函数补丁（省 ~10% CPU） | 中（要 patch 原厂 `.so`） | CPU 吃紧的老机型 |
| `zerocopy` | + P24 rmm 直连 FIFO（塌陷 ↓97%） | 高（要 patch 原厂 `rmm`） | 已跑稳 `safe`、想根治的 |

---

## 文档地图

| 文档 | 内容 |
|---|---|
| [`docs/FINDINGS.md`](docs/FINDINGS.md) | **结论总表**：每个问题→根因→解法→状态；禁改清单；被证伪的方案 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 数据路径（原厂 vs 加固 vs 零拷贝）、进程与 IPC、为何这样分层 |
| [`docs/MEASUREMENT.md`](docs/MEASUREMENT.md) | **验收方法论**：三层判据、五个测量陷阱、工具用法 |
| [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) | 机型 / 固件基线 / 已知限制 |
| [`docs/LEGAL.md`](docs/LEGAL.md) | 许可证继承、不可再分发清单、免责 |
| [`docs/PLAN.md`](docs/PLAN.md) | 项目整理与发布计划、迁移清单 |
| [`ROADMAP.md`](ROADMAP.md) | 版本路线 |

---

## 已知边界（请先读再决定要不要装）

- 本项目**只覆盖一个已实测的组合**：yi_home_1080p (y20) + yi-hack-v5 0.4.1 + Frigate + go2rtc 1.9.2。其它机型的补丁位置需重新验证。
- **相机 Wi-Fi 出口仍有 4–15 秒周期性停滞**，表现为录像偶发跳帧。已确认不在 RTSP 层（连接层 `p5=0 / teardown=0 / drop=0`），根因在射频 / MT7601 驱动 / 单核 CPU 调度。终局方案是有线相机。
- 补齐"最后一步"的取证命令见 [`docs/FINDINGS.md`](docs/FINDINGS.md) §4。

---

## 与 yi-hack-v5 的关系

**不是 fork，不是新固件。**

```
yi-hack-v5 0.4.1  ── 提供：root、SSH、启动脚本、/tmp/view 帧缓冲、IPC 总线
        │
        └─ yi-hack-v5-rtsp-enhanced ── 替换：bin/rRTSPServer（修补 16 处）、bin/h264grabber（1 处）
                      ── 新增：verify/ 验收台架、deploy/ 幂等部署
                      ── 可选：rmm / libhive 的二进制补丁
```

升级 yi-hack-v5 固件会覆盖本项目改动的文件 —— 重装即可，配置不丢。

### 为什么叫 `yi-hack-v5-rtsp-enhanced` 而不是 `v6`

因为它**不是**下一代固件。叫 v6 会同时误导读者的两件事：以为要重刷固件、
以为 yi-hack-v5 被取代了。实际上 v5 仍是地基 —— 换掉它，本项目就不存在。

版本号也顺着前身往下走，不跳代：

| 层 | 版本 |
|---|---|
| yi-hack-v5（上游固件，不改） | 0.4.1 |
| yi-hack-v5-rrtsp-hardened（前身：只做 RTSP 加固） | 0.5.1 |
| **yi-hack-v5-rtsp-enhanced（本项目）** | **0.6.0** |
| 相机侧补丁系列（`media/VERSION`） | 0.5.1 |

## 致谢

- [alienatedsec/yi-hack-v5](https://github.com/alienatedsec/yi-hack-v5) —— 本项目的地基
- [roleoroleo](https://github.com/roleoroleo) —— `rRTSPServer`、`h264grabber`、`onvif_simple_server` 原作者
- [Live555](http://www.live555.com/) —— RTSP 栈
- [Frigate](https://frigate.video/) / [go2rtc](https://github.com/AlexxIT/go2rtc) —— 消费端

## 许可证

GPL-3.0。继承自 yi-hack-v5 与 rRTSPServer。详见 [`docs/LEGAL.md`](docs/LEGAL.md)。

> ⚠️ 本项目仅用于**你自己拥有的设备**。作者不对任何损坏、变砖或法律后果负责。
