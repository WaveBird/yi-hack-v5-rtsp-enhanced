# 架构（ARCHITECTURE）

> 为什么是这一层、为什么这么改。

---

## 1. 数据路径的三代

### 原厂路径（stock）

```
VENC ──HI_MPI──> [rmm] ──memcpy──> /tmp/view ──grabber──> ┌──────┐
                 (1080p 编码帧)    (mmap 共享内存)  ┌──────┤ FIFO ├──> rRTSPServer ──RTP/TCP──> go2rtc
                                                   小表:   └──────┘
                                                   +4 offset / +8 length / +12 ts / +18 counter
```

关键点：**`/tmp/view` 是 yi-hack 生态的公共契约**。原厂 `rmm` 写它，`h264grabber` 读它，
格式是按机型硬编码逆向的（表偏移、记录大小、流区偏移、缓冲区大小）。

### 加固路径（本项目的 `safe` profile）

```
VENC ──> [rmm] ──> /tmp/view ──> [h264grabber*] ──原子写──> FIFO ──> [rRTSPServer*] ──> go2rtc
                                     ↑ 1 处补丁                      ↑ 15 处补丁
```

**只动了两个用户态二进制，不动 rmm、不动内核、不动固件。**

### 零拷贝路径（`zerocopy` profile / P24）

```
VENC ──> [rmm*] ──一次 write()──> FIFO ──> [rRTSPServer*] ──> go2rtc
              ↑ 4 个调用点 patch      （/tmp/view 与 grabber 层被旁路）
```

省掉的不是 CPU（实测持平），而是**两个 memcpy 和两个进程间的背压耦合**。
价值在架构简化 → 塌陷频率 ↓97%。

---

## 2. 加固点分布

| 层 | 文件 | 补丁 | 解决的问题 |
|---|---|---|---|
| FIFO 读端 | `ByteStreamFifoSource.cpp` | 001, 011 | `EAGAIN` 防御、读块 4096B（IDR 截断根因之一） |
| 解析器 | `StreamParser.cpp` | 012a | 最小读余量 8192B（根因之二） |
| 源管理 | `StreamReplicator.cpp` | 002, 005, 012b | 常驻源、截断取证 |
| TCP 出口 | `GenericMediaServer.cpp` | 009 | `SO_SNDBUF` 512KB |
| 发送路径 | `RTPInterface.cpp` | 006, 008, 010 | 干净断链、用户态非阻塞队列、`wmem_max` |
| 服务主逻辑 | `rRTSPServer.cpp` | 004, 007, 014, 015 | teardown、出口巡检、拥塞不断链、drop-until-IDR |
| 抓帧器 | `h264grabber.c` | 013 | 原子 FIFO 写、按 PID 诊断镜像 |

补丁编号与源码 hunk 的对应见 `media/patches/` 文件名（编号即应用顺序）。

---

## 3. 三个最关键的判断

### 3.1 为什么修 `RTPInterface` 而不是加超时重启

因为断链的根因是**状态机撕裂**：发送失败后只摘掉 RTP/RTCP 通道，TCP 连接还挂着。
客户端以为连上了，服务端其实再也不会发数据 —— 这就是"控制面活着、数据面假成功"。

外部 watchdog 只能看到"还有连接"，看不到"没有数据"。所以必须**在进程内**用
`shutdown(SHUT_RDWR)` 让客户端立刻感知。

### 3.2 为什么拥塞时"丢帧不断链"

断链的代价是客户端重连 + 从 GOP 中间起播 + 下游 20 秒看门狗，远大于丢一帧。
所以 P18 把"队列满 → `shutdown`"改成"队列满 → 丢帧"。

但丢帧必须**丢到 IDR 为止**（P18b）：丢掉参考帧或拆断 FU-A 分片，解码器会靠
concealment 硬撑，表现就是花屏 —— 比丢帧更难看，也更难查。

### 3.3 为什么时间戳问题在消费端解决

相机的 `rtp_timestamp` 是合法的，问题出在**上游丢帧后时间轴的可信度**。

四轮实验的共同病灶是同一个：**用"实际送达的包数"当时间轴**。
一旦丢帧，时间轴被静默压缩，而帧间隔看起来完美 —— 最难的 bug：指标全绿，画面在跳。

定案方案：

- 视频：`-bsf:v setts=pts=N/(20*TB):dts=N/(20*TB)` 按名义帧率重建 CFR（丢帧就如实表现为空隙）
- 音频：**不重建 PTS**（`asetpts` 是 AAC-T 根因）

---

## 4. 进程与 IPC

```
┌──────────────────────────────────────────────────────────┐
│ dispatch  ← POSIX mq /ipc_dispatch（系统内部消息总线）      │
│   ● yi-hack 用 LD_PRELOAD 钩 mq_receive，把每条消息镜像到   │
│     /ipc_dispatch_1..9，自己的工具订阅这些影子队列           │
└──────────────────────────────────────────────────────────┘
        │                              │
   ┌────▼─────┐                  ┌─────▼──────┐
   │   rmm    │                  │ rRTSPServer│
   │ 编码/分发 │                  │  RTSP 服务  │
   └────┬─────┘                  └─────▲──────┘
        │ /tmp/view                     │ FIFO
        └──────────► h264grabber ───────┘
```

- `ipc_cmd` 反方向 `mq_send` 可控制 PTZ / 开关机 / 灵敏度（协议字见 yi-hack-v5 的 `ipc_cmd.h`）
- 本项目不依赖 IPC 做媒体流，只在 `tuned`/`zerocopy` 里用到 `rmm` 的补丁点

---

## 5. 为什么不做新固件

| 原因 | 说明 |
|---|---|
| 法律 | 原厂固件镜像不能再分发；重打包需要用户自备 stock image |
| 风险 | 写 mtd 有变砖可能；本项目只动 SD 卡上的用户态文件 |
| 维护 | 固件要跟上游 0.4.1 同步；overlay 只需跟两个二进制同步 |
| 收益 | 我们要解决的问题 100% 在用户态 |

---

## 6. 目录职责

```
media/     源码级改动：patches + 交叉编译（Docker）
prebuilt/  构建产物 + CHECKSUMS（release 附件，不强制入库）
verify/    验收台架：判据实现 + verdict CLI
deploy/    幂等部署 / 回滚（每次覆盖前备份）
tools/     从你自己的相机提取原始二进制、打二进制补丁
docs/      知识库
```
