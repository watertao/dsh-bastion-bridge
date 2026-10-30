# 更新记录

## v1.8.0（未发布）

### 主要变化

- **按目标主机 IP 配置中继凭据**：新增 `relay_hosts` 配置项，可按目标机 IP（或 `"*"` 兜底）分别配置 `username` / `password` / `relay_prompt_timeout`，桥接器按网关传入的目标 IP 精确匹配后回落到全局 `relay_username` / `relay_password`，旧配置零改动兼容。多台凭据不同的目标机之间切换不再需要每次修改 config.json。

### 测试

- 新增 `tests/test_relay_host_profiles.py`：`relay_hosts` 精确匹配、`"*"` 兜底、部分字段回落全局值、每台超时覆盖、畸形条目与超时下限处理。

## v1.7.0（2026-10-30）

### 主要变化

- **launch_mode 启动模式**：同一份 `putty.exe` 可以按需选择行为——`bridge`（默认，只给 AI）、`both`（把齐治传来的参数原样转交给原版 PuTTY，同时保留桥接器，两条独立会话）、`putty`（只转交原版后退出，等价于没换过 putty.exe）、`auto`（用进程级文件锁选出一个桥接实例，第一次点击给 AI，之后每次点击都转交原版 PuTTY——每次点击各建一条隧道，不依赖“一条隧道开两条连接”）。原版路径由 `original_client_path` 指定，默认找 `%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\putty-original.exe`，找不到时安全回退到 `bridge`。
- `auto` 的实例锁用操作系统文件锁实现（Windows `msvcrt.locking` / POSIX `flock`），进程退出（含崩溃）时由内核自动释放，不会留下需要手工清理的锁文件。
- 新增 `mode.txt` 覆盖与 `scripts\set-mode.cmd`：切换模式**不需要管理员权限**，下一次点击资源时生效。
- 修复 README 6.4 的还原命令：正确匹配带时间戳的 `putty.exe.bak.*`，并说明手动生成的 `putty.exe.bak` 不在 `uninstall.ps1` 的匹配范围内。
- **按目标主机 IP 配置中继凭据**：新增 `relay_hosts` 配置项，可按目标机 IP（或 `"*"` 兜底）分别配置 `username` / `password` / `relay_prompt_timeout`，桥接器按网关传入的目标 IP 精确匹配后回落到全局 `relay_username` / `relay_password`，旧配置零改动兼容。多台凭据不同的目标机之间切换不再需要每次修改 config.json。

### 测试

- 新增 `tests/test_launcher.py`：模式解析与别名、`mode.txt` 优先级、原版路径解析、自引用保护、参数原样转发、`putty` 模式等待并透传退出码、原版缺失/启动失败回退 `bridge`。
- 新增 `tests/test_relay_host_profiles.py`：`relay_hosts` 精确匹配、`"*"` 兜底、部分字段回落全局值、每台超时覆盖、畸形条目与超时下限处理。

## v1.6.0（2026-10-29）

### 主要变化

- **中继登录应答（relay）**：适配齐志 TERM-SSHD 这类中继模式——SSH 认证成功后，服务端继续提示 `login:`/`password:` 索要【目标机】的用户名/密码，答完之前 shell 不可用。桥接器现会在注入任何初始化字符之前先探测并应答这些提示符，修复了"`stty -echo` 被当成目标机用户名吃掉 → 认证失败死循环"的问题。
- 新配置项：`relay_username`、`relay_password`（目标机凭据，留空则提示符留给 `/input` 人工应答）、`relay_prompt_timeout`；未配置时行为与直连部署完全一致（零行为变化）。
- 中继应答按状态机推进：已应答 `login:` 后不会因通道静默就提前注入 `stty`，而是继续等目标机 `password:`/shell 提示（密码提示依赖下一跳 ssh 握手，可能晚于静默窗口）；超时或认证被拒时以明确错误中止、不发送任何指令。
- 中继登录未完成时，指令以明确错误返回（提示先经 `/input` 应答），不再污染密码提示符；保活命令在中继完成前自动暂停；流式路径不会用 Ctrl-C 打断仍待应答的提示符。

### 修复

- `params.py` 支持 `-load tmpd:<文件>` 会话前缀（齐志改版 PuTTY 存在 `tmp:`/`tmpd:` 两种前缀）；同时将模块文档字符串改为 raw 字符串，消除 `SyntaxWarning`。

### 测试

- 新增 `tests/test_relay_login.py`：中继应答顺序（先 login/password 后 stty）、每通道仅应答一次、未配置零行为变化、`tmpd:` 解析，以及“密码提示晚于静默窗口”“只配密码不配用户名”“密码被拒”三个边界场景。

## v1.5.1（2026-10-28）

### 修复

- 为 `putty.exe` 补充 Windows VERSIONINFO 资源：文件说明、文件版本、产品名称、产品版本、版权、原始文件名等现在会显示在“属性 → 详细信息”中。
- VERSIONINFO 由 `version.py` 在打包时自动生成（`build_version_info.py` + `putty.spec`），本地构建与 GitHub tag 构建的版本号始终一致。

## v1.5.0（2026-10-28）

### 主要变化

- **默认交互 Shell 通道**：齐治接入客户端上 `exec_command` 常只回登录横幅，默认改走持久 PTY shell，避免首条命令被重复执行；新增 `ssh_prefer_shell` 配置，可切回 exec。
- **交互命令 stdin 隔离**：`read`、`sudo` 密码等命令改为临时脚本 + 单行收尾，包装行不再被误读为应答。
- **Shell 退出码与标记**：命令后立即保存 `$?`；标记改为 SOH 前缀，跨包、回显、无尾换行、持续输出均可正确解析。
- **流式交付**：持续输出按批读取并及时交付；无换行的 `Password:` 等提示不再被缓存。
- **超时与写入**：exec 读取边读边等退出，超时边界不再丢输出；脚本与 `/input` 统一 `sendall` 并带写超时。
- **配置与客户端**：配置含 `%` 不再启动崩溃；流式客户端改用 `read1`，`error`/无 `exit`/断连不再误报成功。
- **控制台**：正文恢复默认色，仅标题与关键状态上色；启动信息补充版本、适用堡垒机、运行配置等。

### 验证

- 109 项单元测试通过。
