# 法律与合规（LEGAL）

> 开源前必须逐条过一遍。**尤其是 §2 的"不可再分发清单"** —— 那里面的文件一旦推上
> 公开仓库，会同时踩到**版权**和**逆向工程条款**两条线。

---

## 1. 许可证继承

本项目是多个上游的衍生作品，必须兼容下游最严格的那个：

| 上游 | 许可证 | 对本项目的约束 |
|---|---|---|
| [alienatedsec/yi-hack-v5](https://github.com/alienatedsec/yi-hack-v5) | GPL-3.0 | 衍生 → **必须 GPL-3.0** |
| `rRTSPServer`（roleoroleo） | GPL-3.0 | 同上 |
| Live555 | LGPL-2.1+ | 静态链接 → 需提供可重新链接的目标文件或源码 |
| `busybox` / `dropbear` / `curl` 等 | GPL-2.0 / MIT / 等 | 不在本仓库内，由 yi-hack-v5 分发 |

**结论：本项目采用 GPL-3.0。** 所有补丁、脚本、文档同许可证。

> 注意 LGPL 的静态链接问题：`build.sh` 把 live555 静态链进 `rRTSPServer`。
> 我们**提供完整源码 + 构建脚本**（`media/`），满足 LGPL §6 的可重新链接要求。

---

## 2. ⚠️ 不可再分发清单

以下文件是 **Xiaomi / 芯片厂专有二进制**，或对它们的**衍生修改版**。
**绝对不能**出现在公开仓库、release 附件、Docker 镜像里。

| 文件 | 性质 | 处置 |
|---|---|---|
| `rmm.stock.bin` | 原厂 `rmm` 二进制 | ❌ 禁止分发 |
| `rmm.patched-v4.bin` | 对原厂 `rmm` 的**二进制修改版** = 衍生作品 | ❌ 禁止分发 |
| `libhive_EQ.orig.bin` | 原厂 VQE 库 | ❌ 禁止分发 |
| `libhive_EQ.patched.bin` | 对其的二进制修改版 | ❌ 禁止分发 |
| 任何 `*.mtd` / `rootfs_*` / `home_*` 固件镜像 | 原厂固件 | ❌ 禁止分发 |
| `h264grabber.stock` / `rRTSPServer.stock` | 上游 GPL 产物 | ✅ 可分发（GPL） |

### 正确做法

**只发"补丁脚本"，不发"打过补丁的二进制"。**

用户在**自己的相机**上执行：

```bash
# 1. 从自己的设备取证（用户自己的合法副本）
./tools/fetch-from-camera.sh root@<CAMERA_IP> rmm libhive_EQ.so

# 2. 打补丁（脚本只含偏移量和字节，不含原始内容）
./tools/patch-rmm.py --in rmm --out rmm.patched --variant v4
./tools/patch-eq.py  --in libhive_EQ.so --out libhive_EQ.patched

# 3. 部署
./deploy/install.sh root@<CAMERA_IP> --profile tuned
```

这样：仓库里只有**偏移量 + 8 字节指令**（`mov r0,#0; bx lr`），
不包含任何原厂代码，也不构成衍生作品的再分发。

### `.gitignore` 强制拦截

见仓库根 `.gitignore` —— 上述文件名模式已全部加入，防止误提交。

---

## 3. 逆向工程的合法性边界

本项目涉及的逆向活动：

| 活动 | 风险 | 说明 |
|---|---|---|
| 分析自己拥有的设备上的二进制 | 低 | 多数司法辖区允许为互操作性/研究目的 |
| 发布**偏移量 + 修改方案**（不含量产代码） | 低 | 属"事实/方法"而非"表达" |
| 发布**原厂二进制的完整或部分副本** | **高** | 直接侵权 |
| 发布**绕过 DRM/加密**的工具 | 高 | 多数辖区（DMCA §1201 等）独立违法 |

**本项目不做的事**：不提供固件解密工具、不提供签名绕过、不分发原厂镜像。
（yi-hack-v5 本身也不分发，见其 `stock_firmware/README.md`。）

---

## 4. 个人数据清理（发布前必做）

原始归档里有大量**你的网络拓扑**，不能进公开仓库：

| 类型 | 说明（不列举真实值） | 处理 |
|---|---|---|
| 相机 / NVR 内网 IP | 归档里的部署目标地址 | 全部替换为 RFC 5737 文档地址（`192.0.2.x`）或环境变量 |
| NAS 绝对路径 | 归档里的挂载点 | 移除，改用相对路径 / 环境变量 |
| SSH 端口 / 账号 | 非默认端口与登录名 | 改为环境变量 `NAS_SSH_PORT` / `NAS_SSH_USER` |
| 证据日志 | `logs/*-evidence/` 含完整会话与配置 | **不发布**，只发布提炼后的结论 |
| 设备序列号 / MAC | — | 移除 |
| git 作者 / 邮箱 | 提交元数据、远程地址 | 用 `noreply@` 假名，推送前核对 `git log` |

**原则：仓库里只放"方法"和"判据"，不放"现场"。**

---

## 5. 免责声明

```
本软件仅供在你本人拥有的设备上用于研究与互操作性目的。

修改相机固件/二进制可能导致设备变砖、失去保修、或违反服务条款。
作者不对任何设备损坏、数据丢失、法律责任或保修失效负责。

在商业或受监管环境中使用前，请自行确认当地法律（特别是
计算机欺诈、版权、以及反规避条款）的适用性。
```

---

## 6. 发布前检查清单

- [ ] `./tools/preflight.sh` 全绿（**必须跑**，它扫全部文件类型，含无扩展名的 `CHECKSUMS`）
- [ ] `git log --format='%an <%ae> %cn <%ce>'` 无真实姓名 / 公司邮箱
- [ ] `git remote -v` 无个人私仓地址；`git config user.email` 已设为 `noreply@`
- [ ] `find . -name '*.bin' -o -name 'rootfs_*' -o -name 'home_*'` 无专有文件
- [ ] `prebuilt/` 只含 GPL 产物，且 `CHECKSUMS` 与之一致
- [ ] `LICENSE` = GPL-3.0 全文
- [ ] 每个源文件有版权头与 SPDX 标识
- [ ] `README` 含免责声明链接
- [ ] `SECURITY.md` 说明如何私密报告漏洞
