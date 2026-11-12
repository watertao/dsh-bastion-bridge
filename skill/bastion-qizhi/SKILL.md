---
name: bastion-qizhi
description: 通过齐治堡垒机桥接器的本地 REST API 在目标服务器上执行命令、流式查看输出、发送交互输入，让 AI 直接接管并运维服务器。
whenToUse: 用户要求操作齐治堡垒机已连接的服务器（查询状态、执行命令、看日志、部署、排障等），或提到堡垒机/桥接器/sshbridge/8766 端口时。
disable-model-invocation: true
---

# 齐治堡垒机 SSH 桥接器操作技能

## 背景

齐治堡垒机的 Web 控制台原本只能唤起 `putty.exe` 等交互式终端，AI 无法稳定执行命令。
「桥接器」是一个替换 `putty.exe` 的 Windows EXE：用户在堡垒机控制台点击 SSH 资源时，
它会自动建立到目标服务器的 SSH 连接，并启动一个 HTTP/WebSocket API。
本技能教你通过这个 API 像亲手敲命令一样运维服务器。

```
堡垒机控制台 --唤起--> 桥接器(putty.exe) --SSH--> 目标服务器
                              |
                              +-- <端口文件里的地址>:<端口> REST API <-- 你(AI)调用
```

## 本工作区的固定链路（中继 + nginx 转发部署）

Mac 端口文件固定指向办公笔记本上的 nginx（`<笔记本IP>:9766`，真实地址见 `references/deployment-local.md`，本地文件不入库），转发到该笔记本本机 `127.0.0.1:8766` 的桥接器，再经齐治中继节点二段登录目标机。要点：

- **桥接器真身、config.json、bridge.log 都在那台笔记本上**（`%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\`），Mac 上的同名文件不是事实来源。
- 控制台传给 putty 的主机是**中继 IP**，目标机只在 `root@<目标>'s password:` 提示符里出现；目标机凭据按桥接器 config.json 的 `relay_hosts`（**键 = 目标机 IP**）配置，应答提示符时查表。
- 一次性凭据实测 **91 秒内有效、6 分钟过期**：点击资源后必须立即 `echo` 探活（同时完成二段登录）。
- 单实例锁：**换目标机前先结束笔记本上的旧 putty.exe 进程**，否则新点击只会转交原版 putty。

详见 `references/deployment-office.md`。

## 第一步：发现桥接器端口（每次操作前必做）

桥接器启动时把实际监听地址写入本机文件：

```text
%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\active_port.txt
```

内容形如 `127.0.0.1:8766`，也可能是局域网地址（如 `192.0.2.10:9766`）——**以文件为准**。
使用本技能目录下的命令行工具可一步完成发现+健康检查：

```powershell
python "<本技能目录>\scripts\bridge.py" health
```

- 输出含 `"connected": true` → 桥接器已就绪，可以执行命令。
- 输出 `connected: false` → SSH 未连通，进入「排错速查」，不要盲目重试命令。
- 报错"端口文件不存在 / 服务不可达" → 桥接器未被唤起。**停止操作**，请用户到齐治堡垒机控制台
  点击目标 SSH 资源（工具选本地工具/putty），等待数秒后再重试健康检查。

> 注意：端口文件是本机唯一事实来源。多台服务器/多个桥接器实例会使用不同端口（8766、8767…），
> 每次操作前重新读取，不要沿用记忆中的旧端口。

> **`connected: true` 只说明 SSH 传输还活着，不代表 shell 可用。** 会话假死时它照样报
> `connected: true`。判断能否干活的办法是执行一条 `echo <标记>`，看到回显才算通。

## 第二步：执行命令

### 方式 A：脚本（推荐）

```powershell
python "<本技能目录>\scripts\bridge.py" exec "uname -a"
python "<本技能目录>\scripts\bridge.py" exec "df -h" --timeout 30
```

脚本自动发现端口、发送请求、把结果以 UTF-8 JSON 打印到 stdout，并在桥接器返回的
`exit_code` 非 0 时以非零进程退出码退出（方便管道判断）。JSON 字段：

```json
{ "stdout": "...", "stderr": "...", "exit_code": 0 }
```

### 方式 B：直接 HTTP

```bash
curl -X POST http://<地址>/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "df -h", "timeout": 30}'
```

### 命令写法：必须能自己退出（最容易把会话搞死的地方）

桥接器 `/execute` 超时后会**重建交互 shell 通道**：工作目录、环境变量都丢了，命令是否还在跑
也不确定。代价取决于部署：直连部署实测连续重建 20 次会话都正常；中继（relay）部署的登录凭据是
**一次性**的，重建后重新登录会被堡垒机拒绝，**整条会话就此报废，只能请用户在控制台重新点击资源**。
所以不要把重建当成可以依赖的恢复手段。

**所有命令都必须满足：不读键盘输入、不进分页器、在 `timeout` 内自己退出。**

> 非 Windows 上用 `python3` 和正斜杠路径，端口文件在 `~/.sshbridge/qizhisshbridge/active_port.txt`。

0. **`timeout` 不要小于 15 秒。** 一条 `echo` 本身就要约 6 秒（多行脚本还要更久，见第 9 条），
   这段时间算在 `timeout` 里；设 3 秒连 `echo` 都会超时，并触发通道重建。

1. **绝不进入分页器。** `git log`、`systemctl status`、`journalctl`、`man`、`less`、`more`
   在有 TTY 时都会分页。git 一律 `git --no-pager ...`；其它命令管道给 `cat`。
   连上后先设好保命环境变量（对持久 shell 生效一次即可）：
   ```bash
   export PAGER=cat GIT_PAGER=cat SYSTEMD_PAGER=cat MANPAGER=cat GIT_TERMINAL_PROMPT=0
   ```
2. **绝不让命令等交互确认。** root 下 `cp`/`mv`/`rm` 常被别名成 `-i`（`cp: overwrite ...?`）。
   用 `\cp -f`、`\mv -f`、`command cp -f` 绕开别名；不要 `read`、不要不带参数的 `sudo`、
   不要数据库客户端/分页器/编辑器，任何需要敲键盘的程序都不要放进 `exec`。
3. **给可能慢的命令加远端 `timeout`。** 如 `timeout 120 systemctl restart dg01`。
   远端 `timeout` 到点会杀掉命令并让 shell 正常返回；桥接器层超时则会重建通道。
4. **可能挂住的长命令用 `stream`，不要用 `exec` 硬等。** 客户端中途放弃流式请求时，
   桥接器会向远端发 Ctrl-C 中断命令，**通道还能继续用**；`/execute` 超时的处理则是重建通道。
5. **一次只做一件事，命令短、非交互、可重试。** 失败先停，用 `echo` 探活再决定下一步；
   不要对着同一个失败命令连环重试。
6. **以仓库属主的身份跑 git。** 属主是 `dg01` 的仓库、你以 root 跑 `git` 会报
   `dubious ownership` 并退出；用 `runuser -u dg01 -- git --no-pager ...`（或 `sudo -u dg01`）。
7. **批量写入前先备份、再安装。** 覆盖文件用 `\cp -f`，装完 `sha256sum` 核对，
   属主/权限跟着原文件走（如 `chown dg01:dg01`、`chmod 755`）。
8. **会读终端的程序绝不放进 `exec`，也不要用 `timeout` 硬杀包装脚本。** 脚本里的真正程序
   会变成孤儿，继续抢桥接器发给 shell 的输入，通道关闭后还可能满 CPU 空转。这是实测里
   唯一稳定造成假死的原因。先看 `file $(command -v <程序>)`，包装脚本就直接启动里面的程序。
9. **命令的行数比字节数贵。** 每多一行约多 1 秒（20 行 26 秒），而 25 万字符的单行只要 11 秒。
   多条命令用 `;` 或 `&&` 连成一行。
10. **退出码 126 是危险操作守卫拦的，提示在 `stderr`，命令没有发到服务器。** 它按 `|`、`;` 切分时
    不看引号，`grep -E 'a|rm|b'` 这样的文本也会被当成 `rm`；被误判时换个写法（比如多个 `-e`）。
    确实危险的操作（`rm`、`kill`、`reboot` 等）请用户手动处理，**不要用别的办法绕开**。

### 退出码语义（务必遵守）

| exit_code | 含义 | 处理 |
|---|---|---|
| 0 | 成功（即使 stdout 为空也正常） | 直接采用输出 |
| 非 0 | 命令失败 | 结合 `stderr` 分析原因，向用户解释，不要反复重试同一命令 |
| -1 | SSH 未连接或执行异常 | 先执行 `health` + `echo` 探活，不要继续发命令 |

> 齐治默认走交互 shell（PTY），此时 `stderr` 会合并进 `stdout`（JSON 里 `stderr` 为空），
> 失败原因请看 `stdout`；若结果带 `error` 字段，说明命令未正常完成（如超时），不要当成命令本身的退出码。

## 第三步：长任务 / 流式输出

耗时或持续输出的命令（`ping`、`tail -f`、长编译、大文件操作）用流式接口：

```powershell
python "<本技能目录>\scripts\bridge.py" stream "dmesg | tail -n 5"
```

HTTP 等价形式：`GET http://<地址>/stream?command=...`，返回 SSE 事件流
（`start` → `stdout`/`stderr` → `exit` → `end`）。

规则：
- 预计 30 秒内完成的命令用 `exec`；超过或不确定用 `stream`。
- 单次输出超过 ~50KB 的命令，结果只摘取关键片段，并明确告知用户"输出过长已截断"。
- **可能挂住的命令优先用 stream**：客户端放弃流式请求时桥接器发 Ctrl-C，通道不重建；
  走 `/execute` 超时则会重建通道（中继部署下等于会话报废）。
- 无论哪条通道，命令本身都要能自己结束；`stream` 里也建议用远端 `timeout` 包一层。

## 交互式输入

命令中途需要应答（`y/n` 确认、sudo 密码等）时：

```powershell
python "<本技能目录>\scripts\bridge.py" input "y`n"
```

发送给桥接器持久 shell 通道。**安全规则**：除非用户明确授权，绝不自动发送 `y`、
密码或任何确认输入。

> `/input` 发的是持久 shell 的键盘输入，谁在读就谁收到：shell、分页器、登录提示都可能吃掉它。
> 只有在**明确知道当前有提示符在等输入**时才用；状态不明时先用 `echo` 探活，不要盲发。

驱动交互程序的规矩（实测踩出来的）：

1. 用 `stream` 启动，同时读它的输出；**看到期望的提示符才用 `/input` 发下一句**。等不到就停，
   尤其是密码：发早了读它的是 shell，密码就进了 shell 历史。
2. 程序外面套 `timeout --foreground -k 5 N`，作为兜底。交互程序读的是终端，少了 `--foreground` 它读不到。
3. **放弃流式请求之前，先让程序退出。** 桥接器要等下次写输出才发现客户端走了：程序要是在静默等输入，
   它就一直占着命令锁，`health` 正常，后面的命令全部卡住。这时发一次 `/input` 的 `\x03` 通常能救回来。
4. `/input` 的返回要看 `status`：守卫拒绝时 HTTP 还是 200，`status` 是 `refused`，内容没有发出去。
   它也检查每一行输入，所以密码、文件名被误判时也会这样。

## 文件传输

桥接器没有上传接口。**完整流程、站点配置见 `references/file-transfer.md`，动手前先读。**

- **几 KB 的文本**：heredoc，行数要少（每行约 1 秒）。
- **2MB 以内**：`python3 "<本技能目录>/scripts/ft.py" push <本地文件> <服务器完整路径>`，分块直传，
  结束时校验 `sha256`，不需要密码和网页。2MB 约 90 秒。
- **超过 2MB，或用户要求走数据交换系统**（再大分块直传就要等好几分钟）：网页上传 → 服务器 `ft.py get` → 校验 → 网页删除。
  默认放在交换系统的 `agent` 文件夹（没有就建），**传完删掉本次上传的文件**；网页由用户登录。
- 单行 base64 不要超过约 700KB（512KB 原始数据 25 秒，1MB 超过 120 秒没完成）。
- 安装前先 `sha256sum` 对照本地值；安装后核对属主与权限。

## 安全红线（不可违反）

1. API 仅监听本机/局域网地址，但**任何人能访问就等于拥有该服务器 shell**。
   只在你和用户都明确授权的会话中执行操作。
2. 不得把命令输出中的口令、私钥、令牌等敏感内容写入文件或转发到本机之外；
   转述给用户时先脱敏（如 `password=***`）。
3. 危险操作（`rm -rf`、`shutdown`、`reboot`、`iptables` 变更、`mkfs`、
   生产数据库的 DDL/DML、批量删除）执行前必须逐条向用户确认并获得明确同意。
4. 所有命令经堡垒机审计通道执行，不要尝试绕过、提权或清理历史记录。

## 排错速查

| 现象 | 处理 |
|---|---|
| 端口文件不存在 | 桥接器未启动：请用户在齐治控制台点击 SSH 资源唤起它 |
| `/health` 返回 `connected: false` | SSH 连接失败：请用户查看 `%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\logs\bridge.log` 末尾的报错（认证失败/网络不通/参数解析失败），据实向用户说明 |
| `connected: true` 但所有命令都超时 | 会话假死：`health` 只看传输层。先 `echo` 探活，仍超时按 `references/troubleshooting.md`「会话假死」处理，**不要加大 timeout 重试**。重连之后第一件事查孤儿进程：`ps -eo pid,ppid,stat,pcpu,etime,comm --sort=-pcpu \| head` |
| 退出码 126 | 危险操作守卫拦的，命令没有发到服务器，见「命令写法」第 10 条 |
| `/input` 返回 `status: refused` | 同上，这段输入没有发出去 |
| 流式请求放弃后，命令全部卡住 | 程序还在静默等输入、占着命令锁：发一次 `/input` 的 `\x03`，见「交互式输入」 |
| 命令超时 | 桥接器层超时可能已重建通道。先 `echo` 探活；连接已坏就不要再发命令（见 `references/troubleshooting.md`「会话假死」） |
| 反复超时 / `中继认证被拒绝` / `交互 shell 通道已关闭` | 会话已报废：**停止重试**，请用户在齐治控制台重新点击资源；恢复后用 `echo` 探活再继续 |
| 同一目标机固定 `Permission denied`（别的机器正常） | 中继部署下 `relay_hosts` 键/密码不对：键必须是**目标机 IP**，密码用原版 putty 手输做 A/B 验证；修好笔记本上的 config.json 后重新点击资源，见 `references/deployment-office.md` |
| 远端输出 `Connection closed for user@host:port` | 远端（堡垒机中继/目标机 sshd）主动关闭会话，不是命令语法问题；重连后查目标机 `/var/log/secure` 和齐治会话审计，不要用重试解决 |
| 命令报 `dubious ownership` | 别人的仓库别用 root 跑 git：`runuser -u <属主> -- git --no-pager ...` |
| 大文件传不上去 | 桥接器没有上传接口：见 `references/file-transfer.md` |
| 中文乱码 | 用 `scripts/bridge.py`（已做 UTF-8 处理），不要手工拼接 curl |

详细接口与排错见本技能目录：
- `references/api.md` —— 全部 REST/SSE/WebSocket 接口参考
- `references/troubleshooting.md` —— 完整排错手册
- `references/file-transfer.md` —— 把文件传到目标服务器：分块直传、数据交换系统
- `references/deployment-office.md` —— 本工作区固定链路拓扑（Mac→nginx→桥接器→中继→目标机）与中继部署守则

## 典型会话示例

用户："帮我看看 192.0.2.5 的磁盘和内存。"
1. `python "<本技能目录>\scripts\bridge.py" health` → 确认 connected。
2. `python "<本技能目录>\scripts\bridge.py" exec "echo PROBE; df -h"` → 探活 + 汇总磁盘。
3. `python "<本技能目录>\scripts\bridge.py" exec "free -h"` → 汇总内存。
4. 用自然语言向用户报告结果（异常项重点说明）。
