# verify/ — 验收台架

> 这个目录是项目的护城河。补丁会被抄走，判据抄不走。

## 三层判据

见 [`../docs/MEASUREMENT.md`](../docs/MEASUREMENT.md)。简表：

| 层 | 看什么 | 判据 |
|---|---|---|
| 连接层 | 相机侧 `p5` / `teardown` / `drop` | 全为 0 |
| 传输层 | wire vs mirror 的 IDR 逐字节 | 100% match |
| 画面层 | 跨分段帧时间轴 `max_gap` | ≤ 0.100s，无 >1s 空隙 |

## 工具分组

**台架**：`rtsp_stall_client.py`、`quant_stall_ab.py`、`continuous_watch.py`、`live_gap_monitor.py`

**画面尺子**：`frame_gap_audit.py`（权威）、`h264_capture_audit.py`、`idr_ab_audit.py`、
`streak_audit.py`（可信）、`concealment_audit.py`（**仅参考**）

**窗口验收**：`p18_final_audit.py`、`p18_production_monitor.py`

## 用法

```bash
# 开监控
python3 p18_production_monitor.py 1800 /tmp/audit/monitor.log

# 窗口关闭后全量审计
python3 p18_final_audit.py 13:21:25 13:51:25 /tmp/audit/final.txt "2026-11-08 13:20:59"

# 三态判定
./verdict.sh --audit /tmp/audit/final.txt
```

## 约定

- 凭据只从环境变量读，不落盘
- 调用 Frigate API **必须带 `User-Agent`**，否则 403 会变成静默假 PASS
- `concealment_audit.py` 的 YDIF 判据**已被证伪**（误报运动、漏报静态拉丝）
