#!/usr/bin/env python3
"""OpenWrt Log Viewer Addon
Single or multi mode
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from collections.abc import Callable

from addon_common import AddonError, find_addon_picture

ADDON = {
    "name": "OpenWrt Log Viewer",
    "version": "3.3",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh"],
    # Single mode rides the app's session, multi mode dials out on its own.
    "connection": "both",
    "description": ("a single-device log viewer and a multi-device view, "
                    "colour-coded connections, the log in time order. "
                    "The search highlights or filters the matching lines."),
}

#: The window tab and its sub-page bar (berry style).
ONE_TAB = "OpenWrt Logs"
MODES_ID = "mode_tabs"
SINGLE_MODE = 0
MULTI_MODE = 1

#: Tab mark drawn as an SVG when the assets folder carries no picture.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="4" y="3" width="16" height="18" rx="2" fill="#ffffff" stroke="#8a8a8a"/>'
    '<path d="M7 7h6" stroke="#ffb020" stroke-width="1.8"/>'
    '<path d="M7 11h10" stroke="#4da3ff" stroke-width="1.8"/>'
    '<path d="M7 15h8" stroke="#2ec27e" stroke-width="1.8"/>'
    '<path d="M7 19h5" stroke="#ff7ea3" stroke-width="1.8"/>'
    '</svg>'
)

#: The picture this addon's own assets folder is expected to hold: its id and
#: then ``icon.png``, the rule every addon follows.
TAB_ICON_FILE = "openwrt_logsicon.png"


def tab_icon() -> str:
    """What to mark the tab with: the shipped picture, else the drawing.

    ``openwrt_logsicon.png`` is the only name looked for, in this addon's own
    ``addon.store/assets/openwrt_logs`` folder and then in the program's, so a
    picture dropped into either is worn without a code change.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api in a bare import
        paths = None
    return find_addon_picture(paths, "openwrt_logs") or TAB_ICON


#: What each device is asked for unless the config names another command.
LOG_COMMAND = "logread"
LOG_TIMEOUT = 20

#: Cap on rows drawn; a table this big is more than anyone reads.
MAX_ROWS = 4000

SETTINGS_KEY = "connections"

#: Quick-pick colours; the picker takes any hex, these are just the defaults.
COLORS = [
    ("amber", "#ffb020"),
    ("sky", "#4da3ff"),
    ("mint", "#2ec27e"),
    ("rose", "#ff7ea3"),
    ("violet", "#b98cff"),
    ("orange", "#ff8c42"),
    ("teal", "#32c5c5"),
    ("lime", "#b5d34a"),
    ("slate", "#9fb3c8"),
    ("pink", "#f06bc0"),
    ("white", "#f5f5f5"),
]
COLOR_NAMES = [name for name, _hex in COLORS]
COLOR_HEX = dict(COLORS)

#: Wash strength over a row; at full the bright colours hide the text.
HIGHLIGHT_ALPHA = "33"
DEVICE_TINT_ALPHA = "66"


def _wash(hex_colour: str, alpha: str) -> str:
    """A #rrggbb as a translucent wash, the way Qt wants the alpha first."""
    return "#" + alpha + str(hex_colour).lstrip("#")[:6]

#: Everything the user can set, read fresh on every render and fetch.
CONFIG_FIELDS = [
    {"key": "search_mode", "label": "Search box acts as", "kind": "choice",
     "choices": ["Highlight", "Filter"],
     "hint": "Highlight keeps the whole log in view and tints the lines the "
             "search matches; Filter hides every line it does not match"},
    {"key": "case_sensitive", "label": "Search is case sensitive", "kind": "bool",
     "hint": "Only the free-text search box; the interface, process, MAC and "
             "IP picks always ignore case"},
    {"key": "use_regex", "label": "Search text is a regular expression",
     "kind": "bool",
     "hint": "Python regex syntax. A pattern that does not compile falls "
             "back to a plain text match"},
    {"key": "highlight_color", "label": "Highlight colour", "kind": "choice",
     "choices": COLOR_NAMES,
     "hint": "The colour a matched line is washed with in Highlight mode, "
             "laid semi-transparent over the row - and over the device "
             "colour - so both still show"},
    {"key": "max_rows", "label": "Most lines drawn", "kind": "number",
     "minimum": 50, "maximum": 50000,
     "hint": "A cap on what is painted, not on what is kept - filtering "
             "still runs over everything the device sent"},
    {"key": "log_command", "label": "Command that reads the log", "kind": "text",
     "hint": "logread prints the ring buffer; on a busy device try "
             "'logread -n 500' or 'logread -e dropbear'"},
    {"key": "log_timeout", "label": "Seconds a device gets", "kind": "number",
     "minimum": 2, "maximum": 300,
     "hint": "Per fetch; the multi view asks all devices at once"},
    {"key": "default_order", "label": "New lines at the top", "kind": "choice",
     "choices": ["Newest first", "Oldest first"],
     "hint": "Where the Order picker starts on this tab; changing it there "
             "as you work still wins"},
    {"key": "auto_refresh", "label": "Re-fetch every N seconds", "kind": "number",
     "minimum": 0, "maximum": 3600,
     "hint": "0 switches it off. Auto-refresh only reads while this tab is "
             "the one showing, so a hidden tab never talks to the devices"},
    {"key": "colorize_devices", "label": "Colour lines per device", "kind": "bool",
     "hint": "Multi view: each device's lines are washed through with the "
             "colour picked for it in the connection list"},
    {"key": "repeat_time", "label": "Keep the timestamp inside the message",
     "kind": "bool",
     "hint": "Off by default: the Time column already says when the line was "
             "written, so the copy at the head of the message is trimmed to "
             "leave room for the message itself"},
]
CONFIG_DEFAULTS = {
    "search_mode": "Highlight",
    "case_sensitive": False,
    "use_regex": False,
    "highlight_color": "white",
    "max_rows": MAX_ROWS,
    "log_command": LOG_COMMAND,
    "log_timeout": LOG_TIMEOUT,
    "default_order": "Newest first",
    "auto_refresh": 0,
    "colorize_devices": True,
    "repeat_time": False,
}

api = None

#: What each tab holds: entry dicts (time, line, device, color, proc).
_single_entries: list[dict] = []
_multi_entries: list[dict] = []
_multi_failed: dict[str, str] = {}

#: The log text each view last showed, or None before its first fetch.
#: Auto-refresh reads the ring buffer again and again; a device whose log
#: has not grown gets the same text back, and nothing on screen has to move.
_single_raw: str | None = None
_multi_raw: tuple | None = None

#: What the filter combos were last offered, keyed by tab and prefix; the
#: word scan over the whole log only runs again for words actually new.
_combo_items: dict[str, dict] = {}

#: Passwords for this session, keyed by (host, username). Never stored.
_passwords: dict[tuple, str] = {}

#: The in-flight multi fetch, or None.
_poll: dict | None = None

#: Auto-refresh timer while the config asks for one, else None.
_refresh_timer = None

#: Connections-list display strings, so a selection maps back to an index.
_conn_rows: list[str] = []
#: Connections-list row the addon last saw picked, or None.
_conn_sel: int | None = None


# --- Reading a log line ---

_MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

# "Oct 12 10:00:00" - busybox logread default, year optional.
_MDHMS = re.compile(
    r"^([A-Z][a-z]{2}) +(\d{1,2}) +(\d{2}):(\d{2}):(\d{2})(?: +(\d{4}))?")
# "Mon Oct 12 10:00:00 2026" - syslog with the weekday in front.
_WMDHMSY = re.compile(
    r"^[A-Za-z]{3} ([A-Z][a-z]{2}) +(\d{1,2}) +(\d{2}):(\d{2}):(\d{2}) +(\d{4})")
# "2026-10-12 10:00:00" / "2026-10-12T10:00:00" - ISO-ish.
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
# "10:00:00" - logread -t; time of day orders a day's log.
_HMS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?![\d:])")

IFACE_RE = re.compile(
    r"\b(eth\d+(?:\.\d+)?|wlan\d+(?:\.\d+)?|br-[\w-]+|lan|wan|"
    r"pppoe-[\w-]+|tun\d+|tap\d+|lo)\b", re.IGNORECASE)
MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# "daemon.info dropbear[999]:" - facility and process before the message;
# the second branch catches a bare "name[123]:" anywhere.
PROC_RE = re.compile(
    r"(?:\b(?:user|daemon|auth|kern|cron|lpr|news|uucp|local\d)\.\w+ +|"
    r"^\S+ +)([\w./-]+)(?:\[\d+\])?:")


def parse_time(line: str) -> dt.datetime | None:
    """The timestamp at the head of a log line, or None; year-less months borrow this one."""
    text = line.strip()
    match = _WMDHMSY.match(text)
    if match:
        month, day, hour, minute, second, year = match.groups()
    elif (match := _MDHMS.match(text)):
        month, day, hour, minute, second, year = match.groups()
        year = year or dt.date.today().year
    elif (match := _ISO.match(text)):
        year, month, day, hour, minute, second = match.groups()
    elif (match := _HMS.match(text)):
        hour, minute, second = match.groups()
        today = dt.date.today()
        year, month, day = today.year, today.month, today.day
    else:
        return None
    try:
        if isinstance(month, str):
            month = int(month) if month.isdigit() else _MONTHS[month]
        return dt.datetime(int(year), month, int(day),
                           int(hour), int(minute), int(second))
    except (TypeError, ValueError, KeyError):
        return None


def line_proc(line: str) -> str:
    """The daemon or facility that wrote a line, or nothing."""
    match = PROC_RE.search(line)
    return match.group(1) if match else ""


def line_body(line: str) -> str:
    """A log line with the timestamp at its head taken off; the raw line still feeds filters."""
    text = str(line or "")
    for pattern in (_WMDHMSY, _MDHMS, _ISO, _HMS):
        matched = pattern.match(text)
        if matched:
            return text[matched.end():].lstrip()
    return text


def make_entries(lines: list[str], device: str = "", color: str | None = None) -> list[dict]:
    """Log text into the entry dicts the tables and filters work on."""
    entries = []
    for line in lines:
        if not line.strip():
            continue
        entries.append({
            "time": parse_time(line),
            "line": line.rstrip("\r\n"),
            "device": device,
            "color": color,
            "proc": line_proc(line),
        })
    return entries


def _words(entries: list[dict], pattern: re.Pattern) -> list[str]:
    found: set[str] = set()
    for entry in entries:
        found.update(match.group(1) if pattern.groups else match.group(0)
                     for match in pattern.finditer(entry["line"]))
    return sorted(found, key=str.lower)


def iface_words(entries: list[dict]) -> list[str]:
    return _words(entries, IFACE_RE)


def mac_words(entries: list[dict]) -> list[str]:
    return _words(entries, MAC_RE)


def ip_words(entries: list[dict]) -> list[str]:
    return _words(entries, IP_RE)


def proc_words(entries: list[dict]) -> list[str]:
    return sorted({e["proc"] for e in entries if e["proc"]}, key=str.lower)


# --- Small helpers around the api ---

def _read(tab: str, identifier: str, what: str = "value", default: Any = "") -> Any:
    """One control's state, falling back quietly when the tab is not built."""
    try:
        got = api.tabs.widget_value(tab, identifier, what)
    except Exception:
        return default
    return default if got is None else got


def _cfg(key: str) -> Any:
    """One value from the config, or the shipped default, read fresh each time."""
    fallback = CONFIG_DEFAULTS.get(key)
    if api is None:
        return fallback
    try:
        got = api.config.get(key, fallback)
    except Exception:
        return fallback
    return fallback if got is None else got


def _cfg_int(key: str) -> int:
    try:
        return int(_cfg(key))
    except (TypeError, ValueError):
        return int(CONFIG_DEFAULTS.get(key) or 0)


def _cfg_bool(key: str) -> bool:
    value = _cfg(key)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _colour_for(name: str) -> str:
    text = str(name or "").strip()
    if text.startswith("#"):
        return text
    return COLOR_HEX.get(text.lower(), "#d4d4d4")


def _connections() -> list[dict]:
    try:
        raw = api.settings.get(SETTINGS_KEY, []) or []
    except Exception:
        return []
    return [dict(item) for item in raw
            if isinstance(item, dict) and str(item.get("host", "")).strip()]


def _save(connections: list[dict]) -> None:
    """Persist the devices without their passwords - those stay in the session."""
    stored = [{key: value for key, value in conn.items() if key != "password"}
              for conn in connections]
    api.settings.set(SETTINGS_KEY, stored)
    _show_connections()


def _label(conn: dict) -> str:
    return str(conn.get("label") or conn.get("host") or "?").strip()


def _stop(timer: Any) -> None:
    for name in ("cancel", "stop"):
        action = getattr(timer, name, None)
        if callable(action):
            try:
                action()
            except Exception:
                pass
            return


# --- The tabs ---

def _filter_rows(prefix: str, with_device: bool) -> list[dict]:
    """A single compact row of filters for both tabs."""
    row_children = [
        {"kind": "label", "text": "Search:"},
        {"kind": "line", "id": prefix + "search",
         "placeholder": "Filter...", "on": ["changed", "returned"]},
        {"kind": "label", "text": "Iface:"},
        {"kind": "combo", "id": prefix + "iface", "items": ["All"],
         "on": ["changed"]},
        {"kind": "label", "text": "Proc:"},
        {"kind": "combo", "id": prefix + "proc", "items": ["All"],
         "on": ["changed"]},
        {"kind": "label", "text": "MAC:"},
        {"kind": "combo", "id": prefix + "mac", "items": ["All"],
         "on": ["changed"]},
        {"kind": "label", "text": "IP:"},
        {"kind": "combo", "id": prefix + "ip", "items": ["All"],
         "on": ["changed"]},
    ]
    if with_device:
        row_children += [
            {"kind": "label", "text": "Dev:"},
            {"kind": "combo", "id": prefix + "device", "items": ["All"],
             "on": ["changed"]},
        ]
    row_children += [
        {"kind": "label", "text": "Order:"},
        {"kind": "combo", "id": prefix + "order",
         "items": ["Newest first", "Oldest first"], "on": ["changed"]},
    ]
    return [{"kind": "row", "children": row_children}]


SINGLE_SPEC = {
    "kind": "column",
    "label": "Single device",
    "children": _filter_rows("s_", False) + [
        {"kind": "row", "children": [
            {"kind": "button", "id": "s_refresh", "text": "Refresh Logs",
             "on": ["clicked"]},
            {"kind": "label", "id": "s_status", "text": ""},
            {"kind": "spacer"},
        ]},
        {"kind": "table", "id": "s_table",
         "columns": ["Time", "Process", "Message"],
         "stretch": 2, "col_widths": [150, 150]},
    ],
}

MULTI_SPEC = {
    # The boxes a log is read from are a short list; the lines are what needs the
    # width. The line between the two moves, so either can be given more of the
    # tab: one part to the connections, three to the log itself.
    "kind": "splitter",
    "sizes": [1, 3],
    "label": "Multi device",
    "children": [
        {
            "kind": "column",
            "children": [
                {"kind": "label", "text": "Connections"},
                {"kind": "list", "id": "m_conns", "on": ["selected"]},
                {"kind": "row", "children": [
                    {"kind": "button", "id": "m_add", "text": "Add...",
                     "on": ["clicked"]},
                    {"kind": "button", "id": "m_edit", "text": "Edit...",
                     "on": ["clicked"]},
                ]},
                {"kind": "row", "children": [
                    {"kind": "button", "id": "m_colour", "text": "Colour...",
                     "on": ["clicked"]},
                    {"kind": "button", "id": "m_remove", "text": "Remove",
                     "on": ["clicked"]},
                ]},
                {"kind": "row", "children": [
                    {"kind": "button", "id": "m_refresh",
                     "text": "Fetch from all", "on": ["clicked"]},
                ]},
                {"kind": "label", "id": "m_status", "text": ""},
                {"kind": "spacer"},
            ],
        },
        {
            "kind": "column",
            "children": _filter_rows("m_", True) + [
                {"kind": "table", "id": "m_table",
                 "columns": ["Time", "Device", "Process", "Message"],
                 "stretch": 3, "col_widths": [150, 110, 150]},
            ],
        },
    ],
}

#: The tab: two mode pages under one sub-page bar, id-ed for the Tools menu.
TAB_SPEC = {
    "kind": "tabs",
    "id": MODES_ID,
    "children": [SINGLE_SPEC, MULTI_SPEC],
}


def init(plugin_api: Any) -> None:
    global api
    api = plugin_api
    # The form the 'Edit settings' button builds on this addon's tab in the
    # Addon manager; the file is read fresh, never cached.
    api.config.declare(
        fields=CONFIG_FIELDS, defaults=CONFIG_DEFAULTS,
        title="OpenWrt Log Viewer - addons/_data/openwrt_logs/config.json")
    api.tabs.add_tab(ONE_TAB, spec=TAB_SPEC)
    api.tabs.set_icon(ONE_TAB, tab_icon())
    _apply_order()
    _show_connections()

    # The two modes, also reachable from the app's Tools menu.
    api.ui.menu_item("OpenWrt Logs (single device)",
                     lambda: _show_mode(SINGLE_MODE))
    api.ui.menu_item("OpenWrt Logs (multi device)",
                     lambda: _show_mode(MULTI_MODE))

    api.events.on("tab_changed", _on_tab_changed)
    _arm_refresh()
    api.log(f"OpenWrt Log Viewer v{ADDON['version']} initialized (the tab wears "
            "its own picture, device colours painted into the rows, colour "
            "wheel with a self-filling hex line, faint white search overlay).")


def shutdown() -> None:
    global _poll, _refresh_timer
    if _poll is not None:
        _stop(_poll.get("timer"))
        _poll = None
    if _refresh_timer is not None:
        _stop(_refresh_timer)
        _refresh_timer = None
    api.tabs.close(ONE_TAB)


def _show_mode(index: int) -> None:
    """Jump to one sub-page, the way the Tools menu asks for a mode."""
    api.tabs.set_widget(ONE_TAB, MODES_ID, "current", index)
    api.tabs.activate(ONE_TAB)
    _maybe_fetch(index)


def _current_mode() -> int:
    """Which sub-page is showing; the single page before the tab is built."""
    try:
        return MULTI_MODE if int(_read(ONE_TAB, MODES_ID, "index", 0)) == MULTI_MODE \
            else SINGLE_MODE
    except (TypeError, ValueError):
        return SINGLE_MODE


def _maybe_fetch(index: int) -> None:
    """Load a page's log the first time anyone looks at it."""
    if index == MULTI_MODE:
        if not _multi_entries and _connections():
            fetch_all()
    elif not _single_entries:
        fetch_logs()


def _on_tab_changed(_payload: Any) -> None:
    try:
        current = api.session.current_tab()
    except Exception:
        return
    if current == ONE_TAB:
        _maybe_fetch(_current_mode())


def _apply_order() -> None:
    """Park both Order pickers where the config says; changing them still wins."""
    order = str(_cfg("default_order"))
    for prefix in ("s_", "m_"):
        try:
            api.tabs.set_widget(ONE_TAB, prefix + "order", "value", order)
        except Exception:            # the tab may not be built yet
            pass


def _arm_refresh() -> None:
    """(Re)start the auto-refresh timer from the config; 0 switches it off."""
    global _refresh_timer
    if _refresh_timer is not None:
        _stop(_refresh_timer)
        _refresh_timer = None
    seconds = _cfg_int("auto_refresh")
    if seconds > 0 and api is not None:
        _refresh_timer = api.schedule.every(seconds, _timed_fetch)


def _timed_fetch() -> None:
    """One turn of the auto-refresh, and only while this tab is showing."""
    if api is None:
        return
    try:
        if api.session.current_tab() != ONE_TAB:
            return
    except Exception:
        return
    if _current_mode() == MULTI_MODE:
        if _poll is None:
            fetch_all()
    else:
        fetch_logs()


def on_config(_payload: Any = None) -> None:
    """The config changed: re-read everything it drives, live."""
    _arm_refresh()
    _apply_order()
    render_single()
    render_multi()


# --- Single device: the app's main connection ---

def fetch_logs() -> None:
    """Read the profile device's log through the app's main connection."""
    try:
        api.device.run_async(str(_cfg("log_command") or LOG_COMMAND),
                             on_done=_got_single, timeout=_cfg_int("log_timeout"))
        api.ui.notify("Reading the log from the main connection...")
    except AddonError as error:
        api.error(str(error))


def _got_single(result: dict) -> None:
    global _single_entries, _single_raw
    if int(result.get("code", -1)) != 0:
        api.error(f"Could not read the log: {result.get('err') or 'command failed'}")
        return
    raw = str(result.get("out", ""))
    if raw == _single_raw:
        # The buffer holds exactly what is on screen already: filters and
        # order are read off the controls at render time, so there is truly
        # nothing new to parse, repaint or announce.
        return
    _single_raw = raw
    _single_entries = make_entries(raw.splitlines())
    _update_filter_items(ONE_TAB, "s_", _single_entries)
    render_single()
    api.ui.notify(f"Read {len(_single_entries)} log lines.")


def render_single() -> None:
    _render(ONE_TAB, "s_", "s_table", _single_entries, with_device=False)


# --- Multi device: the addon's own list of connections ---

def _device_details(conn: dict) -> dict | None:
    """The one-off ``device`` dict for one stored connection, asking for a
    password when it has neither a key file nor one from this session."""
    host = str(conn.get("host", "")).strip()
    user = str(conn.get("username") or "root").strip()
    details = {"host": host,
               "port": int(conn.get("port") or 22),
               "username": user,
               "remote_root": "/"}
    key_file = str(conn.get("key_file") or "").strip()
    if key_file:
        details["key_file"] = key_file
    cached = _passwords.get((host, user))
    if cached:
        details["password"] = cached
    if not key_file and not cached:
        asked = api.ui.input(f"Password for {user}@{host}", "Password", "", True)
        if asked is None:
            return None
        if asked:
            _passwords[(host, user)] = asked
            details["password"] = asked
    if not key_file and not details.get("password"):
        return None
    return details


class _Settled:
    """A finished result wearing a Future's face, for the poller."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def done(self) -> bool:
        return True

    def result(self) -> Any:
        return self._result

    def cancel(self) -> bool:
        return False


def _settled(result: dict) -> _Settled:
    return _Settled(result)


def fetch_all() -> None:
    """Ask every stored device for its log, all at once, off the GUI thread."""
    global _poll
    connections = _connections()
    if not connections:
        api.ui.notify("No connections yet: use Add... on the left.")
        return
    if _poll is not None:
        api.ui.notify("A fetch is already running.")
        return
    wanted: dict[str, object] = {}
    command = str(_cfg("log_command") or LOG_COMMAND)
    timeout = _cfg_int("log_timeout")
    for conn in connections:
        label = _label(conn)
        details = _device_details(conn)
        if details is None:
            wanted[label] = _settled({"code": -1, "out": "",
                                      "err": "no credentials (cancelled)"})
            continue
        try:
            # block=False hands back a Future: every device is asked at once,
            # and the device dict keeps this off the main session.
            wanted[label] = api.device.run(command, timeout=timeout,
                                           block=False, device=details)
        except Exception as error:
            wanted[label] = _settled({"code": -1, "out": "", "err": str(error)})
    # Half-second tries: the fetch's timeout plus grace, so a last-second
    # answer is not cut off.
    _poll = {"wanted": len(wanted), "results": {}, "futures": wanted,
             "tries": 0, "timer": None, "deadline": timeout * 2 + 8}
    if all(isinstance(future, _Settled) for future in wanted.values()):
        _finish_poll()
        return
    api.ui.notify(f"Reading logs from {len(wanted)} devices...")
    _poll["timer"] = api.schedule.every(0.5, _check_poll)


def _check_poll() -> None:
    if _poll is None:
        return
    _poll["tries"] += 1
    still_running = []
    for label, future in _poll["futures"].items():
        if label in _poll["results"]:
            continue
        if future.done():
            try:
                _poll["results"][label] = future.result()
            except Exception as error:
                _poll["results"][label] = {"code": -1, "out": "", "err": str(error)}
        else:
            still_running.append(label)
    if not still_running:
        _finish_poll()
    elif _poll["tries"] >= _poll.get("deadline", 40):
        for label in still_running:
            _poll["futures"][label].cancel()
            _poll["results"][label] = {"code": -1, "out": "",
                                       "err": "timed out"}
        _finish_poll()


def _finish_poll() -> None:
    """Every answer is in: merge the logs, newest first, each device in its colour."""
    global _poll, _multi_entries, _multi_failed, _multi_raw
    if _poll is not None:
        _stop(_poll.get("timer"))
    results = dict(_poll["results"]) if _poll else {}
    _poll = None
    try:
        signature = tuple(
            (str(int((results.get(_label(conn)) or {}).get("code", -1))),
             str((results.get(_label(conn)) or {}).get("out", "")))
            if results.get(_label(conn)) is not None else None
            for conn in _connections())
    except (TypeError, ValueError):
        # A code this check cannot read as a number is not a reason to skip
        # a refresh; the merge below reports it the way it always has.
        signature = None
    if signature == _multi_raw:
        # Every device answered with the text already merged on screen;
        # re-parsing and re-painting the merged table would show the same rows.
        return
    _multi_raw = signature
    entries: list[dict] = []
    failed: dict[str, str] = {}
    for conn in _connections():
        label = _label(conn)
        result = results.get(label)
        if result is None:
            continue
        if int(result.get("code", -1)) != 0:
            failed[label] = str(result.get("err") or "command failed")
            continue
        entries += make_entries(str(result.get("out", "")).splitlines(),
                                device=label, color=_colour_for(conn.get("color")))
    _multi_entries = entries
    _multi_failed = failed
    _update_filter_items(ONE_TAB, "m_", entries, device_items=["All"] + [
        _label(conn) for conn in _connections()])
    render_multi()
    if failed:
        api.ui.notify(f"{len(entries)} lines from {len(_connections()) - len(failed)} "
                      f"devices; failed: {', '.join(failed)}")
    else:
        api.ui.notify(f"{len(entries)} lines merged from "
                      f"{len(results)} devices.")


def render_multi() -> None:
    _render(ONE_TAB, "m_", "m_table", _multi_entries, with_device=True)


# --- Filtering, sorting and drawing ---

def _update_filter_items(tab: str, prefix: str, entries: list[dict],
                         device_items: list[str] | None = None) -> None:
    """Refill the filter combos from what the log actually contains."""
    def items(words: list[str]) -> list[str]:
        return ["All"] + words

    built = {
        prefix + "iface": items(iface_words(entries)),
        prefix + "proc": items(proc_words(entries)),
        prefix + "mac": items(mac_words(entries)),
        prefix + "ip": items(ip_words(entries)),
    }
    if device_items is not None:
        built[prefix + "device"] = device_items
    # Each combo's scan over the whole log still runs - the words come from
    # the entries, not from what was shown - but a combo already offered
    # these exact items is not written again, so an unchanged refresh costs
    # no per-combo rebuild.
    seen = _combo_items.setdefault(tab + "|" + prefix, {})
    for identifier, offer in built.items():
        if seen.get(identifier) == offer:
            continue
        seen[identifier] = offer
        api.tabs.set_widget(tab, identifier, "items", offer)


def _search_matcher(term: str) -> Callable[[Any], bool] | None:
    """A one-argument test for the search box, or None when empty; rebuilt each render."""
    term = str(term or "").strip()
    if not term:
        return None
    if _cfg_bool("use_regex"):
        flags = 0 if _cfg_bool("case_sensitive") else re.IGNORECASE
        try:
            probe = re.compile(term, flags).search
        except re.error:
            return lambda line: term in str(line)
        return lambda line: bool(probe(str(line)))
    if _cfg_bool("case_sensitive"):
        return lambda line: term in str(line)
    lowered = term.lower()
    return lambda line: lowered in str(line).lower()


def _matches(entry: dict, chosen: dict, apply_search: bool = True) -> bool:
    """Whether one log line survives every filter; ``apply_search`` off skips the box."""
    text = entry["line"].lower()
    if apply_search and chosen["matcher"] is not None:
        if not chosen["matcher"](entry["line"]):
            return False
    pats = chosen["pats"]
    for key in ("iface", "mac", "ip"):
        pat = pats[key]
        if pat is not None:
            if not pat.search(entry["line"]):
                return False
    if chosen["proc"] and chosen["proc"] != "All":
        if entry["proc"] != chosen["proc"] and \
                chosen["proc"].lower() not in text:
            return False
    if chosen["device"] and chosen["device"] != "All":
        if entry["device"] != chosen["device"]:
            return False
    return True


def _chosen(tab: str, prefix: str, with_device: bool) -> dict:
    term = str(_read(tab, prefix + "search", "text", "")).strip()
    chosen = {
        "search": term,
        "matcher": _search_matcher(term),
        "iface": str(_read(tab, prefix + "iface", default="All")),
        "proc": str(_read(tab, prefix + "proc", default="All")),
        "mac": str(_read(tab, prefix + "mac", default="All")),
        "ip": str(_read(tab, prefix + "ip", default="All")),
        "device": str(_read(tab, prefix + "device", default="All"))
        if with_device else "All",
        "order": str(_read(tab, prefix + "order", default="Newest first")),
    }
    # The iface/mac/ip whole-word patterns are compiled once per render here,
    # so _matches reuses them instead of rebuilding a regex for every line.
    chosen["pats"] = {
        key: re.compile(rf"\b{re.escape(chosen[key])}\b", re.IGNORECASE)
        if chosen[key] and chosen[key] != "All" else None
        for key in ("iface", "mac", "ip")
    }
    return chosen


def sort_entries(entries: list[dict], newest_first: bool) -> list[dict]:
    """Timestamped lines in time order; the ones without a stamp keep their
    place at the far end, whatever the direction."""
    timed = sorted((e for e in entries if e["time"]),
                   key=lambda e: e["time"], reverse=newest_first)
    untimed = [e for e in entries if not e["time"]]
    return timed + untimed if newest_first else untimed + timed


def _render(tab: str, prefix: str, table_id: str, entries: list[dict],
            with_device: bool) -> None:
    chosen = _chosen(tab, prefix, with_device)
    highlight = str(_cfg("search_mode")).strip().lower() != "filter"
    kept = sort_entries(
        [e for e in entries if _matches(e, chosen, apply_search=not highlight)],
        chosen["order"] != "Oldest first")
    # A wash lights a line only over a live search in Highlight mode; Filter
    # mode already dropped the unmatched. The device colour sits underneath,
    # so a lit line still shows which box it came from.
    overlay = None
    if highlight and chosen["matcher"] is not None:
        overlay = _wash(_colour_for(str(_cfg("highlight_color"))), HIGHLIGHT_ALPHA)
    colorize = _cfg_bool("colorize_devices")
    keep_time = _cfg_bool("repeat_time")
    rows = []
    matches = 0
    for entry in kept[:max(1, _cfg_int("max_rows"))]:
        when = entry["time"].strftime("%b %d %H:%M:%S") if entry["time"] else ""
        cells = [when]
        if with_device:
            cells.append(entry["device"])
        cells += [entry["proc"],
                  entry["line"] if keep_time else line_body(entry["line"])]
        wash = (_wash(entry["color"], DEVICE_TINT_ALPHA)
                if with_device and colorize and entry["color"] else None)
        hit = bool(overlay) and chosen["matcher"](entry["line"])
        if hit:
            matches += 1
        if wash or (overlay and hit):
            row = {"cells": cells}
            if wash:
                row["background"] = wash
            if overlay and hit:
                row["overlay"] = overlay
            rows.append(row)
        else:
            rows.append(cells)
    api.tabs.set_widget(tab, table_id, "rows", rows)
    status_id = prefix + "status"
    note = f"{len(kept)} of {len(entries)} lines"
    if overlay:
        note += f" | {matches} matched"
    if with_device and _multi_failed:
        note += " | failed: " + ", ".join(_multi_failed)
    api.tabs.set_widget(tab, status_id, "text", note)


# --- The connections list (multi mode) ---

def _show_connections() -> None:
    """Redraw the list on the left of the multi tab from the stored devices."""
    global _conn_rows, _conn_sel
    _conn_rows = []
    _conn_sel = None
    for conn in _connections():
        colour = str(conn.get("color", "")).lower()
        name = next((n for n, h in COLORS if h == colour), colour or "?")
        _conn_rows.append(f"{_label(conn)}  ({conn.get('host')}:{conn.get('port', 22)}, {name})")
    try:
        api.tabs.set_widget(ONE_TAB, "m_conns", "items", _conn_rows)
    except Exception:                # the tab may not exist yet (early shutdown)
        pass


def _connection_form(existing: dict | None) -> dict:
    """The add/edit dialog as a spec; the password it shows is offered once
    and remembered for the session only."""
    conn = existing or {}
    host = str(conn.get("host", ""))
    user = str(conn.get("username", "root"))
    return {
        "kind": "form",
        "children": [
            {"label": "Label", "kind": "line", "id": "label",
             "value": str(conn.get("label", "")),
             "placeholder": "e.g. Kitchen router"},
            {"label": "Host", "kind": "line", "id": "host", "value": host,
             "placeholder": "192.168.1.1"},
            {"label": "Port", "kind": "spin", "id": "port",
             "minimum": 1, "maximum": 65535, "value": int(conn.get("port") or 22)},
            {"label": "Username", "kind": "line", "id": "username", "value": user},
            {"label": "Password", "kind": "line", "id": "password",
             "value": _passwords.get((host, user), ""),
             "placeholder": "kept for this session only"},
            {"label": "Key file", "kind": "line", "id": "key_file",
             "value": str(conn.get("key_file", "")),
             "placeholder": "path to a private key (optional)"},
            {"label": "Colour", "kind": "color", "id": "color",
             "value": str(conn.get("color", "")),
             "placeholder": "#rrggbb, e.g. #4da3ff (blank for amber)"},
        ],
    }


def _ask_connection(existing: dict | None, index: int | None) -> None:
    """Show the dialog and store the device; index None means a new one."""
    values = api.ui.dialog(_connection_form(existing),
                           title="Edit device" if existing else "Add device")
    if not values:
        return
    host = str(values.get("host", "")).strip()
    if not host:
        api.ui.warning("Add device", "A host is needed (an address or name).")
        return
    user = str(values.get("username") or "root").strip()
    colour = str(values.get("color") or "").strip()
    if colour and not colour.startswith("#"):
        colour = COLOR_HEX.get(colour.lower(), colour)   # palette names still pass
    if colour and not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        api.ui.warning("Add device",
                       f"{colour!r} is not a colour. Use hex like #4da3ff, "
                       f"or leave it empty for amber.")
        return
    conn = {
        "label": str(values.get("label") or "").strip() or host,
        "host": host,
        "port": int(values.get("port") or 22),
        "username": user,
        "key_file": str(values.get("key_file") or "").strip(),
        "color": colour or _colour_for("amber"),
    }
    password = str(values.get("password") or "")
    if password:
        _passwords[(host, user)] = password
    connections = _connections()
    if index is None or index >= len(connections):
        connections.append(conn)
    else:
        old = connections[index]
        _passwords.pop((old.get("host"), old.get("username", "root")), None)
        connections[index] = conn
    _save(connections)
    api.ui.notify(f"Saved {_label(conn)}. Passwords are never written to the "
                  f"settings - a device without a key file is asked again next "
                  f"session.")


def _remember_selection(payload: Any) -> None:
    """Track which row of the connections list the user picked."""
    global _conn_sel
    texts = list((payload or {}).get("texts") or [])
    _conn_sel = _conn_rows.index(texts[0]) if texts and texts[0] in _conn_rows else None


def _selected_index() -> int | None:
    """Which entry of the stored devices the list has picked, or None."""
    if _conn_sel is not None and _conn_sel < len(_conn_rows):
        return _conn_sel
    return None


def add_connection() -> None:
    _ask_connection(None, None)


def edit_connection() -> None:
    index = _selected_index()
    if index is None:
        api.ui.notify("Pick a connection in the list first.")
        return
    connections = _connections()
    _ask_connection(connections[index], index)


def remove_connection() -> None:
    index = _selected_index()
    if index is None:
        api.ui.notify("Pick a connection in the list first.")
        return
    connections = _connections()
    gone = connections.pop(index)
    _save(connections)
    api.ui.notify(f"Removed {_label(gone)}.")


def recolour_connection() -> None:
    index = _selected_index()
    if index is None:
        api.ui.notify("Pick a connection in the list first.")
        return
    connections = _connections()
    picked = api.ui.pick_color("Colour for " + _label(connections[index]),
                               str(connections[index].get("color") or ""))
    if not picked:
        return
    connections[index]["color"] = picked
    _save(connections)
    render_multi()


# --- Widget events ---

def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    if tab != ONE_TAB:
        return
    if identifier == "s_refresh" and event == "clicked":
        fetch_logs()
    elif identifier == "m_refresh" and event == "clicked":
        fetch_all()
    elif identifier == "m_add" and event == "clicked":
        add_connection()
    elif identifier == "m_edit" and event == "clicked":
        edit_connection()
    elif identifier == "m_remove" and event == "clicked":
        remove_connection()
    elif identifier == "m_colour" and event == "clicked":
        recolour_connection()
    elif identifier == "m_conns" and event == "selected":
        _remember_selection(payload)
    elif event in ("changed", "returned"):
        # Every filter carries its page's prefix: s_ single, m_ multi.
        if identifier.startswith("s_"):
            render_single()
        elif identifier.startswith("m_"):
            render_multi()
