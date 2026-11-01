#!/bin/bash
# exchange_pull.sh — 从交换仓库拉取，并列出未处理的消息
#
# 用法：
#   ./exchange_pull.sh <仓库本地路径> <我的身份>
#
# 例：
#   ./exchange_pull.sh ~/exchange alice
#
# 退出码：
#   0  拉取成功（可能有新消息，也可能没有）
#   1  拉取失败

set -euo pipefail

REPO="${1:?用法: $0 <仓库路径> <我的身份>}"
ME="${2:?用法: $0 <仓库路径> <我的身份>}"

STATE_DIR="$REPO/.local-state"
mkdir -p "$STATE_DIR"
SEEN_FILE="$STATE_DIR/seen-${ME}.txt"
touch "$SEEN_FILE"

cd "$REPO"

# ── 拉取 ──
if ! git pull --rebase --autostash origin main 2>/dev/null; then
    echo "⚠️  git pull 失败，可能有冲突。现状：" >&2
    git status --short >&2
    exit 1
fi

echo "✅ 已同步到 $(git log -1 --format='%h %s' | head -c 80)"

# ── 收集新消息 ──
# 我能收到的消息在 <别人的>/messages/for-<我>/
NEW=()
for d in "$REPO"/*/messages/for-"$ME"; do
    [ -d "$d" ] || continue
    for f in "$d"/*.md; do
        [ -f "$f" ] || continue
        rel="${f#"$REPO"/}"
        if ! grep -qxF "$rel" "$SEEN_FILE" 2>/dev/null; then
            NEW+=("$rel")
        fi
    done
done

if [ ${#NEW[@]} -eq 0 ]; then
    echo "📭 没有新消息"
    exit 0
fi

echo ""
echo "📬 ${#NEW[@]} 条新消息："
for m in "${NEW[@]}"; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📄 $m"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    head -40 "$REPO/$m"
    if [ "$(wc -l < "$REPO/$m")" -gt 40 ]; then
        echo "...（共 $(wc -l < "$REPO/$m") 行）"
    fi
    # 标记已读
    echo "$m" >> "$SEEN_FILE"
done

echo ""
echo "已标记为已读：$SEEN_FILE"
