# tools/ — 取证与二进制补丁

> 本目录的存在是为了**避免分发专有二进制**。见 [`../docs/LEGAL.md`](../docs/LEGAL.md) §2。

## fetch-from-camera.sh

从**你自己的设备**取证，产物留在本地，不入库：

```bash
./fetch-from-camera.sh root@<CAMERA_IP> rmm libhive_EQ.so
# → ./local/rmm, ./local/libhive_EQ.so
```

## patch-rmm.py（zerocopy profile）

P24：把 4 处 `bl MediaDataPut` 改为 `bl strncmp@plt`，配合 `rmm_venc_stub.so` 按 type 过滤帧。

| 偏移 | 语义 | 放行返回地址 |
|---|---|---|
| `0x39508` | 主帧 | `0x3950c` |
| `0x3982c` | SPS | `0x39830` |
| `0x3984c` | PPS | `0x39850` |
| `0x3986c` | IDR | `0x39870` |
| `0x36208` | 音频（**不改**） | — |

```bash
./patch-rmm.py --in local/rmm --out local/rmm.patched --variant v4
```

**脚本只含偏移与替换字节，不含任何原厂代码。**

## patch-eq.py（tuned profile）

`libhive_EQ.so` 的 `HI_EQ_Process`（导出，`0x103c`）入口改为 `mov r0,#0; bx lr`（8 字节）。

```bash
./patch-eq.py --in local/libhive_EQ.so --out local/libhive_EQ.patched
```

收益 ~10% 主线程 CPU；风险：上行录音 EQ 被旁路（音色可能变化）；**固件升级会覆盖**。

## preflight.sh

发布前合规自检，等价于 `docs/LEGAL.md` §6 的清单。
