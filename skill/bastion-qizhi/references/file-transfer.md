# 文件传到目标服务器

桥接器没有上传接口。两条路：

| 文件 | 走哪条 | 用时（实测） |
|---|---|---|
| 几 KB，文本 | `exec` 里写 heredoc，**行数要少**（每行约 1 秒） | 随行数 |
| 2MB 以内 | **分块直传** `scripts/ft.py push` | 2MB 约 90 秒 |
| 超过 2MB，或用户要求走交换系统 | **数据交换系统**（下面的流程） | 取决于文件 |

## 站点配置

地址和账号不写在技能里（本技能在公开仓库），放在本机配置文件。第一次用时问用户要，写好之后 `chmod 600`：

```json
{
  "host": "<摆渡服务器 IP，MiniFTClient 的 -h>",
  "user": "<交换系统账号>",
  "dir": "agent",
  "keychain_service": "bastion-qizhi-miniftclient",
  "web_url": "<交换系统网页地址>"
}
```

位置：macOS / Linux 是 `~/.sshbridge/qizhisshbridge/ft.json`，Windows 是 `%LOCALAPPDATA%\sshbridge\QizhiSSHBridge\ft.json`。

密码由用户自己存，**不要让用户把密码发到对话里**。macOS 上请用户打开单独的终端窗口运行：

```sh
security add-generic-password -a <账号> -s bastion-qizhi-miniftclient -U -w
```

这条命令要读键盘，不能用 Claude Code 的 `!` 前缀跑（那里没有终端，读不到输入，存进去的是空密码）。其他系统设环境变量 `FT_PASSWORD`。

## 数据交换系统的流程

**默认约定：文件放在交换系统的 `agent` 文件夹（没有就建）；传完之后删掉本次上传的文件。** 只删这一次传的，别的一个都不动。

### 1. 准备

```sh
python3 <本技能目录>/scripts/ft.py mkdir       # agent 文件夹，已存在也算成功
```

算好本地文件的 `sha256`，后面对账。

### 2. 网页上传

**登录由用户来做**：网页有验证码，证书也是自签的（浏览器警告页由用户决定要不要继续）。打开配置里的 `web_url`，请用户登录好再继续。

用浏览器自动化工具：

1. **双击** `agent` 进入文件夹（单击不进），页面路径显示「我的文档 > agent」才算进去了。
2. 「上传」按钮不接受文件。真正的输入框是隐藏的 `input#file`，要先让它显示出来，无障碍树里才定位得到：
   ```js
   () => { const i = document.getElementById('file');
     i.style.cssText += ';display:block !important;position:fixed;top:4px;left:4px;width:300px;height:30px;opacity:1;z-index:99999;';
     i.setAttribute('aria-label', 'ft-file-input'); }
   ```
3. 把文件交给这个输入框。**工具通常只允许工作区内的路径**，文件在别处时先拷到工作区里被 git 忽略的临时目录，传完删掉。
4. 弹出「上传文件设置」：
   - 「可见网络」默认全勾。目标服务器所在的网络必须勾上，否则那边看不到。不需要的网络按用户要求取消。
   - 「上传到」**永远显示「我的文档」，这是误导**：实测文件确实进了当前文件夹。以第 3 步的结果为准，不要点「修改」。
5. 点对话框里的「上传」，等到「上传进度:100%」。当前文件夹的列表不会自己刷新，看不到新文件是正常的。

### 3. 服务器下载并校验

```sh
python3 <本技能目录>/scripts/ft.py get <文件名> --to /tmp/xxx-new --sha256 <本地算的值>
```

`--to` 是服务器上的暂存目录，没有就建。`sha256` 不一致时退出码是 1，**不要继续安装**。

### 4. 网页删除

**`MiniFTClient` 的 `rm` 用不了**：桥接器的危险操作守卫连 `/input` 也检查，直接拒绝，也不要想办法绕开。所以从网页删。

1. 刷新页面，双击进入 `agent`。
2. 按文件名勾选（复选框的 `input` 是隐藏的，点它所在行的 `.el-checkbox`）：
   ```js
   () => { const row = [...document.querySelectorAll('.el-table__row')].find(r => r.innerText.includes('<文件名>'));
     row.querySelector('.el-checkbox').click(); }
   ```
3. **核对之后才点删除**，这两步不要并行发出去：路径是「我的文档 > agent」，所有带 `is-checked` 的行只有这一个文件。
4. 点「删除」，在确认框点「确定」，看到「删除成功」。

删除后文件进回收站，容量随之释放，**不要清回收站**。

如果网页删不掉，告诉用户文件还留在交换系统上，不要反复尝试。

### 5. 收尾

本地拷到工作区的临时文件删掉。服务器暂存目录里的文件不能经桥接器 `rm`（守卫拦截），安装时用 `\mv -f` 挪走，剩下的告诉用户。

## MiniFTClient 的脾气

优先用 `scripts/ft.py`，它已经把下面这些都处理了。要手工驱动时必须全部遵守：

| 事实 | 后果 | 做法 |
|---|---|---|
| `MiniFTClient` 是包装脚本，里面的 `MNFTClient $@` 是子进程 | `timeout` 只杀外层，真正的程序变成孤儿，继续抢终端输入，通道关闭后满 CPU 空转 | 直接启动 `MNFTClient`，自己设 `LD_LIBRARY_PATH=<目录>/LibMiniFTClient`，外面套 `timeout --foreground -k 5 N` |
| `exec` 超时不会结束它 | 同上。这是最容易把会话弄死的操作 | **绝不放进 `exec`**，用 `stream` + `input` |
| `-p <密码>` 进 `ps`、shell 历史和审计 | 密码泄露 | 只给 `-h -u`，它会问 `输入密码:`，屏幕上是 `*` |
| 密码发早了，读它的是 shell | 密码进 shell 历史（实际发生过） | **看到 `输入密码:` 才发**；没看到就停 |
| 登录失败退出码也是 0 | 按退出码判断会误以为成功 | 看有没有出现 `请输入菜单号:`。失败不重试，免得锁账号 |
| 登录后是菜单，选 `1` 才进「我的文档」 | 不选的话 `ls`、`get` 都被当成无效菜单号 | 先发 `1`，等到 `<` 结尾的提示符 |
| Ctrl-C 退不出菜单；输入流结束时它死循环 | `</dev/null`、管道喂命令都不行 | 退出只用 `exit` 或 `bye`（`quit` 不认） |
| `cd /` 报参数错 | 回不到根 | 新开一次登录；`ft.py` 里用 `--dir .` 表示根 |
