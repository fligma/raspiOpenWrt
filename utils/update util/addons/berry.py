#!/usr/bin/env python3
"""Berry: an SFTP and local file manager with a notepad-style editor.

One ``Berry`` window-tab holds a file view with two pages - **Device** (the
*current profile*'s own SSH/SFTP connection) and **Local** (this computer). Each
page lists name, size, type, modified time and path - the device page adds mode
too - and each has a typed address bar, Up, Home, a Places bookmark box, a live
filter, a sort that keeps folders on top and a show-hidden switch. Double-click
walks into a folder; every other action is in a slim bar above the listing and,
for most of them, in the right-click menu on a row.

Editing a file opens a **notepad-like tab of its own** (``Berry Editor``): a big
text area with syntax colors for json, python, html, css, javascript and ini.
The text lives in memory - the file is not touched until **Save**, which writes
it back (over SFTP on the device, to disk locally) and closes the tab; **Close**
discards; **Reload** re-reads. A language box re-shades the text on the fly.

Copy and paste bridge the two pages. Copy on one and paste on the other moves
bytes over SFTP - a local file pasted on the device is uploaded, a device file
pasted on the local page is downloaded - while pasting on the page it came from
copies it in place (``cp -a`` on the device, a real file copy here). The local
page also uploads its selected file straight to the device folder that is open.

Nothing about the layout is hardcoded that a user might want to change: the
starting folders on both sides, the bookmarks, the show-hidden and sort defaults,
whether deletes ask first, the size at which the editor warns, where downloads
land and the limits on ``find`` all live in ``addons/_data/berry/config.json``, shown
as a form under this addon's name in Settings > Addons and editable as plain
JSON anywhere.

Everything is built from declarative specs and every control - buttons, the
address bar, the filter, the sort, the listings and their right-clicks, and the
editor - reports back through ``on_widget``, so the same file works imported into
the app and compiled into a program of its own by the Addon Dev tab. The
blocking ``device`` calls run in the app's thread pool while a private event loop
keeps the window painting and buttons greyed, so a big listing or a large upload
never freezes the interface.
"""

from __future__ import annotations

import datetime
import os
import posixpath
import re
import shlex
import shutil
import stat as stat_module
from pathlib import Path

# The Qt-free half of the contract, so this file also runs compiled into a
# program of its own and inside a test script that never opens a window.
from addon_common import AddonError

ADDON = {
    "name": "Berry file manager",
    "version": "1.2",
    "author": "fligma",
    "description": "A device-and-local file manager with a notepad-style editor: "
                   "browse both sides in one tab, edit files with syntax "
                   "highlighting (edits stay in memory until Save), and mkdir, "
                   "rename, move, delete, chmod, upload, download, find, copy and "
                   "paste - most of it from a right-click menu, all configured in "
                   "addons/_data/berry/config.json.",
    "api": 1,
}

TAB = "Berry"                 # the window tab; its file view has Device/Local pages
TAB_EDITOR = "Berry Editor"   # the notepad-like tab, opened when a file is edited

DEVICE = "device"
LOCAL = "local"

api = None                    # filled in by init()
working = False               # one job at a time
clip = None                   # {"side","path","name","directory"} for copy/paste
editing = None                # {"side","path","text","detected"} while the editor is open
_places_guard = False         # set while repopulating a Places combo

STATE: dict[str, dict] = {}


def _fresh_state() -> dict:
    return {"cwd": "", "entries": [], "by_name": {}, "btns": []}


def pid(side: str, name: str) -> str:
    """A spec id on the Berry tab - device ids start with 'd', local with 'l'."""
    return ("l" if side == LOCAL else "d") + str(name)


def side_of_id(identifier: str) -> str:
    return LOCAL if str(identifier).startswith("l") else DEVICE


def base_of_id(identifier: str) -> str:
    return str(identifier)[1:]


def st(side: str) -> dict:
    return STATE.setdefault(str(side), _fresh_state())


# ---------------------------------------------------------------------------
# What can be configured, and what it starts out as
# ---------------------------------------------------------------------------

DEFAULTS = {
    "start_path": "",
    "local_start": "",
    "show_hidden": False,
    "sort_by": "name",
    "sort_desc": False,
    "confirm_delete": True,
    "editor_limit_kb": 512,
    "download_dir": "",
    "quick_places": ["/", "/etc", "/tmp", "/root", "/www", "/overlay", "/bin"],
    "local_places": ["~", "~/Downloads", "~/Documents", "~/Desktop"],
    "find_max_depth": 4,
    "find_max_results": 300,
}

FIELDS = [
    {"key": "start_path", "label": "Device folder to open first", "kind": "text",
     "hint": "Any path on the device. Blank uses the profile's remote folder, "
             "or the root if it has none",
     "placeholder": "the profile's remote folder"},
    {"key": "local_start", "label": "Local folder to open first", "kind": "text",
     "hint": "A folder on this computer. Blank uses your home directory",
     "placeholder": "your home folder"},
    {"key": "show_hidden", "label": "Show dotfiles at first", "kind": "bool"},
    {"key": "sort_by", "label": "Sort by", "kind": "choice",
     "choices": ["name", "size", "mtime", "type"],
     "hint": "name, size, modified time or type. Folders are always kept on top"},
    {"key": "sort_desc", "label": "Sort descending", "kind": "bool"},
    {"key": "confirm_delete", "label": "Ask before deleting", "kind": "bool"},
    {"key": "editor_limit_kb", "label": "Warn in the editor above (KB)", "kind": "number",
     "minimum": 0, "maximum": 1048576,
     "hint": "A file bigger than this still opens, but the editor status says so - "
             "0 never warns"},
    {"key": "download_dir", "label": "Local folder for downloads", "kind": "text",
     "hint": "Where a download lands when you do not name one yourself",
     "placeholder": "the addon's own data folder"},
    {"key": "quick_places", "label": "Bookmarked device places", "kind": "list",
     "hint": "Comma separated device folders offered in the Places box"},
    {"key": "local_places", "label": "Bookmarked local places", "kind": "list",
     "hint": "Comma separated folders on this computer; '~' is your home"},
    {"key": "find_max_depth", "label": "Find: how deep", "kind": "number",
     "minimum": 1, "maximum": 20,
     "hint": "How many levels below the current folder a find search goes"},
    {"key": "find_max_results", "label": "Find: most matches", "kind": "number",
     "minimum": 1, "maximum": 5000},
]

GROUPS = [
    ("Opening a folder", ["start_path", "local_start", "quick_places",
                          "local_places", "show_hidden", "sort_by", "sort_desc"]),
    ("Files and safety", ["confirm_delete", "editor_limit_kb", "download_dir"]),
    ("Find", ["find_max_depth", "find_max_results"]),
]

SORT_LABELS = {"name": "Name", "size": "Size", "mtime": "Modified", "type": "Type"}
SORT_KEYS = {label: value for value, label in SORT_LABELS.items()}

# The editor's language box: friendly labels, the key the highlighter wants, and
# which files get which colors by themselves.
LANG_ITEMS = ["auto", "Python", "JSON", "HTML", "CSS", "JavaScript", "Bash", "INI", "None"]
LANG_KEY = {"Python": "python", "JSON": "json", "HTML": "html", "CSS": "css",
            "JavaScript": "javascript", "Bash": "bash", "INI": "ini", "None": ""}
KEY_LANG = {value: label for label, value in LANG_KEY.items()}
EXT_LANG = {".py": "python", ".pyw": "python", ".json": "json",
            ".js": "javascript", ".mjs": "javascript", ".html": "html",
            ".htm": "html", ".xml": "html", ".svg": "html", ".css": "css",
            ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".editorconfig": "ini",
            ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ksh": "bash",
            ".bashrc": "bash", ".profile": "bash", ".shrc": "bash"}

VIEW_STYLE = """
QWidget { background-color: #161616; }
QPlainTextEdit { font-family: Consolas, monospace; }
QTreeWidget { background-color: #1b1b1b; }
"""

EDITOR_STYLE = """
QWidget { background-color: #1e1e1e; color: #d4d4d4; }
QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c;
                 selection-background-color: #c51a4a; selection-color: #ffffff;
                 font-family: Consolas, 'Courier New', monospace; }
QPushButton { background-color: #333333; color: #e0e0e0; border: 1px solid #4a4a4a;
              border-radius: 4px; padding: 4px 12px; }
QPushButton:hover { background-color: #414141; border-color: #c51a4a; }
QPushButton:pressed { background-color: #a0153c; border-color: #c51a4a; }
QComboBox { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #4a4a4a;
            border-radius: 4px; padding: 2px 6px; }
QComboBox QAbstractItemView { background-color: #2a2a2a; color: #e0e0e0;
                              selection-background-color: #c51a4a; }
"""

TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<circle cx="9" cy="12" r="3.1" fill="#7b2ff7"/>'
    '<circle cx="14" cy="12" r="3.1" fill="#a04fff"/>'
    '<circle cx="11.5" cy="16" r="3.1" fill="#5b1fb0"/>'
    '<circle cx="11.5" cy="8.4" r="2.4" fill="#9b30ff"/>'
    '<path d="M12 6 C12 3 14 2 16 2 C16 4.5 14.5 6 12 6 Z" fill="#2ea44f"/>'
    '</svg>'
)
EDITOR_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="4" y="3" width="16" height="18" rx="2" fill="#ffffff" stroke="#8a8a8a"/>'
    '<path d="M7 8h10M7 11h10M7 14h7" stroke="#3b82f6" stroke-width="1.4"/>'
    '<path d="M18.5 14.5l2 2-5 5-2.6.6.6-2.6z" fill="#f4b400" stroke="#b8860b"/>'
    '</svg>'
)

#: The picture the tab wears when the user has put one in the assets folder.
TAB_ICON_FILE = "berryicon.png"


def tab_icon() -> str:
    """What to mark the Berry tab with: the shipped picture, else the drawing.

    ``assets/berryicon.png`` is the mark when it is there - a picture the app
    builds alongside itself, so a user can drop their own in and restart. Without
    it the tab draws its own drupelets, which needs nothing beside the addon and
    so keeps working in a build that shipped no picture."""
    folder = None
    try:
        folder = api.paths.assets
    except (AddonError, AttributeError, TypeError):    # no api in a bare import
        folder = None
    if folder is None:
        return TAB_ICON
    try:
        candidate = Path(str(folder)) / TAB_ICON_FILE
        if candidate.is_file():
            return str(candidate)
    except (OSError, AddonError):
        return TAB_ICON
    return TAB_ICON


SHQ = shlex.quote


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _cfg(key: str, default=None):
    """One config value, read fresh from the file over the defaults."""
    if api is None:
        return config_default(key, default)
    try:
        value = api.config.get(key, config_default(key, default))
    except AddonError:
        return config_default(key, default)
    return default if value is None else value


def config_default(key: str, default=None):
    """A key straight out of DEFAULTS, dotted included, for when there is no api."""
    node = DEFAULTS
    for part in str(key).split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _places(side: str) -> list[str]:
    key = "local_places" if side == LOCAL else "quick_places"
    raw = _cfg(key, DEFAULTS[key])
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\n]", str(raw or ""))
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def default_root() -> str:
    """Where the device page starts: the configured path, else the profile's."""
    text = str(_cfg("start_path", "") or "").strip()
    if text:
        return posixpath.normpath(text)
    remote = ""
    try:
        remote = str((api.session.status() or {}).get("remote_root") or "").strip()
    except (AddonError, AttributeError, TypeError):
        remote = ""
    return posixpath.normpath(remote) if remote else "/"


def default_local_root() -> str:
    """Where the local page starts: the configured folder, else your home."""
    text = str(_cfg("local_start", "") or "").strip() or "~"
    try:
        return str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(Path.home())


def start_side(side: str) -> str:
    return default_local_root() if side == LOCAL else default_root()


def _read(identifier: str, what: str = "value"):
    try:
        return api.tabs.widget_value(TAB, identifier, what)
    except AddonError:
        return None


def _read_editor(identifier: str, what: str = "value"):
    try:
        return api.tabs.widget_value(TAB_EDITOR, identifier, what)
    except AddonError:
        return None


def _sz(n) -> str:
    """A byte count as something short a human can read."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    for unit in ("B", "K", "M", "G", "T", "P"):
        if n < 1024 or unit == "P":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}P"


def _t(ts) -> str:
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def _kind(entry: dict) -> str:
    if entry.get("link"):
        return "LINK"
    if entry.get("directory"):
        return "DIR"
    return "FILE"


def _mode(entry: dict) -> str:
    """``0644 rw-r--r--`` out of a st_mode and the directory/link flags."""
    try:
        m = int(entry.get("mode") or 0)
    except (TypeError, ValueError):
        m = 0
    bits = stat_module.S_IMODE(m)
    perms = ""
    for who in (6, 3, 0):
        for i, letter in enumerate("rwx"):
            perms += letter if bits & (1 << (who + 2 - i)) else "-"
    head = "d" if entry.get("directory") else ("l" if entry.get("link") else "-")
    sticky = "t" if m & 0o1000 else ""
    return f"{bits:04o} {head}{perms}{sticky}"


def language_for(path: str) -> str:
    """The editor language a file's extension implies, or '' for none."""
    return EXT_LANG.get(posixpath.splitext(str(path or ""))[1].lower(), "")


# ---------------------------------------------------------------------------
# Path handling, told apart by side
# ---------------------------------------------------------------------------

def _norm(side: str, text: str) -> str:
    return (os.path if side == LOCAL else posixpath).normpath(text)


def _join(side: str, *parts: str) -> str:
    return os.path.join(*parts) if side == LOCAL else posixpath.join(*parts)


def _base(side: str, text: str) -> str:
    return (os.path.basename if side == LOCAL else posixpath.basename)(text)


def _parent(side: str, text: str) -> str:
    return (os.path.dirname if side == LOCAL else posixpath.dirname)(text)


def _isabs(side: str, text: str) -> bool:
    return os.path.isabs(text) if side == LOCAL else str(text).startswith("/")


# ---------------------------------------------------------------------------
# The Berry tab: a Device page and a Local page, each a file manager
# ---------------------------------------------------------------------------

def _nav_row(side: str) -> dict:
    children = [
        {"kind": "line", "id": pid(side, "path"), "on": ["returned"],
         "placeholder": "type a folder path and press Enter",
         "tooltip": "The folder on show. Enter goes to it."},
        {"kind": "button", "id": pid(side, "go"), "text": "Go", "on": ["clicked"]},
        {"kind": "button", "id": pid(side, "up"), "text": "Up",
         "tooltip": "The folder above this one", "on": ["clicked"]},
        {"kind": "button", "id": pid(side, "refresh"), "text": "Refresh",
         "tooltip": "List this folder again", "on": ["clicked"]},
    ]
    if side != LOCAL:
        children.append({"kind": "button", "id": pid(side, "root"), "text": "Root",
                         "tooltip": "Jump to /", "on": ["clicked"]})
    children.append({"kind": "button", "id": pid(side, "home"), "text": "Home",
                     "tooltip": "Jump to the folder this page opens by default",
                     "on": ["clicked"]})
    children.append({"kind": "combo", "id": pid(side, "places"), "on": ["changed"],
                     "items": ["Places..."] + _places(side),
                     "tooltip": "Jump to a bookmarked folder (set them in Settings > Addons)"})
    return {"kind": "row", "children": children}


def _refine_row(side: str) -> dict:
    return {"kind": "row", "children": [
        {"kind": "label", "text": "Filter", "style": "color:#aaaaaa;"},
        {"kind": "line", "id": pid(side, "filter"), "on": ["changed"],
         "placeholder": "hide rows whose name lacks this",
         "tooltip": "Filters the listing on this machine; nothing is fetched"},
        {"kind": "label", "text": "Sort", "style": "color:#aaaaaa;"},
        {"kind": "combo", "id": pid(side, "sort"), "on": ["changed"],
         "items": list(SORT_LABELS.values()),
         "value": SORT_LABELS.get(str(_cfg("sort_by", "name")), "Name"),
         "tooltip": "Folders are always kept on top"},
        {"kind": "check", "id": pid(side, "hidden"), "text": "show hidden", "on": ["toggled"],
         "checked": bool(_cfg("show_hidden", False)),
         "tooltip": "Include names that begin with a dot"},
    ]}


def _toolbar(side: str) -> dict:
    if side == LOCAL:
        buttons = [("menu", "Actions \u25be"), ("mkdir", "New folder"),
                   ("up2dev", "Upload to device"), ("paste", "Paste")]
    else:
        buttons = [("menu", "Actions \u25be"), ("mkdir", "New folder"),
                   ("upload", "Upload"), ("download", "Download"), ("paste", "Paste")]
    tips = {"menu": "The full menu for the selected entry (same as right-click)",
            "mkdir": "Make a folder here", "upload": "Send a file here from this computer",
            "download": "Save the selected file onto this computer",
            "up2dev": "Send the selected file into the device folder that is open",
            "paste": "Copy the clip into this folder"}
    return {"kind": "row", "children": [
        {"kind": "button", "id": pid(side, ident), "text": text, "on": ["clicked"],
         "tooltip": tips.get(ident, "")}
        for ident, text in buttons]}


def _page(side: str, label: str) -> dict:
    listing_columns = (["Name", "Size", "Type", "Modified", "Path"] if side == LOCAL
                       else ["Name", "Size", "Type", "Modified", "Mode", "Path"])
    listing = {"kind": "table", "id": pid(side, "files"), "on": ["selected", "context"],
               "columns": listing_columns,
               "tooltip": "Double-click to open; right-click for actions"}
    status = {"kind": "label", "id": pid(side, "status"), "text": "Ready.",
              "word_wrap": True, "style": "color:#23d18b; padding:2px 4px;"}
    return {"kind": "column", "label": label, "children": [
        _nav_row(side), _refine_row(side), _toolbar(side), listing, status]}


def berry_spec() -> dict:
    """The Berry tab: one file view with a Device page and a Local page."""
    return {"kind": "tabs", "children": [_page(DEVICE, "Device"), _page(LOCAL, "Local")]}


def editor_spec(language: str) -> dict:
    """The notepad-like editor tab, shaded for ``language``."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": "ed-path", "text": "", "word_wrap": True,
             "style": "color:#cfcfcf; padding:2px 4px;"},
            {"kind": "spacer"},
            {"kind": "label", "text": "language", "style": "color:#9a9a9a;"},
            {"kind": "combo", "id": "ed-lang", "on": ["changed"], "items": list(LANG_ITEMS),
             "value": KEY_LANG.get(language, "auto"),
             "tooltip": "Syntax colors; 'auto' follows the file's extension"},
        ]},
        {"kind": "multi", "id": "ed-body", "highlight": language,
         "style": "min-height:280px;"},
        {"kind": "row", "children": [
            {"kind": "button", "id": "ed-save", "text": "Save", "on": ["clicked"],
             "tooltip": "Write the file and close the editor (nothing is written before this)"},
            {"kind": "button", "id": "ed-reload", "text": "Reload", "on": ["clicked"],
             "tooltip": "Discard your edits and re-read the file"},
            {"kind": "button", "id": "ed-close", "text": "Close", "on": ["clicked"],
             "tooltip": "Close without saving"},
            {"kind": "label", "id": "ed-status",
             "text": "Editing in memory - nothing is written until Save.",
             "word_wrap": True, "style": "color:#23d18b; padding:2px 6px;"},
        ]},
    ]}


def build_tab() -> None:
    """The Berry tab from a spec; every control reports back through ``on_widget``."""
    STATE.setdefault(DEVICE, _fresh_state())
    STATE.setdefault(LOCAL, _fresh_state())
    st(DEVICE)["btns"] = [pid(DEVICE, b) for b in
                          ("go", "up", "refresh", "root", "home", "menu", "mkdir",
                           "upload", "download", "paste")]
    st(LOCAL)["btns"] = [pid(LOCAL, b) for b in
                         ("go", "up", "refresh", "home", "menu", "mkdir", "up2dev", "paste")]
    api.tabs.add_tab(TAB, spec=berry_spec(), style=VIEW_STYLE)
    api.tabs.set_icon(TAB, tab_icon())


# ---------------------------------------------------------------------------
# Status, progress and the one-job gate
# ---------------------------------------------------------------------------

def say(text: str, side: str = DEVICE, bad: bool = False) -> None:
    try:
        api.tabs.set_widget(TAB, pid(side, "status"), "text", str(text))
        api.tabs.set_widget(TAB, pid(side, "status"), "style",
                            "color:#ffb020; padding:2px 4px;" if bad
                            else "color:#23d18b; padding:2px 4px;")
    except AddonError:
        pass


def editor_say(text: str, bad: bool = False) -> None:
    try:
        api.tabs.set_widget(TAB_EDITOR, "ed-status", "text", str(text))
        api.tabs.set_widget(TAB_EDITOR, "ed-status", "style",
                            "color:#ff6b6b; padding:2px 6px;" if bad
                            else "color:#23d18b; padding:2px 6px;")
    except AddonError:
        pass


def _buttons(usable: bool) -> None:
    for side in (DEVICE, LOCAL):
        for identifier in st(side).get("btns", []):
            try:
                api.tabs.set_widget(TAB, identifier, "enabled", bool(usable))
            except AddonError:
                pass


def _start(text: str, side: str) -> None:
    global working
    working = True
    say(text + "...", side=side)
    _buttons(False)


def _finish() -> None:
    global working
    working = False
    _buttons(True)


def _busy(side: str, text: str, action) -> bool:
    """Run one job with the buttons greyed; report any failure."""
    if api is None or working:
        return False
    _start(text, side)
    ok = False
    try:
        action()
        ok = True
    except AddonError as error:
        say(str(error), side=side, bad=True)
        api.log(f"berry: {error}")
    except OSError as error:
        say(str(error), side=side, bad=True)
        api.log(f"berry: {error}")
    finally:
        _finish()
    return ok


# ---------------------------------------------------------------------------
# Listing both sides
# ---------------------------------------------------------------------------

def _list_local(path: str) -> list[dict]:
    """The folder ``path`` on this computer, in the shape the device list uses."""
    entries: list[dict] = []
    with os.scandir(path) as it:
        for dirent in it:
            try:
                is_dir = dirent.is_dir(follow_symlinks=False)
                is_link = dirent.is_symlink()
                info = dirent.stat(follow_symlinks=False)
            except OSError:
                is_dir = is_link = False
                info = None
            entries.append({
                "name": dirent.name, "path": dirent.path,
                "size": getattr(info, "st_size", 0) if info else 0,
                "mtime": int(getattr(info, "st_mtime", 0) or 0),
                "mode": getattr(info, "st_mode", 0) if info else 0,
                "directory": bool(is_dir), "link": bool(is_link),
            })
    return entries


def _fetch(side: str, path: str) -> list[dict]:
    if side == LOCAL:
        return _list_local(path)
    return api.device.list(path, outside_root=True)


def navigate(side: str, path: str) -> None:
    """Point a page at a folder and list it."""
    if api is None:
        return
    s = st(side)
    text = str(path or "").strip().strip('"')
    if not text:
        text = start_side(side)
    if not _isabs(side, text):
        text = _join(side, s["cwd"] or start_side(side), text)
    s["cwd"] = _norm(side, text) or text
    api.tabs.set_widget(TAB, pid(side, "path"), "value", s["cwd"])
    refresh(side)


def refresh(side: str, _checked: bool = False) -> None:
    """Re-list the folder on show and paint the table."""
    if api is None:
        return
    s = st(side)
    if not s["cwd"]:
        navigate(side, start_side(side))
        return
    if working:
        return

    def action():
        s["entries"] = _fetch(side, s["cwd"])

    if _busy(side, f"listing {s['cwd']}", action):
        render(side)
        say(f"{len(s['entries'])} item(s) in {s['cwd']}", side=side)


def _sort_key_for(kind: str):
    if kind == "size":
        return lambda e: int(e.get("size") or 0)
    if kind == "mtime":
        return lambda e: int(e.get("mtime") or 0)
    if kind == "type":
        return lambda e: (_kind(e), str(e.get("name") or "").lower())
    return lambda e: str(e.get("name") or "").lower()


def render(side: str) -> None:
    """Filter, sort and paint the current listing into the table."""
    if api is None:
        return
    s = st(side)
    s["by_name"] = {}
    show_hidden = bool(_read(pid(side, "hidden"), "checked"))
    needle = str(_read(pid(side, "filter"), "value") or "").lower()
    kind = SORT_KEYS.get(str(_read(pid(side, "sort"), "value") or "Name"), "name")
    try:
        desc = bool(api.config.get("sort_desc", DEFAULTS["sort_desc"]))
    except AddonError:
        desc = False

    items: list[dict] = []
    for entry in s["entries"]:
        name = str(entry.get("name") or "")
        if not show_hidden and name.startswith("."):
            continue
        if needle and needle not in name.lower():
            continue
        items.append(entry)

    # Sort by the chosen key (descending if asked), then let the folders float
    # to the top: Python's sort is stable, so the chosen order survives inside
    # each of the two groups.
    items.sort(key=_sort_key_for(kind), reverse=desc)
    items.sort(key=lambda e: 0 if e.get("directory") else 1)

    rows = []
    for entry in items:
        name = str(entry.get("name") or "")
        s["by_name"][name] = entry
        row = [name, _sz(entry.get("size")), _kind(entry), _t(entry.get("mtime"))]
        if side != LOCAL:
            row.append(_mode(entry))
        row.append(str(entry.get("path") or ""))
        rows.append(row)
    api.tabs.set_widget(TAB, pid(side, "files"), "rows", rows)


def selected(side: str) -> dict | None:
    names = _read(pid(side, "files"), "selected") or []
    if not names:
        return None
    return st(side)["by_name"].get(str(names[0]))


def _entry(side: str, name: str) -> dict | None:
    return st(side)["by_name"].get(str(name)) if name else None


def on_activate(side: str, name: str) -> None:
    """Double-click: walk into a folder, open a file in the editor tab."""
    entry = _entry(side, name)
    if entry is None:
        return
    if entry.get("directory"):
        navigate(side, str(entry.get("path") or ""))
    else:
        open_editor(side, entry)


# ---------------------------------------------------------------------------
# Reading and writing text on either side
# ---------------------------------------------------------------------------

def _read_file(side: str, path: str) -> str:
    if side == LOCAL:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return api.device.read_text(path, outside_root=True)


def _write_file(side: str, path: str, text: str) -> None:
    if side == LOCAL:
        Path(path).write_text(text, encoding="utf-8")
    else:
        api.device.write_text(path, text, mkdir=False, outside_root=True)


def _editor_open() -> bool:
    try:
        return bool(api.tabs.exists(TAB_EDITOR))
    except AddonError:
        return False


def _confirm_discard() -> bool:
    """True if it is safe to drop what is in the editor (no changes, or agreed)."""
    current = _read_editor("ed-body", "value")
    if current is None or editing is None or str(current) == editing.get("text", ""):
        return True
    return api.ui.question("Discard changes?",
                           f"{posixpath.basename(str(editing.get('path') or ''))} has "
                           "unsaved edits.\nDiscard them?", default=False)


def open_editor(side: str, entry: dict) -> None:
    """Read a file into memory and open it in the notepad-like editor tab."""
    global editing
    if api is None or entry is None or entry.get("directory"):
        return
    if _editor_open() and not _confirm_discard():
        return
    path = str(entry.get("path") or "")
    name = str(entry.get("name") or _base(side, path))
    limit = int(_cfg("editor_limit_kb", DEFAULTS["editor_limit_kb"]) or 0)
    warned = bool(limit and int(entry.get("size") or 0) > limit * 1024)

    holder: dict = {}

    def action():
        holder["text"] = _read_file(side, path)

    if not _busy(side, f"reading {name}", action):
        return
    text = holder.get("text", "")
    language = language_for(path)
    editing = {"side": side, "path": path, "text": text, "detected": language}
    api.tabs.add_tab(TAB_EDITOR, spec=editor_spec(language), style=EDITOR_STYLE)
    api.tabs.set_icon(TAB_EDITOR, EDITOR_ICON)
    api.tabs.set_widget(TAB_EDITOR, "ed-path", "text", f"{path}  ({_sz(entry.get('size'))})")
    api.tabs.set_widget(TAB_EDITOR, "ed-body", "text", text)
    api.tabs.activate(TAB_EDITOR)
    editor_say("Editing in memory - nothing is written until Save."
               + ("  (large file)" if warned else ""), bad=warned)


def save_editor(_checked: bool = False) -> None:
    """Write the in-memory text back to the file and close the editor."""
    if api is None or editing is None:
        return
    side = editing["side"]
    path = editing["path"]
    text = str(_read_editor("ed-body", "value") or "")
    name = _base(side, path)

    def action():
        _write_file(side, path, text)

    if _busy(side, f"saving {name}", action):
        editing["text"] = text
        close_editor(force=True)
        refresh(side)
        say(f"saved {name}", side=side)


def reload_editor(_checked: bool = False) -> None:
    """Discard edits and re-read the file into the editor."""
    if api is None or editing is None:
        return
    if not _confirm_discard():
        return
    side = editing["side"]
    path = editing["path"]
    holder: dict = {}

    def action():
        holder["text"] = _read_file(side, path)

    if _busy(side, f"reloading {_base(side, path)}", action):
        editing["text"] = holder.get("text", "")
        api.tabs.set_widget(TAB_EDITOR, "ed-body", "text", editing["text"])
        editor_say("re-read from the source")


def close_editor(force: bool = False) -> None:
    """Close the editor tab; ask first if there are unsaved edits."""
    global editing
    if api is None:
        return
    if not force and not _confirm_discard():
        return
    api.tabs.close(TAB_EDITOR)
    editing = None


def set_editor_language(label: str) -> None:
    """The language box changed: re-shade the editor text."""
    if api is None or editing is None:
        return
    language = editing.get("detected", "") if label == "auto" else LANG_KEY.get(label, "")
    try:
        api.tabs.set_widget(TAB_EDITOR, "ed-body", "highlight", language)
    except AddonError:
        pass


# ---------------------------------------------------------------------------
# File system operations, told apart by side
# ---------------------------------------------------------------------------

def _exists(side: str, path: str) -> bool:
    if side == LOCAL:
        return os.path.lexists(path)
    try:
        return bool(api.device.exists(path, outside_root=True))
    except AddonError:
        return False


def _mkdir(side: str, path: str) -> None:
    if side == LOCAL:
        os.makedirs(path, exist_ok=True)
    else:
        api.device.mkdir(path)


def _remove(side: str, path: str, is_dir: bool) -> None:
    if side == LOCAL:
        if is_dir and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    else:
        api.device.remove(path, recursive=is_dir, outside_root=True)


def _rename(side: str, src: str, dst: str) -> None:
    if side == LOCAL:
        os.rename(src, dst)
    else:
        api.device.rename(src, dst, outside_root=True)


def new_folder(side: str, _checked: bool = False) -> None:
    if api is None:
        return
    name = api.ui.input("New folder", "Folder name:", default="")
    if name is None or not name.strip():
        return
    target = _norm(side, _join(side, st(side)["cwd"], name.strip()))

    def action():
        _mkdir(side, target)

    if _busy(side, f"creating {target}", action):
        say(f"created {target}", side=side)
        refresh(side)


def new_file(side: str, _checked: bool = False) -> None:
    if api is None:
        return
    name = api.ui.input("New file", "File name:", default="")
    if name is None or not name.strip():
        return
    target = _norm(side, _join(side, st(side)["cwd"], name.strip()))
    if _exists(side, target) and not api.ui.question(
            "New file", f"{_base(side, target)} already exists.\n"
                        "Truncate it to empty?", default=False):
        say("nothing was written", side=side)
        return

    def action():
        if side == LOCAL:
            Path(target).write_text("", encoding="utf-8")
        else:
            api.device.write_text(target, "", mkdir=True, outside_root=True)

    if _busy(side, f"creating {target}", action):
        say(f"created empty {target}", side=side)
        refresh(side)


def do_rename(side: str, entry: dict | None) -> None:
    if api is None:
        return
    entry = entry or selected(side)
    if entry is None:
        say("select a file or folder first", side=side, bad=True)
        return
    source = str(entry.get("path") or "")
    new = api.ui.input("Rename / Move", "New name, or a full path:",
                       default=str(entry.get("name") or ""))
    if new is None or not new.strip():
        return
    new = new.strip()
    target = _norm(side, new if _isabs(side, new) else _join(side, st(side)["cwd"], new))
    if target == source:
        say("that is already where it is", side=side)
        return
    if _exists(side, target):
        say(f"{target} already exists; nothing was moved", side=side, bad=True)
        return

    def action():
        _rename(side, source, target)

    if _busy(side, f"moving {entry.get('name')}", action):
        say(f"moved {source} -> {target}", side=side)
        refresh(side)


def do_delete(side: str, entry: dict | None) -> None:
    if api is None:
        return
    entry = entry or selected(side)
    if entry is None:
        say("select a file or folder first", side=side, bad=True)
        return
    path = str(entry.get("path") or "")
    is_dir = bool(entry.get("directory"))
    label = "folder (and everything inside it)" if is_dir else "file"
    if bool(_cfg("confirm_delete", True)) and not api.ui.question(
            "Delete", f"Delete this {label}?\n\n{path}\n\n"
                      "This cannot be undone.", default=False):
        say("delete cancelled", side=side)
        return

    def action():
        _remove(side, path, is_dir)

    if _busy(side, f"deleting {entry.get('name')}", action):
        say(f"deleted {path}", side=side)
        if editing and editing["side"] == side and _norm(side, editing["path"]) == path:
            close_editor(force=True)
        refresh(side)


def do_chmod(side: str, entry: dict | None) -> None:
    if api is None or side == LOCAL:
        return
    entry = entry or selected(side)
    if entry is None:
        say("select a file or folder first", side=side, bad=True)
        return
    path = str(entry.get("path") or "")
    current = format(stat_module.S_IMODE(int(entry.get("mode") or 0)), "04o")
    value = api.ui.input("Change permissions", "Octal mode (e.g. 755):", default=current)
    if value is None:
        return
    value = value.strip().replace("0o", "")
    if len(value) < 3:
        value = value.zfill(3)
    if not re.fullmatch(r"[0-7]{3,4}", value):
        say("give three or four octal digits (0-7)", side=side, bad=True)
        return
    holder: dict = {}

    def action():
        result = api.device.run(f"chmod {value} {SHQ(path)}", timeout=30)
        holder["code"] = int(result.get("code") or 0)
        holder["err"] = str(result.get("err") or "")

    if _busy(side, f"chmod {value} {entry.get('name')}", action):
        if holder.get("code"):
            say(f"chmod did not take: {holder.get('err') or 'unknown'}", side=side, bad=True)
        else:
            say(f"set {path} to {value}", side=side)
            refresh(side)


def show_note(title: str, body: str) -> None:
    """A read-only modal with a block of text (info / df / find)."""
    api.ui.dialog({"kind": "column", "children": [
        {"kind": "multi", "id": "note", "readonly": True, "value": body or "(nothing)"}]},
        title=title)


def do_info(side: str, entry: dict | None) -> None:
    if api is None:
        return
    entry = entry or selected(side)
    if entry is None:
        say("select a file or folder first", side=side, bad=True)
        return
    path = str(entry.get("path") or "")
    if side == LOCAL:
        try:
            info = os.stat(path)
            body = (f"path   : {path}\nsize   : {info.st_size} bytes\n"
                    f"mode   : {stat_module.filemode(info.st_mode)} ({_mode(entry)})\n"
                    f"modified: {_t(int(info.st_mtime))}")
        except OSError as error:
            body = str(error)
        show_note(f"Info: {path}", body)
        return

    holder: dict = {}

    def action():
        result = api.device.run(
            f"ls -ld {SHQ(path)} 2>&1; echo; stat {SHQ(path)} 2>&1", timeout=30)
        holder["out"] = str(result.get("out") or "")
        holder["err"] = str(result.get("err") or "")

    if _busy(side, f"asking about {entry.get('name')}", action):
        show_note(f"Info: {path}", holder.get("out") or holder.get("err") or "(no answer)")


def do_disk(side: str = DEVICE, _checked: bool = False) -> None:
    if api is None or side == LOCAL:
        return
    holder: dict = {}

    def action():
        holder["out"] = str(api.device.run("df -h 2>&1", timeout=30).get("out") or "")

    if _busy(side, "reading disk usage", action):
        show_note("Disk usage (df -h)", holder.get("out") or "(no answer)")


def do_find(side: str = DEVICE, _checked: bool = False) -> None:
    if api is None or side == LOCAL:
        return
    pattern = api.ui.input("Find files", "Name contains (below the current folder):",
                           default="")
    if pattern is None or not pattern.strip():
        return
    pattern = pattern.strip()
    depth = int(_cfg("find_max_depth", DEFAULTS["find_max_depth"]) or 4)
    maximum = int(_cfg("find_max_results", DEFAULTS["find_max_results"]) or 300)
    command = (f"find {SHQ(st(side)['cwd'])} -maxdepth {min(max(depth, 1), 20)} "
               f"-iname {SHQ('*' + pattern + '*')} -print 2>/dev/null "
               f"| head -n {min(max(maximum, 1), 5000)}")
    holder: dict = {}

    def action():
        holder["out"] = str(api.device.run(command, timeout=60).get("out") or "")

    if _busy(side, f"finding *{pattern}* under {st(side)['cwd']}", action):
        lines = [line for line in holder.get("out", "").splitlines() if line.strip()]
        show_note(f"Find '{pattern}' below {st(side)['cwd']}",
                  "\n".join(lines) or "(no matches)")
        say(f"{len(lines)} match(es) for '{pattern}' (max {maximum})", side=side)


def copy_path(side: str, entry: dict | None = None) -> None:
    if api is None:
        return
    entry = entry or selected(side)
    path = str(entry.get("path")) if entry else st(side)["cwd"]
    api.log(path)
    api.ui.information("Full path", path)
    say(path, side=side)


# ---------------------------------------------------------------------------
# Copy / paste (across the pages it transfers over SFTP; on one it copies)
# ---------------------------------------------------------------------------

def do_copy(side: str, entry: dict | None) -> None:
    global clip
    entry = entry or selected(side)
    if entry is None:
        say("select something to copy first", side=side, bad=True)
        return
    clip = {"side": side, "path": str(entry.get("path") or ""),
            "name": str(entry.get("name") or _base(side, str(entry.get("path") or ""))),
            "directory": bool(entry.get("directory"))}
    say(f"copied {clip['name']} ({side})", side=side)


def _confirm_overwrite(side: str, target: str) -> bool:
    if not _exists(side, target):
        return True
    return api.ui.question("Paste", f"{target} already exists.\nOverwrite it?",
                           default=False)


def do_paste(side: str, _checked: bool = False) -> None:
    if api is None:
        return
    if not clip:
        say("the clipboard is empty", side=side, bad=True)
        return
    target = _norm(side, _join(side, st(side)["cwd"], clip["name"]))
    if target == clip["path"]:
        say("that is already here", side=side)
        return
    if not _confirm_overwrite(side, target):
        say("paste cancelled", side=side)
        return
    src = clip["path"]
    is_dir = clip["directory"]
    same = side == clip["side"]

    def action():
        if same:
            if side == LOCAL:
                if is_dir:
                    shutil.copytree(src, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, target)
            else:
                api.device.run(f"cp -a {SHQ(src)} {SHQ(target)}", timeout=120)
        elif side == LOCAL:
            api.device.download(src, target, outside_root=True)
        else:
            api.device.upload(src, target, outside_root=True)

    if _busy(side, f"pasting {clip['name']}", action):
        say(f"pasted {clip['name']} into {st(side)['cwd']}", side=side)
        refresh(side)


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------

def upload_typed(side: str, _checked: bool = False) -> None:
    """Device page: send a file from this computer into the current folder."""
    if api is None or side == LOCAL:
        return
    raw = api.ui.input("Upload a file", "Path of a file on this computer:", default="")
    if raw is None or not raw.strip():
        return
    source = Path(str(raw).strip()).expanduser()
    if not source.is_file():
        say(f"no such local file: {source}", side=side, bad=True)
        return
    remote_name = api.ui.input("Upload a file", "Name on the device:",
                               default=source.name) or source.name
    target = posixpath.normpath(posixpath.join(st(side)["cwd"], remote_name.strip()))
    if api.device.exists(target, outside_root=True) and not api.ui.question(
            "Upload", f"{target} already exists on the device.\nOverwrite it?", default=False):
        say("upload cancelled", side=side)
        return
    holder: dict = {}

    def action():
        holder["n"] = source.stat().st_size
        api.device.upload(str(source), target, outside_root=True)

    if _busy(side, f"uploading {source.name}", action):
        say(f"uploaded {_sz(holder.get('n'))} to {target}", side=side)
        refresh(side)


def upload_selected_to_device(_checked: bool = False) -> None:
    """Local page: send its selected file into the device folder that is open."""
    if api is None:
        return
    entry = selected(LOCAL)
    if entry is None:
        say("select a local file first", side=LOCAL, bad=True)
        return
    if entry.get("directory"):
        say("pick a file, not a folder; folders paste one at a time", side=LOCAL, bad=True)
        return
    source = str(entry.get("path") or "")
    dest_folder = st(DEVICE)["cwd"] or default_root()
    target = posixpath.normpath(posixpath.join(dest_folder, str(entry.get("name") or "")))
    if api.device.exists(target, outside_root=True) and not api.ui.question(
            "Upload to device", f"{target} already exists on the device.\nOverwrite it?",
            default=False):
        say("upload cancelled", side=LOCAL)
        return
    global clip
    clip = {"side": "local", "path": source,
           "name": str(entry.get("name") or ""), "directory": False}

    def action():
        api.device.upload(source, target, outside_root=True)

    if _busy(LOCAL, f"uploading {entry.get('name')}", action):
        say(f"uploaded {entry.get('name')} to {target}", side=LOCAL)
        refresh(DEVICE)


def download_typed(side: str, entry: dict | None = None) -> None:
    """Device page: save a device file onto this computer."""
    if api is None or side == LOCAL:
        return
    entry = entry or selected(side)
    if entry is None:
        say("select a file to download first", side=side, bad=True)
        return
    if entry.get("directory"):
        say("pick a file, not a folder, to download", side=side, bad=True)
        return
    path = str(entry.get("path") or "")
    name = str(entry.get("name") or posixpath.basename(path))
    folder = str(_cfg("download_dir", "") or "").strip() or str(api.paths.data)
    default_local = str(Path(folder) / name)
    raw = api.ui.input("Download", "Save to this local path:", default=default_local)
    if raw is None or not raw.strip():
        return
    destination = Path(str(raw).strip()).expanduser()
    if destination.is_dir():
        destination = destination / name
    if destination.exists() and not api.ui.question(
            "Download", f"{destination} already exists.\nOverwrite it?", default=False):
        say("download cancelled", side=side)
        return

    def action():
        api.device.download(path, str(destination), outside_root=True)

    if _busy(side, f"downloading {name}", action):
        say(f"saved {name} to {destination} ({_sz(entry.get('size'))})", side=side)


# ---------------------------------------------------------------------------
# The right-click / Actions menu
# ---------------------------------------------------------------------------

MENU_LABELS = {
    "open": "Open", "edit": "Edit", "rename": "Rename / Move",
    "delete": "Delete", "chmod": "Chmod", "copy": "Copy", "paste": "Paste",
    "info": "Info", "path": "Show path", "mkdir": "New folder",
    "newfile": "New file", "upload": "Upload...", "download": "Download...",
    "up2dev": "Upload to device", "disk": "Disk usage", "find": "Find...",
    "refresh": "Refresh",
}


def menu_for(side: str, entry: dict | None) -> list[tuple[str, str]]:
    """The action list offered on a right-click, set by page and row kind."""
    local = side == LOCAL
    ids: list[str] = []
    if entry is not None:
        if entry.get("directory"):
            ids += ["open", "rename", "copy", "path"]
            if not local:
                ids += ["chmod"]
            ids += ["info", "delete"]
        else:
            ids += ["edit", "rename", "copy"]
            ids += ["up2dev"] if local else ["download"]
            if not local:
                ids += ["chmod"]
            ids += ["info", "path", "delete"]
    ids += ["mkdir", "newfile"]
    if local:
        ids += (["paste"] if clip else []) + ["refresh"]
    else:
        ids += ["upload"] + (["paste"] if clip else []) + ["refresh", "disk", "find"]
    out: list[tuple[str, str]] = []
    seen = set()
    for ident in ids:
        if ident in seen:
            continue
        seen.add(ident)
        out.append((MENU_LABELS.get(ident, ident), ident))
    return out


def run_action(side: str, ident: str, entry: dict | None) -> None:
    """One menu choice, applied to ``entry`` (or the page when there is none)."""
    if ident == "open":
        if entry and entry.get("directory"):
            navigate(side, str(entry.get("path") or ""))
        elif entry:
            open_editor(side, entry)
    elif ident == "edit":
        open_editor(side, entry)
    elif ident == "rename":
        do_rename(side, entry)
    elif ident == "delete":
        do_delete(side, entry)
    elif ident == "chmod":
        do_chmod(side, entry)
    elif ident == "info":
        do_info(side, entry)
    elif ident == "path":
        copy_path(side, entry)
    elif ident == "copy":
        do_copy(side, entry)
    elif ident == "paste":
        do_paste(side)
    elif ident == "mkdir":
        new_folder(side)
    elif ident == "newfile":
        new_file(side)
    elif ident == "upload":
        upload_typed(side)
    elif ident == "download":
        download_typed(side, entry)
    elif ident == "up2dev":
        upload_selected_to_device()
    elif ident == "disk":
        do_disk(side)
    elif ident == "find":
        do_find(side)
    elif ident == "refresh":
        refresh(side)


def open_menu(side: str, entry: dict | None = None) -> None:
    """Show the page's actions as a menu at the pointer, then run the pick."""
    if api is None or working:
        return
    items = menu_for(side, entry)
    picked = api.ui.choose([label for label, _ident in items])
    if picked is None:
        return
    for label, ident in items:
        if label == picked:
            run_action(side, ident, entry)
            return


def go_up(side: str) -> None:
    here = st(side)["cwd"]
    if not here:
        return
    if side == DEVICE and here == "/":
        say("you are already at the root", side=side)
        return
    parent = _parent(side, here.rstrip("/") if side == DEVICE else here)
    navigate(side, parent or (here if side == LOCAL else "/"))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

CLICK_BASE = {
    "go": lambda side: navigate(side, _read(pid(side, "path"), "value")),
    "up": go_up,
    "refresh": refresh,
    "root": lambda side: navigate(side, "/"),
    "home": lambda side: navigate(side, start_side(side)),
    "menu": lambda side: open_menu(side, selected(side)),
    "mkdir": new_folder,
    "upload": upload_typed,
    "download": lambda side: download_typed(side, selected(side)),
    "paste": do_paste,
    "up2dev": lambda side: upload_selected_to_device(),
}

EDITOR_CLICKED = {"ed-save": save_editor, "ed-reload": reload_editor,
                  "ed-close": lambda: close_editor()}


def _browse_event(side: str, base: str, event: str, payload: dict) -> None:
    """An interaction with a control on one of the two pages."""
    if event == "clicked":
        handler = CLICK_BASE.get(base)
        if handler is not None and not working:
            handler(side)
        return
    if event == "returned" and base == "path":
        if not working:
            navigate(side, _read(pid(side, "path"), "value"))
        return
    if event == "changed" and base in ("filter", "sort"):
        render(side)
        return
    if event == "toggled" and base == "hidden":
        render(side)
        return
    if event == "changed" and base == "places":
        if _places_guard or working:
            return
        value = str(_read(pid(side, "places"), "value") or "")
        if value and value != "Places...":
            if side == LOCAL:
                try:
                    value = str(Path(value).expanduser())
                except (OSError, RuntimeError):
                    pass
            navigate(side, value)
        return
    if event == "context" and base == "files":
        if not working:
            open_menu(side, _entry(side, payload.get("text")))
        return
    if event == "selected" and base == "files":
        if working:
            return
        name = payload.get("text")
        if name:
            on_activate(side, str(name))
            return
        entry = selected(side)
        if entry is not None:
            where = "folder" if entry.get("directory") else "file"
            say(f"{where} {entry.get('path')}  ({_sz(entry.get('size'))})", side=side)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A control reported something, on the Berry tab or on the editor tab.

    Every button is declared ``"on": ["clicked"]`` and every edit box and listing
    names the interactions it cares about, so the app calls this with the tab it
    lives on and the spec id - the one route that also works when the addon has
    been compiled into a program of its own, where the widgets live in the app's
    process and cannot be handed over.
    """
    if api is None:
        return
    identifier = str(identifier)
    event = str(event)
    payload = payload or {}

    if str(tab) == TAB:
        _browse_event(side_of_id(identifier), base_of_id(identifier), event, payload)
        return

    if str(tab) == TAB_EDITOR:
        if event == "clicked":
            handler = EDITOR_CLICKED.get(identifier)
            if handler is not None and not working:
                handler()
            return
        if event == "changed" and identifier == "ed-lang":
            set_editor_language(str(_read_editor("ed-lang", "value") or "auto"))
            return


# ---------------------------------------------------------------------------
# The hooks the app calls
# ---------------------------------------------------------------------------

def focus_berry(_checked: bool = False) -> None:
    if api is None:
        return
    api.tabs.activate(TAB)
    side = DEVICE if not st(DEVICE)["cwd"] and st(LOCAL)["cwd"] else DEVICE
    navigate(side, st(side)["cwd"] or start_side(side))


def init(plugin_api) -> None:
    """Build the Berry tab from the saved config."""
    global api, STATE
    api = plugin_api
    STATE = {DEVICE: _fresh_state(), LOCAL: _fresh_state()}
    api.config.declare(FIELDS, DEFAULTS,
                       title="Berry file manager - addons/_data/berry/config.json")
    build_tab()
    api.ui.menu_item("Open Berry file manager", focus_berry, menu="Tools")


def start() -> None:
    """Set both address bars so the first Go knows where to head."""
    if api is None:
        return
    for side in (DEVICE, LOCAL):
        if not st(side)["cwd"]:
            st(side)["cwd"] = start_side(side)
            api.tabs.set_widget(TAB, pid(side, "path"), "value", st(side)["cwd"])
    say("Ready - Device and Local pages; double-click a file to edit it", side=DEVICE)
    say("Ready - your local files; upload or copy/paste to the device", side=LOCAL)


def shutdown() -> None:
    global api, clip, editing, STATE
    if api is not None:
        try:
            api.tabs.close(TAB_EDITOR)
        except AddonError:
            pass
    api = None
    clip = None
    editing = None
    STATE = {}


def _sync_chrome(side: str) -> None:
    global _places_guard
    _places_guard = True
    try:
        api.tabs.set_widget(TAB, pid(side, "places"), "items", ["Places..."] + _places(side))
    except AddonError:
        pass
    finally:
        _places_guard = False
    try:
        api.tabs.set_widget(TAB, pid(side, "hidden"), "checked", bool(_cfg("show_hidden", False)))
        api.tabs.set_widget(TAB, pid(side, "sort"), "value",
                            SORT_LABELS.get(str(_cfg("sort_by", "name")), "Name"))
    except AddonError:
        pass
    render(side)


def on_config(payload: dict) -> None:
    """The config changed in Settings: make both pages match the file."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    _sync_chrome(DEVICE)
    _sync_chrome(LOCAL)
    say("config reloaded from addons/_data/berry/config.json", side=DEVICE)


def on_profile_changed(payload: dict) -> None:
    """A different device is selected: drop the device page to its own start."""
    if api is None:
        return
    st(DEVICE)["cwd"] = default_root()
    api.tabs.set_widget(TAB, pid(DEVICE, "path"), "value", st(DEVICE)["cwd"])
    api.tabs.set_widget(TAB, pid(DEVICE, "files"), "rows", [])
    say("profile changed - press Go to list the new device", side=DEVICE)


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
