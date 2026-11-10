# media/ — 加固的媒体链路

16 个补丁打在 `rRTSPServer` + `h264grabber` 上（live555 2023.01.19 基线）。

## 应用顺序

```bash
./scripts/setup.sh        # 拉取上游源码 + 按文件名顺序打 001..015
```

`setup.sh` 会把 `h264grabber.c` 平面 stage 到 `live/src/` 供 013 使用。

## 补丁清单

| 补丁 | 目标 | 解决 |
|---|---|---|
| 001 | `ByteStreamFifoSource` | `EAGAIN` 防御 |
| 002 | `GenericMediaServer` | TCP keepalive |
| 003 | `MultiFramedRTPSink` | 发送失败钩子（取证） |
| 004 | `rRTSPServer` | teardown 收敛 |
| 005 | `rRTSPServer` | 常驻 `StreamReplicator` 源 |
| 006 | `RTPInterface` | 干净断链（P5） |
| 007 | `rRTSPServer` | TCP 失败钩子 |
| 008 | `RTPInterface` | 用户态非阻塞输出队列（P11） |
| 009 | `GenericMediaServer` | `SO_SNDBUF` 512KB |
| 010 | `rRTSPServer` | 队列钩子 + `wmem_max` |
| 011 | `ByteStreamFifoSource` | 读块 4096B（P12，IDR 截断根因之一） |
| 012a | `StreamParser` | 最小读余量 8192B（P13，根因之二） |
| 012b | `StreamReplicator` | 截断取证钩子 |
| 013 | `h264grabber.c` | 原子 FIFO 写 + 按 PID 诊断镜像 |
| 014 | `rRTSPServer` | P18：队列 512KB、按整帧发送、满则丢帧不断链 |
| 015 | `rRTSPServer` | P18b：drop-until-IDR |

## 构建

```bash
docker build -t yihackv5rtsp-build .
docker run --rm -v "$PWD/..":/build yihackv5rtsp-build sh /build/media/scripts/build.sh
docker run --rm -v "$PWD/..":/build yihackv5rtsp-build sh /build/media/scripts/build-grabber.sh
```

产物落 `../prebuilt/`。需要 `arm-hisiv300-linux` 工具链 tar 放在旁边（见 `Dockerfile`）。

## zerocopy/

P24 的源码（`rmm_venc_stub.c` 等）与打补丁工具，仅在 `zerocopy` profile 使用。
**不含任何原厂二进制。**
