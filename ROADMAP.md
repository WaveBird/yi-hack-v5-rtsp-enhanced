# ROADMAP

## v0.6.0-alpha.1（当前）

- [x] 仓库骨架、GPL-3.0
- [x] 16 个补丁迁入 `media/patches/`
- [x] 17 个补丁（新增 016-audio-fifo-256k：塌陷期音频零丢失，2026-11-12 实战验证）
- [x] 文档：FINDINGS / ARCHITECTURE / MEASUREMENT / LEGAL / COMPATIBILITY / PLAN
- [ ] `verify/` 22 只工具迁入 + 去个人化
- [ ] `deploy/install.sh` 支持 profile 与自动备份
- [ ] `tools/preflight.sh` 合规自检
- [ ] 用本项目自身脚本在 y20 上重新部署验证一遍

## v0.6.0

- [ ] `verify/verdict.sh` 统一三态判定
- [ ] `tools/fetch-from-camera.sh`（替代分发二进制）
- [ ] 至少 2 个机型完成 30 分钟 PASS 验收
- [ ] Release tgz + CHECKSUMS
- [ ] GitHub Actions：Docker 构建 + 补丁链自校验

## v0.7.0

- [ ] `tuned` profile：`tools/patch-eq.py`（`HI_EQ_Process` 空函数补丁）
- [ ] `zerocopy` profile：`tools/patch-rmm.py` + `media/zerocopy/`
- [ ] 两个 profile 的回滚演练文档
- [ ] `relax_motion.so` 源码纳入 `media/rmm-tuning/`

## v0.8.0

- [ ] 相机侧 Wi-Fi 停滞的取证工具（`/proc/net/wireless` 四元组自动采集）
- [ ] `verify/` 支持离线分析（把日志从相机拉回来再判）
- [ ] 多机型 `h264grabber` 常量表自动生成（从 `/tmp/view` 实测推导）

## 不做

- ❌ 固件重打包（不分发原厂镜像）
- ❌ 云端 P2P / OSS 相关改动
- ❌ 任何绕过签名或解密的工具
- ❌ H.265 / 新平台移植

---

## 版本号语义

`MAJOR.MINOR.PATCH`

- **MAJOR**：改变已部署文件的语义（如从 overlay 变成固件）
- **MINOR**：新增 profile 或新增机型支持
- **PATCH**：补丁修正、文档、不影响已部署行为

相机侧二进制版本（`rRTSPServer` / `h264grabber`）单独打号，见 `media/VERSION`。
