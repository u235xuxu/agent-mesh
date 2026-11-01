# 隔离式知识交换

**让两个 Agent 共享方法论，但绝不共享对话。**

这是本项目最核心的设计，也是我在其他多 Agent 方案里没找到对应实现的部分。

---

## 问题

假设有两个 Agent 实例，分别服务两个人（或者同一个人的两个场景），你想让它们：

- ✅ 共享积累下来的方法论、踩坑记录、工作流
- ❌ 不看到对方用户的对话、偏好、隐私数据

**最常见的做法是「共享记忆池」。它会在三个地方出事：**

### 出事点 1：隐私穿透

A 的记忆里有「用户说他最近在考虑离婚」。这条一旦同步到 B，B 在服务另一个人的时候可能无意中提起。

**不是恶意，是模型无法区分「我该知道」和「我可以说」。**

### 出事点 2：判断污染

B 的用户偏好简洁回答，A 的用户偏好详细分析。共享记忆后，A 开始对它的用户啰嗦，或者 B 开始敷衍。

**两个用户的画像混成一个，两边都不满意。**

### 出事点 3：规则打架

A 定了一条规则「涉及金钱必须二次确认」，B 定了一条「不要反复确认惹恼用户」。合并后行为不可预测。

---

## 解法：分层仓库 + 服务端强制

### 目录结构

```
exchange-repo/
├── _shared/                    # 公共层：双方都读，只有 admin 能写
│   ├── skills/                 # 方法论、通识知识、工具用法
│   │   ├── 001-xxx.md
│   │   └── 002-yyy.md
│   └── rules/                  # 双方都该遵守的通用规则
│       └── global.md
│
├── alice/                      # alice 独占
│   ├── private/                # 只有 alice 读（也可以用作本地缓存）
│   └── messages/
│       └── for-bob/            # alice 写给 bob 的
│           └── 2026-01-15-hello.md
│
└── bob/                        # bob 独占
    ├── private/
    └── messages/
        └── for-alice/
```

### 读写权限矩阵

| 路径 | alice | bob | admin |
|---|---|---|---|
| `_shared/skills/` | 读 | 读 | 读写 |
| `_shared/rules/` | 读 | 读 | 读写 |
| `alice/**` | 读写 | **禁止** | 读写 |
| `bob/**` | **禁止** | 读写 | 读写 |

**关键点：写权限是按目录强制隔离的，不是靠约定。**

---

## 强制执行：服务端 pre-receive hook

光有目录规范没用——Agent 会在错误路径写文件。**必须在服务端拒绝。**

```bash
#!/bin/bash
# pre-receive hook
# 部署位置：<repo>.git/hooks/pre-receive

ZERO="0000000000000000000000000000000000000000"

while read oldrev newrev refname; do
    # 删除分支不检查
    [ "$newrev" = "$ZERO" ] && continue

    # 首次推送：全量检查
    if [ "$oldrev" = "$ZERO" ]; then
        files=$(git ls-tree -r --name-only "$newrev")
    else
        files=$(git diff --name-only "$oldrev" "$newrev")
    fi

    for file in $files; do
        case "$GITEA_USERNAME" in
            alice)
                case "$file" in
                    alice/*) continue ;;
                    *) echo "❌ alice 只能修改 alice/ 目录"; echo "   违规文件: $file"; exit 1 ;;
                esac
                ;;
            bob)
                case "$file" in
                    bob/*) continue ;;
                    *) echo "❌ bob 只能修改 bob/ 目录"; echo "   违规文件: $file"; exit 1 ;;
                esac
                ;;
            *)
                # admin 或其他：不限制
                ;;
        esac
    done
done

exit 0
```

**注意 `GITEA_USERNAME` 这个变量** —— Gitea 会在 hook 环境里注入推送者用户名。如果用裸 Git + SSH，改用 `$SSH_USER` 或自己从 `SSH_ORIGINAL_COMMAND` 解析。

---

## 为什么不做「共享记忆库」

有人会问：为什么不直接把两边的 memory 文件放一个仓库同步？

**因为 memory 里混着事实和隐私，无法自动分离。**

一个 Agent 的 memory 大概长这样：

```
用户偏好简洁回答。
用户在做 CPA 备考，8/29 考试。
用户的车是 Model 3。
用户住在安庆，家在合肥。
用户和伴侣有矛盾（具体见 2026-03-12 对话）。
```

**前四条可以共享，第五条绝对不能。但你怎么用程序判断哪条是隐私？**

答案是：**判断不了。** 所以设计上就不做「自动同步」，只做「人工沉淀」。

**流程是：**

```
Agent A 在工作中总结出方法论
    ↓  写成 skills/xxx.md（不含任何用户数据）
人工/admin review
    ↓  push 到 _shared/skills/
Agent B 拉到，纳入自己的能力
```

**慢，但安全。** 自动化程度低是成本，也是保护。

---

## 消息传递

除了公共知识，还需要「点对点消息」。

**规则：消息写在接收方的命名空间下，但只能由发送方创建。**

这个看似矛盾，实际是：

```
alice/messages/for-bob/2026-01-15-hello.md
```

- 这条消息由 **alice** 写入（所以它在 `alice/` 下，符合写权限）
- **bob** 会定期读 `alice/messages/for-bob/`
- bob 想回复，写到 `bob/messages/for-alice/`

**这样双方的写权限都不用越界，但能双向通信。**

消息格式建议：

```markdown
---
from: alice
to: bob
date: 2026-01-15
id: 20260115-001
---

正文内容。
```

**已读标记不要用文件状态**（容易冲突），改用接收方本地记录一个已处理 id 列表。

---

## 实操清单

部署这套隔离机制：

- [ ] 建仓库，初始化 `_shared/`、`alice/`、`bob/` 三个目录
- [ ] 给双方各建账号，权限设为 write
- [ ] 部署 `pre-receive` hook，测一遍越权推送能否被拒
- [ ] 双方各配一个同步脚本（见 `scripts/exchange_pull.sh`）
- [ ] 约定消息命名和 front matter 格式
- [ ] 定期归档已读消息，控制仓库体积

**第 3 步必须实际测。** 没测过的 hook 等于没有 hook，而你会以为它有。
