# 贡献指南

## 最重要的贡献：机型验证报告

这个项目最大的短板是**只在一个机型上验证过**。所以最有价值的 PR 不是代码，是：

> 「我在 yi_dome (v201) + yi-hack-v5 0.4.1 上装了 `safe` profile，
> 跑完 30 分钟 `verdict.sh`，输出附在下面，结果 PASS/CHECK/FAIL。」

请用 issue 模板 `compatibility-report`，附：
1. `docs/COMPATIBILITY.md` 的相关行
2. `verify/verdict.sh` 完整输出
3. 相机 `uname -a`、`cat /home/app/.camver`、`cat /home/yi-hack-v5/version`

## 代码贡献

### 分支与提交

```
main                 稳定，只接受经过验收的改动
  └── feat/<topic>   功能
  └── fix/<topic>    修复
```

提交信息格式：

```
<type>: <一句话>

type ∈ {feat, fix, perf, docs, test, refactor, chore}

正文写"为什么"，不是"改了什么"（diff 已经说明改了什么）。
如果这个改动推翻了某个旧结论，必须引用 docs/FINDINGS.md 的对应条目。
```

### 补丁规范

新增补丁必须：

1. 编号接续（`016-`、`017-`…），命名 `NNN-<模块>-<简短描述>.patch`
2. 能被 `git apply` 干净应用在 `media/scripts/setup.sh` 产生的基线上
3. 在 `docs/FINDINGS.md` §2 的问题表里有一行对应
4. 附**证伪条件** —— 什么样观测结果会让你撤回这个补丁

第 4 条是硬要求。没有证伪条件的补丁不受理。

### 验收要求

任何影响媒体链路的改动，PR 必须附：

- 30 分钟窗口的 `verdict.sh` 输出
- 与基线对比的帧级 `max_gap`
- 若是消费端改动，附 `#timeout` / 时间戳参数的显式说明

**没有验收数据的性能 PR 一律不合。**

## 文档贡献

文档里的每个数字都必须能复现。写数字时请标注：
- 测量窗口（起止时间 + 时长）
- 使用的工具与参数
- 相机 / 消费端版本

## 不要做的事

- ❌ 提交原厂二进制或其修改版（见 `docs/LEGAL.md`）
- ❌ 提交含真实 IP / 路径的日志
- ❌ 把"分段连续"当"画面连续"下结论
- ❌ 在未说明 `#timeout` 的情况下改消费端参数

## 开发环境

```bash
git clone <repo> && cd yi-hack-v5-rtsp-enhanced
./media/scripts/setup.sh          # 拉上游 + 打补丁
docker build -t yihackv5rtsp-build media/
# 详见 README「三条命令」
```
