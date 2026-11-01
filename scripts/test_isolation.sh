#!/bin/bash
# 端到端验证：隔离 hook 是否真的能拦住越权推送
set -u

ROOT=/tmp/mesh-test-$$
BARE=$ROOT/exchange.git
mkdir -p $ROOT
trap "rm -rf $ROOT" EXIT

pass=0; fail=0
ok()  { echo "  ✅ $1"; pass=$((pass+1)); }
bad() { echo "  ❌ $1"; fail=$((fail+1)); }

echo "=== 建立裸仓库 ==="
git init -q --bare "$BARE"
git -C "$BARE" symbolic-ref HEAD refs/heads/main

HOOK_SRC=/Users/mac/projects/agent-mesh/scripts/pre-receive
cp "$HOOK_SRC" "$BARE/hooks/pre-receive"
chmod +x "$BARE/hooks/pre-receive"
echo "hook 已安装"

# 初始结构
SEED=$ROOT/seed
git init -q "$SEED"
cd "$SEED"
git config user.name admin; git config user.email admin@mesh.local
mkdir -p _shared/skills alice/messages/for-bob bob/messages/for-alice
echo "# 公共技能" > _shared/skills/README.md
echo "# alice 目录" > alice/README.md
echo "# bob 目录" > bob/README.md
git add -A && git commit -q -m "init"
git remote add origin "$BARE"
# 初始化必须用管理员身份推送，否则 hook 会当越权拦掉
GITEA_USERNAME=admin git push -q origin main || { echo "初始推送失败"; exit 1; }
echo ""

# 两个工作副本
A=$ROOT/a; B=$ROOT/b
git clone -q "$BARE" "$A"
git clone -q "$BARE" "$B"
git -C "$A" config user.name alice; git -C "$A" config user.email alice@mesh.local
git -C "$B" config user.name bob;   git -C "$B" config user.email bob@mesh.local

echo "=== 测试 1: alice 写自己的目录（应成功）==="
BASECOMMIT=$(git rev-parse HEAD)
cd "$A"
mkdir -p alice/messages/for-bob
echo "hello" > alice/messages/for-bob/note1.md
git add -A && git commit -q -m "alice: own dir"
NEWCOMMIT=$(git rev-parse HEAD)
if [ "$NEWCOMMIT" = "$BASECOMMIT" ]; then
    bad "测试1无效：commit 没产生"
else
    GITEA_USERNAME=alice git push -q origin main 2>/dev/null && ok "alice 写 alice/ 成功（真实变更已提交）" || bad "alice 写 alice/ 失败"
fi

echo ""
echo "=== 测试 2: alice 写 bob/（应被拒）==="
mkdir -p bob/messages/for-alice
echo "sneaky" > bob/messages/for-alice/hack.md
git add -A && git commit -q -m "alice: cross dir"
if GITEA_USERNAME=alice git push -q origin main 2>/tmp/mesh-t2.err; then
    bad "越权推送成功（hook 失效）"
else
    ok "越权被拒绝"
    grep -q "只能修改" /tmp/mesh-t2.err && ok "拒绝理由=目录越权" || bad "理由不对: $(head -2 /tmp/mesh-t2.err|tr '\n' ' ')"
fi
git reset -q --hard HEAD~1

echo ""
echo "=== 测试 3: alice 写 _shared/（应被拒）==="
mkdir -p _shared/skills
echo "my skill" > _shared/skills/inject.md
git add -A && git commit -q -m "alice: shared"
if GITEA_USERNAME=alice git push -q origin main 2>/tmp/mesh-t3.err; then
    bad "_shared 越权成功"
else
    ok "_shared 写入被拒绝"
    grep -q "_shared" /tmp/mesh-t3.err && ok "理由指向 _shared" || bad "理由不对: $(head -2 /tmp/mesh-t3.err|tr '\n' ' ')"
fi
git reset -q --hard HEAD~1

echo ""
echo "=== 测试 4: admin 写 _shared/（应成功）==="
cd "$SEED"
git pull -q --rebase origin main
mkdir -p _shared/skills
echo "# 新技能" > _shared/skills/new.md
git add -A && git commit -q -m "admin: add skill"
if GITEA_USERNAME=admin git push -q origin main 2>/tmp/mesh-t4.err; then
    ok "admin 写 _shared 成功"
    git -C "$A" pull -q --rebase origin main
    [ -f "$A/_shared/skills/new.md" ] && ok "对方能拉到新技能" || bad "对方拉不到"
else
    bad "admin 不能写 _shared: $(head -3 /tmp/mesh-t4.err|tr '\n' ' ')"
fi

echo ""
echo "=== 测试 5: bob 写自己的目录（应成功）==="
cd "$B"
git pull -q --rebase origin main
mkdir -p bob/messages/for-alice
echo "reply from bob" > bob/messages/for-alice/reply1.md
git add -A && git commit -q -m "bob: own dir"
GITEA_USERNAME=bob git push -q origin main 2>/dev/null && ok "bob 写 bob/ 成功" || bad "bob 写 bob/ 失败"

echo ""
echo "=== 测试 6: 未知身份（应放行）==="
cd "$A"
git pull -q --rebase origin main
mkdir -p alice/messages/for-bob
echo "anon" > alice/messages/for-bob/anon.md
git add -A && git commit -q -m "anon"
env -u GITEA_USERNAME -u SSH_USER -u USER git push -q origin main 2>/dev/null && ok "身份未知放行（不卡死）" || bad "身份未知被拦（死锁）"

echo ""
echo "=== 测试 7: pull 脚本识别新消息 ==="
cd "$A"
git pull -q --rebase origin main
OUT=$(bash /Users/mac/projects/agent-mesh/scripts/exchange_pull.sh "$A" alice 2>&1)
echo "$OUT" | grep -q "reply1.md" && ok "pull 发现 bob 的消息" || bad "未发现: $(echo "$OUT"|head -4|tr '\n' ' ')"
OUT2=$(bash /Users/mac/projects/agent-mesh/scripts/exchange_pull.sh "$A" alice 2>&1)
echo "$OUT2" | grep -q "没有新消息" && ok "二次拉取识别为已读" || bad "已读标记失效"

echo ""
echo "=== 测试 8: push 脚本发消息 ==="
OUT3=$(echo "这是一条测试消息" | bash /Users/mac/projects/agent-mesh/scripts/exchange_push.sh "$A" alice message "测试消息" 2>&1)
if echo "$OUT3" | grep -q "已推送"; then
    ok "push 脚本发送成功"
    OUT4=$(bash /Users/mac/projects/agent-mesh/scripts/exchange_pull.sh "$B" bob 2>&1)
    echo "$OUT4" | grep -q "测试消息" && ok "bob 收到 alice 的消息" || bad "bob 没收到"
else
    bad "push 失败：$(echo "$OUT3"|tail -3|tr '\n' ' ')"
fi

echo ""
echo "════════════════════════════════════"
echo "通过 $pass 项，失败 $fail 项"
[ "$fail" -eq 0 ] && echo "全部通过 ✅" || echo "有失败 ❌"
exit $fail
