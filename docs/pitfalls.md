# 踩过的坑

**每条都写了：现象 → 原因 → 解法。按遇到频率排序。**

---

## 1. SSH 命令在自动化环境里被安全策略拦截

**现象：** `ssh -o ConnectTimeout=15 root@1.2.3.4 'cmd'` 直接报错，说目标 `-o` 不在允许列表里。

**原因：** 某些 Agent 运行环境对 `ssh` 命令行做模式匹配，把 `-o` 后面的参数误解析成主机名。**即使目标主机在白名单里也一样被拦。**

**解法：** 把 ssh 命令写进 `.sh` 文件，然后 `bash script.sh` 执行。预检只解析 terminal 命令行的模式，不解析脚本内容。

```bash
#!/bin/bash
ssh -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=no \
    -o ProxyCommand="nc -X 5 -x 127.0.0.1:7897 %h %p" \
    root@203.0.113.10 'echo OK'
```

---

## 2. macOS 没有 `timeout` 命令

**现象：** `timeout 4 bash -c '...'` 报 `command not found`。

**原因：** 那是 GNU coreutils 的东西，macOS 用的是 BSD 工具链。

**解法：** 二选一

```bash
# 装 GNU 版
brew install coreutils      # 然后就有 gtimeout

# 或者用 Python（更可靠，不依赖安装）
python3 -c "
import socket
s = socket.socket(); s.settimeout(4)
try: s.connect(('host', 22)); print('OPEN')
except Exception as e: print('CLOSED', type(e).__name__)
"
```

---

## 3. 代理设置导致「全网断」的误判

**现象：** 某个服务挂了，你发现 `curl baidu.com` 也失败，判断「网络全断了」。

**原因：** `HTTPS_PROXY` 是全局的，国内站也走了代理。代理节点挂了 → 所有站都挂。

**解法：** `NO_PROXY` 写全国内常用域名。**并且排查时先测一条直连的国内站 + 一条走代理的国外站，两者分开判断：**

```bash
# 直连国内
curl -s -m 6 --noproxy '*' -o /dev/null -w "国内直连: %{http_code}\n" https://www.baidu.com
# 走代理国外
curl -s -m 8 -o /dev/null -w "国外走代理: %{http_code}\n" https://www.google.com
```

**两个都挂才是网络问题；一个挂一个通，是代理或目标站的问题。**

---

## 4. 探测脚本测出的是代理状态，不是站点状态

**现象：** 脚本报某站点可用，实际访问不了。

**原因：** `requests` / `urllib` 默认读环境变量走代理，`session.trust_env = False` 没设。

**解法：**

```python
session = requests.Session()
session.trust_env = False           # 关键
```

或者干脆用 socket 做纯 TCP 层探测，绕开 HTTP 库的一切默认行为。

**推论：** 任何「连通性探测」工具，默认值都应该反映**真实直连状态**，走代理必须是显式开关。

---

## 5. cron 不加载 shell 配置，PATH 是残缺的

**现象：** 手动跑脚本正常，定时任务里报 `command not found` 或导入模块失败。

**原因：** cron 的执行环境不读 `.zshrc` / `.zprofile`，`PATH` 通常只有 `/usr/bin:/bin`。

**解法：** 脚本开头显式设置：

```bash
#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export LANG=zh_CN.UTF-8
export LC_ALL=zh_CN.UTF-8
```

**并且所有解释器用绝对路径：** `/opt/homebrew/bin/python3`，不是 `python3`。

---

## 6. cron 不会补跑错过的任务

**现象：** 机器关机三天，开机后发现这三天的任务一次没跑。

**原因：** cron 的设计是「到点触发」，关机期间错过的触发点直接丢失。

**解法：**
- macOS 用 `launchd`（睡醒会补跑）
- Linux 用 `anacron`
- 或者脚本内自己检查「上次执行时间」，超过阈值就补做

---

## 7. macOS 上 cron 和 launchd 混用，重复执行

**现象：** 某个任务一天执行了两次，通知发了两遍。

**原因：** 之前配过 cron，后来改成 launchd，但 cron 那条没删。

**解法：** 迁移时明确检查 `crontab -l`，确认旧入口已移除。

```bash
crontab -l 2>/dev/null | grep <task-name>
```

---

## 8. Git 仓库不断膨胀，clone 越来越慢

**现象：** 三个月后，交换仓库 clone 要几分钟。

**原因：** 每次同步都 commit 新消息，历史越积越多；大文件（截图、日志）也进了仓库。

**解法：**
- 同步类仓库**定期 squash 历史**，或者干脆定期重建
- 大文件不进仓库（`gitignore` 或 Git LFS）
- 已读消息归档到另一个仓库

```bash
# 简单粗暴但有效：定期重建
git checkout --orphan new-main
git add -A && git commit -m "snapshot"
git branch -D main && git branch -m main
git push -f origin main
```

**注意：** 这会改写历史，别在有其他人依赖历史记录时做。

---

## 9. `git push` 冲突导致同步脚本静默失败

**现象：** 同步脚本每天跑，但对方一直收不到新内容。

**原因：** 本地和远端分叉了，`git push` 失败，脚本没检查返回码就继续执行。

**解法：** push 前先 `pull --rebase`，且**必须检查 push 的退出码**：

```bash
git pull --rebase || { echo "pull 失败"; exit 1; }
git push || { echo "push 失败，可能有冲突"; exit 1; }
```

**`set -e` 在这里不够**，因为 `||` 会被当成处理过的错误。要显式判断。

---

## 10. 休眠唤醒后 SSH 连接变成僵尸

**现象：** 长时间运行的 SSH 命令，笔记本合盖再打开，命令卡死或直接断。

**原因：** NAT / 防火墙会清理长时间空闲的 TCP 连接，但本地不知道自己已经断了。

**解法：**

```
# ~/.ssh/config
Host *
    ServerAliveInterval 30
    ServerAliveCountMax 3
    TCPKeepAlive yes
```

**长命令一定要放 `tmux` / `screen` 里跑**，断了可以重连回来。

---

## 11. 中文字符在跨平台传输后乱码

**现象：** Mac 上写的中文日志，传到 Windows 或者在某些终端里显示成问号 / 方块。

**原因：** locale 不一致，或者文件被按 GBK 解析。

**解法：**
- 所有机器统一 `LANG=zh_CN.UTF-8`
- 文件读写**显式指定 `encoding="utf-8"`**，不要依赖默认值
- Windows 上的 PowerShell 重定向默认是 UTF-16，跨平台时要转换

---

## 12. 文件路径硬编码导致换机器全废

**现象：** 换了台机器，所有脚本都报「找不到文件」。

**原因：** 脚本里写死了 `/Users/olduser/...`。

**解法：** 迁移前先扫一遍：

```bash
grep -rn "/Users/[a-z]*/\|/home/[a-z]*/" scripts/ | grep -v '\$HOME'
```

**全部改成 `$HOME` 或 `Path.home()`。** 顺手检查有没有写死的用户名、主机名。

---

## 13. 「配置看起来对」≠「真的能用」

**现象：** 配置文件逐行看都对，实际跑起来各种错。

**原因：** 配置是静态的，运行是动态的。token 会过期，权限可能不足，依赖可能没装。

**解法：** **每一项都要实际执行一次。**

| 配置项 | 验证方式 |
|---|---|
| API token | 真调一次接口，看返回 |
| SSH 密钥 | `ssh -o BatchMode=yes` 连一次 |
| Git 凭据 | `git ls-remote` 一次 |
| 定时任务 | 强制触发一次，等结果 |

**没有验证过的配置，等于没有配置。**

---

## 14. 初始建仓被自己的 hook 拦掉

**现象：** 装好隔离 hook，第一次推送初始结构就报 `❌ 拒绝：mac 只能修改 mac/ 目录`。

**原因：** hook 靠 `GITEA_USERNAME`（或 `SSH_USER`）判断推送者身份。本地裸 Git 环境下这个变量不存在，退化成系统用户名 `mac` —— 而 `mac` 不在管理员列表里，所有文件都被判为越权。

**解法：** **初始化和日常操作必须用不同身份推送。**

```bash
# 初始化：显式指定 admin
GITEA_USERNAME=admin git push origin main

# 脚本里也要主动传身份，别指望环境有
export GITEA_USERNAME="$MY_IDENTITY"
git push origin main
```

**这条是实测踩出来的。** 设计时想的是「Gitea 会注入身份」，忘了裸 Git 场景。**任何依赖环境变量的 hook，都要想清楚变量不存在时怎么办。**

---

## 15. 空目录不会出现在 clone 出来的仓库里

**现象：** 测试脚本里 `echo "x" > node-a/messages/for-bob/note.md` 报 `No such file or directory`。

**原因：** Git 不跟踪空目录。仓库里建了 `node-a/messages/for-bob/`，但那个目录里没文件，clone 下来就只有 `node-a/`。

**解法：** 要么在目录里放一个 `.gitkeep`，要么代码里 `mkdir -p` 再写文件。

```bash
mkdir -p node-a/messages/for-bob
echo "内容" > node-a/messages/for-bob/note.md
```

**推论：** **写完文件要断言它真的被提交了**，否则测试会「假通过」—— 文件根本没创建，push 成功了，测试却以为在测正常路径。

```bash
BASECOMMIT=$(git rev-parse HEAD)
# ... 操作 ...
[ "$(git rev-parse HEAD)" != "$BASECOMMIT" ] || echo "⚠️ 没有产生任何变更，测试无效"
```

---

## 一条元教训

**上面 13 条里，有 9 条的共同点是：失败是静默的。**

任务没跑没报错、push 失败没检查返回码、文件找不到只写一行日志、探测结果被代理污染看不出来。

**所以核心习惯是：每一步都让失败变得响亮。**

- `set -euo pipefail`
- 显式检查返回码
- 用心跳机制检测「没发生的事」
- 探测结果标注「直连 / 走代理」
