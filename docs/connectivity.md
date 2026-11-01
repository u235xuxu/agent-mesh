# 跨网络连通性

**多机协作最先卡死的地方，不是代码，是「这两台机器到底通不通、走不走代理」。**

---

## 核心原则：链路要一条条实测，不要推

你的机器可能分布在不同网络里，每两个节点之间的可达性都可能不同。**没有任何通用规则能替代实测。**

而且实测有个陷阱：**你测出的结果取决于你在哪台机器上测、走不走代理。**

所以第一件事是搞清楚：**每条链路、每种走法，各自的结果。**

---

## 链路矩阵

建一张这样的表（用 `scripts/probe_mesh.py` 自动生成）：

| 从 ↓ / 到 → | MacBook | VPS:22 | VPS:443 | VPS:3000 | Mac mini | Windows |
|---|---|---|---|---|---|---|
| **MacBook 直连** | — | ❌ 超时 | ❌ 超时 | ❌ 超时 | ✅ | ✅ |
| **MacBook 走代理** | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| **VPS 直连** | ❌ | — | — | ✅ | ❌ | ❌ |
| **Mac mini 直连** | ✅ | ❌ | ❌ | ❌ | — | ✅ |

**读法：**
- 这台 Mac 连自己的 VPS 竟然要走代理（因为 VPS 在墙外，直连被阻）
- VPS 连 MacBook 连不上（Mac 在 NAT 后，没有公网 IP）
- 局域网内互连没问题

**结论：Mac → VPS 的所有操作都必须走代理；VPS 无法主动推给 Mac，只能等 Mac 来拉。**

**这个结论直接决定了架构：同步必须是「拉」模型，不能是「推」模型。**

---

## 代理陷阱

### 陷阱 1：全局代理会让国内站也走代理

如果你的 shell 里设了 `HTTPS_PROXY`，所有请求都会出去绕一圈。访问国内站会变慢甚至失败。

**正确做法：`NO_PROXY` 要写全。**

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
export NO_PROXY="localhost,127.0.0.1,::1,.local,.cn,.com.cn,\
baidu.com,qq.com,tencent.com,aliyun.com,taobao.com,jd.com,\
163.com,sina.com.cn,weibo.com,zhihu.com,bilibili.com,\
douyin.com,xiaohongshu.com,nowcoder.com,zhipin.com"
export no_proxy="$NO_PROXY"
```

**漏掉的后果：** 代理节点挂了的时候，国内站也一起挂。你会以为「网络全断了」，实际只是代理断了。

### 陷阱 2：探测脚本的结果会被代理污染

用 Python 的 `urllib` / `requests` 测连通性时，它们**默认读环境变量走代理**。你测到的其实是代理的状态，不是目标站的状态。

```python
# ❌ 错：测的是代理通不通
requests.get("https://example.cn")

# ✅ 对：直连测
session = requests.Session()
session.trust_env = False        # 无视环境变量
session.get("https://example.cn")

# ✅ 或者用 socket，根本不过 HTTP 层
import socket
s = socket.socket(); s.settimeout(4)
s.connect(("example.cn", 443))   # 纯 TCP 可达性
```

**`scripts/probe_mesh.py` 里默认直连，走代理要显式开 `--use-proxy`。** 这个开关是故意的：默认值应该反映真实情况，而不是被环境变量悄悄改掉。

### 陷阱 3：SSH 也要单独确认走哪条路

`ssh` 不读 `HTTPS_PROXY`（那是 HTTP 的）。要让它走代理，得用 `ProxyCommand`：

```bash
ssh -o ProxyCommand="nc -X 5 -x 127.0.0.1:7897 %h %p" user@host
```

- `-X 5` = SOCKS5
- `-x 127.0.0.1:7897` = 本地代理地址
- **Clash 的混合端口通常同时支持 HTTP 和 SOCKS5**，但要确认

**放进 `~/.ssh/config` 更省事：**

```
Host vps
    HostName 203.0.113.10
    User root
    ProxyCommand nc -X 5 -x 127.0.0.1:7897 %h %p
    ServerAliveInterval 30
```

**`ServerAliveInterval 30` 别省。** NAT 会掐掉闲置连接，长时间命令跑到一半断掉很常见。

---

## 反向连通：NAT 后的机器怎么办

**家里的 Mac 没有公网 IP，VPS 主动连不进来。**

三个可行方案：

| 方案 | 复杂度 | 适用 |
|---|---|---|
| **拉模型**（推荐） | 低 | 定时任务。Mac 定期去 VPS 拉数据，不需要入站连接 |
| **反向隧道** | 中 | 需要 VPS 主动触发 Mac 上的操作 |
| **Tailscale / ZeroTier** | 中 | 节点多、想要组网（但这会引入第三方依赖） |

**优先选拉模型。** 多机同步天生是异步的，「拉」比「推」简单得多，还顺便解决了 NAT 问题。

需要真正的反向控制时，才上隧道：

```bash
# 在 Mac 上执行：把本地 22 端口映射到 VPS 的 2222
ssh -R 2222:localhost:22 -N vps
# 现在 VPS 上 ssh -p 2222 localhost 就能进 Mac
```

**加上 `-o ExitOnForwardFailure=yes` 和 systemd/launchd 守护**，不然断了不重连。

---

## 多平台差异清单

| 平台 | 坑 |
|---|---|
| **macOS** | 没有 `timeout` 命令（用 `gtimeout` 或 Python socket）；`nc` 是 BSD 版，参数跟 GNU 不同 |
| **Linux** | `nc` 版本多（openbsd / traditional），`-X 5` 不一定支持，可能要装 `netcat-openbsd` |
| **Windows** | OpenSSH Server 默认没装（设置 → 应用 → 可选功能）；`ProxyCommand` 需要 `connect.exe` |
| **Android/Termux** | 部分命令缺失，`nc` 是精简版；电池优化会杀后台 |

---

## 实测脚本

```bash
# 探测所有节点（默认直连，反映真实情况）
python3 scripts/probe_mesh.py --config mesh.yaml

# 单独测某条链路走代理
python3 scripts/probe_mesh.py --config mesh.yaml --use-proxy --only vps
```

输出一张矩阵 + 失败原因（超时 / 拒绝 / DNS 失败），**别只给 OPEN/CLOSED，要知道怎么挂的。**
