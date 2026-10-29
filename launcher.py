"""Optional hand-off to the original PuTTY client.

Qizhi launches this bridge by replacing ``putty.exe``.  That gives an AI a
programmable shell, but it also removes the interactive PuTTY window many
operators still need.  This module adds a *launch mode* switch so the same
``putty.exe`` can behave in three ways:

``bridge``
    Default.  Current behaviour: connect with Paramiko and serve the local API.
``both``
    Start the original PuTTY with the very same arguments, then keep running as
    the bridge.  The operator gets the familiar terminal window while the AI
    uses its own session (the bastion must allow a second connection).
``putty``
    Start the original PuTTY and exit.  The bridge never connects, so behaviour
    is identical to the stock client.

The mode can be changed without administrator rights:

* ``config.json`` → ``"launch_mode": "both"``
* or a one-word override file ``mode.txt`` in the application data directory
  (``bridge`` / ``both`` / ``putty``), which wins over the JSON value.

The original client is located through ``original_client_path``; relative
values are resolved against the application data directory first and then
against the folder containing this executable.  Nothing here is allowed to
crash the bridge: a missing or invalid client falls back to ``bridge`` mode.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from logger import get_app_dir, get_logger, log_console

logger = get_logger("launcher")

MODE_BRIDGE = "bridge"
MODE_BOTH = "both"
MODE_PUTTY = "putty"
MODE_AUTO = "auto"

#: Canonical launch modes, in the order they are documented.
LAUNCH_MODES = (MODE_BRIDGE, MODE_BOTH, MODE_PUTTY, MODE_AUTO)

#: Aliases accepted in ``config.json`` / ``mode.txt`` for convenience.
_MODE_ALIASES = {
    "ai": MODE_BRIDGE,
    "bridge": MODE_BRIDGE,
    "both": MODE_BOTH,
    "dual": MODE_BOTH,
    "putty": MODE_PUTTY,
    "manual": MODE_PUTTY,
    "old": MODE_PUTTY,
    "auto": MODE_AUTO,
    "smart": MODE_AUTO,
    "lock": MODE_AUTO,
}

#: Name of the plain-text mode override inside the application data directory.
MODE_FILE_NAME = "mode.txt"

#: Lock file used by ``auto`` mode to elect exactly one bridge instance.
LOCK_FILE_NAME = "bridge.lock"

#: Default location of the stock client, relative to the bridge or app dir.
DEFAULT_ORIGINAL_CLIENT = "putty-original.exe"

#: File names searched, in order, when ``original_client_path`` is left at its
#: default.  ``putty.exe.original`` is accepted because that is a very common
#: way operators rename the stock binary next to the bridge.
DEFAULT_ORIGINAL_CLIENTS = (
    "putty-original.exe",
    "putty.exe.original",
    "putty.original.exe",
    "putty-original",
)


@dataclass
class LaunchResult:
    """Outcome of the launch-mode check performed at startup."""

    mode: str = MODE_BRIDGE
    client_path: Optional[Path] = None
    process: Optional[subprocess.Popen] = None
    exit_code: Optional[int] = None
    #: Held for the whole process lifetime in ``auto`` mode (released on close).
    instance_lock: Optional["InstanceLock"] = None

    @property
    def should_exit(self) -> bool:
        """True when the bridge must exit instead of serving the API."""
        return self.mode == MODE_PUTTY and self.exit_code is not None


class InstanceLock:
    """Exclusive per-user lock that elects a single bridge instance.

    ``auto`` mode needs to know whether another bridge is already alive.  An
    OS-level file lock is ideal here: the kernel drops it when the process
    exits (even on a crash), so there is never a stale lock file to clean up.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: Optional[Any] = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Try to take the lock without blocking.  Returns success."""
        if self._handle is not None:
            return True

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            logger.error("Could not open instance lock %s: %s", self.path, exc)
            return False

        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError as exc:  # pragma: no cover - diagnostics only
            logger.warning("Could not write pid to %s: %s", self.path, exc)

        self._handle = handle
        return True

    def release(self) -> None:
        """Drop the lock (also happens automatically when the process exits)."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def acquire_instance_lock(app_dir: Optional[Path] = None) -> Optional[InstanceLock]:
    """Return the singleton bridge lock, or ``None`` when another one holds it."""
    path = (Path(app_dir) if app_dir else get_app_dir()) / LOCK_FILE_NAME
    lock = InstanceLock(path)
    if lock.acquire():
        logger.info("Acquired instance lock %s", path)
        return lock
    logger.info("Instance lock %s is held by another bridge", path)
    return None


def normalize_mode(value: Any) -> str:
    """Return a canonical launch mode, defaulting to ``bridge``."""
    return _MODE_ALIASES.get(str(value or "").strip().lower(), MODE_BRIDGE)


def read_mode_override(app_dir: Optional[Path] = None) -> Optional[str]:
    """Read ``mode.txt`` and return its canonical mode, or ``None``.

    Only the first whitespace-separated token is considered, so a trailing
    comment such as ``putty  # manual`` is tolerated.
    """
    path = Path(app_dir) if app_dir else get_app_dir()
    mode_file = path / MODE_FILE_NAME
    try:
        if not mode_file.is_file():
            return None
        raw = mode_file.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        logger.warning("Could not read %s: %s", mode_file, exc)
        return None

    if not raw:
        return None

    token = raw.split()[0].strip().lower()
    if token not in _MODE_ALIASES:
        logger.warning(
            "Ignoring unknown launch mode %r in %s; expected one of %s",
            token,
            mode_file,
            ", ".join(LAUNCH_MODES),
        )
        return None
    return _MODE_ALIASES[token]


def resolve_launch_mode(config: Dict[str, Any], app_dir: Optional[Path] = None) -> str:
    """Return the effective launch mode (``mode.txt`` wins over config)."""
    override = read_mode_override(app_dir)
    if override is not None:
        return override
    return normalize_mode(config.get("launch_mode", MODE_BRIDGE))


def resolve_original_client(
    config: Dict[str, Any],
    app_dir: Optional[Path] = None,
    current_exe: Optional[Path] = None,
) -> Optional[Path]:
    """Locate the stock PuTTY executable, or ``None`` when it is absent.

    Relative paths are tried against the application data directory first
    (which every user can write, unlike ``Program Files``) and then against the
    directory holding the running executable.

    When ``original_client_path`` is not customised, several conventional
    names are tried (see :data:`DEFAULT_ORIGINAL_CLIENTS`), so a stock binary
    renamed to ``putty.exe.original`` next to the bridge is found as-is.
    """
    raw = str(config.get("original_client_path") or "").strip()

    if raw and raw != DEFAULT_ORIGINAL_CLIENT:
        names: List[Path] = [Path(os.path.expandvars(raw)).expanduser()]
    else:
        names = [Path(name) for name in DEFAULT_ORIGINAL_CLIENTS]

    bases: List[Path] = [Path(app_dir) if app_dir else get_app_dir()]
    bases.append(Path(current_exe).parent if current_exe else Path(sys.executable).parent)

    candidates: List[Path] = []
    for name in names:
        if name.is_absolute():
            candidates.append(name)
            continue
        candidates.extend(base / name for base in bases)

    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def describe_original_search(config: Dict[str, Any]) -> str:
    """Human-readable description of the client names that will be searched."""
    raw = str(config.get("original_client_path") or "").strip()
    if raw and raw != DEFAULT_ORIGINAL_CLIENT:
        return raw
    return " / ".join(DEFAULT_ORIGINAL_CLIENTS)


def is_same_executable(path: Path, current_exe: Optional[Path] = None) -> bool:
    """Return True when ``path`` is the running bridge itself (recursion guard)."""
    own = Path(current_exe) if current_exe else Path(sys.executable)
    try:
        return path.samefile(own)
    except OSError:
        return os.path.normcase(str(path)) == os.path.normcase(str(own))


def launch_original_client(client_path: Path, argv: List[str]) -> subprocess.Popen:
    """Start the stock client with the exact arguments Qizhi supplied.

    The argument vector is forwarded verbatim (including the temporary PuTTY
    session and any credential flags) so the original client sees a byte-for-byte
    identical invocation.  It is never written to the log.
    """
    command = [str(client_path), *[str(item) for item in argv]]
    logger.info(
        "Launching original client %s with %d forwarded argument(s)",
        client_path,
        len(argv),
    )
    return subprocess.Popen(command, close_fds=True)


def _hide_own_console() -> None:
    """Hide the bridge console window before handing over to the stock PuTTY.

    Only packaged Windows builds get a dedicated console; a source checkout run
    from a terminal must never hide the developer's window.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception as exc:  # noqa: BLE001 - cosmetic only
        logger.debug("Could not hide console window: %s", exc)


def prepare(
    config: Dict[str, Any],
    argv: List[str],
    app_dir: Optional[Path] = None,
    current_exe: Optional[Path] = None,
    mode: Optional[str] = None,
) -> LaunchResult:
    """Apply the launch mode and return what the caller should do next.

    ``auto`` mode elects exactly one bridge per user: the first click that
    acquires the instance lock becomes the bridge, every later click is handed
    over to the stock client.  This gives the operator a second, fully
    independent PuTTY session without needing two connections on one tunnel.

    For ``putty`` mode this blocks until the stock client exits so the process
    tree (and the bastion tunnel owned by Qizhi) stays alive for as long as the
    terminal window is open.
    """
    mode = mode or resolve_launch_mode(config, app_dir)
    lock: Optional[InstanceLock] = None
    if mode == MODE_AUTO:
        lock = acquire_instance_lock(app_dir)
        if lock is not None:
            mode = MODE_BRIDGE
            log_console(">>> auto 模式：本次点击作为桥接器（再次点击将打开原版 PuTTY）")
        else:
            mode = MODE_PUTTY
            log_console(">>> auto 模式：已有桥接器在运行，本次转交原版 PuTTY")

    result = LaunchResult(mode=mode, instance_lock=lock)
    if mode == MODE_BRIDGE:
        return result

    client_path = resolve_original_client(config, app_dir, current_exe)
    if client_path is None:
        searched = describe_original_search(config)
        logger.error(
            "launch_mode=%s but original client was not found (searched %s); staying in bridge mode",
            mode,
            searched,
        )
        log_console(
            "! launch_mode=%s，但未找到原版 PuTTY（查找：%s），本次仍以桥接模式运行",
            mode,
            searched,
            level=30,
        )
        result.mode = MODE_BRIDGE
        return result

    if is_same_executable(client_path, current_exe):
        logger.error(
            "Refusing to launch the bridge as the original client: %s", client_path
        )
        log_console("! original_client_path 指向桥接器自身，已忽略并继续桥接模式", level=30)
        result.mode = MODE_BRIDGE
        return result

    result.client_path = client_path
    try:
        result.process = launch_original_client(client_path, argv)
    except OSError as exc:
        logger.exception("Failed to launch original client %s: %s", client_path, exc)
        log_console("! 原版 PuTTY 启动失败：%s；本次仍以桥接模式运行", exc, level=30)
        result.mode = MODE_BRIDGE
        return result

    logger.info(
        "Original client started (pid=%s, mode=%s)", result.process.pid, mode
    )

    if mode == MODE_PUTTY:
        log_console(">>> 已转交原版 PuTTY（pid=%s），桥接器退出", result.process.pid)
        _hide_own_console()
        try:
            result.exit_code = result.process.wait()
        except KeyboardInterrupt:
            result.exit_code = 0
        logger.info("Original client exited with code %s", result.exit_code)
        return result

    log_console(">>> 已同时拉起原版 PuTTY（pid=%s），桥接器继续为 AI 服务", result.process.pid)
    log_console("    提示：若原版 PuTTY 登录失败，说明堡垒机只允许该会话连接一次", level=30)
    return result
