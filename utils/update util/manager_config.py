#!/usr/bin/env python3
"""Reading, migrating and writing the profile files plus the saved history.

Everything the manager writes to its own root lives in the ``config`` folder
(see ``manager_const.CONFIG_DIR``): the profile files, the command history,
the activity log and the shared addon build record. The folder is created on
the way up, and a loose file left beside the program from an older layout is
moved into it, so an existing installation keeps its settings.

Layout v3: every profile lives in its own ``{profile}.json`` holding the
complete set of settings (connection details, tabs, backups, terminal...),
and ``config.json`` keeps only the index - the profile order, each profile's
file and which one is active. The app always works from the profile named by
the index. Older files - the flat layout, or v2 where every profile lived in
one config.json - are folded into this shape on first load and written back
once, so upgrading an installation keeps every user value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from manager_const import (
    APP_CONFIG_EXAMPLE_FILE,
    APP_CONFIG_FILE,
    BASE_DIR,
    CONFIG_DIR,
    DEFAULT_APP_CONFIG,
    DEFAULT_PROFILE,
    DEFAULT_SOURCE,
    HISTORY_FILE,
    LOG_FILE,
    MAX_COMMAND_HISTORY,
    UI_ZOOM_DEFAULT,
    UI_ZOOM_MAX,
    UI_ZOOM_MIN,
)

# Needs no window and nothing from this module, so no import loop: a theme is
# read from its file the way a profile is read from its own.
import manager_theme

CONFIG_VERSION = 3

# Layout keys, never a profile's settings.
_STRUCTURAL_KEYS = ("config_version", "profiles", "active_profile")

# Chars a profile name can't put in a filename, plus the
# Windows device names that still collide - both go to _.
_FILE_BAD = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{n}" for n in range(1, 10)),
                   *(f"LPT{n}" for n in range(1, 10))}

# Store at load: full profiles, active name, and files.
# GUI edits a copy, writes back via save_app_config_static().
_STORE: dict = {"active": "", "profiles": [], "files": {}}


# --- small helpers ---

def _coerce_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Turn anything read from JSON into an int inside [minimum, maximum]."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


# --- value normalisation (the UI can trust it) ---

def _normalize_values(config: dict) -> dict:
    """Type-check and clamp every settings key of one complete config."""
    # Pre-tab-list config keeps old addresses: rebuild web_tabs
    # from legacy web_port/web_path/luci_url.
    if not isinstance(config.get("web_tabs"), list):
        config = _migrate_web_tabs(config)
    config = _normalize_web_tabs(config)
    config = _normalize_addons(config)

    # A source path from another machine is useless; fall back to
    # the repo, then home, so the local tree is never empty.
    if not config.get("source") or not Path(str(config["source"])).is_dir():
        if DEFAULT_SOURCE.is_dir():
            config["source"] = str(DEFAULT_SOURCE)
        else:
            config["source"] = str(Path.home())

    # Clamp numbers, coerce free-text to str.
    config["ssh_timeout"] = _coerce_int(config.get("ssh_timeout"), 12, 3, 300)
    config["terminal_font_size"] = _coerce_int(config.get("terminal_font_size"), 10, 6, 24)
    # Zoom is a sane percentage; the rungs are the window's
    # business (see manager_const.step_zoom).
    config["ui_zoom"] = _coerce_int(config.get("ui_zoom"), UI_ZOOM_DEFAULT, UI_ZOOM_MIN, UI_ZOOM_MAX)
    config["max_log_lines"] = _coerce_int(config.get("max_log_lines"), 500, 50, 10000)
    config["terminal_history_limit"] = _coerce_int(config.get("terminal_history_limit"), 500, 20, 5000)
    config["web_port"] = _coerce_int(config.get("web_port"), 8989, 1, 65535)
    config["reboot_wait_seconds"] = _coerce_int(config.get("reboot_wait_seconds"), 30, 0, 600)
    config["reboot_retry_interval"] = _coerce_int(config.get("reboot_retry_interval"), 10, 1, 300)
    config["port"] = _coerce_int(config.get("port"), DEFAULT_PROFILE["port"], 1, 65535)
    config["default_tab"] = str(config.get("default_tab", "Berry"))
    # A theme is a file name, and a file that is not there is not a mistake to
    # throw on somebody: the shipped default is used instead, so a profile
    # copied onto another machine - or a theme deleted from its own - still
    # opens. ``manager_theme`` makes the themes folder on the way.
    config["theme"] = (manager_theme.safe_name(config.get("theme"))
                       or DEFAULT_APP_CONFIG["theme"])
    config["host"] = str(config.get("host", DEFAULT_PROFILE["host"]))
    config["username"] = str(config.get("username", DEFAULT_PROFILE["username"]))
    config["key_file"] = str(config.get("key_file", ""))
    config["remote_root"] = str(config.get("remote_root", DEFAULT_PROFILE["remote_root"]))
    config["source"] = str(config.get("source", DEFAULT_PROFILE["source"]))
    config["luci_url"] = str(config.get("luci_url", "http://192.168.1.1/"))
    config["web_path"] = str(config.get("web_path", "/cgi-bin/index.py"))

    # Tab order is a list of titles; else dropped so the app
    # falls back to its built-in arrangement.
    order = config.get("tab_order")
    if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
        config["tab_order"] = []

    # Quick commands are {label, command} pairs; else dropped.
    quick = config.get("quick_commands")
    if not isinstance(quick, list):
        quick = []
    sanitized_quick = []
    for entry in quick:
        if isinstance(entry, dict) and entry.get("command"):
            sanitized_quick.append({"label": str(entry.get("label", entry["command"])), "command": str(entry["command"])})
    config["quick_commands"] = sanitized_quick

    # Two per-protocol switches became one for the main connection,
    # which carries both transports: either one on means the main
    # connection comes up at startup. The merged key takes over and
    # the old two are dropped. The old keys decide, not the shipped
    # default: a merged profile always carries a fresh ``auto_connect``.
    if "auto_connect_sftp" in config or "auto_connect_ssh" in config:
        config["auto_connect"] = bool(config.get("auto_connect")) \
            or bool(config.get("auto_connect_sftp")) \
            or bool(config.get("auto_connect_ssh"))
    for key in ("auto_connect_sftp", "auto_connect_ssh"):
        config.pop(key, None)

    for key in (
        "show_hidden_files",
        "master_connection",
        "auto_connect",
        "use_alt_icon",
        "system_notifications",
        "splash_enabled",
        "log_visible",
        "sftp_enabled",
        "ssh_enabled",
        "ignore_cert_errors",
        "reboot_auto_watch",
        "remember_tab_order",
        "trust_unknown_host",
        "remember_password",
    ):
        config[key] = bool(config.get(key, DEFAULT_APP_CONFIG[key]))

    config["logging_enabled"] = bool(config.get("logging_enabled", True))

    # The deploy era wrote these; nothing reads them now, so old
    # profile files are cleaned of them on load.
    for key in ("deploy_ignore", "post_deploy_commands",
                "backup_directory", "local_backup_directory", "keep_last_n_backups",
                "remote_backup_enabled", "local_backup_enabled",
                "mirror_delete_remote", "mirror_delete_local",
                "backup_before_deploy", "confirm_destructive"):
        config.pop(key, None)

    # Structural keys never belong inside a profile record.
    for key in _STRUCTURAL_KEYS:
        config.pop(key, None)
    return config


# --- profile store: migration and active access ---

def _complete_profile(raw: dict, base: dict) -> dict:
    """Build one full profile: base values overridden by the record, then normalised; never defaults over user data."""
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


def _profile_filename(name: str, taken: set[str]) -> str:
    """The file one profile owns (``{profile}.json``), through Windows rules; a clash gets a counter."""
    cleaned = "".join("_" if ch in _FILE_BAD else ch for ch in str(name).strip())
    cleaned = cleaned.rstrip(" .")
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    stem = cleaned or "Profile"
    candidate = stem
    counter = 1
    # `taken` holds names with extension (callers track disk),
    # so the clash check must compare the same shape.
    while (candidate + ".json").lower() in taken:
        counter += 1
        candidate = f"{stem} {counter}"
    return candidate + ".json"


def _build_store(config: dict) -> None:
    """Fold any supported layout into the store (full profiles); v2 and flat here, v3 by ``_build_store_v3``."""
    raw_profiles = config.get("profiles")
    if not isinstance(raw_profiles, list):
        raw_profiles = []

    if config.get("config_version") == 2:
        base = _normalize_values(_deep_copy(DEFAULT_APP_CONFIG))
        raw_list = raw_profiles
    else:
        # Legacy flat layout: top-level keys are the file's settings
        # (mirrored from active). They become the shared start for
        # every profile so no stored value is replaced by a default.
        # The retired per-protocol auto-connect switches are judged
        # on the raw file, so ``_normalize_values`` merges them into
        # one ``auto_connect`` before a default can cover them up.
        base = _deep_copy(DEFAULT_APP_CONFIG)
        base.update({k: v for k, v in config.items() if k not in _STRUCTURAL_KEYS})
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
        # A flat file with no profile list, or an unreadable one:
        # wrap the shared settings into one default profile, keeping
        # the legacy connection values over the shipped defaults.
        first = _deep_copy(base)
        first["name"] = _unique_name(str(config.get("active_profile") or DEFAULT_PROFILE["name"]), taken)
        profiles = [first]

    active = config.get("active_profile")
    if active not in taken:
        active = profiles[0]["name"]
    _STORE["profiles"] = profiles
    _STORE["active"] = active
    # Files are worked out on first write; this branch has none.
    _STORE["files"] = {}


def _build_store_v3(index: dict) -> bool:
    """Read the v3 layout (index + one file per profile). False if not a usable index; a missing profile file is rebuilt from defaults."""
    raw_entries = index.get("profiles")
    if not isinstance(raw_entries, list) or not raw_entries:
        return False
    base = _normalize_values(_deep_copy(DEFAULT_APP_CONFIG))
    profiles: list[dict] = []
    files: dict[str, str] = {}
    taken_names: set[str] = set()
    used_files: set[str] = {"config.json"}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in taken_names:
            continue
        fname = str(entry.get("file") or "").strip()
        # Only a plain file name is trusted; a path in the index, or
        # a clash with a profile already read, is re-derived.
        if not fname or Path(fname).name != fname or fname.lower() in used_files:
            fname = _profile_filename(name, used_files)
        used_files.add(fname.lower())
        try:
            loaded = json.loads((CONFIG_DIR / fname).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        prof = _complete_profile(loaded if isinstance(loaded, dict) else {}, base)
        prof["name"] = name
        taken_names.add(name)
        files[name] = fname
        profiles.append(prof)
    if not profiles:
        return False
    active = index.get("active_profile")
    if active not in taken_names:
        active = profiles[0]["name"]
    _STORE["profiles"] = profiles
    _STORE["active"] = active
    _STORE["files"] = files
    return True


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
    """Clone these settings into a new profile, activate it, and return the fresh working copy."""
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
    """Remove one profile; first remaining becomes active if needed. Returns the new copy or None."""
    if len(_STORE["profiles"]) <= 1 or not any(p["name"] == name for p in _STORE["profiles"]):
        return None
    _STORE["profiles"] = [p for p in _STORE["profiles"] if p["name"] != name]
    if _STORE["active"] == name:
        _STORE["active"] = _STORE["profiles"][0]["name"]
    persist_store()
    return _working_copy()


# --- web tabs: url, migration, normalisation ---

def web_tab_url(tab: dict, default_host: str) -> str:
    """URL for one tab. Blank ip follows the active host; the port is dropped when it matches the scheme default."""
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
    """Seed web_tabs from legacy web_port/web_path/luci_url, used only for a pre-tab-list config so addresses are kept."""
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


# --- addons: which run, and their data ---

def _normalize_addons(config: dict) -> dict:
    """Keep addons a plain id -> {enabled, own_connection, ssh_enabled,
    sftp_enabled, settings} map; unknown ids kept, each off and on the main
    connection by default, with both of its transports allowed by default."""
    raw = config.get("addons")
    if not isinstance(raw, dict):
        raw = {}
    cleaned = {}
    for key, value in raw.items():
        addon_id = str(key).strip()
        if not addon_id:
            continue
        record = value if isinstance(value, dict) else {}
        settings = record.get("settings")
        cleaned[addon_id] = {
            "enabled": bool(record.get("enabled", False)),
            "own_connection": bool(record.get("own_connection", False)),
            # The addon's own gates over the two transports; the app-wide
            # switch stays above them, so both have to be on.
            "ssh_enabled": bool(record.get("ssh_enabled", True)),
            "sftp_enabled": bool(record.get("sftp_enabled", True)),
            "settings": settings if isinstance(settings, dict) else {},
        }
    config["addons"] = cleaned
    return config


# --- load, save, transport ---

def _prepare_config_dir() -> None:
    """Make the config folder and move loose files in (only into empty slots), picking up the old log name too."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    for old, new in ((BASE_DIR / "config.json", APP_CONFIG_FILE),
                     (BASE_DIR / "command_history.json", HISTORY_FILE),
                     (BASE_DIR / "rasconf_manager.log", LOG_FILE),
                     (CONFIG_DIR / "rasconf_manager.log", LOG_FILE)):
        if old.is_file() and not new.exists():
            try:
                old.replace(new)
            except OSError:
                pass


def load_app_config() -> dict:
    """Load config, build the store, return the active profile as a working dict; older layouts fold in and are written back once."""
    _prepare_config_dir()
    config: dict = {}
    # Real config beats the example; if neither parses, defaults.
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

    if config.get("config_version") == CONFIG_VERSION and _build_store_v3(config):
        pass                      # the files are already in the new layout
    else:
        _build_store(config)
        # First run or older layout: write back once as the index
        # plus one file per profile.
        persist_store()

    return _working_copy()


def import_app_config(loaded: dict) -> dict:
    """Replace the store from an imported document (any layout); nothing is written until save."""
    loaded = loaded if isinstance(loaded, dict) else {}
    if loaded.get("config_version") == CONFIG_VERSION and _build_store_v3(loaded):
        return _working_copy()
    _build_store(loaded)
    return _working_copy()


def export_document() -> dict:
    """The whole store as one portable v2 document; a single exported file restores everything on import."""
    return {
        "config_version": 2,
        "active_profile": _STORE["active"],
        "profiles": _deep_copy(_STORE["profiles"]),
    }


def persist_store() -> None:
    """Write the store: index in config.json, one file per profile; failures ignored so a read-only folder can't stop the app."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    entries = []
    used_files: set[str] = {"config.json"}
    for prof in _STORE["profiles"]:
        name = prof["name"]
        fname = _STORE["files"].get(name) or _profile_filename(name, used_files)
        _STORE["files"][name] = fname
        used_files.add(fname.lower())
        try:
            (CONFIG_DIR / fname).write_text(json.dumps(prof, indent=2) + "\n",
                                            encoding="utf-8")
        except OSError:
            pass
        entries.append({"name": name, "file": fname})
    # A renamed or deleted profile leaves its file; sweep it.
    # Only files this app wrote are tracked, so a hand-placed json
    # is never touched.
    for gone in [n for n in _STORE["files"]
                 if n not in {p["name"] for p in _STORE["profiles"]}]:
        stale = _STORE["files"].pop(gone)
        try:
            (CONFIG_DIR / stale).unlink(missing_ok=True)
        except OSError:
            pass
    index = {"config_version": CONFIG_VERSION,
             "active_profile": _STORE["active"],
             "profiles": entries}
    try:
        APP_CONFIG_FILE.write_text(json.dumps(index, indent=2) + "\n",
                                   encoding="utf-8")
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


# --- command history (separate file, shared) ---

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
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history[-MAX_COMMAND_HISTORY:], indent=2), encoding="utf-8")
    except OSError:
        pass
