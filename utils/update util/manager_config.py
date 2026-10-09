#!/usr/bin/env python3
"""Reading, migrating and writing config.json plus the saved command history.

config.json v2 stores one record per profile and every record holds the
complete set of settings (connection details, tabs, backups, terminal...).
The app always works from the profile named by ``active_profile``; the old
flat layout, where the active profile's values were mirrored to top-level
keys, is folded into that shape on first load, so upgrading a file keeps
every user value.
"""

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

CONFIG_VERSION = 2

# Keys that describe the file layout, never the settings of a profile.
_STRUCTURAL_KEYS = ("config_version", "profiles", "active_profile")

# In-memory store built at load/import time: every full profile plus the
# name of the active one. The GUI works on a plain copy of the active
# profile and writes it back through save_app_config_static().
_STORE: dict = {"active": "", "profiles": []}


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


def _deep_copy(value):
    return json.loads(json.dumps(value))


# ---------------------------------------------------------------------------
# Value normalisation - run on every full profile so the UI can trust it
# ---------------------------------------------------------------------------

def _normalize_values(config: dict) -> dict:
    """Type-check and clamp every settings key of one complete config."""
    # A config that predates the tab list keeps its old addresses by rebuilding
    # web_tabs from the legacy web_port/web_path/luci_url keys.
    if not isinstance(config.get("web_tabs"), list):
        config = _migrate_web_tabs(config)
    config = _normalize_web_tabs(config)

    # A source path from another machine is useless - fall back to the repo,
    # then to the home directory, so the local tree is never empty by accident.
    if not config.get("source") or not Path(str(config["source"])).is_dir():
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
    config["port"] = _coerce_int(config.get("port"), DEFAULT_PROFILE["port"], 1, 65535)
    config["default_tab"] = str(config.get("default_tab", "SFTP"))
    config["host"] = str(config.get("host", DEFAULT_PROFILE["host"]))
    config["username"] = str(config.get("username", DEFAULT_PROFILE["username"]))
    config["key_file"] = str(config.get("key_file", ""))
    config["remote_root"] = str(config.get("remote_root", DEFAULT_PROFILE["remote_root"]))
    config["source"] = str(config.get("source", DEFAULT_PROFILE["source"]))
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

    # The single auto-connect switch became per-protocol ones. The old behavior
    # connected over SFTP first and only fell back to SSH when SFTP was off, so
    # seed accordingly before the legacy key is dropped.
    legacy_auto = config.get("auto_connect")
    if legacy_auto is not None and "auto_connect_sftp" not in config and "auto_connect_ssh" not in config:
        if config.get("sftp_enabled", True):
            config["auto_connect_sftp"] = bool(legacy_auto)
        elif config.get("ssh_enabled", True):
            config["auto_connect_ssh"] = bool(legacy_auto)
    config.pop("auto_connect", None)

    for key in (
        "confirm_destructive",
        "show_hidden_files",
        "auto_connect_sftp",
        "auto_connect_ssh",
        "use_alt_icon",
        "sftp_enabled",
        "ssh_enabled",
        "reboot_auto_watch",
        "trust_unknown_host",
        "remember_password",
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

    # Structural keys never belong inside a profile record.
    for key in _STRUCTURAL_KEYS:
        config.pop(key, None)
    return config


# ---------------------------------------------------------------------------
# Profile store: migration to the v2 shape and access to the active profile
# ---------------------------------------------------------------------------

def _complete_profile(raw: dict, base: dict) -> dict:
    """Build one full profile: the shared base values, overridden by whatever
    the record itself stores, then normalised. Never invents defaults over
    existing user data."""
    prof = _deep_copy(base)
    prof.update({k: v for k, v in raw.items() if k not in _STRUCTURAL_KEYS})
    name = str(raw.get("name") or "").strip()
    prof = _normalize_values(prof)
    prof["name"] = name
    return prof


def _unique_name(name: str, taken: set[str]) -> str:
    base = name or "Profile"
    candidate = base
    counter = 1
    while candidate in taken:
        counter += 1
        candidate = f"{base} {counter}"
    return candidate


def _build_store(config: dict) -> None:
    """Fold any supported file layout into the v2 store (full profiles)."""
    raw_profiles = config.get("profiles")
    if not isinstance(raw_profiles, list):
        raw_profiles = []

    if config.get("config_version") == CONFIG_VERSION:
        base = _normalize_values(_deep_copy(DEFAULT_APP_CONFIG))
        raw_list = raw_profiles
    else:
        # Legacy flat layout: the top-level keys are the settings of the file
        # (mirrored from the active profile). They become the shared starting
        # point for every profile so no stored value is replaced by a default.
        base = _deep_copy(DEFAULT_APP_CONFIG)
        base.update({k: v for k, v in config.items() if k not in _STRUCTURAL_KEYS})
        # The retired single switches must be judged on the raw file, because
        # the merged base already contains the newer keys they seed.
        legacy_auto = config.get("auto_connect")
        if legacy_auto is not None and "auto_connect_sftp" not in config and "auto_connect_ssh" not in config:
            if base.get("sftp_enabled", True):
                base["auto_connect_sftp"] = bool(legacy_auto)
            elif base.get("ssh_enabled", True):
                base["auto_connect_ssh"] = bool(legacy_auto)
        base.pop("auto_connect", None)
        legacy_backup = config.get("backup_before_deploy")
        if legacy_backup is not None and "remote_backup_enabled" not in config:
            base["remote_backup_enabled"] = bool(legacy_backup)
        base.pop("backup_before_deploy", None)
        base = _normalize_values(base)
        raw_list = raw_profiles

    profiles: list[dict] = []
    taken: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        prof = _complete_profile(raw, base)
        prof["name"] = _unique_name(prof["name"], taken)
        taken.add(prof["name"])
        profiles.append(prof)

    if not profiles:
        # Either a plain flat file with no profile list, or an unreadable one.
        # Wrap the shared settings into a single default profile, keeping the
        # legacy connection values instead of the shipped defaults.
        first = _deep_copy(base)
        first["name"] = _unique_name(str(config.get("active_profile") or DEFAULT_PROFILE["name"]), taken)
        profiles = [first]

    active = config.get("active_profile")
    if active not in taken:
        active = profiles[0]["name"]
    _STORE["profiles"] = profiles
    _STORE["active"] = active


def _working_copy() -> dict:
    """A detached copy of the active profile's settings (without its name)."""
    active = next(
        (p for p in _STORE["profiles"] if p["name"] == _STORE["active"]),
        _STORE["profiles"][0],
    )
    return {k: v for k, v in _deep_copy(active).items() if k != "name"}


def profile_names() -> list[str]:
    return [p["name"] for p in _STORE["profiles"]]


def active_profile_name() -> str:
    return _STORE["active"]


def set_active_profile(name: str) -> dict:
    """Point the store at another profile and return the new working copy."""
    if any(p["name"] == name for p in _STORE["profiles"]):
        _STORE["active"] = name
    return _working_copy()


def create_profile(name: str, working: dict) -> dict:
    """Clone the given settings into a new profile, activate it and return
    the fresh working copy. The name is de-duplicated if needed."""
    prof = _deep_copy(working)
    prof = _normalize_values(prof)
    taken = {p["name"] for p in _STORE["profiles"]}
    prof["name"] = _unique_name(str(name).strip(), taken)
    idx = next(
        (i for i, p in enumerate(_STORE["profiles"]) if p["name"] == _STORE["active"]),
        len(_STORE["profiles"]),
    )
    _STORE["profiles"].insert(idx + 1, prof)
    _STORE["active"] = prof["name"]
    persist_store()
    return _working_copy()


def rename_profile(old: str, new: str) -> bool:
    new = str(new).strip()
    if not new or new == old or any(p["name"] == new for p in _STORE["profiles"]):
        return False
    for prof in _STORE["profiles"]:
        if prof["name"] == old:
            prof["name"] = new
            break
    else:
        return False
    if _STORE["active"] == old:
        _STORE["active"] = new
    persist_store()
    return True


def delete_profile(name: str) -> dict | None:
    """Remove one profile; the first remaining one becomes active if needed.
    Returns the new working copy, or None when nothing could be deleted."""
    if len(_STORE["profiles"]) <= 1 or not any(p["name"] == name for p in _STORE["profiles"]):
        return None
    _STORE["profiles"] = [p for p in _STORE["profiles"] if p["name"] != name]
    if _STORE["active"] == name:
        _STORE["active"] = _STORE["profiles"][0]["name"]
    persist_store()
    return _working_copy()


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

    Only used when a config predates the tab list, so an existing setup
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


# ---------------------------------------------------------------------------
# Loading, saving and transporting the whole file
# ---------------------------------------------------------------------------

def load_app_config() -> dict:
    """Load config.json (or the shipped example), build the profile store and
    return the active profile's settings as a plain working dict.

    The result always has the full set of keys with sane, type-checked values,
    so the rest of the app can read the dict without defensive get() calls.
    """
    config: dict = {}
    # A real config wins over the example; if neither parses we keep defaults.
    for config_path in (APP_CONFIG_FILE, APP_CONFIG_EXAMPLE_FILE):
        if not config_path.is_file():
            continue
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            config = loaded
            break

    _build_store(config)

    # First run: write what we worked out so the user has a file to edit.
    if not APP_CONFIG_FILE.is_file():
        persist_store()

    return _working_copy()


def import_app_config(loaded: dict) -> dict:
    """Replace the store from an imported document (any supported layout)
    and return the new working copy. Nothing is written until the app saves."""
    _build_store(loaded if isinstance(loaded, dict) else {})
    return _working_copy()


def export_document() -> dict:
    """The whole store as a JSON-safe document, for exporting."""
    return _deep_copy(_document())


def _document() -> dict:
    return {
        "config_version": CONFIG_VERSION,
        "active_profile": _STORE["active"],
        "profiles": _STORE["profiles"],
    }


def persist_store() -> None:
    """Write config.json. Failures are ignored - a read-only folder must not
    stop the app, the settings simply do not survive the session."""
    try:
        APP_CONFIG_FILE.write_text(json.dumps(_document(), indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def save_app_config_static(config: dict) -> None:
    """Merge the working settings back into the active profile, then write."""
    merged = _normalize_values(_deep_copy(config))
    for i, prof in enumerate(_STORE["profiles"]):
        if prof["name"] == _STORE["active"]:
            merged["name"] = prof["name"]
            _STORE["profiles"][i] = merged
            break
    else:
        merged["name"] = _STORE["active"] or DEFAULT_PROFILE["name"]
        _STORE["active"] = merged["name"]
        _STORE["profiles"].append(merged)
    persist_store()


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
