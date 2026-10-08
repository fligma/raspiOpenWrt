#!/usr/bin/env python3
"""rasconf Manager support module (auto-generated split)."""

from __future__ import annotations

import json
from pathlib import Path

from manager_const import (
    APP_CONFIG_EXAMPLE_FILE,
    APP_CONFIG_FILE,
    BASE_DIR,
    DEFAULT_APP_CONFIG,
    DEFAULT_PROFILE,
    DEFAULT_SOURCE,
    HISTORY_FILE,
    MAX_COMMAND_HISTORY,
)

def _coerce_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _migrate_legacy_config(config: dict) -> dict:
    """Ensure a profiles list exists; if the file only has flat keys, wrap them."""
    profiles = config.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        legacy = {
            "name": "Default",
            "host": str(config.get("host", DEFAULT_PROFILE["host"])),
            "port": _coerce_int(config.get("port"), DEFAULT_PROFILE["port"], 1, 65535),
            "username": str(config.get("username", DEFAULT_PROFILE["username"])),
            "key_file": str(config.get("key_file", "")),
            "remote_root": str(config.get("remote_root", DEFAULT_PROFILE["remote_root"])),
            "source": str(config.get("source", DEFAULT_PROFILE["source"])),
            "trust_unknown_host": bool(config.get("trust_unknown_host", False)),
            "remember_password": bool(config.get("remember_password", False)),
        }
        profiles = [legacy]
        config["profiles"] = profiles
        config.setdefault("active_profile", "Default")
    # sanitize each profile
    cleaned = []
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        prof = {
            "name": str(raw.get("name") or f"Profile {len(cleaned) + 1}"),
            "host": str(raw.get("host", DEFAULT_PROFILE["host"])),
            "port": _coerce_int(raw.get("port"), 22, 1, 65535),
            "username": str(raw.get("username", "root")),
            "key_file": str(raw.get("key_file", "")),
            "remote_root": str(raw.get("remote_root", DEFAULT_PROFILE["remote_root"])),
            "source": str(raw.get("source", str(DEFAULT_SOURCE))),
            "trust_unknown_host": bool(raw.get("trust_unknown_host", False)),
            "remember_password": bool(raw.get("remember_password", False)),
        }
        cleaned.append(prof)
    if not cleaned:
        cleaned = [DEFAULT_PROFILE.copy()]
    config["profiles"] = cleaned
    names = {p["name"] for p in cleaned}
    if config.get("active_profile") not in names:
        config["active_profile"] = cleaned[0]["name"]
    return config


def _sync_flat_mirror(config: dict) -> dict:
    """Copy the active profile values back to the legacy flat keys so
    downstream code that reads config['host'] etc still works."""
    active = next(
        (p for p in config["profiles"] if p["name"] == config["active_profile"]),
        config["profiles"][0],
    )
    for key, value in active.items():
        if key == "name":
            continue
        config[key] = value
    return config


def load_app_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_APP_CONFIG))  # deep copy
    for config_path in (APP_CONFIG_FILE, APP_CONFIG_EXAMPLE_FILE):
        if not config_path.is_file():
            continue
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            config.update(loaded)
            break

    config = _migrate_legacy_config(config)
    config = _sync_flat_mirror(config)

    if not config.get("source") or not Path(config["source"]).is_dir():
        if DEFAULT_SOURCE.is_dir():
            config["source"] = str(DEFAULT_SOURCE)
        else:
            config["source"] = str(Path.home())

    config["ssh_timeout"] = _coerce_int(config.get("ssh_timeout"), 12, 3, 300)
    config["terminal_font_size"] = _coerce_int(config.get("terminal_font_size"), 10, 6, 24)
    config["max_log_lines"] = _coerce_int(config.get("max_log_lines"), 500, 50, 10000)
    config["terminal_history_limit"] = _coerce_int(config.get("terminal_history_limit"), 500, 20, 5000)
    config["web_port"] = _coerce_int(config.get("web_port"), 8989, 1, 65535)
    config["keep_last_n_backups"] = _coerce_int(config.get("keep_last_n_backups"), 5, 0, 100)
    config["default_tab"] = str(config.get("default_tab", "SFTP"))
    config["backup_directory"] = str(config.get("backup_directory", "/tmp/rasconf_backups"))
    config["local_backup_directory"] = str(config.get("local_backup_directory", str(BASE_DIR / "backups")))
    config["luci_url"] = str(config.get("luci_url", "http://192.168.1.1/"))
    config["web_path"] = str(config.get("web_path", "/cgi-bin/index.py"))

    for key in ("deploy_ignore", "post_deploy_commands"):
        value = config.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            config[key] = []

    quick = config.get("quick_commands")
    if not isinstance(quick, list):
        quick = []
    sanitized_quick = []
    for entry in quick:
        if isinstance(entry, dict) and entry.get("command"):
            sanitized_quick.append({"label": str(entry.get("label", entry["command"])), "command": str(entry["command"])})
    config["quick_commands"] = sanitized_quick

    for key in ("confirm_destructive", "show_hidden_files", "auto_connect"):
        config[key] = bool(config.get(key, DEFAULT_APP_CONFIG[key]))

    legacy_backup = config.get("backup_before_deploy")
    config["remote_backup_enabled"] = bool(
        config.get("remote_backup_enabled", legacy_backup if legacy_backup is not None else False)
    )
    config["local_backup_enabled"] = bool(config.get("local_backup_enabled", False))
    config["mirror_delete_remote"] = bool(config.get("mirror_delete_remote", False))
    config["mirror_delete_local"] = bool(config.get("mirror_delete_local", False))
    config["logging_enabled"] = bool(config.get("logging_enabled", True))
    config.pop("backup_before_deploy", None)

    if not APP_CONFIG_FILE.is_file():
        save_app_config_static(config)

    return config


def save_app_config_static(config: dict) -> None:
    try:
        APP_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_command_history() -> list[str]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [str(item) for item in data if isinstance(item, str)]
    return []


def save_command_history(history: list[str]) -> None:
    try:
        HISTORY_FILE.write_text(json.dumps(history[-MAX_COMMAND_HISTORY:], indent=2), encoding="utf-8")
    except OSError:
        pass
