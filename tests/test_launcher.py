"""Launch-mode tests: hand-off to the stock PuTTY client."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import launcher


class NormalizeModeTests(unittest.TestCase):
    def test_canonical_modes(self):
        for mode in launcher.LAUNCH_MODES:
            self.assertEqual(mode, launcher.normalize_mode(mode))
            self.assertEqual(mode, launcher.normalize_mode("  " + mode.upper() + " "))

    def test_aliases(self):
        self.assertEqual(launcher.MODE_BRIDGE, launcher.normalize_mode("ai"))
        self.assertEqual(launcher.MODE_BOTH, launcher.normalize_mode("dual"))
        self.assertEqual(launcher.MODE_PUTTY, launcher.normalize_mode("manual"))
        self.assertEqual(launcher.MODE_AUTO, launcher.normalize_mode("auto"))
        self.assertEqual(launcher.MODE_AUTO, launcher.normalize_mode("smart"))

    def test_unknown_and_empty_default_to_bridge(self):
        self.assertEqual(launcher.MODE_BRIDGE, launcher.normalize_mode("nonsense"))
        self.assertEqual(launcher.MODE_BRIDGE, launcher.normalize_mode(""))
        self.assertEqual(launcher.MODE_BRIDGE, launcher.normalize_mode(None))


class ModeResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_config_is_used_without_override(self):
        self.assertEqual(
            launcher.MODE_BOTH,
            launcher.resolve_launch_mode({"launch_mode": "both"}, self.app_dir),
        )

    def test_mode_file_overrides_config(self):
        (self.app_dir / launcher.MODE_FILE_NAME).write_text("putty\n", encoding="utf-8")
        self.assertEqual(
            launcher.MODE_PUTTY,
            launcher.resolve_launch_mode({"launch_mode": "both"}, self.app_dir),
        )

    def test_mode_file_tolerates_comment_and_bom(self):
        (self.app_dir / launcher.MODE_FILE_NAME).write_text(
            "\ufeffboth  # 两条会话\n", encoding="utf-8"
        )
        self.assertEqual(
            launcher.MODE_BOTH,
            launcher.resolve_launch_mode({"launch_mode": "bridge"}, self.app_dir),
        )

    def test_unknown_mode_file_falls_back_to_config(self):
        (self.app_dir / launcher.MODE_FILE_NAME).write_text("wat\n", encoding="utf-8")
        self.assertEqual(
            launcher.MODE_PUTTY,
            launcher.resolve_launch_mode({"launch_mode": "putty"}, self.app_dir),
        )


class OriginalClientResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_relative_path_resolved_against_app_dir(self):
        target = self.app_dir / "putty-original.exe"
        target.write_bytes(b"MZ")
        found = launcher.resolve_original_client({}, self.app_dir)
        self.assertEqual(target.resolve(), found)

    def test_absolute_path_is_used_directly(self):
        target = self.app_dir / "stock-putty.exe"
        target.write_bytes(b"MZ")
        found = launcher.resolve_original_client(
            {"original_client_path": str(target)}, self.app_dir
        )
        self.assertEqual(target.resolve(), found)

    def test_missing_client_returns_none(self):
        self.assertIsNone(launcher.resolve_original_client({}, self.app_dir))

    def test_default_search_accepts_putty_exe_original_next_to_bridge(self):
        exe_dir = self.app_dir / "client"
        exe_dir.mkdir()
        target = exe_dir / "putty.exe.original"
        target.write_bytes(b"MZ")

        found = launcher.resolve_original_client(
            {}, self.app_dir, exe_dir / "putty.exe"
        )

        self.assertEqual(target.resolve(), found)

    def test_app_dir_wins_over_exe_dir(self):
        exe_dir = self.app_dir / "client"
        exe_dir.mkdir()
        (exe_dir / "putty.exe.original").write_bytes(b"MZ")
        local = self.app_dir / "putty-original.exe"
        local.write_bytes(b"MZ")

        found = launcher.resolve_original_client(
            {}, self.app_dir, exe_dir / "putty.exe"
        )

        self.assertEqual(local.resolve(), found)

    def test_explicit_path_is_not_broadened(self):
        exe_dir = self.app_dir / "client"
        exe_dir.mkdir()
        (exe_dir / "putty.exe.original").write_bytes(b"MZ")

        found = launcher.resolve_original_client(
            {"original_client_path": "nope.exe"}, self.app_dir, exe_dir / "putty.exe"
        )

        self.assertIsNone(found)

    def test_same_executable_detection(self):
        exe = self.app_dir / "putty.exe"
        exe.write_bytes(b"MZ")
        self.assertTrue(launcher.is_same_executable(exe, exe))
        other = self.app_dir / "putty-original.exe"
        other.write_bytes(b"MZ")
        self.assertFalse(launcher.is_same_executable(other, exe))


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self._tmp.name)
        self.bridge_exe = self.app_dir / "putty.exe"
        self.bridge_exe.write_bytes(b"MZ")
        self.original = self.app_dir / "putty-original.exe"
        self.original.write_bytes(b"MZ")
        self.argv = ["-load", "tmp:C:\\Temp\\qizhi-session.txt", "-pw", "secret"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_bridge_mode_does_not_spawn(self):
        with patch("launcher.subprocess.Popen") as popen:
            result = launcher.prepare({}, self.argv, self.app_dir, self.bridge_exe)
        popen.assert_not_called()
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertFalse(result.should_exit)

    def test_both_mode_spawns_original_with_same_argv_and_continues(self):
        process = MagicMock()
        process.pid = 4321
        with patch("launcher.subprocess.Popen", return_value=process) as popen:
            result = launcher.prepare(
                {"launch_mode": "both"}, self.argv, self.app_dir, self.bridge_exe
            )
        command = popen.call_args.args[0]
        self.assertEqual(str(self.original.resolve()), command[0])
        self.assertEqual(self.argv, command[1:])
        self.assertEqual(launcher.MODE_BOTH, result.mode)
        self.assertIs(process, result.process)
        self.assertFalse(result.should_exit)
        process.wait.assert_not_called()

    def test_putty_mode_waits_and_requests_exit(self):
        process = MagicMock()
        process.pid = 99
        process.wait.return_value = 7
        with patch("launcher.subprocess.Popen", return_value=process):
            result = launcher.prepare(
                {"launch_mode": "putty"}, self.argv, self.app_dir, self.bridge_exe
            )
        self.assertEqual(launcher.MODE_PUTTY, result.mode)
        self.assertTrue(result.should_exit)
        self.assertEqual(7, result.exit_code)
        process.wait.assert_called_once_with()

    def test_missing_original_client_falls_back_to_bridge(self):
        self.original.unlink()
        with patch("launcher.subprocess.Popen") as popen:
            result = launcher.prepare(
                {"launch_mode": "both"}, self.argv, self.app_dir, self.bridge_exe
            )
        popen.assert_not_called()
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertFalse(result.should_exit)

    def test_self_reference_is_refused(self):
        with patch("launcher.subprocess.Popen") as popen:
            result = launcher.prepare(
                {"launch_mode": "putty", "original_client_path": str(self.bridge_exe)},
                self.argv,
                self.app_dir,
                self.bridge_exe,
            )
        popen.assert_not_called()
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertFalse(result.should_exit)

    def test_spawn_failure_falls_back_to_bridge(self):
        with patch("launcher.subprocess.Popen", side_effect=OSError("boom")):
            result = launcher.prepare(
                {"launch_mode": "both"}, self.argv, self.app_dir, self.bridge_exe
            )
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertIsNone(result.process)

    def test_putty_mode_returns_child_exit_code(self):
        process = MagicMock()
        process.pid = 1
        process.wait.return_value = 0
        with patch("launcher.subprocess.Popen", return_value=process):
            result = launcher.prepare(
                {"launch_mode": "putty"}, self.argv, self.app_dir, self.bridge_exe
            )
        self.assertTrue(result.should_exit)
        self.assertEqual(0, result.exit_code)


class InstanceLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_lock_is_refused_until_released(self):
        first = launcher.acquire_instance_lock(self.app_dir)
        self.assertIsNotNone(first)
        self.assertTrue(first.held)

        self.assertIsNone(launcher.acquire_instance_lock(self.app_dir))

        first.release()
        self.assertFalse(first.held)

        second = launcher.acquire_instance_lock(self.app_dir)
        self.assertIsNotNone(second)
        second.release()


class AutoModeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self._tmp.name)
        self.bridge_exe = self.app_dir / "putty.exe"
        self.bridge_exe.write_bytes(b"MZ")
        self.original = self.app_dir / "putty-original.exe"
        self.original.write_bytes(b"MZ")
        self.argv = ["-load", "tmp:session.txt"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_click_becomes_bridge_and_holds_lock(self):
        result = launcher.prepare(
            {"launch_mode": "auto"}, self.argv, self.app_dir, self.bridge_exe
        )
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertIsNotNone(result.instance_lock)
        self.assertFalse(result.should_exit)
        result.instance_lock.release()

    def test_later_click_hands_over_to_original_client(self):
        first = launcher.prepare(
            {"launch_mode": "auto"}, self.argv, self.app_dir, self.bridge_exe
        )
        self.assertEqual(launcher.MODE_BRIDGE, first.mode)

        process = MagicMock()
        process.pid = 77
        process.wait.return_value = 0
        with patch("launcher.subprocess.Popen", return_value=process) as popen:
            second = launcher.prepare(
                {"launch_mode": "auto"}, self.argv, self.app_dir, self.bridge_exe
            )

        self.assertEqual(launcher.MODE_PUTTY, second.mode)
        self.assertTrue(second.should_exit)
        self.assertIsNone(second.instance_lock)
        self.assertEqual(str(self.original.resolve()), popen.call_args.args[0][0])
        first.instance_lock.release()

    def test_auto_without_original_client_stays_bridge(self):
        self.original.unlink()
        result = launcher.prepare(
            {"launch_mode": "auto"}, self.argv, self.app_dir, self.bridge_exe
        )
        self.assertEqual(launcher.MODE_BRIDGE, result.mode)
        self.assertIsNotNone(result.instance_lock)
        result.instance_lock.release()


if __name__ == "__main__":
    unittest.main()
