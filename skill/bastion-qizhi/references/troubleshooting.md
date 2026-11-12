# 排错手册（齐治堡垒机桥接器）

## 症状 → 处理

| 现象 | 排查方向 |
|---|---|
| 端口文件 `active_port.txt` 不存在 | 桥接器未被唤起。请用户到齐治堡垒机控制台点击 SSH 资源（工具选本地工具/putty）。 |
| API 连不通 / 连接被拒 | 端口文件与实际端口不一致（端口被占时桥接器会自动顺延 8767、8768…）。以 `active_port.txt` 为准。 |
| `/health` 返回 `connected: false` | SSH 连接失败。查看日志末尾：认证失败（用户名/密码错）、网络不通、参数解析失败。 |
| `/health` 报 `connected: true`，但命令全超时 | 会话假死，见下面「会话假死」。`/health` 只看 SSH 传输，不看 shell 是否可用。 |
| `exit_code: -1` | SSH 已断开或执行异常。先 `/health` + `echo` 探活，必要时请用户重新点击资源。 |
| 命令超时 | 桥接器层超时可能已重建通道。先探活，不要加大 `timeout` 硬重试；见「会话假死」。 |
| 反复超时 / `中继认证被拒绝` / `交互 shell 通道已关闭` | 会话已报废，停止重试，请用户重新点击资源。 |
| 输出里出现 `Connection closed for user@host:port` | 远端（堡垒机中继/目标机 sshd）主动关闭会话，见「远端主动关闭」。 |
| `no acceptable kex algorithm` | 旧服务器算法问题。当前版本已内置 Paramiko 2.12.0 兼容旧算法，如仍出现请升级本工具。 |
| 中文乱码 | 用 `scripts/bridge.py`（已强制 UTF-8）；curl 下为终端编码问题。 |
| 端口被占用 | 桥接器自动顺延端口并写回 `active_port.txt`，无需处理。 |
| 杀毒软件报毒 | PyInstaller 单文件常见误报。加白名单或改用源码方式运行（`python main.py <参数>`）。 |

## 会话假死（最常见，先看这里）

症状：某条命令超时之后，**后面所有命令都超时**；或直接报
`中继认证被拒绝：目标机用户名/密码未被接受`、`交互 shell 通道已关闭，命令可能仍在远端运行`。
`/health` 仍可能报 `connected: true`。

原因分三种：

- **孤儿交互程序（直连部署上实测唯一稳定造成假死的）：** 用 `timeout` 杀包装脚本、或把会读终端的
  程序放进 `exec` 之后，真正的程序还活着，会抢走桥接器发给 shell 的输入，通道关闭后还可能满 CPU 空转。
  对照实验：同一台机器上只用 `sleep` 超时，连续重建 20 次都正常；一旦留下孤儿，命令就开始全部超时。
  孤儿导致新通道也不可用的确切机制还没有查清。
- **命令锁被占着：** 放弃流式请求时程序在静默等输入，桥接器发现不了客户端已经走了。发一次 `/input` 的 `\x03`。
- **中继部署的一次性凭据：** 超时后重建通道，重新登录被堡垒机拒绝，会话报废。

处理：
1. `health` 一次 + `exec "echo PROBE"` 一次，确认是不是真的死了；**不要连续重试**。
2. 已报废就请用户在齐治控制台重新点击目标资源；恢复后先 `echo` 探活再继续。
3. **恢复之后立刻查孤儿**：`ps -eo pid,ppid,stat,pcpu,etime,comm --sort=-pcpu | head`，找 `PPID` 是 1、
   没有终端、CPU 很高的进程，确认是自己启动的（看 `/proc/<PID>/cmdline`）。重新点击不会清掉它们；
   桥接器不让执行 `kill`，告诉用户 PID，请用户手动杀。
4. 不要在状态不明时用 `/input` 盲发 `y`、回车或密码——输入会落到当前等待输入的程序
   （shell、分页器、登录提示），可能让状态更糟。

常见诱因（写命令时规避）：
- **`timeout` 设得太短**：一条 `echo` 就要约 6 秒，3 秒以内必超时。不要小于 15 秒。
- **进了分页器**：`git log`、`systemctl status`、`journalctl`、`man`、`less`、`more`。
  git 用 `--no-pager`；连上后设置
  `export PAGER=cat GIT_PAGER=cat SYSTEMD_PAGER=cat MANPAGER=cat GIT_TERMINAL_PROMPT=0`。
- **等交互确认**：root 的 `cp`/`mv`/`rm` 别名带 `-i`（`cp: overwrite ...?`）。
  用 `\cp -f`、`\mv -f`、`command cp -f`。
- **命令跑太久**：超过 `/execute` 的 `timeout`。给命令加远端 `timeout N` 包一层，
  或改用 `/stream`（客户端放弃时桥接器发 Ctrl-C，不重建通道）。
- **在别人的仓库里以 root 跑 git**：`dubious ownership` 直接退出；
  用 `runuser -u <属主> -- git --no-pager ...`。

## 远端主动关闭

现象：输出里出现 `Connection closed for user@host:port`（或类似字样），随后通道关闭。
这是**远端**（堡垒机中继或目标机 sshd/会话代理）把会话关了，不是桥接器的命令解析问题。
重连后查：目标机 `/var/log/secure`、`/var/log/messages` 对应时间点；齐治控制台的
会话审计（结束原因：超时 / 强制断开 / 策略）。不要用重试解决。

## 文件传输

桥接器只有执行接口，没有上传接口。怎么传见 [file-transfer.md](file-transfer.md)。

- 安装用 `\cp -f`（绕开 root 的 `-i` 别名），保持原文件的属主与权限（如 `dg01:dg01`、755）。

## 日志位置

```text
%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\logs\bridge.log
```

滚动保留 5 份，单份默认 10MB。命令与返回正文只显示在桥接器控制台，不写入日志文件。
控制台默认用 `>>>` / `<<<` 分块展示指令与服务器返回，交互输入正文始终隐藏。日志关键行为：

- `Parsing argv:` —— 堡垒机实际传参（密码已脱敏），适配新版本齐治时以此为准。
- `SSH connected successfully` / `SSH connect attempt N failed` —— 连接过程。
- 命令长度、退出码与 stdout/stderr 字符数 —— 每条命令的元数据。
- `Keep-alive` —— 空闲保活记录（默认空闲 3 分钟执行一次无害命令，有指令在执行时顺延）。

## 配置文件

```text
%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\config.json
```

首次运行自动生成。修改后需重新唤起桥接器生效。关键项：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `api_host` | `127.0.0.1` | **切勿改为 0.0.0.0**（会暴露到局域网） |
| `api_port` | `8766` | 被占用时自动顺延 |
| `ssh_connect_timeout` | `15` | SSH 握手超时 |
| `retry_max_attempts` | `3` | 连接失败重试次数 |
| `keepalive_enabled` | `true` | 空闲保活开关 |
| `keepalive_interval_seconds` | `180` | 空闲多久触发保活（有指令在执行时顺延） |
| `keepalive_command` | `ls` | 保活命令，须无害 |
| `log_level` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `relay_username` | 空 | 中继模式：目标机用户名（留空则 `login:` 交给 `/input`） |
| `relay_password` | 空 | 中继模式：目标机密码（留空则 `password:` 交给 `/input`） |
| `relay_prompt_timeout` | `6` | 等中继提示符的秒数（最小 3；含下一跳 ssh 握手后的密码提示） |

## 齐治版本适配

不同版本齐治堡垒机唤起 putty.exe 的传参格式可能不同。桥接器已支持：

- PuTTY 显式参数：`-ssh user@host -P 22 -pw password`
- PuTTY 会话文件：`-load tmp:C:\...\putty-xxxx-config.txt -pw password`
  （自动解析 `HostName` / `PortNumber` / `UserName` 的 `\`、`=`、`:` 三种分隔格式）

若遇到新格式：查看日志中 `Parsing argv:` 行 → 修改 `params.py` 增加解析分支 → 重新构建。
