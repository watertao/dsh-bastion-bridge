"""
Persistent JSON configuration for Qizhi PuTTY SSH Bridge.

The configuration file lives alongside the log directory under the user's
local application data so the packaged EXE can read and write settings without
needing administrator privileges or modifying its own installation directory.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from logger import get_config_path, get_logger

logger = get_logger("config")


#: Default configuration values. These are used when the config file is missing
#: or when specific keys are absent.
DEFAULT_CONFIG: Dict[str, Any] = {
    # Local API server defaults.
    "api_host": "127.0.0.1",
    "api_port": 8766,
    # SSH connection defaults.
    "ssh_connect_timeout": 15,
    "retry_connect": True,
    "retry_max_attempts": 3,
    "retry_delay_seconds": 2,
    "exit_on_connect_failure": False,
    # Logging.
    "log_level": "INFO",
    # Keep-alive: send a harmless command if idle to prevent bastion timeout.
    "keepalive_enabled": True,
    "keepalive_interval_seconds": 180,
    "keepalive_command": "ls",
    # 指令通道：齐治接入客户端经本地端口转发时 exec 往往只回登录横幅，
    # 默认走持久交互 shell；个别环境可改为 false 使用 exec 通道。
    "ssh_prefer_shell": True,
    # 堡垒机中继模式：SSH 认证成功后，TERM-SSHD 会继续提示 login:/password:
    # 要求输入【目标机】的用户名/密码（此时 shell 尚不可用）。
    # relay_username/relay_password 留空时，提示符留给 /input 人工应答，
    # 行为与直连部署完全一致（不主动发送任何字符）。
    "relay_username": "",
    "relay_password": "",
    "relay_prompt_timeout": 6,
    # 按目标主机 IP 分别配置中继（目标机）凭据；键为网关传入的目标主机
    # 地址字符串（如 "10.1.1.100"），"*" 可作为兜底条目。命中条目的
    # username/password/relay_prompt_timeout 覆盖全局值，未配置的字段
    # 继续回落到全局 relay_username/relay_password/relay_prompt_timeout。
    # 示例::
    #
    #   "relay_hosts": {
    #     "10.1.1.100": {"username": "root", "password": "aaa"},
    #     "10.1.1.101": {"username": "admin", "password": "bbb",
    #                    "relay_prompt_timeout": 10}
    #   }
    "relay_hosts": {},
    # Log rotation.
    "log_max_bytes": 10 * 1024 * 1024,
    "log_backup_count": 5,
    # 危险操作兜底（内部策略，见 command_guard.py；不写入对外文档）。
    "dangerous_command_guard_enabled": True,
    "dangerous_command_guard_message": "",
    "dangerous_command_guard_programs": [],
    "dangerous_command_guard_patterns": [],
    "dangerous_command_guard_allow_patterns": [],
    "dangerous_command_guard_disabled_rules": [],
    # Live console: show sanitized startup fields and command/result previews.
    # Preview bodies are excluded from bridge.log by logger.py.
    "console_log": True,
    "console_verbose": False,
    "console_output_limit": 12000,
    # Optional hand-off to the stock PuTTY client.  ``bridge`` keeps the current
    # behaviour; ``both`` also opens the original window; ``putty`` hands over
    # entirely; ``auto`` elects one bridge and hands every later launch over.
    # See launcher.py.
    "launch_mode": "bridge",
    "original_client_path": "putty-original.exe",
}


def _expand_env(value: Any) -> Any:
    """Recursively expand ``%VAR%`` style environment variables in strings.

    Only string values that actually contain ``%`` are touched, and the result
    stays a plain string: ``os.path.expandvars`` is the right tool here.
    ``pathlib.Path`` has no ``expandvars`` method, and converting arbitrary
    config values (commands, regexes, log messages) into ``Path`` objects
    would both crash on ``%`` and rewrite Windows path separators.
    """
    if isinstance(value, str):
        return os.path.expandvars(value) if "%" in value else value
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the JSON configuration, creating defaults if necessary.

    Args:
        config_path: Optional explicit path. Defaults to
            ``%LOCALAPPDATA%/sshbridge/QizhiSSHBridge/config.json``.

    Returns:
        A dictionary with defaults merged with user-supplied settings.
    """
    path = Path(config_path) if config_path else get_config_path()
    config = DEFAULT_CONFIG.copy()

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                user_config = json.load(handle)
            if isinstance(user_config, dict):
                config.update(user_config)
                logger.info("Loaded configuration from %s", path)
            else:
                logger.warning(
                    "Config file %s did not contain a JSON object; using defaults.", path
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read config %s: %s", path, exc)
    else:
        logger.info("No config file found at %s; using defaults.", path)
        # Materialise the defaults so users can edit the file (the README
        # documents it as auto-created).  Best effort: if the location is not
        # writable the bridge still runs with in-memory defaults.
        save_config(config, path)

    # Resolve Windows environment variables (e.g. %USERPROFILE%).
    config = _expand_env(config)

    # Normalize log level string to a valid logging level name.
    # Use logging._nameToLevel for Python 3.10 compatibility
    # (getLevelNamesMapping was added in Python 3.11).
    level_name = str(config.get("log_level", "DEBUG")).upper()
    if level_name not in logging._nameToLevel:  # type: ignore[attr-defined]
        logger.warning("Invalid log_level %r; falling back to DEBUG.", level_name)
        config["log_level"] = "DEBUG"

    return config


def save_config(config: Dict[str, Any], config_path: Optional[Path] = None) -> bool:
    """Persist the configuration dictionary to JSON.

    Args:
        config: The configuration dictionary to save.
        config_path: Optional explicit path. Defaults to the standard location.

    Returns:
        ``True`` on success, ``False`` otherwise.
    """
    path = Path(config_path) if config_path else get_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
        logger.info("Saved configuration to %s", path)
        return True
    except (TypeError, OSError) as exc:
        logger.error("Failed to save config %s: %s", path, exc)
        return False


def get_config_value(config: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Return a configuration value, falling back to ``default`` if missing."""
    return config.get(key, default)


def resolve_relay_credentials(
    config: Dict[str, Any], target_host: Any = None
) -> Dict[str, Any]:
    """Resolve relay (target-host) credentials for a specific target host.

    Lookup order:

    1. ``relay_hosts[<target_host>]`` — exact match on the host string the
       gateway passed on the command line (typically the target IP);
    2. ``relay_hosts["*"]`` — optional catch-all entry;
    3. Global ``relay_username`` / ``relay_password`` / ``relay_prompt_timeout``.

    Per-host entries may override any subset of the fields; missing fields
    fall back to the global values, so existing single-host deployments keep
    working unchanged.

    Returns:
        A dict with keys ``username`` (str, stripped), ``password`` (str)
        and ``prompt_timeout`` (float, >= 3).
    """
    username = str(config.get("relay_username", "") or "").strip()
    password = str(config.get("relay_password", "") or "")
    try:
        prompt_timeout = float(config.get("relay_prompt_timeout") or 6)
    except (TypeError, ValueError):
        prompt_timeout = 6.0

    entries = config.get("relay_hosts")
    if isinstance(entries, dict):
        key = str(target_host or "").strip()
        entry = entries.get(key)
        if not isinstance(entry, dict) and "*" in entries:
            entry = entries.get("*")
        if isinstance(entry, dict):
            if entry.get("username") is not None:
                username = str(entry["username"] or "").strip()
            if entry.get("password") is not None:
                password = str(entry["password"] or "")
            if entry.get("relay_prompt_timeout") is not None:
                try:
                    prompt_timeout = float(entry["relay_prompt_timeout"] or 6)
                except (TypeError, ValueError):
                    pass  # keep the global/derived timeout

    return {
        "username": username,
        "password": password,
        "prompt_timeout": max(3.0, prompt_timeout),
    }
