# 安全策略

## 报告漏洞

**请勿开公开 issue。**

用 GitHub Security Advisory（仓库 → Security → Report a vulnerability），
或邮件联系维护者（见仓库 About）。

48 小时内确认，7 天内给出处置计划。

## 本项目涉及的安全面

| 面 | 说明 |
|---|---|
| 相机 SSH 凭据 | `deploy/` 只用你提供的 `user@host`，不存储、不落盘 |
| 二进制补丁器 | `tools/patch-*.py` 只在本地对本地文件操作，不联网 |
| `verify/` 工具 | 会连接相机与 Frigate；凭据从环境变量读，不进日志 |
| Release 二进制 | 全部附 `CHECKSUMS`，请校验后再部署 |

## 请务必做的事

1. **改掉默认 SSH 密码**（yi-hack-v5 默认 root 无密码 / 弱口令）。
2. 相机**不要暴露到公网**。RTSP 554 与 80 只在内网可达。
3. 部署前 `sha256sum -c prebuilt/CHECKSUMS`。
4. 保留 `deploy/` 自动生成的 `.bak-*` 备份，直到验收 PASS。

## 已知风险（如实告知）

- `zerocopy` / `tuned` profile 会修改原厂二进制。**补丁失败可能导致相机黑流**，
  但不会变砖（只动 `/home/app/` 与 `/home/hisiko/` 下的用户态文件，可回滚）。
- 本项目无法修复相机 Wi-Fi 层的停滞，也不声称能做到"零断流"。
