"""中继登录应答（login:/password:）与 tmpd: 会话文件解析测试。

齐志 TERM-SSHD 在 SSH 认证成功后还会索要【目标机】的用户名/密码；
SSHBridge 必须先应答提示符再注入 stty/指令，否则初始化字节会被
login: 当成用户名吃掉（原故障表现：``stty -echo@目标机's password:``）。
"""

import os
import re
import tempfile
import time
import unittest

import ssh_client
from connection import ConnectionParams
from params import parse_args


class _RelayChannel:
    """模拟 TERM-SSHD：banner → login: →（可延迟）password: → 目标机 shell。

    ``password_delay`` 用于复现真实时序：目标机密码提示要等堡垒机到目标机的下一跳
    ssh 握手完成，可能晚于通道静默窗口。
    """

    def __init__(
        self,
        expect_user="root",
        expect_password="pw",
        password_delay=0.0,
        reject_password=False,
        direct=False,
    ):
        self.sent = []
        self.reads = 0
        self.closed = False
        self._timeout = None
        # 真实环境抓到的格式：root@192.0.2.216's password:
        self._password_prompt = b"root@192.0.2.216's password: "
        self._password_delay = password_delay
        self._reject_password = reject_password
        self._expect_user = expect_user
        self._expect_password = expect_password
        self._pending = None  # (monotonic 到期时间, 待入队字节)
        if direct:
            # 直连部署：通道一打开就是普通 shell 提示符。
            self._queue = [b"root@bastion-test-host:~# "]
            self._stage = 2
        else:
            self._queue = [b"Authorized users only.\r\n", b"login: "]
            self._stage = 0  # 0=等用户名, 1=等密码, 2=shell

    def _tick(self):
        if self._pending is not None and time.monotonic() >= self._pending[0]:
            self._queue.append(self._pending[1])
            self._pending = None

    def sendall(self, data):
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        self.sent.append(text)
        if "BRIDGE_" in text and "END_" in text:
            match = re.search(r"---BRIDGE_(STREAM_)?END_([0-9a-f]+)---", text)
            if match:
                stream, uid = match.group(1), match.group(2)
                prefix = "BRIDGE_STREAM_" if stream else "BRIDGE_"
                end_marker = f"---{prefix}END_{uid}---"
                exit_marker = f"---{prefix}EXIT_{uid}---"
                self._queue.append(
                    b"\x01" + end_marker.encode() + b"\r\n"
                    + b"\x01" + exit_marker.encode() + b":0\r\n"
                )
            return
        if self._stage == 0 and text.strip() == self._expect_user:
            self._stage = 1
            self._pending = (
                time.monotonic() + self._password_delay,
                self._password_prompt,
            )
        elif self._stage == 1 and text.rstrip("\n") == self._expect_password:
            if self._reject_password:
                self._queue.append(
                    b"Permission denied, please try again.\r\n" + self._password_prompt
                )
                self._stage = 1
            else:
                self._queue.append(b"\r\nroot@bastion-test-host:~# ")
                self._stage = 2

    send = sendall

    def recv_ready(self):
        self._tick()
        return bool(self._queue)

    def recv(self, size):
        self._tick()
        self.reads += 1
        return self._queue.pop(0)

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value):
        self._timeout = value

    def close(self):
        self.closed = True


class _FakeTransport:
    def __init__(self, session):
        self._session = session

    def is_active(self):
        return True

    def open_session(self):
        return self._session


class _FakeClient:
    def __init__(self, transport):
        self._transport = transport

    def get_transport(self):
        return self._transport


def _connected_bridge(channel, **kwargs):
    bridge = ssh_client.SSHBridge(
        ConnectionParams(host="192.0.2.158", port=22, username="operator"),
        keepalive_enabled=False,
        relay_username=kwargs.get("relay_username", "root"),
        relay_password=kwargs.get("relay_password", "pw"),
        # 生产最小窗口是 3s；测试同样走这条下限，避免用例被静默夹紧而不自知。
        relay_prompt_timeout=kwargs.get("relay_prompt_timeout", 3.0),
    )
    transport = _FakeTransport(channel)
    bridge._transport = transport
    bridge._client = _FakeClient(transport)
    bridge._shell = channel
    return bridge


class RelayLoginTests(unittest.TestCase):
    def test_relay_answers_login_and_password_before_stty(self):
        channel = _RelayChannel()
        bridge = _connected_bridge(channel)
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), 0, result)
        sent = "".join(channel.sent)
        # 顺序约束：先应答目标机用户名/密码，之后才允许 stty 和指令。
        self.assertIn("root\n", sent)
        self.assertIn("pw\n", sent)
        self.assertLess(channel.sent.index("root\n"), channel.sent.index("stty -echo\n"))
        self.assertLess(channel.sent.index("pw\n"), channel.sent.index("stty -echo\n"))
        self.assertTrue(bridge._relay_completed_once)

    def test_relay_answers_only_once_per_channel(self):
        channel = _RelayChannel()
        bridge = _connected_bridge(channel)
        bridge.execute("df -h", timeout=15)
        bridge.execute("free -m", timeout=15)
        self.assertEqual(channel.sent.count("root\n"), 1)
        self.assertEqual(channel.sent.count("pw\n"), 1)
        self.assertEqual(channel.sent.count("stty -echo\n"), 2)

    def test_delayed_password_prompt_is_answered_before_stty(self):
        # 密码提示晚于 1.5s 静默窗口：旧实现会在这里把 stty 当成密码发出去。
        channel = _RelayChannel(password_delay=1.8)
        bridge = _connected_bridge(channel)
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), 0, result)
        self.assertLess(channel.sent.index("root\n"), channel.sent.index("pw\n"))
        self.assertLess(channel.sent.index("pw\n"), channel.sent.index("stty -echo\n"))

    def test_unconfigured_relay_keeps_direct_behavior(self):
        channel = _RelayChannel()
        bridge = _connected_bridge(channel, relay_username="", relay_password="")
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), 0, result)
        sent = "".join(channel.sent)
        # 关键约束：不发送任何应答字符，login: 提示符保持未被污染。
        self.assertNotIn("root\n", sent)
        self.assertNotIn("pw\n", sent)
        self.assertIn("stty -echo\n", sent)

    def test_password_only_config_leaves_login_to_manual(self):
        channel = _RelayChannel()
        bridge = _connected_bridge(channel, relay_username="", relay_password="pw")
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), -1)
        self.assertIn("中继登录未完成", result.get("error", ""))
        # 绝不能发空行去“应答” login:，也不能注入 stty/指令。
        self.assertEqual(channel.sent, [])
        self.assertEqual(bridge._relay_ready, set())

    def test_configured_credentials_with_direct_shell_stay_direct(self):
        channel = _RelayChannel(direct=True)
        bridge = _connected_bridge(channel)
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), 0, result)
        sent = "".join(channel.sent)
        self.assertNotIn("root\n", sent)
        self.assertNotIn("pw\n", sent)
        self.assertIn("stty -echo\n", sent)

    def test_rejected_password_aborts_before_stty(self):
        channel = _RelayChannel(reject_password=True)
        bridge = _connected_bridge(channel)
        result = bridge.execute("df -h", timeout=15)
        self.assertEqual(result.get("exit_code"), -1)
        self.assertIn("拒绝", result.get("error", ""))
        self.assertNotIn("stty -echo\n", channel.sent)
        self.assertIn("pw\n", channel.sent)

    def test_stream_relay_pending_does_not_send_interrupt(self):
        channel = _RelayChannel()
        bridge = _connected_bridge(channel, relay_username="", relay_password="pw")
        events = list(bridge.stream_execute("df -h"))
        # 中继待 /input 应答时，流式路径不能用 Ctrl-C 打断提示符，也不能发指令。
        self.assertEqual(channel.sent, [])
        self.assertTrue(
            any(e.get("type") == "exit" and e.get("exit_code") == -1 for e in events),
            events,
        )


class TmpdSessionFileTests(unittest.TestCase):
    def test_tmpd_prefix_parses_session_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "putty-1234-config.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("HostName\\192.0.2.216\\\r\n")
                handle.write("PortNumber\\22\\\r\n")
                handle.write("UserName\\root\\\r\n")
            params, diagnostics = parse_args(["-load", "tmpd:" + path, "-pw", "secret"])
            self.assertTrue(diagnostics["valid"])
            self.assertEqual(params.host, "192.0.2.216")
            self.assertEqual(params.port, 22)
            self.assertEqual(params.username, "root")
            self.assertEqual(params.password, "secret")


if __name__ == "__main__":
    unittest.main()
