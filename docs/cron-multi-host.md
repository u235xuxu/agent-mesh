# 多机定时任务

**单机 cron 最大的问题是：机器睡了，任务就没了。**

而且它不会告诉你任务没跑——你只会在一周后发现问题。

---

## 核心原则

### 1. 关键任务不要放在笔记本上

| 机器 | 适合跑什么 |
|---|---|
| **VPS** | 一切必须准点执行的任务。永不关机，时区可控 |
| **台式机** | 重活（备份、大批量处理）。可以定时开机 |
| **笔记本** | **不适合任何定时任务**。合盖就停，唤醒后可能跳过 |
| **手机** | 只做触发端，不做执行端 |

**这条是最重要的。** 很多人的定时任务失效，根本原因就是把任务放在了一台会休眠的机器上。

### 2. 时区必须显式声明

多机部署时，A 在 UTC，B 在东八区，同一个 `0 9 * * *` 意味着不同的时刻。

**建议：所有任务统一用 UTC 定义，或者全部显式带时区。**

```python
# 不要依赖系统时区
schedule = "0 1 * * *"  # UTC 09:00 = 北京 17:00，写清楚在注释里
```

---

## 防重复执行

**同一个任务如果部署在两台机器上，会跑两次。** 对幂等任务无害，对「发通知」「扣费」这类是事故。

三个方案，按成本从低到高：

### 方案 A：单点部署（推荐）

**只在一台机器上装这个任务，其他机器不装。**

最简单，也最不容易出错。多机高可用的收益通常不值得那份复杂度。

### 方案 B：租约锁

用 Git 仓库当锁（既然已经在用了）：

```bash
#!/bin/bash
# 抢锁：创建带时间戳的文件，push 成功才算抢到
LOCK="locks/daily-report.lock"
git pull -q --rebase

if [ -f "$LOCK" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || stat -c %Y "$LOCK") ))
    if [ $AGE -lt 3600 ]; then
        echo "另一个节点正在执行，退出"; exit 0
    fi
    echo "锁已过期（${AGE}s），接管"
fi

echo "$(hostname) $(date -u +%FT%TZ)" > "$LOCK"
git add "$LOCK"
git commit -q -m "acquire lock: daily-report"
git push -q || { echo "抢锁失败"; exit 0; }

# 执行业务逻辑 ...

# 释放
git rm -q "$LOCK"; git commit -q -m "release lock"; git push -q
```

**注意：`git push` 失败就说明别人抢到了**，不用额外加锁机制——Git 的 push 原子性已经够用。

### 方案 C：中心化调度

一台机器做调度器，分发给其他节点执行。

**只有任务量大、真的需要分布式时才值得。** 否则纯属自找麻烦。

---

## 失效检测

**任务不跑，你通常不知道。** 这是定时任务最隐蔽的问题。

### 心跳模式

每个任务执行完，往 Git 仓库写一个心跳文件：

```bash
# 任务最后一步
echo "$(date -u +%FT%TZ)" > "heartbeats/${TASK_NAME}.last"
git add -A && git commit -q -m "heartbeat: $TASK_NAME" && git push -q
```

**另一个任务（看门狗）检查心跳是否新鲜：**

```python
from datetime import datetime, timedelta, timezone
import pathlib, json

alerts = []
for hb in pathlib.Path("heartbeats").glob("*.last"):
    ts = datetime.fromisoformat(hb.read_text().strip().replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - ts
    expected = json.loads((hb.parent / f"{hb.stem}.meta.json").read_text())["interval_minutes"]
    if age > timedelta(minutes=expected * 2):
        alerts.append(f"{hb.stem}: 已 {age} 未执行（预期每 {expected} 分钟）")

if alerts:
    print("⚠️ 以下任务疑似失效：")
    for a in alerts:
        print(" -", a)
```

**这就是看门狗模式：不是检查任务本身，而是检查它的心跳。**

### 常见失效原因

| 现象 | 原因 |
|---|---|
| 完全不执行 | 机器休眠 / cron 服务没启动 / 时区错 |
| 偶尔跳过 | 关机期间错过了触发时间（cron 不会补跑） |
| 执行了但报错 | 环境变量不同（cron 的 PATH 和交互 shell 不一样） |
| 执行了但没输出 | 输出重定向丢了 / 日志路径不对 |

**第二行是关键：cron 不补跑。** 机器关着的时候该执行的任务，开机后不会补。

需要补跑能力的话，用 `anacron`，或者自己在脚本里检查「上次执行时间」。

---

## 环境差异：最容易踩的坑

**cron 的执行环境和你在终端里完全不同：**

| | 交互 shell | cron |
|---|---|---|
| PATH | 完整（含 `/opt/homebrew/bin`） | 通常只有 `/usr/bin:/bin` |
| 环境变量 | 加载 `.zshrc` / `.zprofile` | 不加载 |
| 工作目录 | 你当前所在 | `$HOME` |
| 代理设置 | 有 | 没有 |

**所以脚本里必须用绝对路径：**

```bash
#!/bin/bash
set -euo pipefail

# 显式设置环境和 PATH，不依赖 cron 的默认值
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export LANG=zh_CN.UTF-8
export LC_ALL=zh_CN.UTF-8

PYTHON=/opt/homebrew/bin/python3
$PYTHON /absolute/path/to/script.py >> /absolute/path/to/log 2>&1
```

**`set -euo pipefail` 别省** —— 没它的话，中间某步失败脚本还会继续跑，最后报个莫名奇妙的错。

---

## 迁移任务时的检查单

从一台机器搬到另一台：

- [ ] 确认新机器的时区
- [ ] 确认 cron 服务在跑（macOS 用 `launchd`，不是 `cron`）
- [ ] 脚本里的路径全部改成绝对路径
- [ ] 依赖的可执行文件在新机器上存在
- [ ] 凭据 / token 已转移
- [ ] **手动跑一次，看输出**
- [ ] 关掉旧机器上的任务
- [ ] 第二天检查心跳 / 日志，确认真的执行了

**第 6 步和第 8 步是必须的。** 只配不验，等于没配。

---

## macOS 特别说明

**macOS 上 `cron` 能用但不推荐**，应该用 `launchd`：

```xml
<!-- ~/Library/LaunchAgents/com.example.daily.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.example.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/absolute/path/to/script.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key><string>/tmp/example-daily.log</string>
    <key>StandardErrorPath</key><string>/tmp/example-daily.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.example.daily.plist
launchctl list | grep example        # 确认已加载
launchctl kickstart -k gui/$(id -u)/com.example.daily   # 立刻跑一次测试
```

**`launchd` 的好处：** 机器睡醒了会补跑错过的任务；`cron` 不会。
