# 换机器 / 加机器清单

**环境迁移最容易漏东西。漏一个，你可能一周后才发现。**

这份清单按「症状」而不是「步骤」组织——每一条都写了**漏了会怎样**。

---

## 一、新机器接入前的准备

### 1. 网络可达性先测，别先改配置

```bash
python3 scripts/probe_mesh.py --config mesh.yaml
```

**先知道新机器能连到谁、不能连到谁，再决定它扮演什么角色。**

**漏了的后果：** 配置全改完，发现新机器在墙内连不上 VPS，白干半天。

### 2. 基础身份

```bash
# 主机名（多机时靠它区分日志来源）
sudo scutil --set HostName my-node        # macOS
sudo hostnamectl set-hostname my-node     # Linux

# SSH 密钥（不要复用旧机器的私钥）
ssh-keygen -t ed25519 -C "my-node@$(hostname)"
```

**漏了的后果：** 所有机器主机名都是 `localhost`，日志里分不清是谁写的。

---

## 二、SSH 互通

### 3. 生成密钥并分发

```bash
# 新机器上
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

**把公钥加到目标机器的 `~/.ssh/authorized_keys`。**

### 4. 测试免密

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes user@target 'echo OK'
```

**`BatchMode=yes` 很重要** —— 它禁止交互式输入密码。如果这条命令卡住，说明密钥没配好，**而普通 `ssh` 会让你以为配好了（其实是在等你输密码）**。

### 5. 写入 `~/.ssh/config`

```
Host vps
    HostName 203.0.113.10
    User root
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host vps-proxy
    HostName 203.0.113.10
    User root
    ProxyCommand nc -X 5 -x 127.0.0.1:7897 %h %p
    ServerAliveInterval 30

Host desktop
    HostName 192.168.1.50
    User mac
```

**同一台机器写两个 Host（直连 + 走代理），出问题时可以立刻切换对比。**

**漏了的后果：** 每次都要手打 `-o ProxyCommand=...`，容易打错，而且容易忘记自己用的是哪个。

---

## 三、运行时环境

### 6. 语言运行时和版本

```bash
# 记录版本，方便回查
python3 --version
node --version 2>/dev/null
git --version
```

**建议在新机器上装跟旧机器一致的版本。** 大版本差异会导致细微行为不同（尤其是 Python 3.9 vs 3.11 的 `datetime` 处理）。

**漏了的后果：** 脚本在旧机器跑得好，新机器报一个你没见过的错。

### 7. 时区和 locale

```bash
# macOS
sudo systemsetup -settimezone Asia/Shanghai
# Linux
sudo timedatectl set-timezone Asia/Shanghai

# 中文字符环境（否则日志里中文会乱码）
export LANG=zh_CN.UTF-8
export LC_ALL=zh_CN.UTF-8
```

**漏了的后果：** 中文输出变问号；定时任务在错误的时刻执行。

### 8. 代理配置

```bash
# ~/.zprofile 或 ~/.bashrc
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
export NO_PROXY="localhost,127.0.0.1,::1,.local,.cn,baidu.com,..."
export no_proxy="$NO_PROXY"
```

**`NO_PROXY` 必须写全**，见 [connectivity.md](connectivity.md)。

**漏了的后果：** 代理一挂，国内站也访问不了，你会误判成「网络全断」。

---

## 四、凭据

### 9. 逐个确认，不要假设

| 类型 | 检查方式 |
|---|---|
| API token | 实际调一次接口，看返回 |
| 数据库密码 | 实际连一次 |
| Git 凭据 | `git ls-remote` 一次 |
| SSH key | `BatchMode=yes` 连一次 |

**每一个都要实际用一次。** 配置文件里写着，不等于能用——token 可能过期、权限可能不对。

**漏了的后果：** 任务跑了，但报 401，而你以为它是成功的（如果没检查返回值）。

### 10. 密钥不要进 Git

```bash
# .gitignore 至少包含
.env
*.pem
*.key
cookies*.txt
credentials*
```

**如果真的需要同步凭据，单独加密，或者手工传输。绝不明文进仓库。**

---

## 五、Agent 相关

### 11. 配置文件和记忆

- [ ] Agent 主配置（模型、provider、API key 引用）
- [ ] 记忆文件 / 数据库
- [ ] 技能目录
- [ ] 定时任务定义

**先备份旧的，再迁移。** 迁移前：

```bash
tar czf ~/agent-backup-$(date +%Y%m%d).tar.gz \
    ~/.agent/config* ~/.agent/memory* ~/.agent/skills ~/.agent/cron
```

### 12. 路径全部改成新机器的

**这是最容易漏的一项。** 脚本里写死 `/Users/oldname/...` 的路径，换机器全废。

```bash
# 扫一遍有没有硬编码路径
grep -rn "/Users/[a-z]*/" ~/.agent/scripts/ | grep -v "\$HOME"
grep -rn "/home/[a-z]*/" ~/.agent/scripts/ | grep -v "\$HOME"
```

**改成 `Path.home()` 或 `$HOME`。**

**漏了的后果：** 任务静默失败（找不到文件），日志里只有一行 `No such file`。

---

## 六、验证（最重要的一段）

### 13. 手动跑一次每一个任务

```bash
# 不要等定时触发，直接跑
bash /path/to/task.sh
```

**看输出，看退出码。**

```bash
bash /path/to/task.sh; echo "exit=$?"
```

**漏了的后果：** 任务配置看着对，实际跑起来报错，而你要等到明天才知道。

### 14. 强制触发一次定时任务

```bash
# launchd
launchctl kickstart -k gui/$(id -u)/com.example.task

# cron（临时加一条 1 分钟后的）
# 或直接跑脚本本体
```

**配置文件和实际执行是两回事。** 必须真的让它按定时器跑一次。

### 15. 确认它自动执行了

**等一个周期，然后查日志 / 心跳文件。**

```bash
# 心跳文件的时间戳应该是最近一次执行
ls -la /path/to/heartbeats/

# 或者看日志末尾
tail -20 /path/to/log
```

**这一步很多人跳过，然后一周后发现任务从来没跑过。**

---

## 七、收尾

### 16. 关掉旧机器上的任务

```bash
# 确认旧机器上的任务确实删了或禁用了
crontab -l | grep <task>     # 应该没有输出
ls ~/Library/LaunchAgents/ | grep <task>
```

**漏了的后果：** 两边都跑，重复发通知 / 重复扣费。

### 17. 记录这次迁移

写进 changelog：什么时候、从哪台到哪台、改了什么、踩了什么坑。

**三个月后你自己会需要这份记录。**

---

## 一页速查

```
□ 网络可达性已实测（probe_mesh.py）
□ 主机名已改
□ SSH 新密钥已生成并分发
□ BatchMode 免密测试通过
□ ~/.ssh/config 已写（含代理版和直连版）
□ Python/Node 版本已确认
□ 时区已设
□ LANG/LC_ALL 已设
□ 代理和 NO_PROXY 已配
□ 所有凭据已实测可用
□ .gitignore 覆盖敏感文件
□ Agent 配置/记忆/技能已迁移
□ 硬编码路径已全部改为 $HOME
□ 每个任务手动跑过一次
□ 定时任务强制触发过一次
□ 等一个周期确认自动执行了
□ 旧机器任务已关闭
□ changelog 已更新
```

**打勾之前不要宣布「迁移完成」。**
