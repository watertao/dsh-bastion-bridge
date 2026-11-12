"""skill/bastion-qizhi/scripts/ft.py：驱动 MiniFTClient 的安全规矩。不连桥接器。"""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skill" / "bastion-qizhi" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ft", SCRIPTS / "ft.py")
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)

PASSWORD = "S3cret-value"


class FakeBridge:
    """按发来的输入吐下一段输出，像真的 MiniFTClient。"""

    def __init__(self, greeting, replies=None, refuse=()):
        self.greeting = greeting
        self.replies = dict(replies or {})
        self.refuse = set(refuse)
        self.sent = []
        self.commands = []
        self.on_text = None
        self.on_end = None

    def stream(self, command, on_text, on_end):
        self.commands.append(command)
        self.on_text, self.on_end = on_text, on_end
        on_text(self.greeting)

    def send(self, data):
        if data in self.refuse:
            raise ft.Stop("refused", 1)
        self.sent.append(data)
        reply = self.replies.get(data)
        if reply == "END":
            self.on_end(0)
        elif reply is not None:
            self.on_text(reply)


LOGGED_IN = {
    PASSWORD + "\n": "****\r\n请选择访问的菜单:\r\n1:我的文档\r\n请输入菜单号:",
    "1\n": "\r\nType 'help'\r\n我的文档<",
    "exit\n": "END",
}


def start(bridge, password=PASSWORD):
    return ft.Session(bridge, "10.0.0.1", "someone", password, "/tmp/x", 60)


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.real_wait = ft.Session.wait
        ft.Session.wait = lambda s, pattern, seconds: self.real_wait(s, pattern, min(seconds, 0.2))
        self.addCleanup(setattr, ft.Session, "wait", self.real_wait)

    def test_password_only_after_the_prompt(self):
        bridge = FakeBridge("[root@host ~]# ")
        session = start(bridge)
        with self.assertRaises(ft.Stop) as caught:
            session.login()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(bridge.sent, [])

    def test_password_is_not_on_the_command_line(self):
        bridge = FakeBridge("输入密码:", LOGGED_IN)
        start(bridge).login()
        self.assertNotIn(PASSWORD, bridge.commands[0])
        self.assertNotIn(" -p ", bridge.commands[0].split("MNFTClient", 2)[-1])

    def test_starts_the_real_program_under_timeout(self):
        bridge = FakeBridge("输入密码:", LOGGED_IN)
        start(bridge).login()
        command = bridge.commands[0]
        self.assertIn('timeout --foreground -k 5 60 "$D/MNFTClient"', command)
        self.assertNotIn("timeout --foreground -k 5 60 MiniFTClient", command)

    def test_failed_login_sends_the_password_once(self):
        bridge = FakeBridge("输入密码:", {PASSWORD + "\n": "****\r\nUsername or password error!\r\nft-rc=0\r\n"})
        session = start(bridge)
        with self.assertRaises(ft.Stop) as caught:
            session.login()
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(bridge.sent, [PASSWORD + "\n"])
        self.assertNotIn("exit\n", bridge.sent)

    def test_chooses_the_menu_then_leaves_with_exit(self):
        bridge = FakeBridge("输入密码:", LOGGED_IN)
        session = start(bridge)
        session.login()
        session.close()
        self.assertEqual(bridge.sent, [PASSWORD + "\n", "1\n", "exit\n"])

    def test_nothing_after_an_unanswered_command(self):
        bridge = FakeBridge("输入密码:", LOGGED_IN)
        session = start(bridge)
        session.login()
        with self.assertRaises(ft.Stop):
            session.command("ls", 0.1)

    def test_error_output_does_not_contain_the_password(self):
        bridge = FakeBridge("输入密码:", {PASSWORD + "\n": f"{PASSWORD}\r\nbad\r\n"})
        session = start(bridge)
        with self.assertRaises(ft.Stop) as caught:
            session.login()
        self.assertNotIn(PASSWORD, str(caught.exception))


class PreflightTest(unittest.TestCase):
    def run_preflight(self, stdout):
        class Fake:
            def execute(self, _command, _timeout):
                return {"stdout": stdout}

        ft.preflight(Fake())

    def test_clean(self):
        self.run_preflight("[root@h ~]# FT-READY\n")

    def test_a_colleagues_session_is_fine(self):
        self.run_preflight("FT-READY\n 1234 5678\n")

    def test_orphan_stops_it(self):
        with self.assertRaises(ft.Stop) as caught:
            self.run_preflight("FT-READY\n 3517182       1\n")
        self.assertIn("3517182", str(caught.exception))

    def test_dead_shell_stops_it(self):
        with self.assertRaises(ft.Stop):
            self.run_preflight("")


class HelpersTest(unittest.TestCase):
    def test_clean_output(self):
        self.assertEqual(ft.clean_output("\x1b]0;t\x07ab\x08 \x08c\r\n"), "ac\n")
        self.assertEqual(ft.clean_output(f"x{PASSWORD}y", PASSWORD), "x***y")

    def test_quote(self):
        self.assertEqual(ft.shell_quote("a'b"), "'a'\\''b'")


if __name__ == "__main__":
    unittest.main()
