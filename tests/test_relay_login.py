"""中继登录应答（login:/password:）与 tmpd: 会话文件解析测试。

齐志 TERM-SSHD 在 SSH 认证成功后还会索要【目标机】的用户名/密码；
SSHBridge 必须先应答提示符再注入 stty/指令，否则初始化字节会被
login: 当成用户名吃掉（表现为空用户名密码提示死循环）。
"""

import os
import re
import tempfile
import unittest

import ssh_client
from connection import ConnectionParams
from params import parse_args


class _RelayChannel:
    """模拟 TERM-SSHD：banner → login: → Password: → 目标机 shell。"""

    def __init__(self, expect_user="root", expect_password="pw"):
        self.sent = []
        self.reads = 0
        self.closed = False
        self._timeout = None
        self._queue = [b"Authorized users only.\r\n", b"login: "]
        self._stage = 0  # 0=等用户名, 1=等密码, 2=shell
        self._expect_user = expect_user
        self._expect_password = expect_password

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
            self._queue.append(b"\r\nPassword: ")
            self._stage = 1
        elif self._stage == 1 and text.rstrip("\n") == self._expect_password:
            self._queue.append(b"\r\nroot@bastion-test-host:~# ")
            self._stage = 2

    send = sendall

    def recv_ready(self):
        return bool(self._queue)

    def recv(self, size):
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
        relay_prompt_timeout=kwargs.get("relay_prompt_timeout", 0.6),
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
