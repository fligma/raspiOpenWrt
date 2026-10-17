#!/usr/bin/env python3
"""Berry web dashboard: put the Berry web UI on the device and get uhttpd serving it.

This is the addon a person installs from the store when the router has no web
interface yet, or has one that has fallen behind. It does four things:

* **looks** - one command over SSH asks the connected host whether the folder is
  there, whether uhttpd points at it, whether ``python3`` is installed and
  whether the page answers. What it finds decides where the tab opens: a live
  dashboard lands on the status page, anything else lands on the installer;
* **installs** - the tree under ``src/www_rasconf`` in this project's repository
  comes down from GitHub (or is read off this machine) and is written into the
  served folder over SFTP, with a backup first, the exec bit on the CGI entry and
  a check that the page answers afterwards;
* **sets the web server up** - the uhttpd options are written with ``uci``, so
  the section that serves the folder is created or repaired rather than hand
  edited, and the file ``/etc/config/uhttpd`` can be read as it stands;
* **moves the folder** - ``/www_rasconf`` to ``/www_berry``, or anywhere else, in
  one action that renames the folder, points uhttpd at the new name, commits and
  restarts.

The look of the page follows the program: ``css/theme.css`` is generated from the
theme the app is wearing and uploaded with the rest, so the dashboard is the same
colours as the window that installed it.

Nothing that a user might want to change is hardcoded. The repository, the ref,
the folder inside it, the patterns that are never sent, the served folder's name,
the uhttpd section, the port and the restart command all live in
``addons/_data/berry_web/config.json``, shown as a form by **Edit settings** on
this addon's tab in the store's Addon manager page. That file is the one the
retired **Rasconf web installer** kept its own settings in - the keys are
deliberately named the same, so an install that existed before carries its
settings straight across.

The tab is built from a declarative spec and its buttons report back through
``on_widget``, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab
"""

from __future__ import annotations

import fnmatch
import http.client
import json
import posixpath
import re
import shutil
import ssl
import time
from collections.abc import Callable
from typing import Any
from pathlib import Path
from urllib.parse import quote

# Qt-free contract half: runs standalone and in tests.
from addon_common import AddonError, config_get, find_addon_picture, merge_config

ADDON = {
    "name": "Berry web dashboard",
    # 1.1 is the same code as 1.0, put out again so the store offers an update:
    # a program built before the launcher was repaired dies the moment it is
    # started, and the only way to replace one is to install it afresh.
    # 1.2 is the check that could not be answered. Asking the host what it has
    # used to leave one address unbounded - a port nobody answers costs the
    # router's own TCP patience, two minutes, and the app gave up on the command
    # first and reported the made-up -1 as though the device had said it. Every
    # ask now bounds itself, a folder that holds the page is looked for rather
    # than assumed, and the move that renames it starts from what the device
    # said instead of from a setting an older install never wrote.
    # 1.3 drops the addon's own GitHub token field. A profile has one token, kept
    # in Settings > Connection, and the switch on this addon's tab in the store's
    # Addon manager decides whether it is handed over; the old key is lifted into
    # the profile by the app the first time it loads, so nothing stops working.
    # 1.4 is the layout pass over the store: the lines that wrap on this tab now
    # hug their own height, so the room a page has over falls to its tables.
    "version": "1.4",
    "author": "fligma",
    "description": "Looks for the Berry dashboard on the connected host and, when "
                   "it is not there, installs src/www_rasconf from GitHub over "
                   "SFTP: a backup, the files, the theme this program is wearing, "
                   "the uhttpd section written with uci, the exec bit on the CGI "
                   "entry, a restart and a check that the page answers. Also "
                   "views and edits those options, finds which folder on the "
                   "device holds the page, and can move that one to /www_berry "
                   "- an install left in /www_rasconf by an older setup "
                   "included.",
    "requires": ["ssh", "sftp"],
    # The files go up over the profile's own session. "both" so a person can
    # switch this addon onto a connection of its own, which is also what lets a
    # long transfer hold one session open instead of one per file.
    "connection": "both",
    "api": 1,
}

TAB = "Berry web"
PAGE = "Berry dashboard"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the job in progress, or nothing
last_check: dict = {}       # what the device last said about itself

# --- what can be configured, and what it starts out as ---

#: The keys the retired installer used are kept as they were, so a translated
#: settings dict means something here without a single value being rewritten.
DEFAULTS = {
    "source": "GitHub",
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "repo_path": "src/www_rasconf",
    "local_dir": "",
    "subdir": "",
    "skip": ["README.md"],
    "keep": ["cgi-bin/hash.file"],
    "skip_unchanged": True,
    "remove_extra": False,
    "backup_first": True,
    "backup_dir": "/tmp/berry_web_backups",
    "make_executable": True,
    "cgi_entry": "cgi-bin/index.py",
    "restart_command": ["/etc/init.d/uhttpd restart"],
    "verify": True,
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
    # Three keys this addon adds: the served folder by name rather than by the
    # profile's root plus a subfolder, the uhttpd section that serves it, and
    # whether the page is repainted in this program's colours.
    "web_home": "/www_berry",
    "uci_section": "berry",
    "theme_sync": True,
}

FIELDS = [
    {"key": "source", "label": "Files come from", "kind": "choice",
     "choices": ["GitHub", "Local folder"],
     "hint": "GitHub needs no checkout on this machine; a local folder is used "
             "when the repository is already here or there is no internet"},
    {"key": "owner", "label": "GitHub owner", "kind": "text",
     "hint": "The account or organisation the repository belongs to"},
    {"key": "repo", "label": "Repository", "kind": "text"},
    {"key": "ref", "label": "Branch, tag or commit", "kind": "text",
     "hint": "main, a tag like v1.6, or a full commit sha"},
    {"key": "repo_path", "label": "Folder inside the repository", "kind": "text",
     "hint": "Its contents are what gets installed"},
    {"key": "local_dir", "label": "Local folder", "kind": "text",
     "hint": "Used when the files come from a local folder; blank means the "
             "source folder of the current profile",
     "placeholder": "the profile's source folder"},

    {"key": "subdir", "label": "Subfolder inside the served folder",
     "kind": "text",
     "hint": "Blank installs straight into the served folder named on the uhttpd "
             "page, which is where the web server looks",
     "placeholder": "straight into it"},
    {"key": "skip", "label": "Never send these", "kind": "list",
     "hint": "Comma separated names or * patterns, matched against the path "
             "inside the folder being installed"},
    {"key": "keep", "label": "Never delete these", "kind": "list",
     "hint": "Files on the device that are not in the download but must stay - "
             "hash.file holds the PIN the dashboard asks for"},
    {"key": "skip_unchanged", "label": "Leave a file alone when its size matches",
     "kind": "bool", "hint": "Makes a repeat install fast. Untick to write every "
                             "file again whatever the device has"},
    {"key": "remove_extra", "label": "Tidy away files that are not in the download",
     "kind": "bool", "hint": "Only ever removes what is under the served folder "
                             "and is not kept by the list above"},

    {"key": "backup_first", "label": "Back the folder up first", "kind": "bool"},
    {"key": "backup_dir", "label": "Where the backup goes", "kind": "text",
     "hint": "On the device. One tarball per install, named with the time"},

    {"key": "uci_section", "label": "uhttpd section", "kind": "text",
     "hint": "The section of /etc/config/uhttpd that serves this folder. uci "
             "creates it when it is not there",
     "placeholder": "berry"},
    {"key": "web_home", "label": "Folder the web server serves", "kind": "text",
     "hint": "An absolute path on the device. /www_berry is what this dashboard "
             "is set up for; /www_rasconf is where an older install left it",
     "placeholder": "/www_berry"},
    {"key": "web_port", "label": "Port the dashboard listens on", "kind": "number",
     "minimum": 1, "maximum": 65535,
     "hint": "Written into the section's listen_http as 0.0.0.0:<port>"},
    {"key": "web_path", "label": "Path of the dashboard on that port", "kind": "text",
     "hint": "What the browser and the checks ask the device for"},
    {"key": "cgi_entry", "label": "Entry file to make executable", "kind": "text",
     "hint": "Relative to the served folder"},
    {"key": "make_executable", "label": "Make the CGI entry executable", "kind": "bool",
     "hint": "uhttpd runs the entry file itself, so it needs the exec bit"},
    {"key": "theme_sync", "label": "Paint the page in this program's colours",
     "kind": "bool",
     "hint": "Writes css/theme.css from the theme the app is wearing"},
    {"key": "restart_command", "label": "Commands to restart the web server",
     "kind": "multiline",
     "hint": "One per line, run on the device once the files are written"},
    {"key": "verify", "label": "Check the page answers afterwards", "kind": "bool"},
]

#: The install form, on the installer page. The GitHub token is not a field of
#: this addon: it belongs to the profile, is written in Settings > Connection and
#: reaches a request through ``profile_token`` while the switch on this addon's
#: tab in the store's Addon manager is on.
GROUPS_INSTALL = [
    ("Where the files come from", ["source", "owner", "repo", "ref", "repo_path",
                                   "local_dir"]),
    ("How they are written", ["subdir", "skip", "keep", "skip_unchanged",
                               "remove_extra"]),
    ("Before the files go over", ["backup_first", "backup_dir"]),
]

#: The uhttpd form, on the uhttpd page: what the web server serves and how it is
#: brought back up afterwards.
GROUPS_UCI = [
    ("What the web server serves", ["uci_section", "web_home", "web_port",
                                    "web_path"]),
    ("The page's own file", ["cgi_entry", "make_executable", "theme_sync"]),
    ("After a change", ["restart_command", "verify"]),
]

FIELDS_BY_KEY = {str(field["key"]): field for field in FIELDS}
KIND_TO_WIDGET = {"text": "line", "number": "spin", "bool": "check",
                  "choice": "combo", "list": "line", "multiline": "multi"}

#: Buttons that stand down while a job runs; ``stop`` is the other way round.
BUTTONS = (("recheck", "Re-check"), ("open", "Open dashboard"),
           ("fetch", "Fetch files"), ("install", "Install"),
           ("restart", "Restart web server"), ("check", "Check page"),
           ("read_uci", "Read"), ("apply_uci", "Apply"), ("move_home", "Move folder"),
           ("refresh_raw", "Refresh"), ("copy_raw", "Copy to editor"),
           ("push", "Save to config"), ("pull", "Reload from config"))

#: Step flow across the top: seven chips for twelve stages.
STEPS = (("files", "Files"), ("compare", "Compare"), ("backup", "Backup"),
         ("write", "Write"), ("serve", "Serve"), ("restart", "Restart"),
         ("check", "Check"))

STAGE_TO_STEP = {"tree": "files", "fetch": "files", "remote": "compare",
                 "ready": "compare", "confirm": "compare", "backup": "backup",
                 "write": "write", "prune": "write", "theme": "serve",
                 "uci": "serve", "after": "restart", "verify": "check"}

#: The notebook's own pages, by position: where the tab lands after a check.
STATUS_PAGE, INSTALL_PAGE, UHTTPD_PAGE = 0, 1, 2

STOP = "stop"
HANDLERS: dict = {}

#: Fallback file list when GitHub gives none: the dashboard as this addon was
#: written, so a refused request still installs something that works.
FALLBACK_TREE = [
    "assets/250px-BrownSpiderMonkey_(edit2).jpg",
    "assets/android-chrome-192x192.png",
    "assets/android-chrome-512x512.png",
    "assets/apple-touch-icon.png",
    "assets/favicon-16x16.png",
    "assets/favicon-32x32.png",
    "assets/favicon.ico",
    "assets/site.webmanifest",
    "cgi-bin/auth.py",
    "cgi-bin/index.py",
    "cgi-bin/netinfo.py",
    "cgi-bin/pages.py",
    "cgi-bin/store.py",
    "cgi-bin/sysinfo.py",
    "css/index.css",
    "css/main.css",
    "css/theme.css",
    "js/api.js",
    "js/format.js",
    "js/index.js",
    "js/main.js",
    "js/render.js",
]

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QTreeWidget, QPlainTextEdit { font-family: Consolas, monospace; }
QProgressBar { border: 1px solid #3c3c3c; border-radius: 4px; text-align: center;
               background-color: #1b1b1b; }
QProgressBar::chunk { background-color: #c51a4a; border-radius: 3px; }
QPushButton { padding: 7px 14px; }
QTabWidget::pane { border: 1px solid #3c3c3c; border-radius: 6px; top: -1px; }
QTabBar::tab { background-color: #1d1d1d; color: #9a9a9a; padding: 5px 14px;
               border: 1px solid #3c3c3c; border-bottom: none;
               border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background-color: #24141a; color: #e8e8e8;
                        border-color: #c51a4a; }
QTabBar::tab:hover:!selected { color: #d0d0d0; }
"""

BANNER_STYLE = ("QLabel { color: #e8e8e8; font-size: 13pt; font-weight: 600;"
                " padding: 4px 2px 2px 2px; }")

#: Step chip moods: reached, doing, waiting, off.
_STEP_STYLES = {
    "done":    "color: #23d18b; font-weight: 600; padding: 2px 4px;",
    "active":  "color: #ffffff; background-color: #c51a4a; border-radius: 9px;"
               " padding: 2px 9px; font-weight: 700;",
    "pending": "color: #6f6f6f; padding: 2px 4px;",
    "off":     "color: #454545; padding: 2px 4px;",
}
_STEP_GLYPHS = {"done": "\u2713 ", "active": "\u25cf ", "pending": "\u25cb ",
                "off": "\u00b7 "}


def _chip_style(foreground: str, background: str, edge: str) -> str:
    return (f"color: {foreground}; background-color: {background};"
            f" border: 1px solid {edge}; border-radius: 9px;"
            f" padding: 1px 9px; font-weight: 600;")


#: One style per tone for the count badges.
_CHIP_STYLES = {
    "crimson": _chip_style("#ff8aa6", "#2a1218", "#7c2140"),
    "gray":    _chip_style("#9a9a9a", "#1e1e1e", "#3c3c3c"),
    "amber":   _chip_style("#ffb020", "#241d12", "#5a4519"),
    "green":   _chip_style("#23d18b", "#12251c", "#1f5c40"),
}

#: Plan row tints by action.
ACTION_COLORS = {"queued": "#5fb3ff", "new": "#5fb3ff", "written": "#23d18b",
                 "replaced": "#ffb020", "written again": "#ffb020",
                 "already there": "#777777"}

#: Shell chars that must never reach a command line.
_UNSAFE = re.compile(r"[\"'`$;|&<>\\\n\r\t]")
_RELATIVE = re.compile(r"^[A-Za-z0-9._/-]+$")
#: A device folder of its own: an absolute path, nothing that a shell or a
#: ``uci`` value could read as anything else.
_ABSOLUTE = re.compile(r"^/[A-Za-z0-9._/-]*$")
#: A uci section name is a bare word.
_SECTION = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
#: What a page calls itself, read off its own ``<title>``.
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

#: Markers that say the page which answered really is this dashboard. The
#: dashboard writes its own name into the title of both its pages and into the
#: id of the block its script reads its settings from, so one of those is proof.
#: A bare ``<html>`` is not: the router's own page answers on the same port and
#: is every bit as much html as this one is.
BERRY_MARKS = ("berry", "berry-config", "pin required")

#: The app's theme tokens, and the variables the dashboard's own stylesheet
#: already declares for them. Overriding those twelve lines is all it takes to
#: repaint the page, so nothing here has to know how the page is laid out.
THEME_VARS = (
    ("base", "--color-background"),
    ("surface", "--color-surface"),
    ("soft", "--color-surface-raised"),
    ("header", "--color-surface-tab"),
    ("border", "--color-line"),
    ("sunken", "--color-field"),
    ("accent", "--color-accent"),
    ("accent_deep", "--color-accent-hover"),
    ("text", "--color-text"),
    ("muted", "--color-muted"),
    ("note", "--color-muted-strong"),
    ("ok", "--color-success"),
    ("close_hot", "--color-error"),
)

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# --- small helpers ---

def one_line(value: Any) -> str:
    """One clean line of text from whatever the form holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def safe_path(value: Any, fallback: str = "") -> str:
    """A relative path that is safe to put into a shell command, or the fallback."""
    text = one_line(value).replace("..", "")
    text = _UNSAFE.sub("", text).strip("/ ")
    return text if text and _RELATIVE.match(text) else fallback


def home_path(value: Any, fallback: str = "") -> str:
    """A served folder as a clean absolute device path, or the fallback.

    ``/www_berry`` and ``www_berry`` both mean the one thing here; anything
    holding a quote, a dollar sign, a space or a ``..`` means nothing at all.
    The word is refused rather than tidied up, because this is a path that goes
    into ``uci set`` and into ``mv``, and a folder quietly rewritten from
    ``/www my dir`` to ``/wwwmydir`` would move a dashboard nobody asked for.
    """
    text = one_line(value)
    if ".." in text or _UNSAFE.search(text):
        return fallback
    if text and not text.startswith("/"):
        text = "/" + text
    text = text.rstrip("/") or ""
    if not text or not _ABSOLUTE.match(text):
        return fallback
    return text


def uci_name(value: Any, fallback: str = "berry") -> str:
    """A uci section name, or the fallback when it is not a bare word."""
    text = one_line(value).strip()
    return text if _SECTION.match(text) else fallback


def clean_list(value: Any) -> list[str]:
    """A config value as a list of clean entries, whatever shape it arrived in."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else re.split(r"[,\n]", str(value))
    out: list[str] = []
    for item in items:
        text = one_line(item)
        if text and text not in out:
            out.append(text)
    return out


def quoted(text: str) -> str:
    """A device path or name as one safe shell word."""
    return '"' + str(text).replace('"', "") + '"'


def squoted(text: str) -> str:
    """A value inside uci's own single quotes; nothing in it can close them."""
    return "'" + str(text).replace("'", "") + "'"


def human(size: int) -> str:
    """A byte count the way a file manager would say it."""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"


def matches(path: str, patterns: list[str]) -> bool:
    """Whether one relative path is covered by a skip or keep pattern."""
    name = posixpath.basename(path)
    for pattern in patterns or []:
        text = str(pattern).strip()
        if not text:
            continue
        if text.endswith("/"):
            if path.startswith(text) or fnmatch.fnmatch(path, text + "*"):
                return True
        elif fnmatch.fnmatch(path, text) or fnmatch.fnmatch(name, text):
            return True
    return False


def port_of(value: Any, fallback: int = 8989) -> int:
    """A port number the device will answer on, clamped to what a port is."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return min(max(number or fallback, 1), 65535)


# --- the four pure answers a test can ask for, without a device ---

def theme_css(colors: dict) -> str:
    """``css/theme.css``: this program's colours, in the page's own variables.

    The dashboard's stylesheet reads its colours from twelve custom properties
    declared once in ``css/main.css``. Overriding those is the whole job, which
    is why this file carries no layout of its own and can be dropped onto any
    version of the page.
    """
    colors = colors if isinstance(colors, dict) else {}
    lines = ["/* Written by the Berry web dashboard addon: the colours of the",
             "   Berry Manager theme that installed this page. Edit the theme,",
             "   not this file - an install writes it again. */", ":root {"]
    said = False
    for token, variable in THEME_VARS:
        value = str(colors.get(token) or "").strip()
        if not _HEX.match(value):
            continue                        # a colour this page cannot use
        lines.append(f"    {variable}: {value.lower()};")
        said = True
    lines.append("}")
    if not said:
        return ""
    return "\n".join(lines) + "\n"


def uci_script(section: str, home: str, port: Any) -> str:
    """The ``uci`` commands that make uhttpd serve one folder on one port.

    The section is created only when it is missing, so an existing router's own
    settings are not replaced wholesale; every option this dashboard needs is
    then set to what the form says. ``add_list`` is asked quietly, because uhttpd
    keeps a listen address that is already there and a duplicate would stop the
    server starting.
    """
    name = uci_name(section)
    folder = home_path(home, DEFAULTS["web_home"])
    number = port_of(port)
    return "\n".join([
        'uci -q get uhttpd.{name} >/dev/null || uci set uhttpd.{name}=uhttpd',
        'uci set uhttpd.{name}.home={home}',
        'uci -q add_list uhttpd.{name}.listen_http={listen}',
        'uci set uhttpd.{name}.cgi_prefix={prefix}',
        'uci commit uhttpd',
        'echo done',
    ]).format(name=name, home=squoted(folder), listen=squoted(f"0.0.0.0:{number}"),
             prefix=squoted("/cgi-bin"))


def apply_uci_lines(section: str, restart: list[str]) -> list[str]:
    """The line that reads a section back, and the ones that bring it up again."""
    name = uci_name(section)
    lines = [f'echo "== uhttpd.{name} after =="',
             f'uci -q show uhttpd.{name} || echo "(there is no such section)"']
    for one in restart or []:
        text = one_line(one)
        if text:
            lines.append(f'{text} || {{ echo "note: {text} did not succeed"; }}')
    return lines


def page_title(body: Any) -> str:
    """What a page calls itself, from its ``<title>``; nothing when it has none."""
    found = _TITLE.search(str(body or ""))
    return one_line(found.group(1)) if found else ""


def parse_check(result: Any) -> dict:
    """Sort one answer from the device into what the status page needs.

    The check script says each thing on a line of its own, with a word in front,
    so this never has to guess which line of a listing was meant as a folder. What
    it returns decides where the tab opens: a served folder and a page that answers
    with this dashboard is a live install.
    """
    out = str(result.get("out") if isinstance(result, dict) else (result or ""))
    info = {"folder": False, "files": 0, "home": "", "python": "",
            "answered": False, "url": "", "is_berry": False, "https": False,
            "sections": {}, "served": [], "no_fetch": False,
            "code": int(result.get("code") or 0) if isinstance(result, dict) else 0,
            "body": ""}
    body: list[str] = []
    for line in out.splitlines():
        text = line.strip()
        if text.startswith("FOLDER "):
            info["folder"] = text[7:].lower() == "yes"
        elif text.startswith("FILES "):
            try:
                info["files"] = int(text[6:].split()[0])
            except (IndexError, ValueError):
                info["files"] = 0
        elif text.startswith("HOME "):
            info["home"] = text[5:].strip().strip("'\"")
        elif text.startswith("HTTPS "):
            # ``on`` when the section listens over https or redirects to it;
            # the page is opened with the scheme the device's own config names.
            info["https"] = text[6:].strip() == "on"
        elif text.startswith("SH "):
            # ``uhttpd.lan.home='/www'``, as the device's own uci showed it.
            name, _, where = text[3:].strip().partition("=")
            parts = name.split(".")
            where = where.strip().strip("'\"")
            if len(parts) >= 3 and parts[2] == "home" and where:
                info["sections"][parts[1]] = where
        elif text.startswith("SERVED "):
            words = text[7:].split()
            if words:
                try:
                    count = int(words[1])
                except (IndexError, ValueError):
                    count = 0
                info["served"].append({"folder": words[0], "files": count})
        elif text.startswith("NOFETCH"):
            info["no_fetch"] = True
        elif text.startswith("PY "):
            word = text[3:].strip()
            info["python"] = "" if word in ("-", "none") else word
        elif text.startswith("ANSWERED "):
            info["answered"] = True
            info["url"] = text[9:].strip()
        elif text.startswith("NOANSWER"):
            info["answered"] = False
        elif text.startswith("B "):
            body.append(text[2:])
    info["body"] = "\n".join(body)
    low = info["body"].lower()
    info["is_berry"] = info["answered"] and any(mark in low for mark in BERRY_MARKS)
    return info


def summary_rows(check: dict) -> list[dict]:
    """The status table: what the device gave, in the words a person reads."""
    check = check or {}
    home = str(check.get("home") or "(nothing set)")
    folder = ("yes" if check.get("folder") else "no")
    if check.get("folder"):
        folder += f"  \u00b7  {check.get('files', 0)} file(s)"
    rows = [
        {"cells": ["Folder served", home]},
        {"cells": ["Folder is there", folder]},
        {"cells": ["python3", str(check.get("python") or "not on the path")]},
        {"cells": ["Page", str(check.get("url") or "no answer")]
         , "color": "#23d18b" if check.get("answered") else "#ffb020"},
    ]
    if check.get("answered"):
        rows.append({"cells": ["Answers as this dashboard",
                              "yes" if check.get("is_berry") else "not obviously"],
                     "color": "#23d18b" if check.get("is_berry") else "#ffb020"})
    # What the device holds even though no setting names it: the answer to
    # "where is my dashboard", and the folder a move has to start from.
    served = check.get("served") or []
    if served:
        names = [f"{one.get('folder')} ({one.get('files', 0)} file(s))"
                 for one in served[:5]]
        rows.append({"cells": ["Folders holding the page", ", ".join(names)]})
    sections = check.get("sections") or {}
    if sections:
        pairs = [f"{name} \u2192 {where}" for name, where
                 in list(sections.items())[:5]]
        rows.append({"cells": ["uhttpd serves", ", ".join(pairs)]})
    if check.get("https"):
        rows.append({"cells": ["https", "the section listens over https, so the "
                                             "page is opened over https"],
                     "color": "#23d18b"})
    if check.get("no_fetch"):
        rows.append({"cells": ["Asking the page", "neither curl nor wget is on "
                                                 "the device, so the page could "
                                                 "not be asked"],
                     "color": "#ffb020"})
    return rows


def elsewhere(check: dict, home: str) -> str:
    """The folder the device says holds the page, when it is not the named one.

    Nothing is decided from this on its own; it is the answer to a person's
    "mine is in /www_rasconf", and the starting point of a rename.
    """
    keep = str(home or "").strip().rstrip("/")
    for one in (check or {}).get("served") or []:
        folder = str(one.get("folder") or "").strip().rstrip("/")
        if folder and folder != keep:
            return folder
    return ""


def bridge_settings(stored: dict, root: str = "") -> dict:
    """The installer's own settings, read as this addon's.

    The retired addon had no served folder of its own: it wrote under the
    profile's remote root, into an optional subfolder. A person who had it
    working must not find the dashboard pointing somewhere else after the
    upgrade, so the one changed concept is carried across once - the root and
    the subfolder become the folder - and the subfolder key is emptied so the
    two are never added together twice.

    Only a file that is really there is bridged: a fresh install has no stored
    settings at all and should simply take the default folder.
    """
    values = dict(stored or {})
    if not values or "web_home" in values:
        return values
    home = home_path(root, DEFAULTS["web_home"])
    sub = safe_path(values.get("subdir", ""))
    if sub:
        home = posixpath.join(home, sub)
        values["subdir"] = ""
    values["web_home"] = home
    return values


# --- reading the form ---

def values_of() -> dict:
    """The form as a config-shaped dict."""
    out: dict = {}
    for key, field in FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        try:
            raw = api.tabs.widget_value(TAB, key,
                                         "checked" if kind == "bool" else "value")
        except (AddonError, AttributeError, TypeError):
            raw = None
        if raw is None:
            out[key] = config_get(DEFAULTS, key)
        elif kind == "list":
            out[key] = clean_list(raw)
        elif kind == "multiline":
            out[key] = [one_line(line) for line in str(raw or "").splitlines()
                        if one_line(line)]
        elif kind == "number":
            out[key] = port_of(raw, int(field.get("minimum", 0)) or 8989) \
                if key == "web_port" else _plain_int(raw, field)
        else:
            out[key] = raw
    return out


def _plain_int(raw: Any, field: dict) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(field.get("minimum", 0))


def merge_current() -> dict:
    """The saved config with whatever is in the form on top of it."""
    merged = merge_config(DEFAULTS, _stored_config())
    return merge_config(merged, values_of())


def _stored_config() -> dict:
    """What the file holds, or nothing when there is no file or no app."""
    try:
        return dict(api.config.stored() or {})
    except (AddonError, AttributeError, TypeError):
        return {}


# --- the tab ---

def control_spec(key: str, values: dict) -> dict:
    """The form row for one config key, prefilled with the current value."""
    field = FIELDS_BY_KEY[key]
    kind = str(field.get("kind") or "text")
    spec = {
        "id": key,
        "kind": KIND_TO_WIDGET.get(kind, "line"),
        "label": str(field.get("label") or key),
    }
    if field.get("hint"):
        spec["tooltip"] = str(field["hint"])
    value = config_get(values, key, config_get(DEFAULTS, key))
    if kind == "bool":
        spec["checked"] = bool(value)
    elif kind == "choice":
        choices = [str(choice) for choice in field.get("choices") or []]
        spec["items"] = choices
        spec["value"] = str(value) if value else (choices[0] if choices else "")
    elif kind == "number":
        spec["minimum"] = int(field.get("minimum", 0))
        spec["maximum"] = int(field.get("maximum", 9999))
        try:
            spec["value"] = int(value or 0)
        except (TypeError, ValueError):
            spec["value"] = 0
    elif kind == "list":
        spec["value"] = ", ".join(clean_list(value))
        if field.get("placeholder"):
            spec["placeholder"] = str(field["placeholder"])
    elif kind == "multiline":
        spec["value"] = "\n".join(clean_list(value))
    else:
        spec["value"] = one_line(value)
        if field.get("placeholder"):
            spec["placeholder"] = str(field["placeholder"])
    return spec


def form_spec(groups: list, values: dict) -> dict:
    """One scroll of group boxes: the install form, or the uhttpd form."""
    return {"kind": "scroll", "children": [{"kind": "column", "children": [
        {"kind": "group", "text": title, "layout": "form",
         "children": [control_spec(key, values) for key in keys]}
        for title, keys in groups]}]}


def step_chip(key: str, caption: str) -> dict:
    """One step of the flow, waiting for its turn."""
    return {"kind": "label", "id": "step_" + key,
            "text": _STEP_GLYPHS["pending"] + caption,
            "style": _STEP_STYLES["pending"],
            "tooltip": "The job walks these steps left to right - the lit one "
                       "is what it is doing now"}


def buttons_spec(tips: tuple) -> dict:
    """A row of buttons, each reporting its own id."""
    return {"kind": "row", "children": [
        {"kind": "button", "id": identifier, "text": text, "tooltip": tip,
         "on": ["clicked"]} for identifier, text, tip in tips]}


def view_page(page: int) -> None:
    """Jump the tab's notebook: 0 status, 1 the installer, 2 uhttpd."""
    try:
        api.tabs.set_widget(TAB, "views", "current", int(page))
    except (AddonError, AttributeError, TypeError):
        pass


def uci_page(page: int) -> None:
    """Jump the uhttpd page's own notebook: 0 the form, 1 the raw file."""
    try:
        api.tabs.set_widget(TAB, "uci_views", "current", int(page))
    except (AddonError, AttributeError, TypeError):
        pass


def paint_steps(stages: list[str], cutoff: int, active: str | None = None) -> None:
    """Re-paint the step flow: ``cutoff`` stages done, ``active`` running now."""
    route: list[str] = []
    for stage in stages:
        step = STAGE_TO_STEP.get(stage)
        if step and step not in route:
            route.append(step)
    for key, caption in STEPS:
        positions = [i for i, stage in enumerate(stages)
                     if STAGE_TO_STEP.get(stage) == key]
        if key == active:
            state = "active"
        elif key not in route:
            state = "off"
        elif positions and max(positions) < cutoff:
            state = "done"
        else:
            state = "pending"
        try:
            api.tabs.set_widget(TAB, "step_" + key, "text",
                                _STEP_GLYPHS[state] + caption)
            api.tabs.set_widget(TAB, "step_" + key, "style", _STEP_STYLES[state])
        except (AddonError, AttributeError, TypeError):
            pass


def _chip(identifier: str, text: str, tone: str) -> None:
    """One of the three count badges, with the words and the colour to match."""
    try:
        api.tabs.set_widget(TAB, identifier, "text", text)
        api.tabs.set_widget(TAB, identifier, "style", _CHIP_STYLES[tone])
    except (AddonError, AttributeError, TypeError):
        pass


def reset_totals() -> None:
    """The badges before any plan: one honest placeholder, two blanks."""
    _chip("sum_write", "no plan yet", "gray")
    _chip("sum_same", "", "gray")
    _chip("sum_extra", "", "gray")


def refresh_totals(items: list[dict] | None = None) -> None:
    """Say what the plan adds up to: to write, already there, left over."""
    items = items if items is not None else (job or {}).get("items") or []
    if not items:
        reset_totals()
        return
    pending = written = same = 0
    for item in items:
        action = str(item.get("action") or "queued")
        if action in ("queued", "new", "replaced", "written again"):
            pending += 1
        elif action == "written":
            written += 1
        elif action == "already there":
            same += 1
    words = f"{pending} to write" + (f" \u00b7 {written} written" if written else "")
    _chip("sum_write", words, "crimson" if pending else "green")
    _chip("sum_same", f"{same} already there", "gray")
    extras = (job or {}).get("extras")
    if extras is None:
        _chip("sum_extra", "", "gray")
    else:
        _chip("sum_extra", f"{extras} extra on device" if extras else "nothing left over",
              "amber" if extras else "green")


def tab_spec(values: dict) -> dict:
    """The whole tab: a header, the step flow, and one notebook of three pages."""
    steps: list[dict] = []
    for index, (key, caption) in enumerate(STEPS):
        if index:
            steps.append({"kind": "label", "text": "\u2192",
                          "style": "color: #454545; padding: 0 1px;"})
        steps.append(step_chip(key, caption))
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry web dashboard", "style": BANNER_STYLE},
            {"kind": "spacer"},
            {"kind": "label", "id": "target", "text": "",
             "style": "color: #9a9a9a; padding: 2px 6px;"},
            {"kind": "label", "id": "state", "text": "",
             "style": _CHIP_STYLES["gray"]},
        ]},
        {"kind": "row", "children": steps},
        {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
         "style": "color: #23d18b; padding: 2px 4px;"},
        {"kind": "progress", "id": "bar", "maximum": 100, "value": 0},
        {"kind": "row", "children": [
            {"kind": "label", "id": "sum_write", "text": "no plan yet",
             "style": _CHIP_STYLES["gray"]},
            {"kind": "label", "id": "sum_same", "text": "",
             "style": _CHIP_STYLES["gray"]},
            {"kind": "label", "id": "sum_extra", "text": "",
             "style": _CHIP_STYLES["gray"]},
            {"kind": "spacer"},
        ]},
        {"kind": "tabs", "id": "views", "children": [
            _status_page(),
            _installer_page(values),
            _uci_page(values),
        ]},
    ]}


def _status_page() -> dict:
    """What the device last said, and the three ways of acting on it."""
    return {"kind": "column", "label": "Status", "children": [
        {"kind": "table", "id": "summary",
         "columns": ["What the device says", "Answer"],
         "stretch": 1, "col_widths": [170, 0],
         "tooltip": "One look, asked of the connected host over SSH: the folder "
                    "uhttpd serves, whether it is there, whether python3 is "
                    "installed and whether the page answers"},
        {"kind": "label", "id": "last_check", "text": "Nothing asked yet.",
         "word_wrap": True, "style": "color: #9a9a9a; padding: 2px 4px;"},
        buttons_spec((
            ("recheck", "Re-check", "Ask the connected host again what it has"),
            ("check", "Check page", "Ask the device for its own page and report "
                                    "what came back"),
            ("open", "Open dashboard", "The page the device is serving, in its own tab"),
            ("to_installer", "Go to installer",
             "The page that fetches the files and writes them over"),
        )),
    ]}


def _installer_page(values: dict) -> dict:
    """The plan, the log and the install form; the buttons that start a job."""
    return {"kind": "column", "label": "Installer", "children": [
        {"kind": "tabs", "id": "install_views", "children": [
            {"kind": "table", "id": "plan", "label": "Files",
             "columns": ["File", "Size", "On the device", "Action"],
             "stretch": 0, "col_widths": [0, 90, 120, 130],
             "tooltip": "The files this job will send, and what the device "
                        "already has of them"},
            {"kind": "multi", "id": "log", "label": "Log", "readonly": True,
             "placeholder": "Every step of the last job, as it happened",
             "tooltip": "The same lines go into the program's own log"},
            dict(form_spec(GROUPS_INSTALL, values), label="Install settings"),
        ]},
        buttons_spec((
            ("fetch", "Fetch files", "Get the tree from GitHub or from the local "
                                     "folder and work out what the device needs"),
            ("install", "Install", "Write those files into the served folder over "
                                   "SFTP, set the web server up and restart it"),
            ("push", "Save to config", "Keep these values in "
                                       "addons/_data/berry_web/config.json"),
            ("pull", "Reload from config", "Put the saved values back into the forms"),
            (STOP, "Stop", "Give up on the job in hand. What has already gone "
                           "across stays on the device"),
            ("to_uci", "uhttpd settings", "The served folder, its port and the "
                                          "section that owns it"),
        )),
    ]}


def _uci_page(values: dict) -> dict:
    """The options that make uhttpd serve this folder, and the file itself."""
    return {"kind": "column", "label": "uhttpd", "children": [
        {"kind": "tabs", "id": "uci_views", "children": [
            {"kind": "column", "label": "Options", "children": [
                form_spec(GROUPS_UCI, values),
                buttons_spec((
                    ("read_uci", "Read", "Ask the device for its uhttpd section "
                                         "and the whole file"),
                    ("apply_uci", "Apply", "Write the options above with uci and "
                                           "commit them"),
                    ("restart", "Restart web server",
                     "Run the restart command on the device"),
                    ("move_home", "Move folder to this name",
                     "Rename the folder the server is using to the name in the "
                     "box above, point uhttpd at it, commit and restart"),
                    ("to_status", "Back to status", "What the device says now"),
                )),
            ]},
            {"kind": "column", "label": "Raw file", "children": [
                buttons_spec((
                    ("refresh_raw", "Refresh", "Read the file off the device "
                                              "again"),
                    ("copy_raw", "Copy to editor", "Put the text below in the "
                                                   "editor box, where it can be "
                                                   "changed and kept for reading"),
                )),
                # The two boxes side by side down the page, with a line between
                # them: what the device holds and the copy made of it can each be
                # given the room their lines need.
                {"kind": "splitter", "vertical": True, "sizes": [1, 1],
                 "children": [
                     {"kind": "multi", "id": "raw_uci", "readonly": True,
                      "placeholder": "/etc/config/uhttpd, exactly as the device "
                                     "holds it",
                      "tooltip": "Read only: this page shows the file, the "
                                 "options above are what change it"},
                     {"kind": "multi", "id": "raw_edit",
                      "placeholder": "A copy of the file, for reading over or "
                                     "for keeping a note in",
                      "tooltip": "Nothing here is ever written back to the "
                                 "device"},
                 ]},
            ]},
        ]},
    ]}


#: The tab's mark: a berry on a page, since this addon is the page the device
#: serves. Held as an SVG document so the tab wears the same thing whether or not
#: the assets folder holds a picture file.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="2.5" y="3.5" width="19" height="17" rx="2.6" fill="none"'
    ' stroke="#7fb3d5" stroke-width="1.5"/>'
    '<path d="M2.5 7.6h19" stroke="#7fb3d5" stroke-width="1.2"/>'
    '<circle cx="5.2" cy="5.6" r="0.9" fill="#e0e0e0"/>'
    '<circle cx="8" cy="5.6" r="0.9" fill="#e0e0e0"/>'
    '<circle cx="10.4" cy="12.1" r="2.1" fill="#c51a4a"/>'
    '<circle cx="13.9" cy="12.1" r="2.1" fill="#9c1239"/>'
    '<circle cx="12.1" cy="15.4" r="2.1" fill="#e0356b"/>'
    '<path d="M12.1 10.4c0-1.6.8-2.7 2.3-3.2" fill="none" stroke="#23d18b"'
    ' stroke-width="1.5" stroke-linecap="round"/>'
    '</svg>'
)

#: The picture the tab wears when the assets folder holds one: this addon's id
#: and then ``icon.png``, so ``berry_webicon.png`` - the one name every addon's
#: picture is looked up under, written here only to say where it goes.
TAB_ICON_FILE = "berry_webicon.png"


def tab_icon() -> str:
    """The addon's own picture as a path, or its drawing when no folder has one."""
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    return find_addon_picture(paths, "berry_web") or TAB_ICON


def build_tab() -> None:
    """The tab as a spec, and its buttons as reports; nothing holds a widget."""
    api.tabs.add_tab(TAB, spec=tab_spec(merge_config(DEFAULTS, _stored_config())),
                     style=TAB_STYLE)
    api.tabs.set_icon(TAB, tab_icon())


def show_form(values: dict) -> None:
    for key, field in FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        value = config_get(values, key, config_get(DEFAULTS, key))
        if kind in ("list", "multiline"):
            items = clean_list(value) if kind == "list" else [
                one_line(line) for line in (value or []) if one_line(line)]
            text = ", ".join(items) if kind == "list" else "\n".join(items)
            api.tabs.set_widget(TAB, key, "value", text)
        else:
            api.tabs.set_widget(TAB, key, "value", value)
    show_target(values)


def say(text: str, bad: bool = False) -> None:
    api.tabs.set_widget(TAB, "status", "text", str(text))
    api.tabs.set_widget(TAB, "status", "style",
                        "color: #ffb020; padding: 2px 4px;" if bad
                        else "color: #23d18b; padding: 2px 4px;")


def note(text: str) -> None:
    """One line into the tab's own log and the program's log, the same words."""
    api.tabs.set_widget(TAB, "log", "append", str(text))
    api.log(text)


def progress(done: int, total: int) -> None:
    """Move the bar; each running stage owns a slice of it (see _bar_spans)."""
    total = max(int(total), 1)
    done = min(max(int(done), 0), total)
    stage = str((job or {}).get("stage") or "")
    span = (job or {}).get("spans") or {}
    if stage in span:
        start, weight = span[stage]
        api.tabs.set_widget(TAB, "bar", "value", start + int(weight * done / total))
        return
    api.tabs.set_widget(TAB, "bar", "maximum", total)
    api.tabs.set_widget(TAB, "bar", "value", done)


def bar_text(text: str) -> None:
    """The step painted inside the bar itself; Qt still expands ``%p`` there."""
    api.tabs.set_widget(TAB, "bar", "format", f"{text}  %p%" if text else "%p%")


def show_plan(items: list[dict]) -> None:
    """The table, in the order the job works through it, tinted by action."""
    rows = []
    for item in items:
        seen = item.get("remote")
        action = str(item.get("action") or "queued")
        row = {"cells": [item.get("path", ""), human(item.get("size") or 0),
                         seen if seen else "-", action]}
        colour = ACTION_COLORS.get(action)
        if colour:
            row["color"] = colour
        rows.append(row)
    api.tabs.set_widget(TAB, "plan", "rows", rows)
    refresh_totals(items)


def describe(values: dict) -> str:
    """One line saying where the files come from, in the user's own terms."""
    if is_local(values):
        return f"From the local folder {local_folder(values) or '(none given)'}"
    return (f"From {config_get(values, 'owner', '')}/"
            f"{config_get(values, 'repo', '')} at "
            f"{config_get(values, 'ref', '')}, the folder "
            f"{config_get(values, 'repo_path', '')}")


def _profile_root() -> str:
    """The current profile's remote folder, as a device path."""
    try:
        status = api.session.status() or {}
    except (AddonError, AttributeError, TypeError):
        return DEFAULTS["web_home"]
    return home_path(str(status.get("remote_root") or ""), DEFAULTS["web_home"])


def show_target(values: dict) -> None:
    """The header line: where these files come from and which folder they land in."""
    status: dict = {}
    try:
        status = api.session.status() or {}
    except (AddonError, AttributeError, TypeError):
        pass
    whole = job_paths(values)["home"]
    try:
        api.tabs.set_widget(TAB, "target", "text", f"{describe(values)}  \u2192  {whole}")
    except (AddonError, AttributeError, TypeError):
        pass
    host = one_line(str(status.get("host") or ""))
    # Ask what *this addon* may reach, not just whether the app's session is
    # up: the SSH and SFTP switches on its own Settings row count too.
    try:
        ready = bool(api.device.is_connected("sftp"))
    except (AddonError, AttributeError, TypeError):
        ready = False
    up = bool(status.get("sftp_connected"))
    words = ("\u25cf " if ready else "\u25cb ") + (host or "no host yet")
    words += ("  \u00b7  SFTP up" if ready else
              "  \u00b7  " + ("SFTP is switched off for this addon" if up
                              else "not connected"))
    try:
        api.tabs.set_widget(TAB, "state", "text", words)
        api.tabs.set_widget(TAB, "state", "style",
                            _CHIP_STYLES["green" if ready else "gray"])
    except (AddonError, AttributeError, TypeError):
        pass


def show_check(check: dict) -> None:
    """Paint what the device answered: the table, and the line under it."""
    try:
        api.tabs.set_widget(TAB, "summary", "rows", summary_rows(check))
    except (AddonError, AttributeError, TypeError):
        pass
    if not check:
        return
    if check.get("is_berry"):
        words = f"{check.get('url')} answers as the Berry dashboard"
    elif check.get("answered"):
        words = (f"{check.get('url')} answered, but not with what looks like "
                 "the Berry dashboard")
    else:
        words = "nothing answered the page"
    title = page_title(check.get("body"))
    if title:
        words += f'  \u00b7  it calls itself "{title}"'
    if not check.get("python"):
        words += "  \u00b7  python3 is not on the path, so the CGI cannot run"
    try:
        api.tabs.set_widget(TAB, "last_check", "text", words)
        api.tabs.set_widget(TAB, "last_check", "style",
                            "color: #23d18b; padding: 2px 4px;"
                            if check.get("is_berry")
                            else "color: #ffb020; padding: 2px 4px;")
    except (AddonError, AttributeError, TypeError):
        pass


# --- network: standard library only ---

def profile_token() -> str:
    """The token this addon is allowed to send to GitHub, which is the profile's.

    There is one token for a profile and it is written in Settings > Connection;
    the switch on this addon's tab in the store's Addon manager decides whether
    ``api.session.git_token()`` answers with it at all. An empty string is a real
    answer rather than a failure - it means anonymous, which is what a public
    repository is read with - so a build with no api, no token and a refused call
    all fetch exactly the same way.
    """
    if api is None:
        return ""
    try:
        return one_line(api.session.git_token()).replace(" ", "")
    except (AddonError, AttributeError, TypeError):
        return ""


class Fetcher:
    """A few HTTPS connections, kept open and reused for the whole download."""

    def __init__(self, token: str = "") -> None:
        self.token = str(token or "")
        self.connections: dict[str, http.client.HTTPSConnection] = {}

    def _connection(self, host: str) -> http.client.HTTPSConnection:
        connection = self.connections.get(host)
        if connection is None:
            connection = http.client.HTTPSConnection(
                host, timeout=25, context=ssl.create_default_context())
            self.connections[host] = connection
        return connection

    def get(self, host: str, path: str,
            accept: str = "application/octet-stream") -> tuple[int, bytes]:
        """``(status, body)`` for one GET, retried once on a dropped connection."""
        status, body = 0, b""
        for attempt in (0, 1):
            headers = {"User-Agent": "berry-manager", "Accept": accept}
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            connection = self._connection(host)
            try:
                connection.request("GET", path, headers=headers)
                answer = connection.getresponse()
                body = answer.read()
                status = int(answer.status)
                if status < 500:
                    return status, body
            except (http.client.HTTPException, OSError):
                self.close(host)
                if attempt:
                    raise
                time.sleep(0.4)
        return status, body

    def json(self, host: str, path: str) -> Any:
        status, body = self.get(host, path, accept="application/json")
        if status != 200:
            raise AddonError(_refused(status, body, path))
        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as error:
            raise AddonError(f"{host}{path} was not the JSON expected: {error}")

    def close(self, host: str = "") -> None:
        """Drop one connection, or all of them: they are not keep-alive forever."""
        for name in ([host] if host else list(self.connections)):
            connection = self.connections.pop(name, None)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass


def _refused(status: int, body: bytes, path: str) -> str:
    """A failure worth showing to a user, in words that say what to do next."""
    detail = ""
    try:
        detail = str(json.loads(body.decode("utf-8", errors="replace")).get("message") or "")
    except (ValueError, AttributeError, TypeError):
        detail = ""
    if status == 404:
        return (f"GitHub has no such branch, tag or commit, or no such repository: "
                f"{path}. Check the owner, the repository and the ref on the form, "
                "and the token in Settings > Connection if the repository is "
                "private.")
    if status == 403:
        return (f"GitHub refused the request for {path} (403)"
                + (f": {detail}" if detail else
                   ". Anonymous requests are limited to sixty an hour, so put a "
                     "token in Settings > Connection, switch this addon on for "
                     "it, or try again later."))
    return f"GitHub answered {status} for {path}" + (f": {detail}" if detail else "")


_fetchers: dict[str, Fetcher] = {}


def _http(values: dict) -> Fetcher:
    """The shared fetcher, keyed by the profile's token.

    ``values`` says where the files come from, which is nothing to do with what
    is sent as credentials: a profile has one token and every repository named in
    it is read with that one. Keying the pool by the token is what keeps a
    switched-off addon, or a profile that never wrote one, on the anonymous
    fetcher - and makes a token typed into Settings take effect at once rather
    than inherit connections made before it.
    """
    key = profile_token()
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = Fetcher(token=key)
        _fetchers[key] = fetcher
    return fetcher


def _drop_http() -> None:
    for fetcher in _fetchers.values():
        fetcher.close()
    _fetchers.clear()


# --- where the files come from, and which folder they land in ---

def is_local(values: dict) -> bool:
    """Whether this install reads a folder on this machine instead of GitHub."""
    return str(config_get(values, "source", "GitHub")).lower().startswith("local")


def local_folder(values: dict) -> str:
    """The folder a local install reads: the form's, or the profile's own source."""
    text = one_line(config_get(values, "local_dir", ""))
    if text:
        return text
    try:
        status = api.session.status() or {}
    except (AddonError, AttributeError, TypeError):
        status = {}
    return str(status.get("source") or "")


def job_paths(values: dict) -> dict:
    """Every path, name and pattern this job works with, cleaned once here.

    ``home`` is where the files go: the served folder and, when the form names
    one, a subfolder inside it. ``home_root`` is the folder itself, which is the
    thing a rename in the device's own tree can act on - a folder holding the
    dashboard under another name has to be moved whole, subfolder and all, and
    not the path inside it.
    """
    root = home_path(config_get(values, "web_home", ""), "") or _profile_root()
    sub = safe_path(config_get(values, "subdir", ""))
    home = posixpath.join(root, sub) if sub else root
    entry = safe_path(config_get(values, "cgi_entry", DEFAULTS["cgi_entry"]),
                      DEFAULTS["cgi_entry"])
    return {
        "home": home,
        "home_root": root,
        "subdir": sub,
        "entry": entry,
        "section": uci_name(config_get(values, "uci_section", DEFAULTS["uci_section"])),
        "skip": clean_list(config_get(values, "skip", DEFAULTS["skip"])),
        "keep": clean_list(config_get(values, "keep", DEFAULTS["keep"])),
        "repo_path": one_line(config_get(values, "repo_path",
                                         DEFAULTS["repo_path"])).strip("/ "),
        "web_path": "/" + str(config_get(values, "web_path",
                                         DEFAULTS["web_path"])).lstrip("/ "),
        "backup_dir": home_path(config_get(values, "backup_dir", ""), "")
        or "/tmp/berry_web_backups",
        "port": port_of(config_get(values, "web_port", DEFAULTS["web_port"])),
        "theme_sync": bool(config_get(values, "theme_sync", True)),
        "restart": [one_line(text) for text in
                    (config_get(values, "restart_command", []) or []) if one_line(text)],
    }


def target_path(home: str, relative: str) -> str:
    """One device path below the served folder, or nothing; nothing escapes it."""
    clean = safe_path(relative)
    root = home.rstrip("/") or ""
    if not clean or not root:
        return ""
    joined = posixpath.normpath(posixpath.join(root, clean))
    return joined if joined.startswith(root + "/") else ""


def page_urls(paths: dict) -> list[str]:
    """The addresses the device can ask itself for its page at; loopback first.

    Both schemes are probed: the section is set up to answer over plain HTTP, but
    a router that has TLS switched on for uhttpd answers ``https`` and nothing
    else. Loopback is asked before the address the app connected to, because the
    two are the same machine here and a router answering itself costs no more
    than a second, while an address that is filtered costs what it is allowed to.
    """
    host = ""
    try:
        status = api.session.status() or {}
        host = one_line(str(status.get("host") or ""))
    except (AddonError, AttributeError, TypeError):
        host = ""
    out: list[str] = []
    for place in ["127.0.0.1"] + ([host] if host and host != "127.0.0.1" else []):
        for scheme in ("http", "https"):
            out.append(f"{scheme}://{place}:{paths['port']}{paths['web_path']}")
    return out


def _ref(values: dict) -> str:
    """A branch, tag or commit as one URL-safe path piece."""
    return quote(one_line(config_get(values, "ref", "main")).strip("/"), safe="-./_")


def _ignored(relative: str) -> bool:
    """Junk that has no business on a web server, whatever the settings say."""
    for part in str(relative).split("/"):
        if part.startswith(".") or part.startswith("_"):
            return True
    return str(relative).endswith((".pyc", ".pyo", ".tmp", ".bak", ".swp", "~"))


# --- asking the host what it has ---

#: The probe's budget, in seconds, per address: a connect that no one answers
#: is cut off at ``PROBE_CONNECT``, and an answer that never finishes at
#: ``PROBE_ASK``. Both are options given to the device's own tool, so they hold
#: whether or not this machine's timeout applet exists.
PROBE_CONNECT = 2
PROBE_ASK = 5
#: How much of a page is worth reading to find out what answered.
PROBE_BYTES = 4096


def _page_probe(urls: list[str]) -> list[str]:
    """Shell that asks for the page at each address, stopping at the first answer.

    Every ask bounds itself, and that is the whole point of this function. A
    filtered port used to cost the router's own TCP patience - about two minutes
    - because the fallback fetch had no timeout option at all, and the app gave
    up on the command long before that and handed back a made-up ``-1`` that
    looked like something the device had said.

    One tool is chosen for all the addresses rather than tried at each: curl and
    then wget would double the cost of the slowest address. What comes back is
    capped, since a page that streams forever is the same stall in another hat,
    and six lines of its head is enough to know what answered. Those lines are
    printed with ``B `` in front of each, so a reader can tell the lines of the
    page from the lines of the report above it.
    """
    return [
        'if command -v curl >/dev/null 2>&1; then'
        f' FETCH="curl -s -k --connect-timeout {PROBE_CONNECT}'
        f' --max-time {PROBE_ASK}";'
        ' elif command -v wget >/dev/null 2>&1; then'
        f' FETCH="wget -q -T {PROBE_ASK} -t 1 --no-check-certificate -O -";'
        ' else FETCH=""; fi',
        'if [ -z "$FETCH" ]; then echo "NOFETCH"; echo "NOANSWER"; exit 0; fi',
        "for URL in %s; do" % " ".join(quoted(text) for text in urls),
        f'  BODY=$($FETCH "$URL" 2>/dev/null | head -c {PROBE_BYTES})',
        '  if [ -n "$BODY" ]; then echo "ANSWERED $URL";'
        ' echo "$BODY" | head -n 6 | sed "s/^/B /"; exit 0; fi',
        "done",
        'echo "NOANSWER"',
    ]


def check_script(paths: dict) -> str:
    """The one command that asks the device everything the status page shows.

    The file questions are one test each and cannot stall; the page probe past
    the end carries its own seconds, which is what keeps this command inside the
    app's patience. The folder is named ``WEBHOME`` rather than ``HOME``: a bare
    assignment gives the new value to every command the script runs, and curl
    reads ``$HOME/.curlrc``.

    As well as the folder the form names, the device is asked two questions an
    older install can answer and a setting cannot: which of its web folders
    actually hold this dashboard's entry file (``SERVED``) and which uhttpd
    section serves which folder (``SH``). Without them, a dashboard left in
    ``/www_rasconf`` by a setup that never wrote a section of its own was
    invisible here, and the rename had nothing to start from.
    """
    section = paths["section"]
    entry = paths["entry"]
    return "\n".join([
        f'WEBHOME={quoted(paths["home"])}',
        'echo "FOLDER $(if [ -d "$WEBHOME" ]; then echo yes; else echo no; fi)"',
        'echo "FILES $(find "$WEBHOME" -type f 2>/dev/null | wc -l)"',
        f'echo "HOME $(uci -q get uhttpd.{section}.home 2>/dev/null)"',
        # Whether this section answers over https at all: a listen address of
        # its own, or a redirect that pushes every plain ask over there. The
        # page is opened with whatever the device's own config says.
        f'echo "HTTPS $( {{ uci -q get uhttpd.{section}.listen_https;'
        f' uci -q get uhttpd.{section}.redirect_https;'
        f' }} 2>/dev/null | grep -q . && echo on)"',
        'uci -q show uhttpd 2>/dev/null | grep home= | sed "s/^/SH /"',
        f'for D in /www /www_* /srv /srv/www; do'
        f' if [ -f "$D/{entry}" ]; then'
        ' echo "SERVED $D $(find "$D" -type f 2>/dev/null | wc -l)"; fi; done',
        'echo "PY $(command -v python3 2>/dev/null)"',
    ] + _page_probe(page_urls(paths)))


def land_on(check: dict, landing: bool = True) -> None:
    """Where the tab opens: a live dashboard is looked at as a page, anything
    else fixed.

    ``landing`` is the boot and the reconnect: a dashboard that answers gets
    shown straight away, in its own web tab. A re-check the user pressed on the
    status page only moves the notebook - yanking a fresh tab in front of the
    buttons they are reading would be the addon reading its own mail first.
    """
    if check.get("is_berry"):
        view_page(STATUS_PAGE)
        if landing:
            open_dashboard()
    else:
        view_page(INSTALL_PAGE)


def check_device(_checked: bool = False, landing: bool = True) -> None:
    """One look at the connected host, and the tab arranged around what it said."""
    if api is None or working:
        return
    paths = job_paths(merge_current())

    def told(result: dict) -> None:
        check = parse_check(result)
        last_check.clear()
        last_check.update(check)
        show_check(check)
        land_on(check, landing)
        found = elsewhere(check, paths["home"])
        if check.get("is_berry"):
            say(f"the Berry dashboard answers on {check.get('url')}"
                + (f", and its files are in {found}" if found and
                   not check.get("folder") else ""))
        elif not check.get("folder"):
            if found:
                say(f"the page's files are in {found}, not in {paths['home']} - "
                    "\u201cMove folder\u201d on the uhttpd page renames it and "
                    "points the web server at the new name", bad=True)
            else:
                say(f"{paths['home']} is not on the device - the installer page "
                    "writes it", bad=True)
        elif not check.get("answered"):
            say("the folder is there but nothing answers the page - the installer "
                "can set the web server up", bad=True)
        else:
            say("something answers, but it is not this dashboard - an install will "
                "put these files in its place", bad=True)
        if check.get("no_fetch"):
            note("neither curl nor wget is on the device, so the page could not be "
                 "asked from it: opkg install curl")
        if not check.get("python"):
            note("python3 is not on the device's path: install it before the CGI "
                 "can run (opkg install python3)")

    # The script bounds its own asks, so this is patience rather than a guess at
    # the router's TCP timeout: it is what the device gets before the app says it
    # never came back.
    _quick(check_script(paths), told, "asking the host what it has", timeout=45)


# --- the job: one unit of work per tick ---

#: Bar share per stage; the file-by-file ones get most.
STAGE_WEIGHTS = {"tree": 4, "fetch": 34, "remote": 8, "ready": 4, "confirm": 2,
                 "backup": 10, "write": 34, "prune": 6, "theme": 6, "uci": 8,
                 "after": 10, "verify": 8}

#: What the bar says during a stage; short, it fits the bar.
STAGE_WORDS = {"tree": "finding the files", "fetch": "downloading",
               "remote": "asking the device", "ready": "nothing to write",
               "confirm": "waiting for you", "backup": "backing up",
               "write": "writing files", "prune": "tidying away",
               "theme": "painting the page", "uci": "setting the server up",
               "after": "restarting the web server", "verify": "checking the page"}


def route(values: dict, action: str) -> list[str]:
    """The stages this job walks; only an install writes, serves and checks."""
    stages = ["tree"]
    if not is_local(values):
        stages.append("fetch")
    stages.append("remote")
    stages += (["confirm", "backup", "write", "prune", "theme", "uci",
                "after", "verify"] if action == "install" else ["ready"])
    return stages


def _bar_spans(stages: list[str]) -> tuple[dict, int]:
    """Slice the bar across this route; the last stage's end is the maximum."""
    spans: dict[str, tuple[int, int]] = {}
    total = 0
    for stage in stages:
        spans[stage] = (total, STAGE_WEIGHTS.get(stage, 4))
        total += STAGE_WEIGHTS.get(stage, 4)
    return spans, total


def _bar_stage(stage: str) -> None:
    """The bar as one stage begins: ground covered is filled in, word set."""
    start, _weight = (job.get("spans") or {}).get(stage, (0, 0))
    api.tabs.set_widget(TAB, "bar", "value", start)
    bar_text(STAGE_WORDS.get(stage, stage))


def start_job(stages: list[str], label: str, values: dict) -> None:
    """Set a job going, and keep its whole state in one dict as it runs."""
    global working, job
    if working:
        return
    working = True
    spans, units = _bar_spans(stages)
    job = {"stages": list(stages), "stage": "", "index": 0, "items": [], "queue": [],
           "label": label, "values": values, "paths": job_paths(values),
           "remote": {}, "errors": [], "written": 0, "removed": [], "fetched": 0,
           "session": None, "context": None, "summary": None,
           "spans": spans, "units": units}
    _buttons(False)
    api.tabs.set_widget(TAB, "log", "value", "")
    api.tabs.set_widget(TAB, "plan", "rows", [])
    reset_totals()
    # The plan is what a new job starts on, inside the installer page.
    api.tabs.set_widget(TAB, "install_views", "current", 0)
    api.tabs.set_widget(TAB, "bar", "maximum", units)
    api.tabs.set_widget(TAB, "bar", "value", 0)
    if "fetch" in stages:
        _reset_staging()
    say(f"{label}...")
    api.log(f"{label}: {describe(values)} \u2192 {job['paths']['home']}")
    _enter(stages[0])


def _enter(stage: str) -> None:
    """Begin a stage: its own count, its own progress, its own words."""
    if job is None:
        return
    job["stage"] = stage
    job["index"] = 0
    _bar_stage(stage)
    # Step flow follows the route: past done, this one lit.
    stages = list(job["stages"])
    paint_steps(stages, stages.index(stage) if stage in stages else 0,
                STAGE_TO_STEP.get(stage))
    handler = STAGES.get(stage)
    if handler is None:
        stop(f"this addon asked for a stage it has no code for: {stage}")
        return
    handler()


def _next() -> None:
    """One stage over: go on to the next, or finish."""
    if job is None:
        return
    stages = job["stages"]
    stage = job.get("stage")
    if stage not in stages:
        _done()
        return
    position = stages.index(stage) + 1
    if position < len(stages):
        _enter(stages[position])
        return
    _done()


def _again(delay: float = 0.01) -> None:
    """Come back for the next unit of this stage; a piece at a time keeps painting."""
    if job is not None:
        api.schedule.after(delay, _tick)


def _tick() -> None:
    """One unit of the current stage, then the next, until that stage is over."""
    if job is None or not working:
        return
    unit = UNITS.get(str(job.get("stage") or ""))
    if unit is None:
        _next()
        return
    try:
        unit()
    except Exception as error:            # an addon never takes the app down with it
        stop(f"the job stopped: {type(error).__name__}: {error}")


def _progress(done: int, total: int, text: str) -> None:
    say(text)
    progress(done, total)


def stop(text: str = "stopped") -> None:
    """Give the job up: the buttons come back and the session goes."""
    global working, job
    stages = list((job or {}).get("stages") or [])
    stage = str((job or {}).get("stage") or "")
    _close_session()
    _drop_http()
    if stages:
        # Steps before the cut-off read done; the rest do not.
        paint_steps(stages, stages.index(stage) if stage in stages else len(stages))
    working = False
    job = None
    _buttons(True)
    bar_text("stopped")
    say(text, bad=True)
    note(text)


def _done() -> None:
    """The end of a route, in one line; the words are made while the job exists."""
    global working, job
    if job is None:
        return
    errors = list(job.get("errors") or [])
    label = str(job.get("label") or "job")
    summary = job.get("summary")
    written = int(job.get("written") or 0)
    removed = len(job.get("removed") or [])
    already = len([item for item in job["items"]
                   if item.get("action") == "already there"])
    text = ""
    if not errors:
        if callable(summary):
            text = str(summary())
        else:
            parts = [f"{written} file(s) written"] if written else ["nothing needed writing"]
            if already:
                parts.append(f"{already} already in place")
            if removed:
                parts.append(f"{removed} tidied away")
            text = ", ".join(parts)
    units = int(job.get("units") or 100)
    # Finish every step while the job can still be read.
    paint_steps(list(job["stages"]), len(job["stages"]))
    _close_session()
    _drop_http()
    api.tabs.set_widget(TAB, "bar", "value", units)   # the bar ends full, every time
    bar_text("done" if not errors else "stopped short")
    working = False
    job = None
    _buttons(True)
    if errors:
        say(f"{label} stopped short: {len(errors)} problem(s), the first was "
            f"{errors[0]}", bad=True)        # and the log above has the rest
        return
    say(f"{label} - {text}.")
    note(f"{label} finished: {text}")


def _problem(text: str) -> None:
    """Log one thing that went wrong and keep the job moving."""
    if job is not None:
        job.setdefault("errors", []).append(str(text))
    note(f"problem: {text}")
    api.error(text)


def _why_failed(result: dict, step: str, timeout: int = 0) -> str:
    """Say in words why a command did not come back.

    ``-1`` is not something a device ever answered: it is what the app makes up
    when the command did not return at all, and the reason it stopped - a read
    that ran out of patience, a channel that would not open - travels in a field
    this addon used to throw away. So a stall is said as a stall, with the
    seconds it was given and the reason beside it.
    """
    code = int(result.get("code") or 0)
    reason = str(result.get("err") or "").strip()
    first = f" ({reason.splitlines()[0]})" if reason else ""
    if code == -1:
        said = f"{step}: the device never came back"
        if timeout:
            said += f" within {timeout}s"
        return said + (first or " - and it said nothing about why")
    return f"{step} did not finish: the device answered {code}{first}"


def _device(command: str, then: Callable[[dict], None], timeout: int = 90,
            step: str = "") -> None:
    """One SSH command for the job; its answer comes back on the GUI thread."""
    if job is None:
        return
    if step:
        say(f"{job['label']} - {step}...")

    def answered(result: dict) -> None:
        if job is None:                   # stopped while the device was thinking
            return
        code = int(result.get("code") or 0)
        out = str(result.get("out") or "").strip()
        for line in out.splitlines()[:40]:
            note(line)
        if code != 0:
            _problem(_why_failed(result, step or "a command", timeout))
        then(result)

    api.device.run_async(command, answered, timeout=timeout)


# --- stage: the list of files ---

def _stage_tree() -> None:
    """Work out what there is to install, from GitHub or from a folder here."""
    values = job["values"]
    if is_local(values):
        _local_tree(values, job["paths"])
        return
    _github_tree(values, job["paths"])


def _github_tree(values: dict, paths: dict) -> None:
    """Ask GitHub for the tree of that folder; fall back to the built-in list."""
    owner = safe_path(config_get(values, "owner", ""), DEFAULTS["owner"])
    repo = safe_path(config_get(values, "repo", ""), DEFAULTS["repo"])
    prefix = paths["repo_path"]
    say(f"{job['label']} - asking GitHub what is in {prefix or repo}...")
    try:
        tree = _http(values).json(
            "api.github.com", f"/repos/{owner}/{repo}/git/trees/{_ref(values)}?recursive=1")
    except AddonError as error:
        note(f"GitHub would not give the file list, so the built-in list of the "
             f"dashboard is used instead ({error})")
        note("That list is the dashboard as this addon was written: files added "
             "to the repository since then will be missed here")
        _set_items([{"path": name, "size": 0} for name in FALLBACK_TREE])
        return
    items: list[dict] = []
    for entry in tree.get("tree") or []:
        if str(entry.get("type") or "") != "blob":
            continue
        full = str(entry.get("path") or "")
        if not full.startswith(prefix + "/"):
            continue
        relative = full[len(prefix) + 1:]
        if not relative or matches(relative, paths["skip"]) or _ignored(relative):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        items.append({"path": relative, "size": size})
    if not items:
        stop(f"nothing to install under {prefix or 'that repository'} in "
             f"{owner}/{repo} at {one_line(config_get(values, 'ref', 'main'))}")
        return
    if tree.get("truncated"):
        note("GitHub cut its list of this repository short, so a file at the far "
             "end of a very large tree may be missing")
    _set_items(items)


def _local_tree(values: dict, paths: dict) -> None:
    """The same list, walked off a folder on this machine."""
    text = str(local_folder(values) or "")
    folder = Path(text).expanduser()
    if not text or not folder.is_dir():
        stop(f"there is no folder here to install from: {text or '(none given)'}")
        return
    say(f"{job['label']} - reading {folder}...")
    items: list[dict] = []
    for candidate in sorted(folder.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(folder).as_posix()
        if matches(relative, paths["skip"]) or _ignored(relative):
            continue
        try:
            size = int(candidate.stat().st_size)
        except OSError:
            size = 0
        items.append({"path": relative, "size": size, "local": str(candidate)})
    if not items:
        stop(f"{folder} holds no files that are not skipped")
        return
    _set_items(items)


def _set_items(items: list[dict]) -> None:
    """The list is settled: hand it to the job and move on."""
    items.sort(key=lambda item: item["path"])
    job["items"] = items
    show_plan(items)
    note(f"{len(items)} file(s) in the download")
    _next()


# --- stage: the files themselves ---

def _stage_fetch() -> None:
    """Start the download, one file per tick."""
    if not job["items"]:
        _next()
        return
    _progress(0, len(job["items"]),
              f"{job['label']} - downloading {len(job['items'])} file(s)...")
    _again()


def _unit_fetch() -> None:
    """One raw file, written into the addon's own staging folder."""
    values, paths = job["values"], job["paths"]
    items = job["items"]
    index = int(job.get("index", 0))
    if index >= len(items):
        _next()
        return
    item = items[index]
    owner = safe_path(config_get(values, "owner", ""), DEFAULTS["owner"])
    repo = safe_path(config_get(values, "repo", ""), DEFAULTS["repo"])
    full = "/".join([part for part in (paths["repo_path"], item["path"]) if part])
    address = f"/{owner}/{repo}/{_ref(values)}/{full}"
    _progress(index, len(items),
              f"{job['label']} - {index + 1} of {len(items)}: {item['path']}")
    try:
        status, body = _http(values).get("raw.githubusercontent.com", address)
    except (http.client.HTTPException, OSError) as error:
        stop(f"the download stopped: {error}")
        return
    if status == 404 and not job.get("fetched"):
        # A first 404 means a wrong address, not one file gone.
        stop(f"{one_line(config_get(values, 'owner', ''))}/"
             f"{one_line(config_get(values, 'repo', ''))} has no "
             f"{full} at {one_line(config_get(values, 'ref', 'main'))}. Check the "
             "branch, tag or commit, the folder inside the repository, and the "
             "token in Settings > Connection if the repository is private.")
        return
    if status != 200:
        _problem(f"{item['path']}: GitHub answered {status}")
        job["index"] = index + 1
        _again()
        return
    target = staging() / item["path"]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError as error:
        stop(f"the download could not be written on this machine: {error}")
        return
    item["local"] = str(target)
    item["size"] = len(body)
    job["fetched"] = int(job.get("fetched") or 0) + 1
    job["index"] = index + 1
    _again()


def staging() -> Path:
    """The addon's own folder under ``api.paths.data`` for what it downloads."""
    folder = Path(api.paths.data) / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _reset_staging() -> None:
    """Empty the staging folder before a download, so no old file rides along."""
    for child in staging().iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


# --- stage: what the device already has ---

def _stage_remote() -> None:
    """One command listing every file under the served folder, with its size."""
    script = "\n".join([
        f'ROOT={quoted(job["paths"]["home"])}',
        'if [ ! -d "$ROOT" ]; then echo "NOTHING: that folder is not there yet"; '
        'exit 0; fi',
        """find "$ROOT" -type f -exec stat -c '%s|%n' {} + 2>/dev/null""",
    ])
    _device(script, on_remote, timeout=60, step="asking the device what it has")


def on_remote(result: dict) -> None:
    """Fill the plan table in: what is there, and what this job means to do."""
    if job is None:
        return
    root = job["paths"]["home"].rstrip("/") or "/"
    remote: dict[str, int] = {}
    for line in str(result.get("out") or "").splitlines():
        size, _, path = line.partition("|")
        path = path.strip()
        if not path.startswith(root + "/"):
            continue
        try:
            remote[path[len(root) + 1:]] = int(size)
        except ValueError:
            continue
    job["remote"] = remote
    values = job["values"]
    unchanged = bool(config_get(values, "skip_unchanged", True))
    for item in job["items"]:
        seen = remote.get(item["path"])
        item["remote"] = human(seen) if seen is not None else "-"
        if seen is None:
            item["action"] = "new"
        elif not unchanged:
            item["action"] = "written again"
        elif seen == int(item.get("size") or 0):
            item["action"] = "already there"
        else:
            item["action"] = "replaced"
    # Leftovers no route asked for, counted for the third badge.
    job["extras"] = len(_extras(job["items"], job["paths"]))
    show_plan(job["items"])
    if not remote:
        note(f"{root} is empty or missing - this is a fresh install")
    note(f"{todo_count()} of {len(job['items'])} file(s) need writing")
    _next()


def todo_count() -> int:
    """How many of the planned files the device does not already have intact."""
    items = (job or {}).get("items") or []
    return len([item for item in items if item.get("action") != "already there"])


def _stage_ready() -> None:
    """A look-around job says what it would do, and stops there."""
    items = job["items"]
    todo = todo_count()
    extra = _extras(items, job["paths"])
    note(f"{len(items)} file(s) fetched, {todo} would be written"
         + (f", {len(extra)} would be tidied away" if extra else ""))
    job["summary"] = lambda: (f"{len(items)} file(s) looked at, {todo} to write - "
                              "press Install when you want it done")
    progress(len(items), len(items))
    say(f"{len(items)} file(s) ready: {todo} would be written")
    _next()


# --- stage: the ask, the backup, and the writing ---

def _stage_confirm() -> None:
    """Ask once, in plain words, before anything on the device is touched."""
    items = job["items"]
    values, paths = job["values"], job["paths"]
    todo = todo_count()
    extra = _extras(items, paths)
    lines = [f"Write {todo} of {len(items)} file(s) into\n\n  {paths['home']}\n"]
    if extra and bool(config_get(values, "remove_extra", False)):
        lines.append(f"{len(extra)} file(s) that are not in the download will be "
                     "deleted, except those the config keeps.\n")
    elif extra:
        lines.append(f"{len(extra)} file(s) on the device are not in the download; "
                     "they stay where they are.\n")
    lines.append("The folder is backed up on the device first.\n"
                 if bool(config_get(values, "backup_first", True))
                 else "No backup will be taken.\n")
    lines.append(f"uhttpd.{paths['section']} is set to serve it on port "
                 f"{paths['port']}, and the web server is restarted.\n")
    if not api.ui.question("Install the Berry dashboard",
                           "\n".join(lines) + "Continue?", default=True):
        stop("install cancelled")
        return
    _next()


def _extras(items: list[dict], paths: dict) -> list[str]:
    """What the device has that this download does not, minus what must stay."""
    wanted = {item["path"] for item in items}
    return [relative for relative in sorted((job or {}).get("remote") or {})
            if relative not in wanted
            and not matches(relative, paths["keep"])
            and not _ignored(relative)]


def _stage_backup() -> None:
    """tar the folder up on the device, if there is a folder to tar."""
    values, paths = job["values"], job["paths"]
    if not bool(config_get(values, "backup_first", True)):
        _next()
        return
    root = paths["home"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{posixpath.basename(root) or 'dashboard'}-{stamp}.tgz"
    script = "\n".join([
        f'ROOT={quoted(root)}',
        f'INTO={quoted(paths["backup_dir"])}',
        'if [ ! -d "$ROOT" ]; then echo "(nothing to back up yet)"; exit 0; fi',
        'if [ -z "$(ls -A "$ROOT" 2>/dev/null)" ]; then'
        '  echo "(the folder is empty)"; exit 0; fi',
        'mkdir -p "$INTO" 2>/dev/null ||'
        '  { echo "note: cannot write into $INTO, so no backup"; exit 0; }',
        f'FILE="$INTO/{name}"',
        'if tar -czf "$FILE" -C "$ROOT" . 2>/dev/null; then',
        '  echo "backed up to $FILE"',
        '  ls -lh "$FILE" | tail -n 1',
        'else',
        '  echo "note: tar gave up, so nothing was backed up"',
        'fi',
    ])
    _device(script, lambda _result: _next(), timeout=180, step="backing the folder up")


def _stage_write() -> None:
    """Send the files over, holding one session if this addon may hold one."""
    queue = [item for item in job["items"] if item.get("action") != "already there"]
    job["queue"] = queue
    job["index"] = 0
    if not queue:
        note("every file was already there in the same size")
        _next()
        return
    _open_session()
    _progress(0, len(queue), f"{job['label']} - writing {len(queue)} file(s)...")
    _again()


def _unit_write() -> None:
    """One file up to the device per tick."""
    queue = job.get("queue") or []
    index = int(job.get("index", 0))
    if index >= len(queue):
        _close_session()
        _next()
        return
    item = queue[index]
    _progress(index, len(queue),
              f"{job['label']} - {index + 1} of {len(queue)}: {item['path']}")
    try:
        _put(item.get("local") or "", item["path"])
    except (AddonError, OSError) as error:
        _problem(f"{item['path']}: {error}")
    else:
        item["action"] = "written"
        job["written"] = int(job.get("written") or 0) + 1
        note(f"wrote {item['path']} ({human(item.get('size') or 0)})")
    job["index"] = index + 1
    show_plan(job["items"])
    _again()


def _stage_prune() -> None:
    """Delete what the device has that the download does not, if that was asked."""
    values, paths = job["values"], job["paths"]
    if not bool(config_get(values, "remove_extra", False)):
        _next()
        return
    removed: list[str] = []
    lines = ['echo "== tidying away =="']
    for relative in _extras(job["items"], paths):
        full = target_path(paths["home"], relative)
        if not full:
            continue
        removed.append(relative)
        lines.append(f'if [ -e {quoted(full)} ]; then rm -f {quoted(full)} && '
                     f'echo "removed {relative}"; fi')
    job["removed"] = removed
    if not removed:
        note("nothing left over to tidy away")
        _next()
        return
    lines.append(f'ROOT={quoted(paths["home"])}')
    lines.append('find "$ROOT" -type d -empty -delete 2>/dev/null || true')
    _device("\n".join(lines), lambda _result: _next(), timeout=90,
            step="tidying away what is left over")


# --- stage: the page's own colours, and the web server's settings ---

def _stage_theme() -> None:
    """Write ``css/theme.css`` from the theme this program is wearing."""
    paths = job["paths"]
    if not paths["theme_sync"]:
        note("the colour sync is switched off, so the page keeps its own theme.css")
        _next()
        return
    colors: dict = {}
    try:
        colors = dict(api.theme.colors() or {})
    except (AddonError, AttributeError, TypeError) as error:
        _problem("the theme could not be read, so the page keeps its own colours: "
                 f"{error}")
        _next()
        return
    css = theme_css(colors)
    if not css:
        note("this theme names no colour the page can use, so nothing was written")
        _next()
        return
    try:
        local = staging() / "css" / "theme.css"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(css, encoding="utf-8")
        _put(str(local), "css/theme.css")
    except (AddonError, OSError) as error:
        _problem(f"css/theme.css was not written: {error}")
    else:
        note("css/theme.css written from the colours this program is wearing")
    _next()


def _stage_uci() -> None:
    """Create or repair the uhttpd section that serves this folder."""
    paths = job["paths"]
    _device(uci_script(paths["section"], paths["home"], paths["port"]),
            lambda _result: _next(), timeout=90,
            step=f"setting uhttpd.{paths['section']} up")


def _stage_after() -> None:
    """The exec bit on the CGI entry, then whatever restarts the web server."""
    values, paths = job["values"], job["paths"]
    lines: list[str] = []
    entry = target_path(paths["home"], paths["entry"])
    if bool(config_get(values, "make_executable", True)) and entry:
        lines += [
            'echo "== the CGI entry =="',
            f'if [ -f {quoted(entry)} ]; then chmod 755 {quoted(entry)} && '
            f'echo "{paths["entry"]} is executable"; '
            f'else echo "note: there is no {paths["entry"]} there to set"; fi',
        ]
    if paths["restart"]:
        lines.append('echo "== after =="')
        for one in paths["restart"]:
            lines.append(f'{one} || {{ echo "note: {one} did not succeed"; }}')
    else:
        lines.append("echo '(no restart command configured)'")
    lines.append('echo "DONE"')
    _device("\n".join(lines), lambda _result: _next(), timeout=180,
            step="setting it up and restarting the web server")


def _stage_verify() -> None:
    """Ask the device for its own page: the one proof that it is really working."""
    values, paths = job["values"], job["paths"]
    if not bool(config_get(values, "verify", True)):
        _next()
        return
    entry = target_path(paths["home"], paths["entry"])
    script = "\n".join([
        f'ENTRY={quoted(entry)}',
        'if [ -n "$ENTRY" ] && [ ! -f "$ENTRY" ]; then'
        '  echo "MISSING: there is no CGI entry file on the device"; fi',
        'echo "== the page =="',
    ] + _page_probe(page_urls(paths)))
    _device(script, on_verified, timeout=45,
            step="asking the device for its page")


def on_verified(result: dict) -> None:
    """Read the answer back: a login page means it worked, anything else does not."""
    if job is None:
        return
    check = parse_check(result)
    last_check.clear()
    last_check.update(check)
    show_check(check)
    body = str(result.get("out") or "").lower()
    written = int(job.get("written") or 0)
    port = job["paths"]["port"]
    if "noanswer" in body:
        _problem("the dashboard did not answer. Check that uhttpd listens on that "
                 "port, that python3 is installed and that the folder it serves is "
                 "the one this installed into")
    elif "missing" in body:
        _problem("there is no CGI entry file where the settings say there should be")
    elif check.get("is_berry"):
        note("the Berry dashboard answers")
        job["summary"] = lambda: (f"{written} file(s) written and the dashboard "
                                  f"answers on port {port}")
    else:
        note("the port answered, but not with what looks like the dashboard")
        job["summary"] = lambda: f"{written} file(s) written; the page did not look right"
    _next()


# --- one SFTP session, when the app will hand one out ---

def _open_session() -> bool:
    """Hold one SSH session for the whole transfer, if this addon may.

    Only an addon that may connect on its own is handed one, which is why the
    manifest says ``both``: on the app's session the files go over the app's
    channel instead, one upload at a time, and the install is no slower for it.
    """
    if job is None:
        return False
    if job.get("session") is not None:
        return True
    try:
        context = api.device.open(timeout=60)
        job["session"] = context.__enter__()
        job["context"] = context
    except Exception as error:              # a refusal here is normal, not a failure
        job["session"] = None
        job["context"] = None
        api.log(f"each file goes over the app's session on its own: {error}")
        return False
    note(f"one session held for the transfer to {job['paths']['home']}")
    return True


def _close_session() -> None:
    if job is None:
        return
    context = job.get("context")
    job["context"] = None
    job["session"] = None
    if context is None:
        return
    try:
        context.__exit__(None, None, None)
    except Exception as error:
        api.log(f"the session did not close tidily: {error}")


def _put(local: str, relative: str) -> None:
    """Send one staged or local file into the served folder."""
    if not local or not Path(str(local)).is_file():
        raise AddonError(f"there is no local file to send for {relative}")
    target = target_path(job["paths"]["home"], relative)
    if not target:
        raise AddonError(f"{relative} is not a path this addon will write")
    session = (job or {}).get("session")
    if session is not None:
        _remote_dirs(session.sftp, posixpath.dirname(target))
        session.sftp.put(str(local), target)
        return
    api.device.upload(str(local), target, outside_root=True)


def _remote_dirs(sftp: Any, directory: str) -> None:
    """mkdir -p over SFTP, so every file has somewhere to land."""
    if not directory or directory == "/":
        return
    parent = posixpath.dirname(directory)
    if parent and parent != directory:
        _remote_dirs(sftp, parent)
    try:
        sftp.stat(directory)
    except OSError:
        try:
            sftp.mkdir(directory)
        except OSError:
            pass


# --- buttons that are a single command, not a job ---

def _quick(command: str, told: Callable[[dict], None], step: str,
           timeout: int = 90) -> None:
    """One command with the buttons held down while the device thinks."""
    global working
    if working or api is None:
        return
    working = True
    _buttons(False)
    progress(0, 1)
    bar_text(step)                      # the bar speaks these one-command jobs too
    say(f"{step}...")

    def answered(result: dict) -> None:
        global working
        working = False
        _buttons(True)
        progress(1, 1)
        out = str(result.get("out") or "").strip()
        for line in out.splitlines()[:20]:
            note(line)
        if int(result.get("code") or 0) != 0:
            bar_text(f"{step} failed")
            say(_why_failed(result, step, timeout), bad=True)
            return
        bar_text("done")
        told(result)

    api.device.run_async(command, answered, timeout=timeout)


def restart_server(_checked: bool = False) -> None:
    """Run the restart command from the config, and nothing else."""
    if api is None or working:
        return
    commands = job_paths(merge_current())["restart"]
    if not commands:
        say("there is no restart command in the config", bad=True)
        return
    script = "\n".join(f'{one} || echo "note: {one} did not succeed"'
                       for one in commands)
    _quick(script, lambda _result: say("the web server was restarted"),
           "restarting the web server")


def check_page(_checked: bool = False) -> None:
    """Ask the device for the dashboard and say what came back."""
    if api is None or working:
        return
    paths = job_paths(merge_current())
    script = "\n".join(['echo "== the page =="'] + _page_probe(page_urls(paths)))

    def told(result: dict) -> None:
        check = parse_check(result)
        last_check.clear()
        last_check.update(check)
        show_check(check)
        if not check.get("answered"):
            say(f"nothing answers on port {paths['port']}", bad=True)
        elif check.get("is_berry"):
            say(f"the Berry dashboard answers on {check.get('url')}")
        else:
            say(f"{check.get('url')} answered, but not with the dashboard", bad=True)

    _quick(script, told, "asking the device for its page", timeout=35)


def read_uci(_checked: bool = False) -> None:
    """The section as uci reads it, and the whole file, into the raw page.

    The file is asked for with ``cat`` on the same SSH channel as everything else
    here: reading it over SFTP instead would block the interface until the device
    answered, and the file is only ever shown, never written from this page.
    """
    if api is None or working:
        return
    section = job_paths(merge_current())["section"]
    script = "\n".join([
        f'echo "== uci show uhttpd.{section} =="',
        f'uci -q show uhttpd.{section} 2>/dev/null || echo "(there is no such section yet)"',
        'echo "== /etc/config/uhttpd =="',
        'cat /etc/config/uhttpd 2>/dev/null || echo "(the file is not there)"',
    ])

    def told(result: dict) -> None:
        try:
            api.tabs.set_widget(TAB, "raw_uci", "value",
                                str(result.get("out") or "").strip())
        except AddonError:
            pass
        uci_page(1)                       # show what was just read
        say("the device's uhttpd settings are on the Raw file page")

    _quick(script, told, "reading the web server's settings", timeout=60)


def apply_uci(_checked: bool = False) -> None:
    """Write the options the form shows, commit them and bring the server up."""
    if api is None or working:
        return
    paths = job_paths(merge_current())
    script = "\n".join([uci_script(paths["section"], paths["home"], paths["port"])]
                       + apply_uci_lines(paths["section"], paths["restart"]))

    def told(_result: dict) -> None:
        say(f"uhttpd.{paths['section']} now serves {paths['home']} on "
            f"port {paths['port']}")
        check_device_later()

    _quick(script, told, "applying the web server settings", timeout=120)


def check_device_later() -> None:
    """Re-check once the restarted server has had a moment to come up."""
    try:
        api.schedule.after(1.5, check_device)
    except (AddonError, AttributeError, TypeError):
        pass


#: Folders a rename must never take for the dashboard's own: the device runs out
#: of them, or out of its own admin page, and no dashboard is worth that.
PROTECTED_FOLDERS = ("/", "/www", "/bin", "/etc", "/lib", "/overlay",
                     "/rom", "/root", "/sbin", "/srv", "/tmp", "/usr")


def _root_of(folder: Any) -> str:
    """A device folder as one plain path: no quotes, no trailing slash."""
    return str(folder or "").strip().strip("'\"").rstrip("/")


def _find_old(paths: dict) -> tuple[str, str]:
    """Where the dashboard's files sit now, and which section serves them.

    The section named in the form is believed when uci has a folder for it. When
    it has none - the usual for an install that predates this addon, which never
    wrote a section of its own - a section of the router's that serves one of the
    folders holding the entry file is believed next; and failing even that, the
    folder holding the file on its own, whose section comes back empty because
    nothing admits to serving it. ``("", "")`` means the device never said where
    anything is, which is a question for a check, not for a rename.
    """
    into = _root_of(paths["home_root"])
    served = [_root_of(one.get("folder"))
              for one in last_check.get("served") or []]
    served = [one for one in served if one]
    sections = last_check.get("sections") or {}
    home = _root_of(last_check.get("home"))
    if home:
        # Our own section says something, so the folder it names is where the
        # files are - with what the form keeps inside it taken back off, since a
        # rename moves the folder and not the path inside it.
        sub = _root_of(paths["subdir"])
        if sub and home.endswith("/" + sub):
            home = home[: -len("/" + sub)]
        return home, str(paths["section"])
    for folder in served:
        if folder == into:
            continue
        for name, where in sections.items():
            if name != paths["section"] and _root_of(where).startswith(folder):
                return folder, name
    for folder in served:
        if folder != into:
            return folder, ""
    for name, where in sections.items():
        if name != paths["section"] and _root_of(where) != into:
            return _root_of(where), name
    return "", ""


def move_home(_checked: bool = False) -> None:
    """Rename the folder the device really serves to the name in the form.

    Two answers are needed and only one of them is in the settings: where the
    files are now, and where they are to go. The first is read off the device
    rather than assumed (see :func:`_find_old`). When it says nothing, nothing is
    renamed and nothing is written: this button used to point uhttpd at an empty
    ``/www_berry`` whenever it could not work out where the files were, which is
    the one way to lose the dashboard without moving anything back into place.

    It is also the only thing here that moves a device's own files rather than
    writing this addon's, so it is asked about by name first, and the script
    refuses outright when the new name is already taken - a ``mv`` onto an
    existing folder would bury the dashboard inside it rather than replace it.
    """
    if api is None or working:
        return
    values = merge_current()
    paths = job_paths(values)
    into = _root_of(paths["home_root"])
    section = paths["section"]
    if not into:
        say("there is no name to move to: the served folder box is empty", bad=True)
        return
    if into in PROTECTED_FOLDERS:
        say(f"{into} is a folder the device needs as itself - name somewhere "
            "else for the dashboard to live", bad=True)
        return
    if not last_check:
        say("the device has not been asked what it has yet - Re-check first, so "
            "the move starts from what is really there", bad=True)
        return
    old, old_section = _find_old(paths)
    if not old:
        say(f"the device named no folder to move from, and {into} is not on it - "
            "the installer page writes the dashboard first", bad=True)
        return
    if old in PROTECTED_FOLDERS:
        say(f"{old} is the device's own folder and stays where it is - an install "
            f"writes {into} beside it", bad=True)
        return
    if old == into and old_section == section:
        say(f"{into} is already what uhttpd.{section} serves")
        return
    foreign = "" if not old_section or old_section == section \
        else uci_name(old_section)
    who = f"uhttpd.{old_section}" if old_section else "no section of uhttpd"
    ask = (f"{who} serves\n\n  {old}\n\nand the form names\n\n  {into}\n\n"
           "instead.\n\nThe folder on the device is renamed, the setting is "
           "written with uci, committed, and the web server restarted. What is "
           "inside the folder goes with it, the file holding the page's PIN "
           "included - which is why this is a move and not an install.")
    if foreign:
        ask += ("\n\nuhttpd." + foreign + " is what serves that folder today, so "
                "its home moves along with the files under it.")
    if not api.ui.question("Move the served folder", ask + "\n\nContinue?",
                           default=False):
        say("the folder was left where it is")
        return
    # The rename works on the folder itself; anything the form keeps inside it as
    # a subfolder goes with it, and is put back on the end for the setting.
    into_home = posixpath.join(into, paths["subdir"]) if paths["subdir"] else into
    lines = [
        f'OLD={quoted(old)}',
        f'NEW={quoted(into)}',
        'if [ ! -d "$OLD" ]; then echo "STOP: $OLD is not on the device, so '
        'nothing was moved"; exit 0; fi',
        'if [ -e "$NEW" ]; then echo "STOP: $NEW is already there, holding '
        '$(find "$NEW" -type f 2>/dev/null | wc -l) file(s), so nothing was '
        'moved"; exit 0; fi',
        'mv "$OLD" "$NEW" && echo "moved $OLD to $NEW"',
    ]
    if foreign:
        # That section named a folder, not a name: whatever followed the old path
        # keeps following the new one, so a dashboard in a subfolder of it is
        # still in a subfolder of the folder it moves to.
        was = _root_of((last_check.get("sections") or {}).get(old_section) or old)
        after = into + was[len(old):]
        lines.append(f'uci set uhttpd.{foreign}.home={squoted(after)}')
        lines.append(f'echo "uhttpd.{foreign}.home now {after}"')
    lines.append(uci_script(section, into_home, paths["port"]))
    lines.append('echo "== what uhttpd serves now =="')
    lines.append('uci -q show uhttpd 2>/dev/null | grep home= '
                 '|| echo "(nothing is set)"')
    restart = " ; ".join(paths["restart"]) if paths["restart"] else "true"
    lines.append(f'{restart} || echo "note: the web server did not restart"')

    def told(result: dict) -> None:
        out = str(result.get("out") or "")
        refused = next((one for one in out.splitlines() if one.startswith("STOP")), "")
        if refused:
            say(refused[len("STOP"):].lstrip(": "), bad=True)
            return
        # The name on the device and the name in the settings have to agree, or
        # the next install makes the old folder again and the dashboard ends up
        # in two pieces; the form is put right for the same reason.
        try:
            api.config.update({"web_home": into})
        except AddonError:
            pass
        try:
            api.tabs.set_widget(TAB, "web_home", "value", into)
        except AddonError:
            pass
        say(f"{into} is what uhttpd.{section} serves now")
        check_device_later()

    _quick("\n".join(lines), told, "moving the served folder", timeout=180)


def copy_raw(_checked: bool = False) -> None:
    """The file read off the device, into the editable box below it."""
    if api is None:
        return
    try:
        text = str(api.tabs.widget_value(TAB, "raw_uci", "value") or "")
        if not text:
            say("there is nothing to copy: read the file first", bad=True)
            return
        api.tabs.set_widget(TAB, "raw_edit", "value", text)
        say("the file is in the editor box below")
    except AddonError:
        pass


# --- the little menu that rides the dashboard's own page ---

#: What the small button floated over the embedded page offers, by id and
#: caption. Each entry comes back through ``on_widget(PAGE, id, "clicked")``,
#: so it is handled exactly like a button on the addon's own tab.
WEB_MENU_ITEMS = (
    ("menu_status", "Status page"),
    ("menu_installer", "Go to installer"),
    ("menu_uci", "uhttpd settings"),
    ("menu_recheck", "Re-check the device"),
)


def show_tab_page(page: int) -> None:
    """Bring the addon's own tab forward and stand it on one of its pages."""
    view_page(page)
    focus_tab()


#: Where each entry of that menu leads.
WEB_MENU = {
    "menu_status": lambda: show_tab_page(STATUS_PAGE),
    "menu_installer": lambda: show_tab_page(INSTALL_PAGE),
    "menu_uci": lambda: show_tab_page(UHTTPD_PAGE),
    "menu_recheck": check_device,
}


def menu_entries() -> list[dict]:
    """The corner button's entries, in the shape ``add_web_tab`` takes."""
    return [{"id": identifier, "label": label}
            for identifier, label in WEB_MENU_ITEMS]


def open_dashboard(_checked: bool = False) -> None:
    """The page in a tab of its own, so an install can be looked at straight away.

    The scheme follows the device's own config: the check asks the uhttpd
    section whether it listens over https or redirects there, and a section
    that does is visited over https - whatever the plain probe happened to
    answer on first."""
    if api is None:
        return
    paths = job_paths(merge_current())
    scheme = "https" if (str(last_check.get("url") or "").startswith("https")
                         or last_check.get("https")) else "http"
    url = ""
    try:
        url = str(api.session.web_url({"ip": "", "port": paths["port"],
                                       "path": paths["web_path"],
                                       "scheme": scheme}) or "")
    except (AddonError, AttributeError, TypeError):
        url = ""
    if not url.startswith("http"):
        say("there is no address to open: put a host in the connection panel",
            bad=True)
        return
    try:
        if api.tabs.exists(PAGE):
            api.tabs.activate(PAGE)
            api.tabs.navigate(PAGE, url)
        else:
            api.tabs.add_web_tab(PAGE, url=url, menu=menu_entries())
        say(f"opened {url}")
    except AddonError as error:
        say(f"the page could not be opened: {error}", bad=True)


# --- the buttons ---

def _buttons(usable: bool) -> None:
    """Grey the buttons out while a job runs; Stop is the other way round."""
    for identifier, _text in BUTTONS:
        try:
            api.tabs.set_widget(TAB, identifier, "enabled", bool(usable))
        except AddonError:
            pass
    try:
        api.tabs.set_widget(TAB, STOP, "enabled", not usable)
    except AddonError:
        pass


def _begin(action: str) -> None:
    """Fetch, or fetch and write: the same job, one stage longer."""
    if api is None or working:
        return
    values = merge_current()
    if action == "install":
        _ensure_sftp(values)
        start_job(route(values, "install"), "Install", values)
    else:
        start_job(route(values, "fetch"), "Fetch", values)


def _ensure_sftp(values: dict) -> None:
    """Ask the app for a session if there is none, so a missing password says so up front.

    One connection carries both transports, so this asks the app to open its
    main session; the addon never connects itself, and the answer says which
    switch is still in the way.
    """
    try:
        if api.device.is_connected("sftp"):
            return
        api.device.ensure_connected("sftp")
        note("the main connection was not up for this addon, so the program is "
             "opening it")
    except AddonError as error:
        note(f"SFTP is not connected: {error}")


def push_config(_checked: bool = False) -> None:
    if api is None:
        return
    api.config.update(values_of())
    say("saved to addons/_data/berry_web/config.json")
    api.log("config saved from the tab")


def pull_config(_checked: bool = False) -> None:
    if api is None:
        return
    show_form(api.config.all())
    say("forms reloaded from the config")


def stop_job(_checked: bool = False) -> None:
    """Give up on the job in hand. Nothing half written is left running."""
    if working and job is not None:
        stop("stopped by hand - the files already written stay where they are")
        return
    say("there is nothing to stop")


def to_page(page: int) -> None:
    """One of the page buttons: jump the notebook to where the work is."""
    if api is not None:
        view_page(page)


#: What each button on the tab does, by its spec id.
HANDLERS.update({
    "recheck": lambda: check_device(landing=False),
    "check": check_page,
    "open": open_dashboard,
    "to_installer": lambda: to_page(INSTALL_PAGE),
    "to_uci": lambda: to_page(UHTTPD_PAGE),
    "to_status": lambda: to_page(STATUS_PAGE),
    "fetch": lambda: _begin("fetch"),
    "install": lambda: _begin("install"),
    "push": push_config,
    "pull": pull_config,
    "read_uci": read_uci,
    "refresh_raw": read_uci,
    "copy_raw": copy_raw,
    "apply_uci": apply_uci,
    "restart": restart_server,
    "move_home": move_home,
    "stop": stop_job,
})

#: The stages a route is made of, and the work each repeats.
STAGES = {
    "tree": _stage_tree,
    "fetch": _stage_fetch,
    "remote": _stage_remote,
    "ready": _stage_ready,
    "confirm": _stage_confirm,
    "backup": _stage_backup,
    "write": _stage_write,
    "prune": _stage_prune,
    "theme": _stage_theme,
    "uci": _stage_uci,
    "after": _stage_after,
    "verify": _stage_verify,
}

UNITS = {"fetch": _unit_fetch, "write": _unit_write}


# --- the hooks the app calls ---

def focus_tab(_checked: bool = False) -> None:
    """The Tools menu entry: go to the tab."""
    if api is None:
        return
    api.tabs.activate(TAB)


def init(plugin_api: Any) -> None:
    """Build the tab from the saved config, carrying the old installer's across."""
    global api
    api = plugin_api
    # Read before anything is written: the file a retired addon left has no
    # ``web_home`` in it, and once the defaults are on disk that fact is gone.
    kept = _stored_config()
    carried = bridge_settings(kept, _profile_root())
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Berry web dashboard - addons/_data/berry_web/config.json")
    if carried != kept:
        values = api.config.update(carried)
        api.log("the settings the retired installer left have been carried "
                f"across: the dashboard is served from "
                f"{values.get('web_home')}")
    build_tab()
    show_form(values)
    api.ui.menu_item("Berry web dashboard", focus_tab, menu="Tools")


def _refresh_state(_payload: Any = None) -> None:
    """Redraw the header line: the connection came or went, or a switch that
    rules over this addon's use of it moved."""
    if api is not None:
        try:
            show_target(api.config.all())
        except AddonError:
            pass


def start() -> None:
    """Say where the files would go, then ask the host what it has.

    The chip is kept honest from here on: the main connection, either app-wide
    transport switch and this addon's own switches all redraw it, because the
    addon may only use file transfer while every one of them is on. The look at
    the device is what decides whether the tab opens on the status page or on the
    installer, and it is only asked when there is a connection to ask it on.
    """
    if api is None:
        return
    show_target(api.config.all())
    for name in ("sftp_connected", "sftp_disconnected", "ssh_connected",
                 "ssh_disconnected", "settings_applied", "profile_changed",
                 "transport_changed", "connections_enabled",
                 "connections_disabled"):
        api.events.on(name, _refresh_state)
    try:
        reachable = bool(api.device.is_connected("ssh"))
    except (AddonError, AttributeError, TypeError):
        reachable = False
    if reachable:
        check_device()
    else:
        say("not connected yet: press Connect and the dashboard will be looked for",
            bad=True)


def shutdown() -> None:
    global api, working, job
    _close_session()
    _drop_http()
    working = False
    job = None
    last_check.clear()
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A button on the tab, or an entry of the dashboard page's own corner
    menu, was clicked: run what its id means."""
    if str(event) != "clicked":
        return
    if str(tab) == TAB:
        handler = HANDLERS.get(str(identifier))
        if handler is not None:
            handler()
        return
    if str(tab) == PAGE:
        handler = WEB_MENU.get(str(identifier))
        if handler is not None:
            handler()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: make the forms match the file."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    show_form(api.config.all())
    say("config changed in Settings; the forms now match it")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
