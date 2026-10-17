#!/usr/bin/env python3
"""Multi SSH: several machines open at once, each on a connection of its own.

One tab holds a row of sub-tabs and every sub-tab is a shell to a different
machine - or to the same machine twice. Each of them runs on its own thread
over its own socket rather than borrowing the app's main session, which is why
the manifest says ``"connection": "own"``: nothing here is a view of a
connection the user opened elsewhere, and pressing Connect (or not) in the
Connection panel changes nothing in these tabs.

The machines worth visiting often are kept as *quickstart entries* in
``addons/_data/multi_ssh/config.json`` under ``quickstart``. One name, address,
port and user each, and a picture of its own: picked from any file on this
machine, it is copied into ``addons/_data/multi_ssh/icons`` so the entry keeps
it whatever happens to the original, and the store's row and the list beside
these tabs both draw it. The password is deliberately *not* in that file - the
app's config is plain JSON that gets shared and backed up, so a quickstart
click asks for it each time and keeps it only in the connection it opens.

The tab is built from a declarative spec and everything it does - opening a
page, connecting a shell, typing into several at once - is one ``set_widget``
property away, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from addon_common import AddonError, find_addon_picture

ADDON = {
    "name": "Multi SSH",
    "version": "1.2",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh"],
    # Every shell brings up its own socket; none of them is a view of the
    # app's main session, so this says so rather than leaning on it.
    "connection": "own",
    # The picture is published under the standard name - the id and then
    # ``icon.png`` - so the manifest names none and the store finds it by rule.
    "description": "Opens as many SSH sessions as you like in sub-tabs, each on a "
                   "connection of its own, with quickstart entries that ask only "
                   "for a password and one field that types a command into every "
                   "session at once.",
}

#: The tab's mark when the assets folder carries no picture: two shells, one
#: behind the other, since more than one at a time is the whole point.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="1.5" y="3.5" width="14.5" height="10.5" rx="2" fill="#1e1e1e"'
    ' stroke="#7a2030" stroke-width="1.1"/>'
    '<path d="M4 7.2 6 9l-2 1.8" fill="none" stroke="#23d18b"'
    ' stroke-width="1.2"/>'
    '<rect x="7.5" y="9.5" width="15" height="11" rx="2" fill="#242424"'
    ' stroke="#c51a4a" stroke-width="1.2"/>'
    '<path d="M10.2 13.2 12.6 15l-2.4 1.8" fill="none" stroke="#23d18b"'
    ' stroke-width="1.3"/>'
    '<path d="M14.4 16.8h4" fill="none" stroke="#ffb020" stroke-width="1.3"/>'
    '</svg>'
)

#: What this addon's own assets folder is expected to hold: its id and then
#: ``icon.png``, the rule every addon follows - so putting the picture in is
#: enough, there is no name here to keep in step.
TAB_ICON_FILE = "multi_sshicon.png"

TAB = "Multi SSH"
SESSIONS = "sessions"

#: The caption of the page that stands in while no session is open. It is a
#: page like any other, so it is taken away when a real one arrives and put
#: back when the last one goes.
EMPTY_CAPTION = "No session"

# --- what can be configured, and its defaults ---

FIELDS = [
    {"key": "default_user", "label": "Username to offer", "kind": "text",
     "hint": "Pre-filled for a session that names none of its own"},
    {"key": "default_port", "label": "Port to offer", "kind": "number",
     "minimum": 1, "maximum": 65535},
    {"key": "trust_unknown_host", "label": "Accept a host key nobody has seen",
     "kind": "bool",
     "hint": "Switched off, a machine whose key is not in your known_hosts "
             "file is refused - the safe answer the first time, the annoying "
             "one for a lab router with a key that keeps changing"},
    {"key": "close_on_disconnect", "label": "Close the tab when the link drops",
     "kind": "bool",
     "hint": "On, a session's page goes with its connection. Off, the page "
             "stays put with what was last on it and a Connect button to try "
             "again - which is the way to keep the scrollback of a machine "
             "that drops"},
]

#: ``quickstart`` is in the file but named in no field: the app's form writes
#: from the file, so a key it does not show is kept exactly as it stands. That
#: is what lets the entries be edited here, in any editor, as the list of
#: objects they are - and what lets this addon add and drop them itself.
DEFAULTS = {
    "default_user": "root",
    "default_port": 22,
    "trust_unknown_host": True,
    "close_on_disconnect": True,
    "quickstart": [
        {"name": "Router", "host": "192.168.1.1", "port": 22, "username": "root"},
    ],
}

#: Where a quickstart entry's picture is kept, inside the addon's data folder.
ICON_DIR = "icons"

#: Endings a picture may have, so a copied file keeps the shape it came with.
PICTURE_SUFFIXES = (".png", ".ico", ".icns", ".svg", ".jpg", ".jpeg", ".webp")

# --- the tab ---

#: The left-hand half: the machines kept in the config file, and the four
#: things that can be done to the one under the cursor.
QUICKSTART_SPEC = {
    "kind": "group",
    "text": "Quickstart",
    "layout": "column",
    "stretch": False,
    "children": [
        {"kind": "list", "id": "qs_list", "on": ["selected"],
         "tooltip": "A machine from addons/_data/multi_ssh/config.json. "
                    "Select one and press Connect - it only ever asks for "
                    "the password."},
        {"kind": "row", "children": [
            {"kind": "button", "id": "qs_connect", "text": "Connect",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": "qs_new", "text": "New", "on": ["clicked"],
             "tooltip": "Open a session to any address, once"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": "qs_add", "text": "Add", "on": ["clicked"]},
            {"kind": "button", "id": "qs_edit", "text": "Edit", "on": ["clicked"]},
            {"kind": "button", "id": "qs_icon", "text": "Icon", "on": ["clicked"],
             "tooltip": "Give this entry a picture of its own, kept in the "
                        "addon's data folder"},
            {"kind": "button", "id": "qs_remove", "text": "Remove",
             "on": ["clicked"]},
        ]},
        {"kind": "button", "id": "qs_reload", "text": "Reload from config",
         "on": ["clicked"],
         "tooltip": "Read the file again, picking up entries edited outside "
                    "Berry Manager"},
        {"kind": "label", "id": "qs_note", "text": "", "word_wrap": True},
    ],
}

TAB_SPEC = {
    "kind": "column",
    "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Send to every session:"},
            {"kind": "line", "id": "bc_cmd", "placeholder": "uptime",
             "expand": True, "on": ["returned"],
             "tooltip": "Enter, or Broadcast, types this into each session "
                        "that is up. Nothing is sent to one that is not."},
            {"kind": "button", "id": "bc_send", "text": "Broadcast",
             "on": ["clicked"]},
            {"kind": "label", "id": "qs_status", "text": "No session is open."},
        ]},
        {"kind": "splitter", "vertical": False, "sizes": [1, 3], "children": [
            QUICKSTART_SPEC,
            {"kind": "tabs", "id": SESSIONS, "children": [
                {"kind": "label", "label": EMPTY_CAPTION, "text": EMPTY_CAPTION},
            ]},
        ]},
    ],
}

api = None                  # filled in by init()
entries: list = []          # the quickstart list, normalised, in order
sessions: dict = {}         # terminal id -> what that session is and how it is doing
current = -1                # the row the list has under the cursor
_seq = 0                    # session numbers, so ids cannot be reused


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark."""
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api in a bare import
        paths = None
    return find_addon_picture(paths, "multi_ssh") or TAB_ICON


def init(plugin_api: Any) -> None:
    global api
    api = plugin_api
    api.config.declare(fields=FIELDS, defaults=DEFAULTS, title="Multi SSH")
    api.tabs.add_tab(TAB, spec=TAB_SPEC)
    api.tabs.set_icon(TAB, tab_icon())
    reload_entries()
    api.log("Multi SSH ready: every session is a connection of its own.")


def shutdown() -> None:
    """Close the tab. The app stops every shell inside it on its way out."""
    sessions.clear()
    if api is not None:
        api.tabs.close(TAB)


# --- small pure helpers, so the shapes above can be read without a window ---

def port_of(value: Any, default: int = 22) -> int:
    """A port number, or ``default``: a config file typed by hand is allowed to
    hold ``"22"``, a blank, or ``0``, and none of those is worth an exception."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return number if 1 <= number <= 65535 else int(default)


def split_address(text: Any) -> dict:
    """``root@192.168.1.1:2222`` cut into the parts a connection needs.

    An entry may be written as one address instead of four fields, which is how
    people keep hosts in their own notes, so the same shape is read here. A
    ``::`` is not guessed at: an IPv6 literal keeps its colons and goes into
    ``host`` whole.
    """
    body = str(text or "").strip()
    out = {"username": "", "host": "", "port": ""}
    if "@" in body:
        out["username"], _, body = body.partition("@")
        out["username"] = out["username"].strip()
    if body.count(":") == 1:
        out["host"], _, out["port"] = body.rpartition(":")
        out["host"] = out["host"].strip()
    else:
        out["host"] = body
    return out


def normalise_entry(raw: Any) -> dict:
    """One quickstart entry, in the shape this addon uses, from whatever the
    file held: a bare address, or an object whose fields may be missing.

    What the address itself says wins over the entry's own fields, since it is
    the more specific of two answers written by the same person. An entry with
    no address is dropped rather than kept as a row that can only fail.
    """
    if isinstance(raw, str):
        raw = {"host": raw}
    if not isinstance(raw, dict):
        return {}
    given = split_address(raw.get("host", raw.get("address", "")))
    host = given["host"]
    if not host:
        return {}
    name = " ".join(str(raw.get("name") or "").split())
    stored = str(raw.get("icon") or "").strip()
    key = str(raw.get("key_file", raw.get("key", "")) or "").strip()
    return {
        "name": name or host,
        "host": host,
        "port": port_of(given["port"], 0) or port_of(raw.get("port"), 0)
                or port_of(config("default_port", 22), 22),
        "username": (given["username"] or str(raw.get("username") or "").strip()
                     or str(config("default_user", "root") or "")),
        "key_file": key,
        "icon": stored,
    }


def entries_for(raw: Any) -> list:
    """The config file's list as the addon uses it; anything unreadable is left
    out, so one broken entry does not hide the machines beside it."""
    items = raw if isinstance(raw, (list, tuple)) else []
    out: list[dict] = []
    for item in items:
        entry = normalise_entry(item)
        if entry:
            out.append(entry)
    return out


def unique_caption(wanted: Any, taken: list) -> str:
    """A tab caption no open session is using: ``Router``, then ``Router 2``.

    A page is closed by its caption, so two sessions wanting the same name have
    to be told apart - otherwise dropping one would take the other away.
    """
    base = " ".join(str(wanted or "").split()) or "Session"
    if base not in taken:
        return base
    step = 2
    while f"{base} {step}" in taken:
        step += 1
    return f"{base} {step}"


def icon_name(name: Any, suffix: str = "") -> str:
    """The file a copied picture is kept under: the entry's name, cleaned far
    enough to be a file name, keeping the ending it arrived with."""
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "")).strip("._-")[:40]
    tail = str(suffix or "").lower()
    return f"{stem or 'entry'}{tail}" if tail in PICTURE_SUFFIXES \
        else f"{stem or 'entry'}.png"


def session_spec(identifier: str, who: str) -> dict:
    """One sub-page: a line of its own, then the shell under it.

    Both buttons live here rather than in the tab's header, because a session
    is disconnected on its own - and the Connect button is what a dead-but-kept
    page is for (see ``close_on_disconnect``).
    """
    return {
        "kind": "column",
        "children": [
            {"kind": "row", "children": [
                {"kind": "label", "id": f"{identifier}_who", "text": who},
                {"kind": "spacer"},
                {"kind": "label", "id": f"{identifier}_state",
                 "text": "opening..."},
                {"kind": "button", "id": f"{identifier}_conn", "text": "Connect",
                 "on": ["clicked"], "enabled": False,
                 "tooltip": "Bring this shell's connection back up"},
                {"kind": "button", "id": f"{identifier}_disc", "text": "Disconnect",
                 "on": ["clicked"],
                 "tooltip": "Drop the connection; the tab closes with it unless "
                            "that is switched off in this addon's config"},
            ]},
            {"kind": "terminal", "id": identifier,
             "on": ["ready", "closed", "failed"]},
        ],
    }


# --- the config file, read and written ---

def config(key: str, default: Any = None) -> Any:
    """One key of ``addons/_data/multi_ssh/config.json``; the default whenever
    there is no api yet, which is how the helpers above stay testable alone."""
    if api is None:
        return default
    try:
        value = api.config.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def reload_entries() -> list:
    """Read the quickstart list from the file and redraw the list beside it."""
    global entries, current
    entries = entries_for(config("quickstart", []))
    current = -1
    draw_list()
    note("")
    return entries


def write_entries(items: list) -> None:
    """Save the quickstart list, so the next Berry Manager sees what this one
    added - and the file stays the plain JSON an editor can change."""
    global entries, current
    entries = [entry for entry in (items or []) if entry]
    if api is not None:
        api.config.set("quickstart", [dict(entry) for entry in entries])
    current = -1
    draw_list()


def draw_list() -> None:
    """Put every entry in the list, each wearing its own picture."""
    if api is None:
        return
    api.tabs.set_widget(TAB, "qs_list", "items", [
        {"text": str(entry.get("name") or entry.get("host")),
         "icon": icon_file(entry)}
        for entry in entries])


def note(text: str) -> None:
    """The line under the list: what the entry under the cursor really means."""
    if api is None:
        return
    body = str(text or "")
    if not body and 0 <= current < len(entries):
        entry = entries[current]
        body = (f"{entry.get('username') or '?'}@{entry.get('host')}"
                f":{entry.get('port')}")
        if entry.get("key_file"):
            body += " (key file)"
    api.tabs.set_widget(TAB, "qs_note", "text", body)


# --- a quickstart entry's picture, kept in the addon's own folder ---

def icon_folder() -> Path:
    """``addons/_data/multi_ssh/icons``, made on the way."""
    folder = Path(api.paths.data) / ICON_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def icon_file(entry: dict) -> str:
    """The path of one entry's picture, or nothing when it has none.

    The name in the file is stored on purpose, not the address it was copied
    from: the picture lives beside the config, so moving the whole data folder
    keeps every entry wearing what it wears now.
    """
    stored = str((entry or {}).get("icon") or "").strip()
    if not stored or api is None:
        return ""
    try:
        found = icon_folder() / stored
    except (OSError, ValueError, AddonError):
        return ""
    return str(found) if found.is_file() else ""


def store_icon(source: str, name: Any) -> str:
    """Copy a picked picture in and say what it is called; ``""`` when nothing
    could be copied, which leaves an entry with no picture rather than a
    broken one."""
    if api is None:
        return ""
    try:
        origin = Path(str(source))
        if not origin.is_file():
            return ""
        target = icon_folder() / icon_name(name, origin.suffix)
        shutil.copy2(origin, target)
    except (OSError, ValueError, AddonError):
        return ""
    return target.name


# --- sessions ---

def pages() -> list:
    """The captions of the sub-pages the tab is holding, left to right."""
    if api is None:
        return []
    try:
        return list(api.tabs.widget_value(TAB, SESSIONS, "pages") or [])
    except Exception:
        return []


def clear_placeholder() -> None:
    """Take the standing-in page away, now that a real session has arrived."""
    if api is not None and EMPTY_CAPTION in pages():
        api.tabs.set_widget(TAB, SESSIONS, "remove_page", EMPTY_CAPTION)


def restore_placeholder() -> None:
    """An empty row of tabs looks broken, so say what it means instead."""
    if api is None or pages():
        return
    api.tabs.set_widget(TAB, SESSIONS, "add_page", {
        "label": EMPTY_CAPTION,
        "spec": {"kind": "column", "children": [
            {"kind": "label", "text": "No session is open.\n"},
            {"kind": "label", "word_wrap": True, "expand": True,
             "text": "Select a quickstart entry and press Connect, or press "
                     "New to reach a machine that is not on the list."},
        ]},
    })


def status() -> None:
    """One line saying how many sessions there are and how many can be typed
    into - the count a broadcast is about to use."""
    if api is None:
        return
    live = [tid for tid, session in sessions.items() if session.get("connected")]
    if not sessions:
        api.tabs.set_widget(TAB, "qs_status", "text", "No session is open.")
    else:
        api.tabs.set_widget(
            TAB, "qs_status", "text",
            f"{len(sessions)} session(s), {len(live)} connected")


def mark(identifier: str, connected: bool, saying: str = "") -> None:
    """One session's state line and its two buttons, from what its shell is
    doing - never from what a connect call hoped for."""
    session = sessions.get(identifier)
    if session is None:
        return
    session["connected"] = bool(connected)
    if not connected and saying:
        session["error"] = str(saying)
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_state", "text",
                            "connected" if connected
                            else (session.get("error") or "disconnected"))
        api.tabs.set_widget(TAB, f"{identifier}_conn", "enabled", not connected)
        api.tabs.set_widget(TAB, f"{identifier}_disc", "enabled", connected)
    status()


def open_session(entry: dict, password: Any = None) -> str:
    """One more sub-page, and the connection that runs it.

    Returns the terminal's id, or ``""`` when the person walked out of the
    password box - which is the only failure that happens before a page exists,
    and the only one that leaves nothing behind to tidy up.
    """
    global _seq
    if api is None:
        return ""
    host = str((entry or {}).get("host") or "").strip()
    if not host:
        api.ui.error(TAB, "That entry has no address to connect to.")
        return ""
    user = str((entry or {}).get("username") or "")
    port = port_of((entry or {}).get("port"), config("default_port", 22))
    if password is None:
        password = api.ui.input("Connect", f"Password for {user}@{host}:{port}",
                               "", secret=True)
        if password is None:
            return ""                    # cancelled: nothing was opened
    _seq += 1
    identifier = f"sh{_seq}"
    caption = unique_caption((entry or {}).get("name") or host, pages())
    clear_placeholder()
    api.tabs.set_widget(TAB, SESSIONS, "add_page", {
        "label": caption, "spec": session_spec(identifier, f"{user}@{host}:{port}")})
    sessions[identifier] = {"caption": caption, "host": host, "port": port,
                            "username": user, "connected": False, "error": ""}
    connect(identifier, entry, str(password or ""))
    status()
    return identifier


def connect(identifier: str, entry: dict, password: str) -> None:
    """Hand one terminal the machine it belongs on. The password goes here and
    nowhere else: not into the config file, not into the session record."""
    api.tabs.set_widget(TAB, identifier, "connect", {
        "host": str(entry.get("host") or ""),
        "port": port_of(entry.get("port"), config("default_port", 22)),
        "username": str(entry.get("username") or ""),
        "password": str(password or ""),
        "key_file": str(entry.get("key_file") or ""),
        "trust_unknown_host": bool(config("trust_unknown_host", True)),
    })


def reconnect(identifier: str) -> None:
    """Try again on a page that is still there: the same details, asked for
    again, because this addon never keeps a password."""
    session = sessions.get(identifier)
    if session is None or api is None:
        return
    who = f"{session['username']}@{session['host']}:{session['port']}"
    password = api.ui.input("Connect", f"Password for {who}", "", secret=True)
    if password is None:
        return
    entry = {"host": session["host"], "port": session["port"],
             "username": session["username"],
             "key_file": session.get("key_file", "")}
    session["error"] = ""
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_state", "text", "opening...")
    connect(identifier, entry, password)


def drop_session(identifier: str) -> None:
    """Take a session's page away and forget it, and mind the row of tabs when
    that was the last one."""
    session = sessions.pop(identifier, None)
    if session is None or api is None:
        return
    api.tabs.set_widget(TAB, SESSIONS, "remove_page", session.get("caption"))
    restore_placeholder()
    status()


def connected_ids() -> list:
    """Every session a broadcast can type into - those its shell reported up,
    which is the only saying of it this addon trusts."""
    return [tid for tid, session in sessions.items() if session.get("connected")]


def broadcast(text: Any = "") -> None:
    """One line, typed into every session that is up.

    A shell that has in fact gone answers by raising, so the count that comes
    back may be smaller than the number of pages open - and says so, because a
    command that quietly reached two of five routers is worse than none.
    """
    if api is None:
        return
    line = " ".join(str(text or "").split())
    if not line:
        line = str(api.tabs.widget_value(TAB, "bc_cmd", "text") or "").strip()
    if not line:
        api.ui.notify("Type the command to send first.")
        return
    targets = connected_ids()
    if not targets:
        api.ui.notify("No session is connected, so nothing was sent.")
        return
    sent, lost = 0, []
    for identifier in targets:
        try:
            api.tabs.set_widget(TAB, identifier, "command", line)
            sent += 1
        except Exception:
            lost.append(identifier)
    for identifier in lost:
        mark(identifier, False, "connection lost")
    if lost:
        api.ui.notify(f"Sent to {sent} session(s); "
                      f"{len(lost)} could not be reached.")
    else:
        api.ui.notify(f"Sent to {sent} session(s).")


# --- the entry editor ---

def entry_spec(entry: dict | None) -> dict:
    """The form an entry is added or changed with, filled in from ``entry``."""
    record = entry or {}
    return {
        "kind": "form",
        "children": [
            {"label": "Name", "kind": "line", "id": "name",
             "value": str(record.get("name") or ""), "placeholder": "Router"},
            {"label": "Address", "kind": "line", "id": "host",
             "value": str(record.get("host") or ""),
             "placeholder": "192.168.1.1, or root@192.168.1.1:2222",
             "tooltip": "A user and a port written here are used for this "
                        "entry, whatever the two fields below say"},
            {"label": "Port", "kind": "spin", "id": "port",
             "value": port_of(record.get("port"), config("default_port", 22)),
             "minimum": 1, "maximum": 65535},
            {"label": "Username", "kind": "line", "id": "username",
             "value": str(record.get("username") or ""),
             "placeholder": str(config("default_user", "root") or "")},
            {"label": "Key file", "kind": "line", "id": "key_file",
             "value": str(record.get("key_file") or ""),
             "placeholder": "optional; blank asks for a password",
             "tooltip": "A private key on this machine. A password is still "
                        "asked for when this does not open the door."},
        ],
    }


def asked_entry(values: dict, keeps: dict | None = None) -> dict:
    """The form's answers as an entry, keeping what the form never shows - the
    picture, and the address of the entry being edited when a blank is left."""
    record = dict(keeps or {})
    form = values or {}
    address = str(form.get("host") or record.get("host") or "").strip()
    record.update({
        "name": " ".join(str(form.get("name") or "").split()),
        "host": address,
        "port": port_of(form.get("port"), config("default_port", 22)),
        "username": str(form.get("username") or "").strip(),
        "key_file": str(form.get("key_file") or "").strip(),
    })
    record.setdefault("icon", "")
    return normalise_entry(record)


def edit_entry(row: int) -> None:
    """Add one entry, or change the one under the cursor, and save the file."""
    if api is None:
        return
    keeps = dict(entries[row]) if 0 <= row < len(entries) else None
    values = api.ui.dialog(entry_spec(keeps),
                           title="Edit quickstart entry" if keeps
                           else "New quickstart entry")
    if values is None:
        return
    entry = asked_entry(values, keeps)
    if not entry:
        api.ui.warning("Multi SSH", "An entry needs an address to connect to.")
        return
    updated = list(entries)
    if 0 <= row < len(updated):
        updated[row] = entry
    else:
        updated.append(entry)
    write_entries(updated)
    api.ui.notify(f"{entry['name']} is in the quickstart list.")


def remove_entry(row: int) -> None:
    """Drop one entry from the file. Sessions already open are left alone -
    they were opened from a copy of the details, and closing a router's tab by
    deleting its bookmark is not what anyone means."""
    if api is None:
        return
    picked = picked_row(row)
    if picked < 0:
        api.ui.notify("Select the entry to remove first.")
        return
    entry = entries[picked]
    if not api.ui.question("Remove entry",
                           f"Take {entry.get('name')} out of the quickstart "
                           f"list? Its picture stays in the addon's folder."):
        return
    updated = list(entries)
    gone = updated.pop(picked)
    if gone.get("icon"):
        api.log(f"{gone.get('name')} left, its picture kept beside the config")
    write_entries(updated)


def choose_icon(row: int) -> None:
    """Give the entry under the cursor a picture: any file on this machine,
    copied into ``addons/_data/multi_ssh/icons`` so it stays with the entry.

    An entry that already has one is asked first, because picking a file to
    take a picture away is a strange errand; saying no to that box is the way
    to change it instead.
    """
    if api is None:
        return
    picked = picked_row(row)
    if picked < 0:
        api.ui.notify("Select the entry to give a picture first.")
        return
    entry = dict(entries[picked])
    if entry.get("icon") and api.ui.question(
            "Multi SSH", f"{entry.get('name')} already wears a picture.\n"
                         "Take it off instead of choosing another one?"):
        entry["icon"] = ""
        entries[picked] = entry
        write_entries(entries)
        return
    start = str(icon_file(entry) or "")
    source = api.ui.pick_file(
        "Choose a picture", "open",
        "Pictures (*.png *.ico *.svg *.jpg *.jpeg *.webp)", start)
    if not source:
        return
    stored = store_icon(source, entry.get("name") or entry.get("host"))
    if not stored:
        api.ui.error("Multi SSH", "That file could not be copied into the "
                                  "addon's folder.")
        return
    entry["icon"] = stored
    entries[picked] = entry
    write_entries(entries)


def new_session() -> None:
    """A machine that is not worth a bookmark: the same form, but nothing is
    saved and only a session comes out of it."""
    if api is None:
        return
    values = api.ui.dialog(entry_spec(None), title="Connect once")
    if values is None:
        return
    entry = asked_entry(values)
    if not entry:
        api.ui.warning("Multi SSH", "There is no address to connect to.")
        return
    open_session(entry)


def picked_row(row: int) -> int:
    """The entry under the cursor, taken from the list rather than remembered
    when a click elsewhere has already moved it."""
    if 0 <= row < len(entries):
        return row
    if api is None:
        return -1
    found = api.tabs.widget_value(TAB, "qs_list", "selected") or []
    names = [str(entry.get("name") or entry.get("host")) for entry in entries]
    for text in found:
        if text in names:
            return names.index(text)
    return -1


def connect_row(row: int) -> str:
    """Connect the entry under the cursor - the one click, and the password.

    The id of the session that came out of it, or ``""`` when nothing was
    opened, so a caller can tell the two apart without reading the tab back.
    """
    picked = picked_row(row)
    if picked < 0:
        if api is not None:
            api.ui.notify("Select a quickstart entry first.")
        return ""
    return open_session(entries[picked])


# --- what the tab says back ---

def owner_of(identifier: str) -> str:
    """The session a control belongs to: a terminal answers with its own id,
    and a button on its page with the same id in front of ``_conn``."""
    body = str(identifier or "")
    for tail in ("_conn", "_disc", "_state", "_who"):
        if body.endswith(tail):
            return body[:-len(tail)]
    return body


def terminal_news(identifier: str, event: str, payload: dict) -> None:
    """What a shell reports about itself: up, refused, or gone.

    The screen gets these words too - the app writes them on it - so this only
    has to keep the tab's buttons and the count in step with it, and mind the
    page when the config says a dropped link means a dropped tab.
    """
    session = sessions.get(identifier)
    if session is None or api is None:
        return
    if event == "ready":
        session["error"] = ""
        mark(identifier, True)
        return
    if event == "failed":
        session["error"] = str(payload.get("error") or "the connection failed")
        mark(identifier, False, session["error"])
        return
    if event == "closed":
        caption = str(session.get("caption") or "Session")
        reason = str(session.get("error") or "connection closed")
        mark(identifier, False)
        if bool(config("close_on_disconnect", True)):
            drop_session(identifier)
            api.ui.notify(f"{caption}: {reason}")
        return
    if event in ("bell", "title"):
        return


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Every click, Enter and selection this tab can produce."""
    if api is None or str(tab) != TAB:
        return
    payload = payload or {}
    body = str(identifier or "")
    owner = owner_of(body)
    if owner in sessions:
        if body != owner:
            # A button on one session's own page.
            if event != "clicked":
                return
            if body.endswith("_conn"):
                reconnect(owner)
            else:
                api.tabs.set_widget(TAB, owner, "disconnect", None)
            return
        terminal_news(owner, event, payload)
        return
    if body == "qs_list" and event == "selected":
        rows = [int(row) for row in (payload.get("rows") or [])
                if isinstance(row, int) or str(row).lstrip("-").isdigit()]
        global current
        current = rows[0] if rows else -1
        note("")
        return
    if event not in ("clicked", "returned"):
        return
    if body == "bc_send" and event == "clicked":
        broadcast()
    elif body == "bc_cmd" and event == "returned":
        broadcast(payload.get("text", ""))
    elif body == "qs_connect":
        connect_row(current)
    elif body == "qs_new":
        new_session()
    elif body == "qs_add":
        edit_entry(-1)
    elif body == "qs_edit":
        edit_entry(current)
    elif body == "qs_icon":
        choose_icon(current)
    elif body == "qs_remove":
        remove_entry(current)
    elif body == "qs_reload":
        reload_entries()
