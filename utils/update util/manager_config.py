#!/usr/bin/env python3
"""Reading, migrating and writing config.json plus the saved command history."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

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


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _coerce_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Turn anything read from JSON into an int inside [minimum, maximum].
    """
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


# ---------------------------------------------------------------------------
# Config shape: migration and the flat/profile split
# ---------------------------------------------------------------------------

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
    # Rebuild every profile from scratch so stray or renamed keys cannot leak
    # into the saved file, and drop entries that are not dicts at all.
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
    # Point at a profile that actually exists, otherwise the UI has no selection.
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


# ---------------------------------------------------------------------------
# Web tabs: url building, migration from the legacy keys, and normalisation
# ---------------------------------------------------------------------------

def web_tab_url(tab: dict, default_host: str) -> str:
    """Build the URL for one configurable tab.

    A blank ip means "follow the active profile host", so a tab can track the
    device the user is connected to. The port is omitted when it already matches
    the scheme default to keep the address tidy.
    """
    scheme = str(tab.get("scheme") or "http").lower()
    host = str(tab.get("ip") or "").strip() or (default_host or "127.0.0.1")
    port = _coerce_int(tab.get("port"), 80, 1, 65535)
    path = str(tab.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}{path}"
    return f"{scheme}://{host}:{port}{path}"


def _normalize_web_tab(raw: dict) -> dict | None:
    """Validate a single tab entry, dropping it if it has no usable name."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    scheme = str(raw.get("scheme") or "http").strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    return {
        "name": name,
        "ip": str(raw.get("ip") or "").strip(),
        "port": _coerce_int(raw.get("port"), 80, 1, 65535),
        "path": str(raw.get("path") or "/").strip() or "/",
        "scheme": scheme,
    }


def _normalize_web_tabs(config: dict) -> dict:
    tabs = config.get("web_tabs")
    if not isinstance(tabs, list):
        tabs = []
    cleaned = []
    for raw in tabs:
        tab = _normalize_web_tab(raw)
        if tab is not None:
            cleaned.append(tab)
    if not cleaned:
        cleaned = json.loads(json.dumps(DEFAULT_APP_CONFIG["web_tabs"]))
    config["web_tabs"] = cleaned
    return config


def _migrate_web_tabs(config: dict) -> dict:
    """Seed web_tabs from the legacy web_port/web_path/luci_url keys.

    Only used when a config file predates the tab list, so an existing setup
    keeps its exact addresses: the Web Interface follows the profile host, while
    LuCI keeps whatever host its stored URL pointed at.
    """
    luci = urlparse(str(config.get("luci_url", "http://192.168.1.1/")))
    luci_host = luci.hostname or ""
    luci_port = luci.port or (443 if luci.scheme == "https" else 80)
    luci_path = luci.path or "/"
    config["web_tabs"] = [
        {
            "name": "Web Interface",
            "ip": "",
            "port": _coerce_int(config.get("web_port"), 8989, 1, 65535),
            "path": str(config.get("web_path", "/cgi-bin/index.py")),
            "scheme": "http",
        },
        {
            "name": "LuCI",
            "ip": luci_host,
            "port": luci_port,
            "path": luci_path,
            "scheme": luci.scheme or "http",
        },
    ]
    return config


def load_app_config() -> dict:
    """Load config.json (or the shipped example), then normalise every key.

    The result always has the full set of keys with sane, type-checked values,
    so the rest of the app can read the dict without defensive get() calls.
    """
    config = json.loads(json.dumps(DEFAULT_APP_CONFIG))  # deep copy
    # A real config wins over the example; if neither parses we keep defaults.
    had_web_tabs = False
    for config_path in (APP_CONFIG_FILE, APP_CONFIG_EXAMPLE_FILE):
        if not config_path.is_file():
            continue
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            had_web_tabs = isinstance(loaded.get("web_tabs"), list)
            config.update(loaded)
            break

    config = _migrate_legacy_config(config)
    config = _sync_flat_mirror(config)
    # A config that predates the tab list keeps its old addresses by rebuilding
    # web_tabs from the legacy keys; newer files are just type-checked.
    if not had_web_tabs:
        config = _migrate_web_tabs(config)
    config = _normalize_web_tabs(config)

    # A source path from another machine is useless - fall back to the repo,
    # then to the home directory, so the local tree is never empty by accident.
    if not config.get("source") or not Path(config["source"]).is_dir():
        if DEFAULT_SOURCE.is_dir():
            config["source"] = str(DEFAULT_SOURCE)
        else:
            config["source"] = str(Path.home())

    # Clamp the numeric settings and coerce the free-text ones to str.
    config["ssh_timeout"] = _coerce_int(config.get("ssh_timeout"), 12, 3, 300)
    config["terminal_font_size"] = _coerce_int(config.get("terminal_font_size"), 10, 6, 24)
    config["max_log_lines"] = _coerce_int(config.get("max_log_lines"), 500, 50, 10000)
    config["terminal_history_limit"] = _coerce_int(config.get("terminal_history_limit"), 500, 20, 5000)
    config["web_port"] = _coerce_int(config.get("web_port"), 8989, 1, 65535)
    config["keep_last_n_backups"] = _coerce_int(config.get("keep_last_n_backups"), 5, 0, 100)
    config["reboot_wait_seconds"] = _coerce_int(config.get("reboot_wait_seconds"), 30, 0, 600)
    config["reboot_retry_interval"] = _coerce_int(config.get("reboot_retry_interval"), 10, 1, 300)
    config["default_tab"] = str(config.get("default_tab", "SFTP"))
    config["backup_directory"] = str(config.get("backup_directory", "/tmp/rasconf_backups"))
    config["local_backup_directory"] = str(config.get("local_backup_directory", str(BASE_DIR / "backups")))
    config["luci_url"] = str(config.get("luci_url", "http://192.168.1.1/"))
    config["web_path"] = str(config.get("web_path", "/cgi-bin/index.py"))

    # List-valued settings must stay lists of strings.
    for key in ("deploy_ignore", "post_deploy_commands"):
        value = config.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            config[key] = []

    # Quick commands are {label, command} pairs; anything without a command is dropped.
    quick = config.get("quick_commands")
    if not isinstance(quick, list):
        quick = []
    sanitized_quick = []
    for entry in quick:
        if isinstance(entry, dict) and entry.get("command"):
            sanitized_quick.append({"label": str(entry.get("label", entry["command"])), "command": str(entry["command"])})
    config["quick_commands"] = sanitized_quick

    for key in (
        "confirm_destructive",
        "show_hidden_files",
        "auto_connect",
        "use_alt_icon",
        "sftp_enabled",
        "ssh_enabled",
        "reboot_auto_watch",
    ):
        config[key] = bool(config.get(key, DEFAULT_APP_CONFIG[key]))

    # The old single "backup before deploy" switch became two independent ones;
    # it seeds the remote backup flag and is then removed.
    legacy_backup = config.get("backup_before_deploy")
    config["remote_backup_enabled"] = bool(
        config.get("remote_backup_enabled", legacy_backup if legacy_backup is not None else False)
    )
    config["local_backup_enabled"] = bool(config.get("local_backup_enabled", False))
    config["mirror_delete_remote"] = bool(config.get("mirror_delete_remote", False))
    config["mirror_delete_local"] = bool(config.get("mirror_delete_local", False))
    config["logging_enabled"] = bool(config.get("logging_enabled", True))
    config.pop("backup_before_deploy", None)

    # First run: write what we worked out so the user has a file to edit.
    if not APP_CONFIG_FILE.is_file():
        save_app_config_static(config)

    return config


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def save_app_config_static(config: dict) -> None:
    """Write config.json. Failures are ignored - a read-only folder must not
    stop the app, the settings simply do not survive the session."""
    try:
        APP_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Terminal command history (separate file, shared by all profiles)
# ---------------------------------------------------------------------------

def load_command_history() -> list[str]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [str(item) for item in data if isinstance(item, str)]
    return []


def save_command_history(history: list[str]) -> None:
    """Persist the most recent entries, newest last."""
    try:
        HISTORY_FILE.write_text(json.dumps(history[-MAX_COMMAND_HISTORY:], indent=2), encoding="utf-8")
    except OSError:
        pass
