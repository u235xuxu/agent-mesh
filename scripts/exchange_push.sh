#!/bin/bash
# exchange_push.sh — 往交换仓库推送消息或知识
#
# 用法：
#   ./exchange_push.sh <仓库路径> <我的身份> <目标: message|skill|rule> <标题> [正文文件]
#
# 例：
#   ./exchange_push.sh ~/exchange alice message "关于备份的建议" /tmp/note.md
#   echo "内容" | ./exchange_push.sh ~/exchange alice skill "SSH 隧道用法"
#
# 目录规则（由服务端 hook 强制，这里只是按规则写）：
#   message → <我>/messages/for-<对方>/
#   skill   → _shared/skills/     （需要 admin 权限，普通身份会失败）
#   rule    → _shared/rules/      （同上）
#
# 退出码：
#   0  推送成功
#   1  推送失败（含被 hook 拒绝）

set -euo pipefail

REPO="${1:?用法: $0 <仓库路径> <我的身份> <msg|skill|rule> <标题> [正文文件]}"
ME="${2:?缺少身份}"
KIND="${3:?缺少类型：message|skill|rule}"
TITLE="${4:?缺少标题}"
BODY_FILE="${5:-}"

if [ -n "$BODY_FILE" ]; then
    [ -f "$BODY_FILE" ] || { echo "❌ 找不到正文文件: $BODY_FILE" >&2; exit 1; }
    BODY="$(cat "$BODY_FILE")"
else
    BODY="$(cat)"      # 从 stdin 读
fi

[ -n "$BODY" ] || { echo "❌ 正文为空" >&2; exit 1; }

# 生成安全文件名
SAFE_TITLE="$(echo "$TITLE" | tr ' /\\:*?"<>|' '---------' | cut -c1-50)"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
NOW="$(date -u +%FT%TZ)"

cd "$REPO"

case "$KIND" in
    message)
        # 对方名字：仓库里除我和 _shared 之外的目录
        PEER=""
        for d in */; do
            d="${d%/}"
            [ "$d" = "$ME" ] && continue
            [ "$d" = "_shared" ] && continue
            [ "$d" = ".local-state" ] && continue
            [ "$d" = ".git" ] && continue
            PEER="$d"; break
        done
        [ -n "$PEER" ] || { echo "❌ 找不到对方目录" >&2; exit 1; }
        TARGET="${ME}/messages/for-${PEER}/${STAMP}-${SAFE_TITLE}.md"
        ;;
    skill)
        TARGET="_shared/skills/${STAMP}-${SAFE_TITLE}.md"
        ;;
    rule)
        TARGET="_shared/rules/${STAMP}-${SAFE_TITLE}.md"
        ;;
    *)
        echo "❌ 未知类型: $KIND（应为 message|skill|rule）" >&2
        exit 1
        ;;
esac

mkdir -p "$(dirname "$TARGET")"

# front matter + 正文
{
    echo "---"
    echo "from: $ME"
    echo "date: $NOW"
    echo "title: $TITLE"
    echo "kind: $KIND"
    echo "---"
    echo ""
    echo "$BODY"
} > "$TARGET"

echo "📝 写入 $TARGET"

# ── 提交并推送 ──
git add "$TARGET"
git -c user.name="$ME" -c user.email="${ME}@mesh.local" \
    commit -q -m "$KIND: $TITLE" || { echo "⚠️  没有变更"; exit 0; }

# 先 rebase，避免分叉导致 push 失败
if ! git pull --rebase --autostash origin main 2>/dev/null; then
    echo "⚠️  rebase 失败，可能有冲突：" >&2
    git status --short >&2
    exit 1
fi

# 推送身份：Gitea 环境下由服务端注入 GITEA_USERNAME；
# 裸 Git + SSH 环境下 hook 靠这个变量判断权限，必须显式传，
# 否则 hook 会拿系统用户名（如 mac）判定，直接拒绝。
export GITEA_USERNAME="$ME"

if ! git push origin main 2>/tmp/push-err.txt; then
    echo "❌ 推送被拒：" >&2
    grep -v '^remote: *$' /tmp/push-err.txt >&2
    echo "" >&2
    echo "如果是目录越权，检查服务端 pre-receive hook 的输出。" >&2
    exit 1
fi

echo "✅ 已推送"
