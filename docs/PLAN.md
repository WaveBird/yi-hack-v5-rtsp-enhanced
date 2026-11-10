# 整理与发布计划（PLAN）

> 从「一个人排障两年攒下的归档」到「别人能用的开源项目」。
> 本文是迁移清单 —— 每一项都标了**动作**和**去向**。

---

## 0. 现状盘点

原始归档（私有，不进仓库）：

| 资产 | 体量 | 价值 | 去向 |
|---|---|---|---|
| `yi-hack-v5-rrtsp-hardened/`（git 仓库 v0.5.1） | 16 patch + 13 二进制 + 5 脚本 | ★★★★★ | **整体升格为本项目的 `media/`** |
| `tools/`（22 个 Python 审计工具） | ~5000 行 | ★★★★★ | → `verify/`（这是本项目的护城河） |
| `logs/FINAL-VERDICT.md` 等 8 份闭档 | — | ★★★★ | → `docs/FINDINGS.md`（已提炼） |
| `logs/DELETED-INDEX.md` | — | ★★★★ | → `docs/FINDINGS.md` §6（证伪记录） |
| `logs/*-evidence/`（1.8 MB 原始件） | 1.8 MB | ★★ | **不发布**（含个人网络信息） |
| `minimal/`（零拷贝源码 + 专有二进制） | 7 MB | ★★★ | 源码 → `media/zerocopy/`；**二进制全部剔除** |
| `camera-rmm-tuning/`（`relax_motion.c/.so`） | 很小 | ★★★ | → `media/rmm-tuning/` |
| `audit/`（全量坏文件扫描） | 很小 | ★★ | → `verify/` 的一个子命令 |

---

## 1. 定位决策

### 命名：为什么**不**叫 v6

最初草案名叫 `yi-hack-v6`，**已否决**。三条理由：

1. **误导范围** —— "v6" 暗示"下一代固件"，而本项目连 mtd 都不碰；
2. **误导关系** —— 暗示 yi-hack-v5 被取代了，实际上 v5 仍是地基，换掉它项目就不存在；
3. **污染版本体系** —— 上游固件是 0.4.1，我们自称 6.0.0，用户会以为相机固件版本是 6。

定名 **`yi-hack-v5-rtsp-enhanced`**：

- `yi-hack-v5` 前缀 = 明确声明依赖基线，一眼看出"必须先有 v5 0.4.1"
- `rtsp` = 说明增强的是**哪一层**（媒体/RTSP 链路），不是泛泛的 "enhanced"
- `enhanced` = 在此之上增强，**不替换**

> 曾考虑过单纯的 `yi-hack-v5-enhanced`：范围不限定的好处是能容纳 CPU/时钟/
> 可观测性等副线，坏处是搜索时容易和别的 "enhanced" 项目混淆。最终选了更精确的
> `rtsp-enhanced` —— 主线本来就 90% 在 RTSP 链路上，副线在文档里说明即可。

### 版本号继承关系

| 层 | 版本 | 说明 |
|---|---|---|
| yi-hack-v5（上游固件） | **0.4.1** | 基线，本项目不修改 |
| yi-hack-v5-rrtsp-hardened（前身） | **0.5.1** | 只做 RTSP 加固，本项目的前一版 |
| **yi-hack-v5-rtsp-enhanced（本项目）** | **0.6.0** | 前者 + 验收台架 + 部署 + 零拷贝 |
| 相机侧补丁系列（`media/VERSION`） | **0.5.1** | 二进制补丁版本，与项目版本解耦 |

> 项目版本（0.6.0）与相机侧补丁版本（0.5.1）是两套号，别混：
> 前者跟仓库走，后者跟 `rRTSPServer` / `h264grabber` 二进制走。

### 形态选择

三个选项：

| 方案 | 工作量 | 风险 | 结论 |
|---|---|---|---|
| A. 完整固件 fork（像 v5 那样重新打包 rootfs/home） | 巨大（需 stock image + 工具链 + mtd 写入） | 高（变砖、法律） | ❌ 否决 |
| B. 只做 RTSP 二进制加固（现状 `yi-hack-v5-rrtsp-hardened`） | 小 | 极低 | ✅ 已是 v0.5.1 |
| C. **加固层 + 验收台架 + 知识库** | 中 | 低 | ✅ **选这个** |

选 C 的理由：

1. **问题 100% 在用户态** —— 我们改了两年，没碰过一次 mtd；
2. **验收台架才是真正的差异点** —— 补丁会被抄，方法论抄不走；
3. **可回滚** —— 目标用户是"生产环境里有一台正在录像的相机"的人；
4. **法律干净** —— 不分发固件、不分发专有二进制。

### 一句话定位

> **yi-hack-v5-rtsp-enhanced = 让小蚁相机的 RTSP 流"可证明没坏"的加固层。**

不是"又一个固件"，是"给现有固件装上安全带 + 仪表盘"。

---

## 2. 仓库结构（已落地）

```
yi-hack-v5-rtsp-enhanced/
├── media/                  ← 来自 yi-hack-v5-rrtsp-hardened
│   ├── patches/            16 个补丁（001–015，含 012a/012b）
│   ├── scripts/            setup / build / build-grabber
│   ├── Dockerfile          交叉编译环境
│   └── zerocopy/           ← 来自 minimal/src（P24 源码，不含二进制）
├── verify/                 ← 来自 tools/（22 只）
├── deploy/                 ← 新增：幂等安装 + 回滚
├── tools/                  ← 新增：取证 / 打补丁（替代分发二进制）
├── docs/
│   ├── FINDINGS.md         结论总表（已写）
│   ├── ARCHITECTURE.md     架构（已写）
│   ├── MEASUREMENT.md      验收方法论（已写）
│   ├── LEGAL.md            合规（已写）
│   ├── COMPATIBILITY.md    机型矩阵
│   └── PLAN.md             本文
├── prebuilt/               构建产物（release 附件，gitignore）
└── ROADMAP.md
```

---

## 3. 迁移动作清单

### 3.1 直接搬运（零改动）

- [x] `patches/*.patch`（16 个）→ `media/patches/`
- [x] `scripts/{setup,build,build-grabber}.sh` → `media/scripts/`
- [x] `Dockerfile` → `media/Dockerfile`
- [x] `LICENSE`（GPL-3.0）
- [ ] `tools/*.py`（22 个）→ `verify/`

### 3.2 需改写

| 项 | 动作 |
|---|---|
| `README.md` / `README.zh-CN.md` | 合并重写为本项目定位（已写） |
| `deploy.sh` / `rollback.sh` | 增强：支持 profile、多文件备份、版本戳 |
| `logs/FINAL-VERDICT.md` | 拆成 `docs/FINDINGS.md`（结论）+ `docs/MEASUREMENT.md`（判据） |
| `tools/README.md` | 去掉个人路径，改为 §工具表 |
| Python 工具里的硬编码 IP | 改为参数 / 环境变量 |

### 3.3 必须剔除

| 项 | 原因 |
|---|---|
| `rmm.stock.bin`、`rmm.patched-v4.bin` | 专有二进制 / 衍生作品 |
| `libhive_EQ.orig.bin`、`libhive_EQ.patched.bin` | 同上 |
| `logs/*-evidence/`（1.8 MB） | 含完整网络拓扑与会话 |
| `.DS_Store`、`.backup-*/` | 垃圾 |

### 3.4 需新增

| 项 | 用途 |
|---|---|
| `deploy/install.sh` | 一条命令装完，自动备份 + MD5 校验 + profile 选择 |
| `verify/verdict.sh` | 把散落的判据收成一个 PASS/CHECK/FAIL |
| `tools/fetch-from-camera.sh` | 从用户自己设备取证（替代分发二进制） |
| `tools/patch-rmm.py` / `patch-eq.py` | 只含偏移量的二进制补丁器 |
| `CONTRIBUTING.md` / `SECURITY.md` | 开源项目标配 |
| `.gitignore` | **强制拦截**专有文件名 |

---

## 4. 发布节奏

### Phase 0：私仓整理（1–2 天）

1. 按 §3 完成迁移与剔除
2. 跑 §5 合规检查清单
3. 用本项目的 `deploy/` 在自己的相机上**重新部署一遍**，证明脚本自洽

### Phase 1：v0.6.0-alpha（公开预发布）

- 只发 `safe` profile
- README 明确写"仅验证过 yi_home_1080p (y20) + yi-hack-v5 0.4.1"
- 收 issue，重点是**别人机型上的失败报告**（这是补全 COMPATIBILITY 的唯一途径）

### Phase 2：v0.6.0

- 补齐至少 2 个机型的验证
- `verify/verdict.sh` 可用
- 提供 release tgz（二进制 + CHECKSUMS）

### Phase 3：v0.7（`tuned` / `zerocopy` profile）

- 二进制补丁器上线
- 必须附**回滚演练**文档

---

## 5. 合规检查（发布前逐条打勾）

```bash
# 一行跑完全部（扫工作区全部文件 + git 元数据 + reflog）
./tools/preflight.sh
```

它内部依次检查：网络拓扑与路径、硬编码凭据、提交者身份与 reflog、
专有二进制、许可证、必备文件、现场证据目录。详见脚本注释。

完整清单见 [`LEGAL.md`](LEGAL.md) §6。

---

## 6. 命名与品牌

| 项 | 建议 |
|---|---|
| 仓库名 | `yi-hack-v5-rtsp-enhanced` |
| 一句话 | "Hardened media layer + acceptance harness for Yi cameras" |
| 与 v5 的关系 | README 里明确写 **overlay, not fork**，避免与原项目混淆 |
| 商标 | 不使用 Xiaomi / Yi 官方 logo；"Yi" 仅作描述性使用 |
| 上游沟通 | 在 yi-hack-v5 提 issue 说明存在这个下游项目（礼貌且能引流） |

---

## 7. 风险清单

| 风险 | 缓解 |
|---|---|
| 别人机型上一装就黑流 | alpha 阶段限定机型；`deploy/` 强制备份 + 一键回滚 |
| 有人直接把 `tuned` 装上生产 | README 三档 profile 表 + 安装时二次确认 |
| 上游 yi-hack-v5 停止维护 | 本项目不依赖上游发版，只依赖 0.4.1 的目录约定 |
| 被当成"破解工具" | README 首屏强调"这是可靠性加固，不是破解" |
| 结论被滥用（"100% 不断流"） | `FINDINGS.md` §8 明确写适用边界 |
