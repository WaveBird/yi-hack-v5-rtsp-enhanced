#!/usr/bin/env bash
# 发布前合规自检（见 docs/LEGAL.md §6）
# 用法: ./tools/preflight.sh
#
# 设计约束：
#   * 扫「全部已跟踪文件」，不用 --include 限定扩展名（CHECKSUMS 无扩展名，曾漏扫）
#   * 检测规则必须是「通用模式」—— 绝不把真实姓名/公司名硬编码进本脚本
#     （那本身就是一种泄露，且会让自检永远查不干净）
#   * 同时检查 git 提交元数据与生效的 git 身份
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FAIL=0
ok() { printf '  ✅ %s\n' "$1"; }
bad() { printf '  ❌ %s\n' "$1"; FAIL=$((FAIL+1)); }

# 所有已跟踪的文本文件（含无扩展名的 CHECKSUMS）；未建库时退回全量扫描
TRACKED=$(git ls-files 2>/dev/null)
[ -z "$TRACKED" ] && TRACKED=$(find . -path ./.git -prune -o -type f -print | sed 's|^\./||')

echo "════ yi-hack-v5-rtsp-enhanced 发布前自检 ════"
echo

echo "[1] 网络拓扑 / 路径（扫全部已跟踪文件）"
# RFC 1918 私网段、常见 NAS 挂载点、非默认 SSH 端口；只放行 RFC 5737 文档地址 192.0.2.x。
# 注意：规则是通用模式，不含任何具体人名/公司名/真实地址。
PAT='192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+|172\.1[6-9]\.[0-9]+\.[0-9]+'
PAT="$PAT"'|/(Users|Volumes|volume[0-9]+)/'
HITS=$(printf '%s\n' "$TRACKED" | while read -r f; do
  [ -f "$f" ] || continue
  grep -nHE "($PAT)" "$f" 2>/dev/null | sed "s|^|$f:|"
done \
  | grep -vE '192\.0\.2\.[0-9]+' \
  | grep -vE '^tools/preflight\.sh:.*PAT=' \
  | grep -vE '^docs/(LEGAL|PLAN)\.md:.*(git grep|检查|说明)' || true)
if [ -n "$HITS" ]; then bad "发现疑似个人路径/IP："; echo "$HITS"; else ok "无真实 IP / 路径"; fi

echo
echo "[2] 凭据不得硬编码"
CRED=$(printf '%s\n' "$TRACKED" | while read -r f; do
  [ -f "$f" ] || continue
  grep -nHiE '(password|passwd|secret|token|api[_-]?key)[[:space:]]*=[[:space:]]*"[^"<][^"]*"' "$f" 2>/dev/null | sed "s|^|$f:|"
done | grep -viE 'placeholder|example|environ|getenv|说明|注释' || true)
if [ -n "$CRED" ]; then bad "疑似硬编码口令："; echo "$CRED"; else ok "凭据均从环境变量读取"; fi

echo
echo "[3] git 身份（通用规则：任何「看起来像真实/公司邮箱」的地址都拒绝）"
# 通用判据：邮箱域名不属于允许清单（noreply / example / local / 保留域名）即视为可疑。
# 不列举任何具体姓名或公司。
# 允许：noreply 系列、example/localhost/local 等保留域。用「域名片段」而非行尾锚定，
# 因为提交信息里的邮箱后面常跟着 '>' 或空格。
allowed='@(users\.noreply\.github\.com|noreply\.github\.com|example\.(com|org|net)|localhost|local|\.invalid|\.test)'
suspect_email() {
  printf '%s\n' "$1" | grep -iE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' \
    | grep -viE "$allowed" || true
}
META=$(git log --format='%an <%ae>%n%cn <%ce>%n%B' 2>/dev/null)
BADMETA=$(suspect_email "$META")
if [ -n "$BADMETA" ]; then bad "提交信息含非假名邮箱："; echo "$BADMETA"; else ok "提交者均为假名"; fi

CFG=$(git config user.email 2>/dev/null || true)
if [ -n "$CFG" ] && [ -n "$(suspect_email "$CFG")" ]; then
  bad "生效的 git user.email 非假名：$CFG（请设 git config --local user.email）"
else
  ok "生效的 git user.email = ${CFG:-(未设置)}"
fi

# reflog 会记录「改配置之前」的身份，分享整个 .git/ 时同样会泄露
REFLOG=$(grep -rhoiE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' .git/logs/ 2>/dev/null \
  | grep -viE "$allowed" | sort -u || true)
if [ -n "$REFLOG" ]; then
  bad ".git/logs/ reflog 内含真实邮箱（运行 git reflog expire --expire=now --all 清理）："
  echo "$REFLOG"
else
  ok "reflog 无真实邮箱"
fi

echo
echo "[4] 专有二进制"
BINS=$(find . -path ./.git -prune -o \
        \( -name '*.bin' -o -name 'rmm' -o -name 'rmm.*' -o -name 'rootfs_*' \
           -o -name 'home_*' -o -name '*.mtd' -o -name 'libhive_*' \) -print 2>/dev/null \
        | grep -v '^./prebuilt/' || true)
if [ -n "$BINS" ]; then bad "发现禁止分发的文件："; echo "$BINS"; else ok "无专有二进制"; fi

echo
echo "[5] 许可证"
if head -1 LICENSE 2>/dev/null | grep -q 'GNU GENERAL PUBLIC LICENSE'; then
  ok "LICENSE = GPL-3.0"
else
  bad "LICENSE 缺失或不是 GPL"
fi

echo
echo "[6] 必备文件"
for f in README.md README.en.md CONTRIBUTING.md SECURITY.md ROADMAP.md VERSION \
         docs/FINDINGS.md docs/ARCHITECTURE.md docs/MEASUREMENT.md docs/LEGAL.md; do
  [ -f "$f" ] && ok "$f" || bad "缺少 $f"
done

echo
echo "[7] 证据/日志目录"
if [ -d logs ] || ls -d ./*-evidence 2>/dev/null | grep -q .; then
  bad "存在 logs/ 或 *-evidence/（含现场数据，不应入库）"
else
  ok "无现场证据目录"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "════ 结果：可以发布 ════"
else
  echo "════ 结果：$FAIL 项待修 ════"
fi
exit "$FAIL"
