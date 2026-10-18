#!/usr/bin/env python3
"""BerryVM: a virtual desktop in one tab, carrying five little programs.

The tab is a desktop - a plain area holding free-floating windows the person
drags by their title bar, sizes from a corner and shuts with a button. Windows
are opened from a Start menu and are shown, one per window, on a taskbar under
the desktop. Five programs ship with the addon:

* **Main SSH** - a terminal to the profile's host, on its own socket;
* **SFTP view** - a read-only listing of the app's remote root, over the
  main connection;
* **Local disk** - a browser of this computer's folders;
* **Machines** - SSH to any other host, one window per connection;
* **Files** - the addon's own home, laid out like one: ``Desktop``, ``bin``,
  ``tmp``, ``config``.

Text files in any of those windows open in *Berrypad*, the addon's editor: a
window per file, syntax matched to the suffix, and a Save button that writes
back to wherever the file came from - local disk, the VM's home, or the
device over SFTP. Binaries are refused with a saying of what they are, and a
file larger than a stated cap is refused for the same reason, so opening a
megabyte of firmware never pretends to be an editor.

The manifest says ``"connection": "both"`` because the two kinds of window are
really two kinds of link: the SFTP view rides the app's session (and says so
when that session is down), while every terminal brings its own socket. The
addon's data folder is the VM's home; ``config/settings.json`` is a copy of
what the addon's own settings hold, so the ``Files`` window can show it as a
file - passwords are kept out of that copy, the same bargain Multi SSH makes.
"""

from __future__ import annotations

import json
import posixpath
from datetime import datetime
from pathlib import Path
from typing import Any

from addon_common import AddonError, find_addon_picture

ADDON = {
    "name": "BerryVM",
    "version": "1.0",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh", "sftp"],
    # The desktop's two kinds of link really are two: the SFTP view reads the
    # app's open session, and every shell brings its own socket. So the
    # manifest says "both" and the store's own-connection switch is offered.
    "connection": "both",
    "description": "A virtual desktop in one tab: free-floating, draggable "
                   "windows for Main SSH, an SFTP view, the local disk, other "
                   "machines and the addon's own home folder, with text files "
                   "opened in Berrypad - an editor per file that writes back "
                   "where the file came from.",
}

#: The tab's mark when the assets folder carries no picture: a monitor wearing
#: a small floating window, since a desktop of windows is the whole idea.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="1.5" y="3" width="21" height="14" rx="1.5" fill="#1e1e1e"'
    ' stroke="#c51a4a" stroke-width="1.2"/>'
    '<path d="M9 20.5h6M12 17v3.5" stroke="#7a2030" stroke-width="1.2"'
    ' stroke-linecap="round"/>'
    '<rect x="4.5" y="6" width="9" height="6" rx="0.8" fill="#2c2c2c"'
    ' stroke="#ffb020" stroke-width="0.9"/>'
    '<path d="M4.5 7.7h9" stroke="#ffb020" stroke-width="0.7"/>'
    '<path d="M6.2 9.3l1.2 1.1-1.2 1.1" fill="none" stroke="#23d18b"'
    ' stroke-width="0.9" stroke-linecap="round"/>'
    '</svg>'
)

#: The name the store's picture is expected to be filed under: the id and then
#: ``icon.png``, the rule every shipped addon follows.
TAB_ICON_FILE = "berryvmicon.png"

# --- the tab's own ids ---

TAB = "BerryVM"
DESKTOP = "desktop"           # the ``windows`` canvas
TASKBAR = "taskbar"           # the list of open windows
START_BTN = "start_btn"       # the Start menu
CLOSE_BTN = "close_win"       # taskbar button shutting the picked window
CLOCK = "clock"               # the taskbar's own time

# --- the programs' window ids (single-instance each) ---

P_MAIN = "main_ssh"
P_SFTP = "sftp_view"
P_LOCAL = "local_disk"
P_MACHINE = "machines"
P_FILES = "files"

#: The Start menu's list: an id and a caption per program, in that order.
PROGRAMS: list[tuple[str, str]] = [
    (P_MAIN, "Main SSH"),
    (P_SFTP, "SFTP view"),
    (P_LOCAL, "Local disk"),
    (P_MACHINE, "Machines"),
    (P_FILES, "Files"),
]

CAPTIONS: dict[str, str] = dict(PROGRAMS)

#: A machine opened from the ``Machines`` window gets its own terminal window;
#: ``msh<seq>`` names it, and the counter never runs backwards.
MACHINE_PREFIX = "msh"

#: Berrypad is one window per file; ``bp<seq>`` names it.
BERRY_PREFIX = "bp"

# --- the VM's home layout ---

#: Folder names created inside ``addons/_data/berryvm`` on first init: this is
#: the shape a home directory is expected to have, kept small and readable.
HOME_DIRS = ("Desktop", "bin", "tmp", "config")

#: The settings file that mirrors the addon's own config into the ``config``
#: folder, so the ``Files`` window can open it in Berrypad.
SETTINGS_NAME = "settings.json"

#: Berrypad will not read or write a file bigger than this. Half a megabyte is
#: far past what any of the shipped views can usefully edit and small enough
#: that a stray ``cat`` of a firmware blob cannot freeze the tab.
MAX_SIZE = 512 * 1024

#: Endings Berrypad treats as text at all; anything else is a binary as far as
#: the editor is concerned and gets a saying of what it is.
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".json", ".yaml", ".yml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".ksh", ".py", ".js", ".ts", ".html", ".htm",
    ".xml", ".css", ".svg", ".csv", ".tsv", ".log", ".editorconfig",
    ".service", ".theme", ".list", ".sources", ".repo", ".gitignore",
})

#: Suffix to the language ``multi``'s highlighter knows. Unlisted text
#: endings still open, just without colour.
LANG_BY_SUFFIX = {
    ".py": "python", ".json": "json", ".theme": "json",
    ".js": "javascript", ".ts": "javascript",
    ".html": "html", ".htm": "html", ".xml": "html", ".svg": "html",
    ".css": "css",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".editorconfig": "ini",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ksh": "bash",
    ".list": "bash", ".sources": "bash",
}

# --- what can be configured, and its defaults ---

FIELDS = [
    {"key": "window_width", "label": "Default window width", "kind": "number",
     "minimum": 240, "maximum": 2400,
     "hint": "The size a program window starts at, before the person drags it"},
    {"key": "window_height", "label": "Default window height", "kind": "number",
     "minimum": 180, "maximum": 1600},
    {"key": "clock_on", "label": "Show the clock in the taskbar", "kind": "bool",
     "hint": "Off, the taskbar loses its time and the timer stops; the desktop "
             "does not care either way"},
    {"key": "default_user", "label": "Username to offer", "kind": "text",
     "hint": "Pre-filled for a machine that names none of its own"},
    {"key": "default_port", "label": "Port to offer", "kind": "number",
     "minimum": 1, "maximum": 65535},
    {"key": "trust_unknown_host", "label": "Accept a host key nobody has seen",
     "kind": "bool",
     "hint": "The same bargain Multi SSH makes: a machine whose key is not in "
             "your known_hosts file is refused when this is off"},
]

#: ``machines`` is in the file but named in no field: the app's form writes
#: only what it shows, so a key it never showed survives the save unchanged -
#: which is what lets ``Machines`` add and drop entries on its own.
DEFAULTS = {
    "window_width": 520,
    "window_height": 360,
    "clock_on": True,
    "default_user": "root",
    "default_port": 22,
    "trust_unknown_host": True,
    "machines": [],
}

# --- the tab's spec ---

#: The whole tab: a desktop of floating windows over a taskbar. The desktop
#: opts in to every window event so the taskbar can follow it, and the
#: taskbar's list and buttons route back through :func:`on_widget`.
TAB_SPEC = {
    "kind": "column",
    "children": [
        {"kind": "windows", "id": DESKTOP, "expand": True,
         "on": ["activated", "closed", "moved", "resized"],
         "children": []},
        {"kind": "row", "children": [
            {"kind": "button", "id": START_BTN, "text": "Start",
             "on": ["clicked"],
             "tooltip": "Open one of BerryVM's programs; a program already "
                        "open is raised rather than started again"},
            {"kind": "list", "id": TASKBAR, "on": ["selected"],
             "expand": True,
             "tooltip": "One line per window on the desktop. Select one and "
                        "it comes forward; press Close to shut it."},
            {"kind": "button", "id": CLOSE_BTN, "text": "Close",
             "on": ["clicked"],
             "tooltip": "Shut the window picked in the taskbar"},
            {"kind": "label", "id": CLOCK, "text": ""},
        ]},
    ],
}

# --- module state ---

api = None                     # filled in by init()
machines: list = []            # the Machines quickstart list, normalised
machine_row: int = -1          # the row the Machines list has under the cursor
machine_seq: int = 0           # ``msh<seq>`` ids, so they cannot be reused
berry_seq: int = 0             # ``bp<seq>`` ids, one editor per file
opened: dict = {}              # win_id -> {"title", "kind", extra}
berrypads: dict = {}           # win_id -> {"path", "origin", "title"}
terminals: dict = {}           # terminal id -> win_id it lives inside
home_ready: bool = False       # ``_ensure_home()`` has run in this process
local_path: str = ""           # the folder the Local-disk window is showing
files_path: str = ""           # the folder inside the VM home Files is showing
sftp_path: str = ""            # the folder inside the remote root SFTP shows


def init(plugin_api: Any) -> None:
    """Declare the config, lay out the desktop, and start the clock.

    ``home_ready`` is ``_ensure_home()``'s own business - this function only
    reaches it through that call, so it is not named in the ``global`` here."""
    global api
    api = plugin_api
    api.config.declare(fields=FIELDS, defaults=DEFAULTS, title="BerryVM")
    _ensure_home()
    mirror_settings()
    api.tabs.add_tab(TAB, spec=TAB_SPEC)
    api.tabs.set_icon(TAB, tab_icon())
    reload_machines()
    refresh_taskbar()
    if bool(config("clock_on", True)):
        api.schedule.every(30, tick_clock)
        tick_clock()
    api.log("BerryVM ready: a virtual desktop in one tab.")


def shutdown() -> None:
    """Close the tab; the app stops any shell inside on its way out."""
    global home_ready, machine_seq, berry_seq
    opened.clear()
    berrypads.clear()
    terminals.clear()
    machines.clear()
    machine_seq = 0
    berry_seq = 0
    home_ready = False
    if api is not None:
        api.tabs.close(TAB)


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else
    the drawn mark - the same rule every other store addon follows."""
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):
        paths = None
    return find_addon_picture(paths, "berryvm") or TAB_ICON


# --- small pure helpers, so the shapes above can be read without a window ---

def config(key: str, default: Any = None) -> Any:
    """One key of the addon's own settings file, or ``default`` when nothing
    could be read - so the helpers below stay usable before ``init`` has run."""
    if api is None:
        return default
    try:
        value = api.config.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def port_of(value: Any, default: int = 22) -> int:
    """A port number, or ``default``: a hand-typed file is allowed to hold
    ``"22"``, a blank, or ``0``, and none of those is worth an exception."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return number if 1 <= number <= 65535 else int(default)


def split_address(text: Any) -> dict:
    """``root@192.168.1.1:2222`` cut into the parts a connection needs, the
    same rule Multi SSH applies."""
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


def normalise_machine(raw: Any) -> dict:
    """One machine entry in the shape ``Machines`` uses: a bare address, or an
    object whose fields may be missing. An entry with no address is dropped."""
    if isinstance(raw, str):
        raw = {"host": raw}
    if not isinstance(raw, dict):
        return {}
    given = split_address(raw.get("host", raw.get("address", "")))
    host = given["host"]
    if not host:
        return {}
    name = " ".join(str(raw.get("name") or "").split())
    key = str(raw.get("key_file", raw.get("key", "")) or "").strip()
    return {
        "name": name or host,
        "host": host,
        "port": port_of(given["port"], 0) or port_of(raw.get("port"), 0)
                or port_of(config("default_port", 22), 22),
        "username": (given["username"] or str(raw.get("username") or "").strip()
                     or str(config("default_user", "root") or "")),
        "key_file": key,
    }


def machines_from(raw: Any) -> list:
    """The config's ``machines`` list as the addon uses it; a broken entry is
    left out rather than hiding the machines beside it."""
    items = raw if isinstance(raw, (list, tuple)) else []
    out: list[dict] = []
    for item in items:
        entry = normalise_machine(item)
        if entry:
            out.append(entry)
    return out


def language_for(path: Any) -> str:
    """The language Berrypad shows this file in, from its suffix; ``""`` is
    plain text, which is what an unknown ending is."""
    suffix = Path(str(path or "")).suffix.lower()
    return LANG_BY_SUFFIX.get(suffix, "")


def is_text(path: Any) -> bool:
    """Whether this name looks like something Berrypad could open."""
    suffix = Path(str(path or "")).suffix.lower()
    return suffix in TEXT_SUFFIXES


def short_name(path: Any) -> str:
    """The last piece of a path, for a title bar that has room for one word."""
    text = str(path or "").rstrip("/\\")
    if not text:
        return ""
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def window_size() -> tuple[int, int]:
    """The default width and height, from the config, kept inside the range
    the form offers."""
    width = int(config("window_width", DEFAULTS["window_width"]))
    height = int(config("window_height", DEFAULTS["window_height"]))
    return (max(240, min(2400, width)), max(180, min(1600, height)))


def describe_size(size: Any) -> str:
    """A byte count as the note beside a refused file likes to say it."""
    try:
        number = int(size)
    except (TypeError, ValueError):
        return "an unknown amount"
    for unit, div in (("KiB", 1024), ("MiB", 1024 * 1024),
                      ("GiB", 1024 * 1024 * 1024)):
        if number < div * 1024:
            return f"{number / div:.1f} {unit}"
    return f"{number / (1024 ** 3):.1f} GiB"


# --- the VM's home folder ---

def home_root() -> Path:
    """``addons/_data/berryvm``, the VM's root."""
    return Path(api.paths.data)


def _ensure_home() -> Path:
    """Create the VM's home layout once, and only once, per running addon.

    ``api.paths.data`` has already made the folder itself; what is added here
    is the shape inside it - ``Desktop``, ``bin``, ``tmp``, ``config`` - so
    the Files program has something to show and the settings mirror has a
    place to sit. A second call is a no-op rather than a repeat mkdir.
    """
    global home_ready
    root = home_root()
    if home_ready:
        return root
    for name in HOME_DIRS:
        try:
            (root / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    home_ready = True
    return root


def settings_file() -> Path:
    """Where the mirror of the addon's own settings lives inside the VM."""
    return home_root() / "config" / SETTINGS_NAME


def mirror_settings() -> dict:
    """Write the addon's config into ``config/settings.json`` and hand back
    what landed.

    The file is read first so a key neither the form nor ``DEFAULTS`` knows
    survives unchanged, then the named keys are put over it and the whole is
    written. No password goes into this file: ``DEFAULTS`` names none, and
    anything the user has added outside the app's own schema is left exactly
    as it was found.
    """
    stored: dict = {}
    target = settings_file()
    try:
        if target.is_file():
            loaded = json.loads(target.read_text() or "{}")
            if isinstance(loaded, dict):
                stored = loaded
    except (OSError, ValueError):
        stored = {}
    for key, fallback in DEFAULTS.items():
        stored[key] = config(key, fallback)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(stored, indent=2, sort_keys=True))
    except OSError as error:
        if api is not None:
            api.log(f"berryvm: settings.json not written: {error}")
    return stored


def read_settings() -> dict:
    """The mirror file, or an empty dict when it is missing or unreadable."""
    try:
        loaded = json.loads(settings_file().read_text() or "{}")
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


# --- the taskbar and the clock ---

def tick_clock() -> None:
    """Put the current time on the taskbar; a clock turned off in the config
    writes an empty string rather than hiding the label."""
    if api is None:
        return
    if not bool(config("clock_on", True)):
        api.tabs.set_widget(TAB, CLOCK, "text", "")
        return
    api.tabs.set_widget(TAB, CLOCK, "text", datetime.now().strftime("%H:%M"))


def open_window_ids() -> list:
    """The ids the desktop is holding, in the order it opened them."""
    if api is None:
        return []
    try:
        return list(api.tabs.widget_value(TAB, DESKTOP, "windows") or [])
    except Exception:
        return []


def caption_for(win_id: str) -> str:
    """What the taskbar line for one window says: the opened record's title,
    or a program caption when the id is one of the five that ship."""
    record = opened.get(win_id)
    if record is not None:
        return str(record.get("title") or CAPTIONS.get(win_id, win_id))
    if win_id in CAPTIONS:
        return CAPTIONS[win_id]
    return win_id


def refresh_taskbar() -> None:
    """Redraw the list of open windows from what the desktop reports."""
    if api is None:
        return
    api.tabs.set_widget(TAB, TASKBAR, "items",
                        [{"text": caption_for(win_id)}
                         for win_id in open_window_ids()])


def picked_window() -> str:
    """The window the taskbar's list is on: the picked caption mapped back
    through the desktop's own order, so a rename never points at the wrong
    frame."""
    if api is None:
        return ""
    try:
        found = api.tabs.widget_value(TAB, TASKBAR, "selected") or []
    except Exception:
        return ""
    texts = [caption_for(win_id) for win_id in open_window_ids()]
    for item in found:
        text = str(item or "")
        if text in texts:
            return open_window_ids()[texts.index(text)]
    return ""


# --- the desktop's own calls ---

def open_window(entry: dict) -> None:
    """Hand the desktop a window descriptor; opening an id that is already
    there is what the primitive already handles - the frame is raised, not
    rebuilt - so a running shell keeps its scrollback."""
    api.tabs.set_widget(TAB, DESKTOP, "open_window", entry)


def close_window(win_id: str) -> None:
    """Take one window off the desktop by its id."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "close_window", str(win_id))


def raise_window(win_id: str) -> None:
    """Bring one window to the front and show it if it had been hidden."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "raise_window", str(win_id))


def hide_window(win_id: str) -> None:
    """Put a window out of sight without losing what it holds: the taskbar's
    raise is how it comes back."""
    if api is not None and win_id:
        api.tabs.set_widget(TAB, win_id, "visible", False)


def forget_window(win_id: str) -> None:
    """Drop every bit of addon-side state that a closed window owned."""
    opened.pop(str(win_id), None)
    berrypads.pop(str(win_id), None)
    for term_id in [tid for tid, owner in terminals.items()
                    if str(owner) == str(win_id)]:
        terminals.pop(term_id, None)


def start_program(win_id: str) -> None:
    """Dispatch on the id a Start-menu row named; each program is the same
    single call, so a raised window and a fresh one look the same to the
    taskbar."""
    if api is None:
        return
    if win_id == P_MAIN:
        open_main_ssh()
    elif win_id == P_SFTP:
        open_sftp_view()
    elif win_id == P_LOCAL:
        open_local_disk()
    elif win_id == P_MACHINE:
        open_machines()
    elif win_id == P_FILES:
        open_files()
    else:
        api.ui.notify(f"BerryVM has no program {win_id!r}.")


def start_menu() -> None:
    """The Start button's own menu: every program it can bring up."""
    if api is None:
        return
    labels = [CAPTIONS[pid] for pid, _cap in PROGRAMS]
    picked = api.ui.choose(labels)
    if picked is None:
        return
    for pid, caption in PROGRAMS:
        if caption == picked:
            start_program(pid)
            return


# --- program windows: shared bits ---

def _program_entry(win_id: str, title: str, spec: dict) -> dict:
    """One of the five programs, wrapped as a window descriptor the primitive
    opens: default size from the config, no explicit position so the desktop
    cascades them."""
    width, height = window_size()
    return {"id": win_id, "title": title, "width": width, "height": height,
            "spec": spec}


def _header(identifier: str, text: str, extra: list | None = None) -> dict:
    """The line above every program's list: what it is looking at, and the
    buttons that only make sense beside that path."""
    row: list[dict] = [
        {"kind": "label", "id": f"{identifier}_path", "text": text,
         "expand": True, "word_wrap": True},
    ]
    row.extend(extra or [])
    return {"kind": "row", "children": row}


def _note(identifier: str, text: str = "") -> None:
    """Set one program window's note line: what the last action achieved or
    refused, always kept short enough to sit under a table."""
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_note", "text", str(text or ""))


def _set_path(identifier: str, text: str) -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_path", "text", str(text or ""))


def _fill(identifier: str, items: list) -> None:
    """Put rows into one of the programs' lists."""
    if api is not None:
        api.tabs.set_widget(TAB, identifier, "items", list(items or []))


def _selected_text(identifier: str) -> str:
    """The picked row's text, or ``""`` when nothing is picked."""
    if api is None:
        return ""
    try:
        found = api.tabs.widget_value(TAB, identifier, "selected") or []
    except Exception:
        return ""
    return str(found[0]) if found else ""


# --- 1. Main SSH ---

def main_ssh_spec() -> dict:
    """The Main-SSH window: one line saying where it is going, and a terminal
    underneath it that brings its own socket up."""
    return {"kind": "column", "children": [
        _header(P_MAIN, "main ssh", [
            {"kind": "label", "id": f"{P_MAIN}_state", "text": "not connected"},
            {"kind": "button", "id": f"{P_MAIN}_conn", "text": "Connect",
             "on": ["clicked"], "enabled": False,
             "tooltip": "Ask for the password again and try once more"},
            {"kind": "button", "id": f"{P_MAIN}_disc", "text": "Disconnect",
             "on": ["clicked"], "enabled": False},
        ]),
        {"kind": "terminal", "id": f"{P_MAIN}_term",
         "on": ["ready", "closed", "failed"]},
    ]}


def open_main_ssh() -> str:
    """Open Main SSH, or raise it if it is already up.

    Returns the window's id, or ``""`` when the profile has no host to point
    at - the terminal's socket is opened once the window is there, so an
    empty host is refused before any frame is drawn rather than as a shell
    that fails on screen.
    """
    if api is None:
        return ""
    profile = api.session.profile() or {}
    host = str(profile.get("host") or "").strip()
    if not host:
        api.ui.warning(TAB, "Main SSH needs a host in the profile. Fill in "
                            "the address on the Connection panel first.")
        return ""
    user = str(profile.get("username") or "") or str(config("default_user", "root"))
    port = port_of(profile.get("port"), config("default_port", 22))
    key_file = str(profile.get("key_file") or "")
    title = f"Main SSH - {host}"
    open_window(_program_entry(P_MAIN, title, main_ssh_spec()))
    opened[P_MAIN] = {"title": title, "kind": "main_ssh", "host": host,
                      "port": port, "username": user, "key_file": key_file}
    terminals[f"{P_MAIN}_term"] = P_MAIN
    refresh_taskbar()
    _connect_main_ssh()
    return P_MAIN


def _connect_main_ssh() -> None:
    """Ask for the password and hand the terminal its connection. A cancelled
    box leaves the window standing with a Connect button and an empty shell,
    which is exactly the state a "try again" is meant to answer."""
    record = opened.get(P_MAIN) or {}
    host = str(record.get("host") or "")
    user = str(record.get("username") or "")
    port = port_of(record.get("port"), 22)
    password = api.ui.input("Connect", f"Password for {user}@{host}:{port}",
                            "", secret=True)
    if password is None:
        _set_main_state("cancelled")
        return
    api.tabs.set_widget(TAB, f"{P_MAIN}_term", "connect", {
        "host": host, "port": port, "username": user,
        "password": str(password),
        "key_file": str(record.get("key_file") or ""),
        "trust_unknown_host": bool(config("trust_unknown_host", True)),
    })
    _set_main_state("opening...")


def _set_main_state(text: str) -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{P_MAIN}_state", "text", str(text or ""))


def main_ssh_news(event: str, payload: dict) -> None:
    """The three sayings the shell gives us, reflected on the window's own
    state line and on its two buttons."""
    record = opened.get(P_MAIN) or {}
    if event == "ready":
        _set_main_state(f"connected: {record.get('host', '?')}")
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", False)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", True)
    elif event == "failed":
        _set_main_state(str(payload.get("error") or "connection failed"))
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", True)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", False)
    elif event == "closed":
        _set_main_state("disconnected")
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", True)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", False)


# --- 2. SFTP view ---

def sftp_view_spec() -> dict:
    """A read-only listing of the main connection's remote root. Every text
    file it can name opens in Berrypad; the note under the list says whether
    the connection is up at all."""
    return {"kind": "column", "children": [
        _header(P_SFTP, "sftp: /", [
            {"kind": "button", "id": f"{P_SFTP}_up", "text": "Up",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_SFTP}_open", "text": "Open",
             "on": ["clicked"],
             "tooltip": "Read the picked file into Berrypad through SFTP"},
            {"kind": "button", "id": f"{P_SFTP}_refresh", "text": "Refresh",
             "on": ["clicked"]},
        ]),
        {"kind": "list", "id": f"{P_SFTP}_list", "on": ["selected"],
         "expand": True,
         "tooltip": "Double-click a directory to walk into it, a text file "
                    "to open it in Berrypad."},
        {"kind": "label", "id": f"{P_SFTP}_note", "text": "", "word_wrap": True},
    ]}


def open_sftp_view() -> str:
    """Show the main connection's remote root, or say plainly that the
    connection is down. Nothing here is a view of a machine's session the
    addon could bring up itself - the manifest is honest about that."""
    if api is None:
        return ""
    title = "SFTP view"
    open_window(_program_entry(P_SFTP, title, sftp_view_spec()))
    opened[P_SFTP] = {"title": title, "kind": "sftp_view"}
    refresh_taskbar()
    sftp_list("")
    return P_SFTP


def sftp_list(path: str) -> None:
    """Read one directory of the main connection and put it in the list."""
    global sftp_path
    if api is None:
        return
    if not api.device.is_connected("sftp"):
        sftp_path = str(path or "")
        _set_path(P_SFTP, f"sftp: /{sftp_path}")
        _fill(f"{P_SFTP}_list", [])
        _note(P_SFTP, "The main connection is not open - Connect it in the "
                      "Connection panel and press Refresh.")
        return
    try:
        entries = api.device.list(path or "") or []
    except Exception as error:
        _note(P_SFTP, f"could not read that folder: {error}")
        return
    sftp_path = str(path or "")
    _set_path(P_SFTP, f"sftp: /{sftp_path}")
    rows = []
    for entry in sorted(entries, key=lambda e: (not e.get("directory"),
                                                str(e.get("name") or ""))):
        marker = "/" if entry.get("directory") else ""
        rows.append({"text": f"{entry.get('name')}{marker}",
                     "data": {"path": entry.get("path") or "",
                              "directory": bool(entry.get("directory")),
                              "size": entry.get("size", 0)}})
    _fill(f"{P_SFTP}_list", rows)
    _note(P_SFTP, f"{len(rows)} item(s)")


def sftp_open_selected(action: str = "open") -> None:
    """Send the SFTP list's current row either into Berrypad, or say what it
    is if it cannot go there."""
    picked = _selected_text(f"{P_SFTP}_list")
    if not picked:
        _note(P_SFTP, "Select an item first.")
        return
    for row in _rows_of(f"{P_SFTP}_list"):
        data = row.get("data") or {}
        if str(row.get("text")) != picked:
            continue
        if data.get("directory"):
            sftp_list(str(data.get("path") or "").lstrip("/"))
            return
        if not is_text(picked):
            _note(P_SFTP, f"{picked} is not a text file Berrypad can open.")
            return
        open_berrypad(str(data.get("path") or picked), "remote", picked)
        return


def _rows_of(identifier: str) -> list:
    """The rows a program's list is holding right now, from what the addon
    last pushed there - never from the widget, so this stays cheap in a test."""
    if api is None:
        return []
    try:
        value = api.tabs.widget_value(TAB, identifier, "items") or []
    except Exception:
        return []
    return list(value) if isinstance(value, (list, tuple)) else []


# --- 3. Local disk ---

def local_disk_spec() -> dict:
    """A browser of this computer's folders. Text files open in Berrypad;
    anything executable on the local side can be handed to the device - up
    over the addon's transport, then run over SSH - which is exactly what
    the plan asks for."""
    return {"kind": "column", "children": [
        _header(P_LOCAL, str(Path.home()), [
            {"kind": "button", "id": f"{P_LOCAL}_up", "text": "Up",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_LOCAL}_home", "text": "Home",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_LOCAL}_open", "text": "Open",
             "on": ["clicked"],
             "tooltip": "Text files go to Berrypad; directories walk in"},
            {"kind": "button", "id": f"{P_LOCAL}_run", "text": "Run on Main SSH",
             "on": ["clicked"],
             "tooltip": "Upload this file and run it on the device"},
        ]),
        {"kind": "list", "id": f"{P_LOCAL}_list", "on": ["selected"],
         "expand": True},
        {"kind": "label", "id": f"{P_LOCAL}_note", "text": "",
         "word_wrap": True},
    ]}


def open_local_disk(start: str = "") -> str:
    """Show a folder on this machine; ``start`` names another place than the
    user's home, which is how a "Run on Main SSH" that came from Berrypad
    returns to where it left off."""
    global local_path
    if api is None:
        return ""
    title = "Local disk"
    open_window(_program_entry(P_LOCAL, title, local_disk_spec()))
    opened[P_LOCAL] = {"title": title, "kind": "local_disk"}
    refresh_taskbar()
    local_path = str(start or Path.home())
    local_list(local_path)
    return P_LOCAL


def local_list(path: str) -> None:
    """Put one local folder in the list, directories in front of files."""
    global local_path
    if api is None:
        return
    target = Path(str(path or ".")).expanduser()
    local_path = str(target)
    _set_path(P_LOCAL, local_path)
    try:
        names = sorted((p.name for p in target.iterdir()), key=str.lower)
    except (OSError, ValueError) as error:
        _fill(f"{P_LOCAL}_list", [])
        _note(P_LOCAL, f"cannot read this folder: {error}")
        return
    rows = []
    for name in names:
        child = target / name
        try:
            entry = child
            marker = "/" if entry.is_dir() else ""
            size = entry.stat().st_size if entry.is_file() else 0
            mode = entry.stat().st_mode if entry.exists() else 0
        except OSError:
            marker, size, mode = "", 0, 0
        rows.append({"text": f"{name}{marker}",
                     "data": {"path": str(child), "directory": bool(marker),
                              "size": size, "mode": mode}})
    _fill(f"{P_LOCAL}_list", rows)
    _note(P_LOCAL, f"{len(rows)} item(s)")


def local_action(action: str) -> None:
    """Do one of the four things the Local disk's buttons can ask for."""
    picked = _selected_text(f"{P_LOCAL}_list")
    if not picked:
        if action == "up":
            parent = str(Path(local_path).parent)
            local_list(parent if parent and parent != local_path else local_path)
            return
        if action == "home":
            local_list(str(Path.home()))
            return
        _note(P_LOCAL, "Select an item first.")
        return
    row = _match_row(f"{P_LOCAL}_list", picked)
    data = row or {}
    path = str(data.get("path") or "")
    if action == "up":
        local_list(str(Path(local_path).parent))
    elif action == "home":
        local_list(str(Path.home()))
    elif action == "open":
        if data.get("directory"):
            local_list(path)
        elif not is_text(path):
            _note(P_LOCAL, f"{short_name(path)} is not a text file Berrypad "
                            "can open.")
        else:
            open_berrypad(path, "local", short_name(path))
    elif action == "run":
        run_on_main(path)


def _match_row(identifier: str, text: str) -> dict:
    for row in _rows_of(identifier):
        if str(row.get("text")) == str(text):
            return row.get("data") or {}
    return {}


def run_on_main(local: str) -> None:
    """Upload a local file to the device and run it there.

    The upload lands under the remote root's ``tmp/`` and the run asks the
    shell to invoke it with ``sh``; either failing is reported through the
    Local-disk window's own note, so the person sees which half went wrong
    rather than a general message on the app's bar.
    """
    if api is None:
        return
    source = Path(str(local))
    if not source.is_file():
        _note(P_LOCAL, f"{source.name} is not a file.")
        return
    if not api.device.is_connected("ssh"):
        _note(P_LOCAL, "The main connection is not open, so nothing can run "
                        "there.")
        return
    try:
        target = api.device.upload(str(source), posixpath.join("tmp", source.name))
    except Exception as error:
        _note(P_LOCAL, f"upload failed: {error}")
        return
    _note(P_LOCAL, f"uploaded to {target}, running...")

    def done(result: Any) -> None:
        code = int((result or {}).get("code", -1))
        out = str((result or {}).get("out") or "")
        err = str((result or {}).get("err") or "")
        if code == 0:
            _note(P_LOCAL, f"{target}: ok")
        else:
            _note(P_LOCAL, f"{target} exited {code}: {err.strip() or out.strip()}")

    api.device.run_async(f"sh {target}", on_done=done, timeout=30)


# --- 4. Machines ---

def machines_spec() -> dict:
    """The Machines launcher: the list from the config, and the buttons that
    reach or edit one entry. Every connected machine is a window of its own,
    made the same way Multi SSH makes its sessions."""
    return {"kind": "column", "children": [
        {"kind": "list", "id": f"{P_MACHINE}_list", "on": ["selected"],
         "expand": True,
         "tooltip": "Machines kept in addons/_data/berryvm/config.json under "
                    "'machines'. Select one and press Connect."},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{P_MACHINE}_connect", "text": "Connect",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": f"{P_MACHINE}_new", "text": "New",
             "on": ["clicked"],
             "tooltip": "Reach a machine that is not on the list, once"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{P_MACHINE}_add", "text": "Add",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_MACHINE}_edit", "text": "Edit",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_MACHINE}_remove", "text": "Remove",
             "on": ["clicked"]},
        ]},
        {"kind": "button", "id": f"{P_MACHINE}_reload", "text": "Reload from config",
         "on": ["clicked"]},
        {"kind": "label", "id": f"{P_MACHINE}_note", "text": "",
         "word_wrap": True},
    ]}


def open_machines() -> str:
    """Bring up the launcher window; the entries come from the addon's own
    config, so a file edited elsewhere is picked up on Reload."""
    if api is None:
        return ""
    title = "Machines"
    open_window(_program_entry(P_MACHINE, title, machines_spec()))
    opened[P_MACHINE] = {"title": title, "kind": "machines"}
    refresh_taskbar()
    draw_machines()
    return P_MACHINE


def reload_machines() -> list:
    """Read ``machines`` from the config and keep the addon's copy in step."""
    global machines
    machines = machines_from(config("machines", []))
    return machines


def draw_machines() -> None:
    if api is None:
        return
    _fill(f"{P_MACHINE}_list",
          [{"text": f"{m.get('name')} - {m.get('username')}@{m.get('host')}:{m.get('port')}"}
           for m in machines])


def machine_row_of(text: str) -> int:
    """The row a caption names, so a selection maps back into the list."""
    for index, entry in enumerate(machines):
        caption = (f"{entry.get('name')} - {entry.get('username')}"
                   f"@{entry.get('host')}:{entry.get('port')}")
        if caption == text:
            return index
    return -1


def machine_spec_spec(entry: dict | None) -> dict:
    """The form an entry is added or changed with, filled from ``entry``."""
    record = entry or {}
    return {
        "kind": "form",
        "children": [
            {"label": "Name", "kind": "line", "id": "name",
             "value": str(record.get("name") or ""), "placeholder": "Router"},
            {"label": "Address", "kind": "line", "id": "host",
             "value": str(record.get("host") or ""),
             "placeholder": "192.168.1.1, or root@192.168.1.1:2222"},
            {"label": "Port", "kind": "spin", "id": "port",
             "value": port_of(record.get("port"), config("default_port", 22)),
             "minimum": 1, "maximum": 65535},
            {"label": "Username", "kind": "line", "id": "username",
             "value": str(record.get("username") or ""),
             "placeholder": str(config("default_user", "root") or "")},
            {"label": "Key file", "kind": "line", "id": "key_file",
             "value": str(record.get("key_file") or ""),
             "placeholder": "optional; blank asks for a password"},
        ],
    }


def write_machines(items: list) -> None:
    """Save the ``machines`` list; a key the form never showed survives the
    save unchanged, which is what lets the file be edited outside the app."""
    global machines
    machines = [m for m in (items or []) if m]
    if api is not None:
        api.config.set("machines", [dict(m) for m in machines])
    draw_machines()
    mirror_settings()


def ask_machine(entry: dict | None) -> dict:
    """One form's answers as an entry; empty when the person walked out."""
    if api is None:
        return {}
    values = api.ui.dialog(machine_spec_spec(entry),
                           title="Edit machine" if entry else "New machine")
    if values is None:
        return {}
    record = dict(entry or {})
    record.update({
        "name": " ".join(str(values.get("name") or "").split()),
        "host": str(values.get("host") or "").strip(),
        "port": port_of(values.get("port"), config("default_port", 22)),
        "username": str(values.get("username") or "").strip(),
        "key_file": str(values.get("key_file") or "").strip(),
    })
    return normalise_machine(record)


def machine_add() -> None:
    entry = ask_machine(None)
    if not entry:
        _note(P_MACHINE, "An entry needs an address.")
        return
    write_machines(list(machines) + [entry])
    _note(P_MACHINE, f"{entry.get('name')} added.")


def machine_edit() -> None:
    if not (0 <= machine_row < len(machines)):
        _note(P_MACHINE, "Select an entry to change.")
        return
    entry = ask_machine(machines[machine_row])
    if not entry:
        _note(P_MACHINE, "Nothing was changed.")
        return
    updated = list(machines)
    updated[machine_row] = entry
    write_machines(updated)


def machine_remove() -> None:
    """Drop one machine from the list. Windows already opened from it are left
    alone - they were launched from a copy of the details, and closing a
    router's window by deleting its bookmark is not what anyone means."""
    global machine_row
    if not (0 <= machine_row < len(machines)):
        _note(P_MACHINE, "Select an entry to remove.")
        return
    entry = machines[machine_row]
    if not api.ui.question("Remove machine",
                           f"Take {entry.get('name')} out of the list?"):
        return
    updated = list(machines)
    updated.pop(machine_row)
    write_machines(updated)
    machine_row = -1


def machine_connect(entry: dict) -> str:
    """Open a machine in its own window and its own socket. Each machine gets
    a fresh id, so visiting the same host twice is allowed."""
    global machine_seq
    if api is None:
        return ""
    host = str(entry.get("host") or "").strip()
    if not host:
        _note(P_MACHINE, "That entry has no address.")
        return ""
    user = str(entry.get("username") or config("default_user", "root"))
    port = port_of(entry.get("port"), config("default_port", 22))
    password = api.ui.input("Connect", f"Password for {user}@{host}:{port}",
                            "", secret=True)
    if password is None:
        _note(P_MACHINE, "No password, no session.")
        return ""
    machine_seq += 1
    win_id = f"{MACHINE_PREFIX}{machine_seq}"
    term_id = f"{win_id}_term"
    title = f"{entry.get('name') or host}"
    spec = {"kind": "column", "children": [
        _header(win_id, f"{user}@{host}:{port}", [
            {"kind": "label", "id": f"{win_id}_state", "text": "opening..."},
        ]),
        {"kind": "terminal", "id": term_id,
         "on": ["ready", "closed", "failed"]},
    ]}
    width, height = window_size()
    open_window({"id": win_id, "title": title, "width": width, "height": height,
                 "spec": spec})
    opened[win_id] = {"title": title, "kind": "machine", "host": host,
                      "port": port, "username": user}
    terminals[term_id] = win_id
    api.tabs.set_widget(TAB, term_id, "connect", {
        "host": host, "port": port, "username": user,
        "password": str(password),
        "key_file": str(entry.get("key_file") or ""),
        "trust_unknown_host": bool(config("trust_unknown_host", True)),
    })
    refresh_taskbar()
    return win_id


def machine_news(win_id: str, event: str, payload: dict) -> None:
    """One machine's shell reporting itself onto its window's state line."""
    if event == "ready":
        api.tabs.set_widget(TAB, f"{win_id}_state", "text", "connected")
    elif event == "failed":
        api.tabs.set_widget(TAB, f"{win_id}_state",
                            "text", str(payload.get("error") or "failed"))
    elif event == "closed":
        api.tabs.set_widget(TAB, f"{win_id}_state", "text", "disconnected")


# --- 5. Files (the VM's own home) ---

def files_spec() -> dict:
    """The addon's own home folder as a browsing target: Desktop, bin, tmp
    and config are the whole of it, and the person can walk into any of them."""
    return {"kind": "column", "children": [
        _header(P_FILES, str(home_root()), [
            {"kind": "button", "id": f"{P_FILES}_up", "text": "Up",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_FILES}_root", "text": "Home",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_FILES}_open", "text": "Open",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_FILES}_run", "text": "Run on Main SSH",
             "on": ["clicked"],
             "tooltip": "For anything kept in ``bin`` - upload and run"},
        ]),
        {"kind": "list", "id": f"{P_FILES}_list", "on": ["selected"],
         "expand": True},
        {"kind": "label", "id": f"{P_FILES}_note", "text": "",
         "word_wrap": True},
    ]}


def open_files() -> str:
    """Bring up the VM's home; ``_ensure_home()`` runs again here rather than
    trusting that no other addon has moved the folder out from under us."""
    if api is None:
        return ""
    _ensure_home()
    title = "Files"
    open_window(_program_entry(P_FILES, title, files_spec()))
    opened[P_FILES] = {"title": title, "kind": "files"}
    refresh_taskbar()
    files_list("")
    return P_FILES


def files_list(inside: str) -> None:
    """List one folder inside the VM's home; ``inside`` is relative to that
    root, so the person cannot walk out of it by an accident in a path."""
    global files_path
    if api is None:
        return
    root = home_root().resolve()
    inside = str(inside or "").strip("/").replace("\\", "/")
    target = (root / inside).resolve() if inside else root
    try:
        target.relative_to(root)
    except ValueError:
        _note(P_FILES, "That is outside the VM's home.")
        return
    files_path = inside
    _set_path(P_FILES, str(target))
    try:
        names = sorted((p.name for p in target.iterdir()), key=str.lower)
    except OSError as error:
        _fill(f"{P_FILES}_list", [])
        _note(P_FILES, f"cannot read this folder: {error}")
        return
    rows = []
    for name in names:
        child = target / name
        try:
            is_dir = child.is_dir()
            size = child.stat().st_size if child.is_file() else 0
        except OSError:
            is_dir, size = False, 0
        rows.append({"text": f"{name}{'/' if is_dir else ''}",
                     "data": {"inside": posixpath.join(inside, name) if inside
                                       else name,
                              "directory": bool(is_dir), "size": size,
                              "path": str(child)}})
    _fill(f"{P_FILES}_list", rows)
    _note(P_FILES, f"{len(rows)} item(s)")


def files_action(action: str) -> None:
    picked = _selected_text(f"{P_FILES}_list")
    root = home_root()
    if not picked:
        if action == "up":
            parent = posixpath.dirname(files_path)
            files_list(parent)
            return
        if action == "root":
            files_list("")
            return
        _note(P_FILES, "Select an item first.")
        return
    row = _match_row(f"{P_FILES}_list", picked)
    inside = str(row.get("inside") or "")
    full = str(row.get("path") or (root / inside))
    if action == "up":
        files_list(posixpath.dirname(files_path))
    elif action == "root":
        files_list("")
    elif action == "open":
        if row.get("directory"):
            files_list(inside)
        elif not is_text(full):
            _note(P_FILES, f"{short_name(full)} is not a text file Berrypad "
                            "can open.")
        else:
            open_berrypad(full, "local", short_name(full))
    elif action == "run":
        if not inside.startswith("bin/"):
            _note(P_FILES, "Only things kept in ``bin`` are offered to the "
                            "device to run.")
            return
        run_on_main(full)


# --- Berrypad, the editor window ---

def berry_spec(win_id: str, path: str, origin: str) -> dict:
    """The editor: a path line, a Save button and a text area wearing the
    language the suffix suggests. ``origin`` is ``local`` or ``remote``."""
    readonly = False
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_path", "text": path,
             "expand": True, "word_wrap": True},
            {"kind": "button", "id": f"{win_id}_save", "text": "Save",
             "on": ["clicked"], "enabled": not readonly},
        ]},
        {"kind": "multi", "id": f"{win_id}_edit",
         "highlight": language_for(path), "expand": True,
         "readonly": readonly},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_berrypad(path: str, origin: str, show_title: str = "") -> str:
    """One editor window per file. Reopening a path that is already up raises
    that window rather than losing unsaved words to a new copy.

    Returns the window's id, or ``""`` when the file was refused - too big,
    or a binary as far as this editor is concerned - which the caller does
    not have to check because the note is written on the caller's own window.
    """
    global berry_seq
    if api is None:
        return ""
    wanted = str(path or "")
    for win_id, record in berrypads.items():
        if record.get("path") == wanted and record.get("origin") == origin:
            raise_window(win_id)
            return win_id
    title = show_title or short_name(wanted) or wanted
    if not is_text(wanted):
        api.ui.notify(f"{title} is not a text file Berrypad can open.")
        return ""
    if origin == "local":
        try:
            size = Path(wanted).stat().st_size
        except OSError:
            api.ui.notify(f"{title} could not be read.")
            return ""
        if size > MAX_SIZE:
            api.ui.notify(f"{title} is {describe_size(size)}, over Berrypad's "
                          f"cap of {describe_size(MAX_SIZE)}.")
            return ""
    berry_seq += 1
    win_id = f"{BERRY_PREFIX}{berry_seq}"
    width, height = window_size()
    open_window({"id": win_id, "title": f"Berrypad - {title}",
                 "width": width, "height": height,
                 "spec": berry_spec(win_id, wanted, origin)})
    berrypads[win_id] = {"path": wanted, "origin": origin, "title": title}
    opened[win_id] = {"title": f"Berrypad - {title}", "kind": "berrypad",
                      "path": wanted, "origin": origin}
    refresh_taskbar()
    _berry_load(win_id, wanted, origin)
    return win_id


def _berry_load(win_id: str, path: str, origin: str) -> None:
    """Read the file into the editor. Refusals say what they are on the
    window's own note, so the person sees the reason next to the empty box."""
    if api is None:
        return
    if origin == "local":
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                                f"could not read: {error}")
            return
    else:
        if not api.device.is_connected("sftp"):
            api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                                "The main connection is not open.")
            return
        try:
            stat = api.device.stat(path) or {}
            size = int(stat.get("size") or 0)
            if size > MAX_SIZE:
                api.tabs.set_widget(
                    TAB, f"{win_id}_note", "text",
                    f"{describe_size(size)}, over the cap of "
                    f"{describe_size(MAX_SIZE)}")
                return
            text = api.device.read_text(path)
        except Exception as error:
            api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                                f"could not read: {error}")
            return
    api.tabs.set_widget(TAB, f"{win_id}_edit", "value", str(text or ""))


def _berry_save(win_id: str) -> None:
    """Write the editor's contents back where they came from. A local file
    keeps the same path; a remote one goes over the addon's transport."""
    record = berrypads.get(win_id)
    if record is None or api is None:
        return
    path = str(record.get("path") or "")
    origin = str(record.get("origin") or "local")
    text = str(api.tabs.widget_value(TAB, f"{win_id}_edit", "value") or "")
    if len(text.encode("utf-8", "replace")) > MAX_SIZE:
        api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                            f"refusing to save {describe_size(len(text))} - "
                            f"over the cap of {describe_size(MAX_SIZE)}")
        return
    if origin == "local":
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                                f"could not write: {error}")
            return
    else:
        try:
            api.device.write_text(path, text)
        except Exception as error:
            api.tabs.set_widget(TAB, f"{win_id}_note", "text",
                                f"could not write: {error}")
            return
    stamp = datetime.now().strftime("%H:%M:%S")
    api.tabs.set_widget(TAB, f"{win_id}_note", "text", f"saved {stamp}")


# --- what the tab says back ---

def is_terminal_id(identifier: str) -> bool:
    """Whether one event came from a terminal inside a program window."""
    return str(identifier or "") in terminals


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Every click, selection and window report this tab can produce.

    The dispatcher is a wide ``if`` chain rather than a table, because the
    ids a window's controls carry are made up on the spot (``<win_id>_path``,
    ``<win_id>_save``) and a table of them would have to be built every
    time the desktop changes.
    """
    global machine_row
    if api is None or str(tab) != TAB:
        return
    payload = payload or {}
    body = str(identifier or "")
    # 1. The desktop itself: window events. ``body`` is the window's id when
    # the primitive reported a change inside a frame, or ``DESKTOP`` when the
    # canvas has something of its own to say.
    if body == DESKTOP:
        refresh_taskbar()
        return
    if body in opened and event in ("activated", "moved", "resized", "closed"):
        if event == "closed":
            forget_window(body)
        refresh_taskbar()
        return
    # 2. Taskbar and Start button.
    if body == START_BTN and event == "clicked":
        start_menu()
        return
    if body == CLOSE_BTN and event == "clicked":
        win_id = picked_window()
        if win_id:
            close_window(win_id)
            forget_window(win_id)
            refresh_taskbar()
        return
    if body == TASKBAR and event == "selected":
        win_id = picked_window()
        if win_id:
            raise_window(win_id)
        return
    # 3. A terminal's news, whether Main SSH or a machine window.
    if is_terminal_id(body):
        owner = terminals[body]
        if owner == P_MAIN:
            main_ssh_news(event, payload)
        else:
            machine_news(owner, event, payload)
        return
    # 4. Program windows' own controls.
    if body == f"{P_SFTP}_list" and event == "selected":
        return                                    # selection is read on demand
    if body == f"{P_SFTP}_up" and event == "clicked":
        parent = posixpath.dirname(sftp_path.rstrip("/"))
        sftp_list(parent or "")
        return
    if body == f"{P_SFTP}_open" and event == "clicked":
        sftp_open_selected()
        return
    if body == f"{P_SFTP}_refresh" and event == "clicked":
        sftp_list(sftp_path)
        return
    if body == f"{P_MAIN}_conn" and event == "clicked":
        _connect_main_ssh()
        return
    if body == f"{P_MAIN}_disc" and event == "clicked":
        api.tabs.set_widget(TAB, f"{P_MAIN}_term", "disconnect", None)
        return
    if body == f"{P_LOCAL}_list" and event == "selected":
        return
    if body == f"{P_LOCAL}_up" and event == "clicked":
        local_action("up"); return
    if body == f"{P_LOCAL}_home" and event == "clicked":
        local_action("home"); return
    if body == f"{P_LOCAL}_open" and event == "clicked":
        local_action("open"); return
    if body == f"{P_LOCAL}_run" and event == "clicked":
        local_action("run"); return
    if body == f"{P_FILES}_list" and event == "selected":
        return
    if body == f"{P_FILES}_up" and event == "clicked":
        files_action("up"); return
    if body == f"{P_FILES}_root" and event == "clicked":
        files_action("root"); return
    if body == f"{P_FILES}_open" and event == "clicked":
        files_action("open"); return
    if body == f"{P_FILES}_run" and event == "clicked":
        files_action("run"); return
    if body == f"{P_MACHINE}_list" and event == "selected":
        picked = _selected_text(f"{P_MACHINE}_list")
        machine_row = machine_row_of(picked)
        return
    if body == f"{P_MACHINE}_connect" and event == "clicked":
        if 0 <= machine_row < len(machines):
            machine_connect(machines[machine_row])
        else:
            _note(P_MACHINE, "Select a machine first.")
        return
    if body == f"{P_MACHINE}_new" and event == "clicked":
        entry = ask_machine(None)
        if entry:
            machine_connect(entry)
        return
    if body == f"{P_MACHINE}_add" and event == "clicked":
        machine_add(); return
    if body == f"{P_MACHINE}_edit" and event == "clicked":
        machine_edit(); return
    if body == f"{P_MACHINE}_remove" and event == "clicked":
        machine_remove(); return
    if body == f"{P_MACHINE}_reload" and event == "clicked":
        reload_machines()
        draw_machines()
        _note(P_MACHINE, "Reloaded from the config.")
        return
    # 5. A Berrypad window's Save. The other controls on that window are the
    # editor itself and two labels; neither has an event that means anything
    # back here.
    if body.endswith("_save") and event == "clicked":
        win_id = body[:-len("_save")]
        if win_id in berrypads:
            _berry_save(win_id)
        return
