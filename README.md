# agent-mesh

**把散在多台机器上的 AI Agent，连成一张能互相干活、互不串味的协作网。**

Connect independent AI agent instances running on different machines, OSes, and networks into one cooperating mesh — without them reading each other's conversations.

---

## 这套东西解决什么问题

单个 AI Agent 有个天然天花板：**它只看得见自己那台机器**。

你有 Mac、有 Windows 台式机、有一台海外 VPS、家里还有一台 Mac Mini。每台都能装 Agent，但它们是四个孤岛：

- 笔记本休眠了，上面的定时任务就断了
- VPS 上有能力，但你的助手连不上
- 同一个人两个助手，各记各的，知识不互通
- 想跨机器干一件事，得手动 SSH 一趟趟跑

**现有的多 Agent 框架基本假设「一台机器 / 同一个 Docker 网络」。** 一旦你的设备跨了 OS、跨了 NAT、跨了运营商、甚至跨了国界，那些方案全部失效。

这个仓库记录的是一套**在异构、跨网、落后的真实环境里跑起来的多机 Agent 方案**，以及它的全部踩坑。

---

## 它是怎么工作的

```
┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│  MacBook     │        │  VPS (Linux) │        │  Mac mini    │
│  macOS       │◄──SSH──┤  公网 / 海外  ├──SSH──►│  macOS       │
│  主力 Agent   │        │  常驻服务     │        │  第二 Agent  │
└──────┬───────┘        └──────┬───────┘        └──────┬───────┘
       │                       │                       │
       │        ┌──────────────┴──────────────┐        │
       │        │   Git 仓库（自建 Gitea）      │        │
       └────────┤   不是消息队列，但当作消息队列用 ├────────┘
                └──────────────┬──────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  Windows 台式机      │
                    │  备份 / 重活         │
                    └─────────────────────┘
```

**核心设计决定：用 Git 仓库当通信总线。**

不用 Redis、不用 MQTT、不用 WebSocket。理由写在 [docs/why-git-as-bus.md](docs/why-git-as-bus.md)，一句话版本是：

> **这四台机器可能任何两台之间都不通，但每一台都能 `git push`。**

---

## 目录

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 整体架构、角色划分、数据流 |
| [docs/why-git-as-bus.md](docs/why-git-as-bus.md) | 为什么不用消息队列，Git 当总线的取舍 |
| [docs/isolation.md](docs/isolation.md) | **核心创新**：怎么让两个 Agent 共享知识但不读对方的对话 |
| [docs/connectivity.md](docs/connectivity.md) | 跨网络连通性：代理感知的链路矩阵、SSH 拓扑 |
| [docs/cron-multi-host.md](docs/cron-multi-host.md) | 多机定时任务：防重复、失效检测、自愈 |
| [docs/migration.md](docs/migration.md) | 换机器 / 加机器的完整清单 |
| [docs/pitfalls.md](docs/pitfalls.md) | 踩过的坑，每条都带现象和原因 |

| 脚本 | 用途 |
|---|---|
| [scripts/probe_mesh.py](scripts/probe_mesh.py) | 探测全网节点连通性，输出矩阵 |
| [scripts/exchange_push.sh](scripts/exchange_push.sh) | 往交换仓推送（含冲突处理） |
| [scripts/exchange_pull.sh](scripts/exchange_pull.sh) | 拉取并解析新消息 |
| [scripts/pre-receive](scripts/pre-receive) | **服务端 Git hook**：强制目录隔离 |

---

## 核心创新：隔离式知识交换

大多数「多 Agent 共享记忆」的做法是**共享一个记忆池**。这在单用户场景可行，一旦两个 Agent 服务不同的人，立刻出问题：

- A 的用户信息泄露给 B
- B 的对话历史污染 A 的判断
- 两边规则冲突，互相覆盖

**这个项目的做法：**

```
repo/
├── _shared/              # 双方都能读，只能由 admin 写
│   ├── skills/           # 沉淀下来的方法论文档
│   └── rules/            # 通用铁律
├── alice/                # alice 只读
│   └── messages/for-bob/
└── bob/                  # bob 只读
    └── messages/for-alice/
```

**约束不是靠自觉，是靠服务端 `pre-receive` hook 强制执行** —— 任何人推送到不属于自己的目录，push 直接被拒。

```bash
# bob 尝试往 alice/ 推
❌ Rejected: bob can only modify bob/ directory
   Offending file: alice/messages/for-alice/note.md
```

**这套机制让「知识共享」和「数据隔离」可以同时成立**，这是我在其他地方没找到的设计。

---

## 跨平台支持

| 平台 | 角色 | 注意事项 |
|---|---|---|
| macOS | 主力 Agent | 会休眠，cron 不可靠，需外部唤醒或改用 launchd |
| Linux VPS | 常驻节点 / 交换仓库宿主 | 最稳定，适合放长期任务 |
| Windows | 备份 / 重活 | OpenSSH Server 需手动装；路径分隔符和编码要处理 |
| Android | 便携触发端 | 通过 Termux 或开放接口触发，不跑常驻 Agent |

**关键结论：不要指望在笔记本上跑关键定时任务。** 把它放 VPS 或台式机，笔记本只做交互端。

---

## 快速开始

```bash
# 1. 探测你的网络拓扑
python3 scripts/probe_mesh.py --config mesh.example.yaml

# 2. 部署交换仓库（在一台能常驻的机器上）
#    见 docs/architecture.md 第 3 节

# 3. 配置隔离约束
#    见 scripts/pre-receive

# 4. 接入新机器
#    见 docs/migration.md
```

---

## 它不做什么

- **不做 Agent 编排框架** —— 不定义任务图、不调度子 Agent。那是另一类问题。
- **不解决同机多 Agent** —— 同机协作有更简单的做法（共享文件系统），没必要上 Git。
- **不追求低延迟** —— Git 推送有秒级延迟，适合异步协作，不适合实时对话。
- **不提供开箱即用的 Docker 镜像** —— 这套东西的价值在于适配你的实际设备，而不是标准化部署。

---

## 为什么开源这个

这套东西是在真实环境里长出来的，不是设计出来的。每一条规则背后都有一次失败：跨 GFW 的机器连不上、笔记本休眠断了任务、两个 Agent 互相污染记忆。

**这些坑别人也会踩。** 写下来，省掉重新试错的时间。

---

## 验证

隔离机制不是「设计上应该能拦住」，是**实际跑过测试的**：

```bash
bash scripts/test_isolation.sh
```

```
=== 测试 1: alice 写自己的目录（应成功）===
  ✅ alice 写 alice/ 成功（真实变更已提交）
=== 测试 2: alice 写 bob/（应被拒）===
  ✅ 越权被拒绝
  ✅ 拒绝理由=目录越权
=== 测试 3: alice 写 _shared/（应被拒）===
  ✅ _shared 写入被拒绝
  ✅ 理由指向 _shared
=== 测试 4: admin 写 _shared/（应成功）===
  ✅ admin 写 _shared 成功
  ✅ 对方能拉到新技能
=== 测试 5: bob 写自己的目录（应成功）===
  ✅ bob 写 bob/ 成功
=== 测试 6: 未知身份（应放行）===
  ✅ 身份未知放行（不卡死）
=== 测试 7: pull 脚本识别新消息 ===
  ✅ pull 发现 bob 的消息
  ✅ 二次拉取识别为已读
=== 测试 8: push 脚本发消息 ===
  ✅ push 脚本发送成功
  ✅ bob 收到 alice 的消息

通过 13 项，失败 0 项
全部通过 ✅
```

连通性探测也是实测的（直连 / SOCKS5 代理两种模式）：

```
$ python3 scripts/probe_mesh.py --host www.baidu.com:443 --host github.com:22 --host 1.2.3.4:22

节点             端口     结果          耗时      地址
───────────────────────────────────────────────────────
1.2.3.4        22     ❌ TIMEOUT  5006ms  1.2.3.4
github.com     22     ✅ OPEN     113ms   github.com
www.baidu.com  443    ✅ OPEN     550ms   www.baidu.com
───────────────────────────────────────────────────────
可达 2 / 3

失败原因分布：
  TIMEOUT      1 条  ← 网络层不通：防火墙 / 被墙 / 主机不在线
```

---

## License

MIT
