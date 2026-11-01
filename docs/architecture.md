# 整体架构

## 角色划分

不是所有机器都干所有事。**按「可靠性和算力」分配角色：**

| 角色 | 部署在哪 | 职责 | 为什么是它 |
|---|---|---|---|
| **常驻节点** | VPS / 云主机 | 交换仓库宿主、长期定时任务 | 永不关机、有公网 IP、时区可控 |
| **主力节点** | 笔记本 / 工作站 | 日常交互、重活 | 算力好，但会休眠 |
| **辅助节点** | 另一台常驻 PC / mini | 备份、次要任务 | 分担负载，互为备份 |
| **触发端** | 手机 | 远程下达指令 | 不跑常驻逻辑，只做入口 |

**核心原则：会休眠的机器，不放关键任务。**

---

## 数据流

```
        ┌──────────────────────┐
        │   Git remote         │   ← 唯一必须所有节点都能到达的地方
        │   (Gitea / GitHub)   │
        └──────────┬───────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
┌────▼────┐  ┌─────▼─────┐  ┌───▼─────┐
│ 节点 A  │  │  节点 B   │  │ 节点 C  │
│ pull    │  │  pull     │  │ pull    │
│ 处理    │  │  处理     │  │ 处理    │
│ push    │  │  push     │  │ push    │
└─────────┘  └───────────┘  └─────────┘
```

**每个节点独立循环：拉 → 处理 → 推。** 节点之间从不直接通信。

**这个设计的代价：** 延迟取决于轮询间隔。
**换来的好处：** 任意节点离线都不影响其他节点，没有单点故障（除了 Git remote 本身）。

---

## 三类数据

| 类型 | 位置 | 特征 |
|---|---|---|
| **公共知识** | `_shared/skills/`、`_shared/rules/` | 双读单写（admin），慢更新，人工审核 |
| **点对点消息** | `<node>/messages/for-<peer>/` | 自己写自己的目录，对方读 |
| **节点私有** | `<node>/private/` | 别人不读，可用于跨设备同步自己的草稿 |

**为什么不直接共享 memory：** 见 [isolation.md](isolation.md)。一句话——memory 里混着事实和隐私，程序分不开。

---

## 部署顺序

### 第 1 步：起一个 Git remote

选一台常驻机器（VPS 最合适）。

**方案 A：自建 Gitea（数据自主）**

```bash
# Docker 方式
docker run -d --name gitea \
  -p 3000:3000 -p 2222:22 \
  -v /srv/gitea:/data \
  -v /etc/timezone:/etc/timezone:ro \
  -v /etc/localtime:/etc/localtime:ro \
  --restart always \
  gitea/gitea:latest
```

**注意 `--restart always`。** 不加的话机器重启后服务不会自己起来，而你会很久之后才发现。

**方案 B：GitHub 私有仓库（零维护）**

不需要任何部署，但要接受数据存在第三方。

### 第 2 步：初始化仓库结构

```bash
git init --bare exchange.git    # 自建裸仓库
# 或直接在 Gitea/GitHub 上建仓

# 克隆到本地，建目录骨架
git clone <repo> exchange && cd exchange
mkdir -p _shared/{skills,rules} \
         node-a/{private,messages/for-node-b} \
         node-b/{private,messages/for-node-a}
touch _shared/skills/README.md _shared/rules/README.md
git add -A && git commit -m "init" && git push
```

**关键：初始化必须用管理员身份推送**，否则隔离 hook 会把这次推送当越权拦掉。见 [pitfalls.md](pitfalls.md) 第 14 条。

### 第 3 步：装隔离 hook

```bash
cp scripts/pre-receive <repo>.git/hooks/pre-receive
chmod +x <repo>.git/hooks/pre-receive
```

**装完立刻测一遍越权推送能否被拒。** 用 `scripts/test_isolation.sh`：

```bash
bash scripts/test_isolation.sh
```

### 第 4 步：每个节点接入

见 [migration.md](migration.md) 的检查单。核心是：

1. 探测连通性（`scripts/probe_mesh.py`）
2. 配 SSH（含代理配置）
3. 克隆仓库
4. 配同步脚本
5. **手动跑一次，确认真的能推拉**

### 第 5 步：加定时同步

```bash
# 每 15 分钟同步一次（放常驻机器上）
*/15 * * * * /path/to/exchange_pull.sh /path/to/exchange node-a >> /var/log/mesh.log 2>&1
```

**注意时区和 PATH**，见 [cron-multi-host.md](cron-multi-host.md)。

---

## 容量与清理

**这个问题一定会遇到，别等仓库膨胀了才处理。**

| 数据 | 建议保留 |
|---|---|
| 点对点消息 | 处理完就归档，仓库里只留 30 天 |
| 公共技能 | 长期保留，但定期合并同类项 |
| 心跳 / 状态文件 | 只保留最新的，历史不留 |

**清理策略：**

```bash
# 归档 30 天前的消息
find */messages -name '*.md' -mtime +30 -exec mv {} archive/ \;

# 归档后重建历史（仓库太大时）
git checkout --orphan clean-main
git add -A && git commit -m "snapshot $(date -u +%F)"
git branch -D main && git branch -m main
git push -f origin main
```

**重建历史会改写 commit，多个节点要同时重新 clone。**

---

## 什么时候该升级架构

**这套方案的上限大概在 5-8 个节点、每天几百次同步。**

超过的话，考虑：

- **换成真正的消息队列**（Redis / NATS）——如果网络条件允许
- **加一个中心调度器**——避免每个节点都轮询
- **分仓**——公共知识和消息分成两个仓库，各自不同的同步频率

**但先问一句：真的需要那么多节点吗？** 大多数情况下，三台机器足够了。
