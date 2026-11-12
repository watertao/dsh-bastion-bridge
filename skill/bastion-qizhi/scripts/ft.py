#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经桥接器在目标服务器上收文件：驱动 MiniFTClient，或者分块直传。纯标准库。

用法：
    python ft.py ls    [--dir agent]
    python ft.py mkdir [--dir agent]                        已存在也算成功
    python ft.py get NAME --to /tmp/x [--dir agent] [--sha256 HEX]
    python ft.py push LOCAL REMOTE [--chunk-kb 256] [--force]

站点信息（host、user、dir、keychain_service）不写在技能里，按这个次序取：
命令行参数 > 环境变量 FT_HOST / FT_USER / FT_DIR > 配置文件
~/.sshbridge/qizhisshbridge/ft.json（Windows 是
%LOCALAPPDATA%\\sshbridge\\QizhiSSHBridge\\ft.json）。

密码按这个次序取：环境变量 FT_PASSWORD > macOS 钥匙串（配置里的
keychain_service）> 终端提示。只经 /input 发送，不进命令行、不打印。

退出码：0 成功；1 操作失败（登录失败、文件没传到、校验不一致）；
2 桥接器或服务器状态不对，什么都没发送就停下了；3 参数或配置错误。

这些做法都是实测踩出来的，为什么这么写见 references/file-transfer.md：
- 没等到提示符绝不发送（盲发时密码会落到 shell 上）。
- 跳过包装脚本直接启动 MNFTClient：包装脚本里它是子进程，timeout 只杀得到外层。
- 孤儿 MNFTClient 还在就不开始。
- 退出只靠 exit：Ctrl-C 退不出它的菜单。
- 登录失败不重试，免得锁账号。退出码靠不住（失败也是 0），看输出。
"""

import argparse
import getpass
import hashlib
import json
import base64
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge import discover_port, ensure_utf8_console  # noqa: E402

CONFIG_FILES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "sshbridge" / "QizhiSSHBridge" / "ft.json",
    Path.home() / ".sshbridge" / "qizhisshbridge" / "ft.json",
)

#: 找到真正的程序和它的库目录。包装脚本在 PATH 里，程序在它旁边。
FIND_CLIENT = 'D=$(dirname "$(readlink -f "$(command -v MiniFTClient)")")'

PROMPT_PASSWORD = r"输入密码:\s*$"
PROMPT_MENU = r"请输入菜单号:\s*$"
PROMPT_SHELL = r"<\s*$"

ANSI_RE = re.compile(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]")

#: 单次 exec 的超时下限：一条 echo 本身就要 6 秒左右，设短了连它都超时、触发通道重建。
EXEC_TIMEOUT_MIN = 15


class Stop(Exception):
    """什么都没发送就停下了（退出码 2），或操作失败（退出码 1）。"""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def clean_output(text: str, secret: str = "") -> str:
    """去掉控制序列和退格，遮掉密码。"""
    if secret:
        text = text.replace(secret, "***")
    text = ANSI_RE.sub("", text).replace("\r", "")
    while re.search(r"[^\x08]\x08", text):
        text = re.sub(r"[^\x08]\x08", "", text)
    return re.sub(r" {3,}", " ", text.replace("\x08", ""))


class Bridge:
    """桥接器的三个接口。"""

    def __init__(self, base: str) -> None:
        self.base = base

    def _post(self, path: str, payload: dict, timeout: float) -> dict:
        request = urllib.request.Request(
            f"http://{self.base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")

    def execute(self, command: str, timeout: int = 30) -> dict:
        wait = max(EXEC_TIMEOUT_MIN, timeout)
        result = self._post("/execute", {"command": command, "timeout": wait}, wait + 30)
        result["stdout"] = clean_output(result.get("stdout", ""))
        return result

    def send(self, data: str) -> None:
        """发给正在读输入的程序。守卫拒绝时 HTTP 还是 200，只有 status 看得出来。"""
        body = self._post("/input", {"data": data}, 15)
        if body.get("status") != "ok":
            raise Stop("桥接器拒绝发送这段输入（可能被危险操作守卫拦下），这一步没有发到服务器。", 1)

    def stream(self, command: str, on_text: Callable[[str], None], on_end: Callable[[Optional[int]], None]) -> None:
        url = f"http://{self.base}/stream?command={urllib.parse.quote(command)}"
        exit_code: Optional[int] = None
        try:
            with urllib.request.urlopen(url, timeout=3600) as response:
                buffer = b""
                read = getattr(response, "read1", None) or response.read
                for chunk in iter(lambda: read(4096), b""):
                    buffer += chunk
                    while b"\n\n" in buffer:
                        raw, buffer = buffer.split(b"\n\n", 1)
                        event, data = "", ""
                        for line in raw.decode("utf-8", errors="replace").splitlines():
                            if line.startswith("event:"):
                                event = line[6:].strip()
                            elif line.startswith("data:"):
                                data = line[5:].strip()
                        if event in ("stdout", "stderr"):
                            on_text(json.loads(data or "{}").get("data", ""))
                        elif event == "exit":
                            exit_code = json.loads(data or "{}").get("exit_code")
        except (urllib.error.URLError, OSError):
            pass
        on_end(exit_code)


def preflight(bridge: Bridge) -> None:
    """shell 活着、没有孤儿客户端，才开始。"""
    result = bridge.execute("echo FT-READY; ps -C MNFTClient -o pid=,ppid= ; true", 20)
    out = result.get("stdout", "")
    if "FT-READY" not in out:
        raise Stop(f"shell 没有响应，先按「会话假死」处理：{result.get('error') or out[-200:]}")
    tail = out[out.rindex("FT-READY"):]
    orphans = [m.group(1) for m in re.finditer(r"^\s*(\d+)\s+1\s*$", tail, re.M)]
    if orphans:
        raise Stop(
            f"服务器上有孤儿 MNFTClient（PID {', '.join(orphans)}）：它占着 CPU，还可能抢输入。"
            "桥接器不让执行 kill，请用户手动杀掉后再试。"
        )


class Session:
    """一次 MiniFTClient 登录。用完必须 close，退出靠 exit。"""

    def __init__(self, bridge: Bridge, host: str, user: str, password: str, cwd: str, limit: int) -> None:
        self.bridge = bridge
        self.password = password
        self.buffer = ""
        self.mark = 0
        self.ended = threading.Event()
        self.opened = False
        command = (
            f"{FIND_CLIENT}; mkdir -p {shell_quote(cwd)} && cd {shell_quote(cwd)} && "
            f'LD_LIBRARY_PATH="$D/LibMiniFTClient" timeout --foreground -k 5 {limit} "$D/MNFTClient" '
            f"-h {shell_quote(host)} -u {shell_quote(user)}; echo ft-rc=$?"
        )
        threading.Thread(target=bridge.stream, args=(command, self._on_text, self._on_end), daemon=True).start()

    def _on_text(self, text: str) -> None:
        self.buffer += text

    def _on_end(self, _code: Optional[int]) -> None:
        self.ended.set()

    def _pending(self) -> str:
        return clean_output(self.buffer[self.mark:], self.password)

    def wait(self, pattern: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if re.search(pattern, self._pending()):
                return True
            if self.ended.is_set():
                return False
            time.sleep(0.05)
        return False

    def take(self) -> str:
        text = self._pending()
        self.mark = len(self.buffer)
        return text

    def login(self) -> None:
        if not self.wait(PROMPT_PASSWORD, 30):
            raise Stop(f"没等到密码提示，密码没有发送。程序的输出：{self.take()[-200:]}")
        self.take()
        self.bridge.send(self.password + "\n")
        if not self.wait(PROMPT_MENU, 30):
            raise Stop(f"登录失败，不重试（重试可能锁账号）：{self.take()[-200:]}", 1)
        self.take()
        self.opened = True
        self.command("1")  # 1:我的文档

    def command(self, line: str, seconds: float = 30) -> str:
        """发一行，等回到提示符。没回到提示符就算失败：状态不明时不再往下发。"""
        self.bridge.send(line + "\n")
        if not self.wait(PROMPT_SHELL, seconds):
            raise Stop(f"「{line}」之后 {seconds:.0f} 秒没回到提示符：{self.take()[-200:]}", 1)
        time.sleep(0.2)
        return self.take()

    def close(self) -> None:
        if self.ended.is_set():
            return
        if self.opened:
            try:
                self.bridge.send("exit\n")
            except Stop:
                pass
            if self.ended.wait(20):
                return
        print("MiniFTClient 没有按时退出，它会在会话时限到时被 timeout 结束；在那之前桥接器的命令会排队。", file=sys.stderr)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def load_config() -> Dict[str, str]:
    for path in CONFIG_FILES:
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Stop(f"读不了配置文件 {path}：{exc}", 3)
    return {}


def resolve_password(config: Dict[str, str]) -> str:
    value = os.environ.get("FT_PASSWORD")
    if value:
        return value
    service = config.get("keychain_service")
    if service and shutil.which("security"):
        done = subprocess.run(
            ["security", "find-generic-password", "-a", config.get("user", ""), "-s", service, "-w"],
            capture_output=True,
            text=True,
        )
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.rstrip("\n")
    if sys.stdin.isatty():
        return getpass.getpass("交换系统密码：")
    raise Stop("没有密码：设 FT_PASSWORD，或在配置里写 keychain_service 并存进钥匙串。", 3)


def open_session(bridge: Bridge, args: argparse.Namespace, config: Dict[str, str], cwd: str) -> Session:
    host = args.host or os.environ.get("FT_HOST") or config.get("host")
    user = args.user or os.environ.get("FT_USER") or config.get("user")
    if not host or not user:
        raise Stop("缺交换系统地址或账号：--host / --user，或写进配置文件。", 3)
    config = {**config, "user": user}
    password = resolve_password(config)
    preflight(bridge)
    session = Session(bridge, host, user, password, cwd, args.session_timeout)
    session.login()
    return session


def target_dir(args: argparse.Namespace, config: Dict[str, str]) -> str:
    return args.dir or os.environ.get("FT_DIR") or config.get("dir") or "agent"


def change_dir(session: Session, directory: str) -> None:
    if directory == ".":  # 登录后就在「我的文档」根下，它的 cd 不认 /
        return
    out = session.command(f"cd {directory}")
    if "Working Directory" not in out:
        raise Stop(f"交换系统上进不了 {directory}：{out.strip()[-160:]}", 1)


def cmd_ls(bridge: Bridge, args: argparse.Namespace, config: Dict[str, str]) -> int:
    session = open_session(bridge, args, config, "/tmp")
    try:
        change_dir(session, target_dir(args, config))
        print(re.sub(r"\S*<\s*$", "", session.command("ls")).strip())
    finally:
        session.close()
    return 0


def cmd_mkdir(bridge: Bridge, args: argparse.Namespace, config: Dict[str, str]) -> int:
    directory = target_dir(args, config)
    session = open_session(bridge, args, config, "/tmp")
    try:
        out = session.command(f"mkdir {directory}")
        if "failed" in out and "exist" not in out.lower():
            raise Stop(f"建不了 {directory}：{out.strip()[-160:]}", 1)
        print(f"{directory} 已就绪")
    finally:
        session.close()
    return 0


def cmd_get(bridge: Bridge, args: argparse.Namespace, config: Dict[str, str]) -> int:
    session = open_session(bridge, args, config, args.to)
    try:
        change_dir(session, target_dir(args, config))
        out = session.command(f"get {args.name} ./", args.transfer_timeout)
        if "succ" not in out:
            raise Stop(f"下载没有成功：{out.strip()[-200:]}", 1)
    finally:
        session.close()
    path = f"{args.to.rstrip('/')}/{args.name}"
    result = bridge.execute(f"sha256sum {shell_quote(path)}", 60)
    found = re.search(r"\b([0-9a-f]{64})\b", result.get("stdout", ""))
    if not found:
        raise Stop(f"下载之后读不到 {path}：{result.get('stdout', '')[-160:]}", 1)
    print(json.dumps({"path": path, "sha256": found.group(1)}, ensure_ascii=False))
    if args.sha256 and found.group(1) != args.sha256.lower():
        raise Stop(f"sha256 不一致：期望 {args.sha256}，实际 {found.group(1)}。文件留在原处，不要安装。", 1)
    return 0


def cmd_push(bridge: Bridge, args: argparse.Namespace, _config: Dict[str, str]) -> int:
    raw = Path(args.local).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if args.chunk_kb < 1 or args.chunk_kb > 512:
        raise Stop("--chunk-kb 要在 1 到 512 之间：单行超过约 700KB base64 就开始超时。", 3)
    preflight(bridge)
    target = shell_quote(args.remote)
    part = shell_quote(args.remote + ".part")
    if not args.force:
        result = bridge.execute(f"test -e {target} && echo FT-EXISTS || echo FT-FREE", 20)
        if "FT-FREE" not in result.get("stdout", ""):
            raise Stop(f"{args.remote} 已存在。确认要覆盖就加 --force（先备份）。", 1)
    result = bridge.execute(f': > {part} && echo FT-OK', 20)
    if "FT-OK" not in result.get("stdout", ""):
        raise Stop(f"写不了 {args.remote}.part：{result.get('stdout', '')[-160:]}", 1)
    size = args.chunk_kb * 1024
    total = (len(raw) + size - 1) // size
    for index in range(total):
        data = base64.b64encode(raw[index * size:(index + 1) * size]).decode("ascii")
        # 必须是一行：多行的代价是每行 1 秒，字节数反而便宜。
        result = bridge.execute(f"echo {data} | base64 -d >> {part} && echo FT-OK", 60)
        if "FT-OK" not in result.get("stdout", ""):
            raise Stop(f"第 {index + 1}/{total} 块没写进去：{result.get('error') or result.get('stdout', '')[-160:]}", 1)
        print(f"{index + 1}/{total}", file=sys.stderr, flush=True)
    result = bridge.execute(f"sha256sum {part}", 60)
    if digest not in result.get("stdout", ""):
        raise Stop(f"sha256 不一致，文件留在 {args.remote}.part，没有改名。", 1)
    result = bridge.execute(f"\\mv -f {part} {target} && echo FT-OK", 20)
    if "FT-OK" not in result.get("stdout", ""):
        raise Stop(f"校验过了但改名失败：{result.get('stdout', '')[-160:]}", 1)
    print(json.dumps({"path": args.remote, "sha256": digest, "bytes": len(raw)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ft.py", description="经桥接器在目标服务器上收文件")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def client(name: str, func: Callable, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dir", default=None, help="交换系统上的文件夹（默认配置里的 dir，再默认 agent）")
        p.add_argument("--host", default=None)
        p.add_argument("--user", default=None)
        p.add_argument("--session-timeout", type=int, default=300, help="MiniFTClient 最多跑多久（秒），到时由 timeout 结束")
        p.add_argument("--base", default=None, help="桥接器地址（默认自动发现）")
        p.set_defaults(func=func)
        return p

    client("ls", cmd_ls, "列出文件夹")
    client("mkdir", cmd_mkdir, "建文件夹（已存在也算成功）")
    p_get = client("get", cmd_get, "下载到服务器目录并算 sha256")
    p_get.add_argument("name")
    p_get.add_argument("--to", required=True, help="服务器上的目录，没有就建")
    p_get.add_argument("--sha256", default=None, help="期望值，不一致退出码 1")
    p_get.add_argument("--transfer-timeout", type=int, default=240, help="等一个文件下载完的秒数")

    p_push = sub.add_parser("push", help="不经交换系统，分块直传（2MB 以内用它，再大走交换系统）")
    p_push.add_argument("local")
    p_push.add_argument("remote", help="服务器上的完整路径")
    p_push.add_argument("--chunk-kb", type=int, default=256)
    p_push.add_argument("--force", action="store_true", help="目标已存在也覆盖")
    p_push.add_argument("--base", default=None, help="桥接器地址（默认自动发现）")
    p_push.set_defaults(func=cmd_push)
    return parser


def main(argv=None) -> int:
    ensure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        base = args.base or discover_port()
        if not base:
            raise Stop("端口文件不存在：桥接器可能未被唤起。请到齐治堡垒机控制台点击 SSH 资源后再试。")
        return args.func(Bridge(base), args, load_config())
    except Stop as stop:
        print(json.dumps({"error": str(stop)}, ensure_ascii=False))
        return stop.code
    except (urllib.error.URLError, OSError) as exc:
        print(json.dumps({"error": f"桥接器不可达：{exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
