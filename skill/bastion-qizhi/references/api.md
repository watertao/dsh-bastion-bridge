# 桥接器 API 参考（齐治堡垒机）

桥接器在本机监听 HTTP/WebSocket，默认 `127.0.0.1:8766`，实际端口以
`%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\active_port.txt` 为准。下文 `<BASE>` 代指实际地址。

## 1. 健康检查

```http
GET <BASE>/health
```

```json
{ "status": "ok", "connected": true }
```

- `connected: true` → SSH 已连接，可执行命令
- `connected: false` → SSH 未连接（失败原因见桥接器日志）

> `connected: true` 只表示 SSH 传输层还活着，**不代表交互 shell 可用**：通道已失效时
> 它仍会报 true。判断能否干活，执行一条 `echo <标记>` 看回显。

## 2. 执行单次命令

```http
POST <BASE>/execute
Content-Type: application/json

{ "command": "uname -a", "timeout": 30 }
```

响应：

```json
{ "stdout": "Linux ...", "stderr": "", "exit_code": 0 }
```

- `timeout` 可选，默认 30 秒，单位秒。
- `exit_code`：`0` 成功；非 `0` 失败（结合 `stderr`）；`-1` SSH 未连接或执行异常。
- 默认走交互 shell（PTY），此时 `stderr` 合并进 `stdout`（`stderr` 为空字符串），退出码仍是命令本身的 `$?`；需要区分两路输出时可让桥接器使用 exec 通道（配置 `ssh_prefer_shell=false`）。
- 400：`command` 缺失或非法；500：执行异常（含 `error`/`detail`/`exception_type`）。

> **`timeout` 到点会重建交互 shell 通道**，工作目录和环境变量都会丢。中继（relay）部署的登录凭据是一次性的，
> 重建后重新登录会被堡垒机拒绝，整条会话报废（只能请用户重新点击资源）。
> 因此：命令要能自己退出（远端 `timeout N` 包一层）；可能挂住的命令改用 `/stream`
> —— 客户端放弃流式请求时桥接器发 Ctrl-C 中断命令，**不会重建通道**。
> 但它要等下次写输出才发现客户端走了：程序在静默等输入时发现不了，会一直占着命令锁。

## 3. 流式输出（SSE）

```http
GET <BASE>/stream?command=dmesg%20%7C%20tail
```

事件序列：`start` → 若干 `stdout`/`stderr` → `exit`（附退出码）→ `end`；出错时 `error`。

事件格式：

```text
event: stdout
data: {"data": "<输出片段>"}
```

## 4. 交互式输入

```http
POST <BASE>/input
Content-Type: application/json

{ "data": "y\n" }
```

数据发送到桥接器维护的持久交互 shell 通道（PTY），命令执行期间也可以发送（用于 `read`、
`sudo` 密码等交互应答）。成功返回 `{"status": "ok", ...}`，shell 不可用时返回 503。
被危险操作守卫拒绝时 **HTTP 仍是 200**，`status` 是 `refused`，内容没有发出去，一定要看 `status`。

## 5. WebSocket（Socket.IO）

连接 `ws://<BASE>/ws`（Socket.IO 协议），事件：

| 发送事件 | 载荷 | 接收事件 |
|---|---|---|
| `execute` | `{"command": "...", "timeout": 30}` | `result`（同 /execute 响应） |
| `stream` | `{"command": "..."}` | `start` → `output`（`{type, data}`）→ `end` |
| `input` | `{"data": "y\n"}` | `input_status`（`{ok, message}`） |

连接建立时服务端主动发送 `status` 事件：`{"status": "connected", "ssh_connected": bool}`。

## 6. 错误响应通用格式

```json
{
  "error": "execute failed",
  "detail": "<异常信息>",
  "exception_type": "SomeException"
}
```

## 7. 接口选择建议

| 场景 | 接口 |
|---|---|
| 简单查询（`uname -a`、`df -h`） | `POST /execute` |
| 耗时/持续输出 | `GET /stream` 或 WS `stream` |
| 命令中途需应答 | `POST /input` |
| 长会话、多命令 | WebSocket `/ws` |
