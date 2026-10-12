#!/usr/bin/env python3
"""Rasconf web installer: put the web dashboard onto the device from GitHub.

Adds a ``Web installer`` tab. The dashboard the device serves - the tree under
``src/www_rasconf`` in this project's repository - is fetched from GitHub and
written into the remote folder of the *current profile* over SFTP, so a device
can be given the web interface, or brought up to date, without a checkout of
the repository anywhere near the machine running the program.

The job runs one file at a time. Nothing here holds the interface still: each
step is asked to happen on the next tick, so the progress bar and the plan table
move while twenty-odd files come down and go up.

Nothing is hardcoded that a user might want to change: the repository, the
branch or tag, the folder inside it, the patterns that are never sent, the
command that restarts the web server, whether to back the old copy up first and
what to leave alone when tidying are kept in
``addons/_data/web_installer/config.json``. That file is shown as a form under this
addon's name in Settings > Addons, and it is plain JSON, so it can be edited
anywhere.

The files go where the profile's own deploy puts them: the remote root is the
one in the connection panel, and every path is below it. ``hash.file`` - the PIN
the dashboard checks - is never uploaded and never deleted, because it is not in
the repository and belongs to the device.

The tab is built from a declarative spec and its buttons report back through
``on_widget``, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab.
"""

from __future__ import annotations

import fnmatch
import http.client
import json
import posixpath
import re
import ssl
import time
from pathlib import Path
from urllib.parse import quote

# The Qt-free half of the contract, so this file also runs compiled into a
# program of its own and inside a test script that never opens a window.
from addon_common import AddonError, config_get, merge_config

ADDON = {
    "name": "Rasconf web installer",
    "version": "1.0",
    "author": "fligma",
    "description": "Downloads src/www_rasconf from GitHub and writes it into the "
                   "profile's remote folder over SFTP, with a backup, the exec "
                   "bit on the CGI entry, a web server restart and a check that "
                   "the page answers afterwards.",
    "api": 1,
}

TAB = "Web installer"
PAGE = "Rasconf dashboard"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the job in progress, or nothing

# ---------------------------------------------------------------------------
# What can be configured, and what it starts out as
# ---------------------------------------------------------------------------

DEFAULTS = {
    "source": "GitHub",
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "repo_path": "src/www_rasconf",
    "token": "",
    "local_dir": "",
    "subdir": "",
    "skip": ["README.md"],
    "keep": ["cgi-bin/hash.file"],
    "skip_unchanged": True,
    "remove_extra": False,
    "backup_first": True,
    "backup_dir": "/tmp/rasconf_web_backups",
    "make_executable": True,
    "cgi_entry": "cgi-bin/index.py",
    "restart_command": ["/etc/init.d/uhttpd restart"],
    "verify": True,
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
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
     "hint": "main, a tag like v1.4, or a full commit sha"},
    {"key": "repo_path", "label": "Folder inside the repository", "kind": "text",
     "hint": "Its contents are what gets installed"},
    {"key": "token", "label": "GitHub token", "kind": "text",
     "hint": "Only needed for a private repository, or when the file list is "
             "refused because too many anonymous requests were made",
     "placeholder": "blank for a public repository"},
    {"key": "local_dir", "label": "Local folder", "kind": "text",
     "hint": "Used when the files come from a local folder; blank means the "
             "source folder of the current profile",
     "placeholder": "the profile's source folder"},

    {"key": "subdir", "label": "Subfolder of the remote directory", "kind": "text",
     "hint": "Blank installs straight into the profile's remote root, which is "
             "where the web server looks by default",
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
     "kind": "bool", "hint": "Only ever removes what is under the remote root and "
                             "is not kept by the list above"},

    {"key": "backup_first", "label": "Back the folder up first", "kind": "bool"},
    {"key": "backup_dir", "label": "Where the backup goes", "kind": "text",
     "hint": "On the device. One tarball per install, named with the time"},
    {"key": "make_executable", "label": "Make the CGI entry executable", "kind": "bool",
     "hint": "uhttpd runs the entry file itself, so it needs the exec bit"},
    {"key": "cgi_entry", "label": "Entry file to make executable", "kind": "text"},
    {"key": "restart_command", "label": "Commands to restart the web server",
     "kind": "multiline",
     "hint": "One per line, run on the device once the files are written"},
    {"key": "verify", "label": "Check the page answers afterwards", "kind": "bool"},
    {"key": "web_port", "label": "Port the dashboard listens on", "kind": "number",
     "minimum": 1, "maximum": 65535},
    {"key": "web_path", "label": "Path of the dashboard on that port", "kind": "text"},
]

GROUPS = [
    ("Where the files come from", ["source", "owner", "repo", "ref", "repo_path",
                                   "token", "local_dir"]),
    ("Where they go on the device", ["subdir", "skip", "keep", "skip_unchanged",
                                     "remove_extra"]),
    ("Before and after", ["backup_first", "backup_dir", "make_executable",
                          "cgi_entry", "restart_command", "verify", "web_port",
                          "web_path"]),
]

FIELDS_BY_KEY = {str(field["key"]): field for field in FIELDS}
KIND_TO_WIDGET = {"text": "line", "number": "spin", "bool": "check",
                  "choice": "combo", "list": "line", "multiline": "multi"}

BUTTONS = (("fetch", "Fetch files"), ("install", "Install"), ("open", "Open dashboard"),
           ("restart", "Restart web server"), ("check", "Check page"),
           ("push", "Save to config"), ("pull", "Reload from config"))

#: The way out, which stays live while everything else is greyed out.
STOP = "stop"

#: What each button on the tab does, filled in once the functions exist.
HANDLERS: dict = {}

#: The list for when GitHub will not give one: the dashboard's own files as they
#: are in this repository. The raw files come from a CDN that is not counted
#: against the API's sixty anonymous requests an hour, so an installer that knows
#: the layout keeps working when the file list stops answering.
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
"""

BANNER_STYLE = ("QLabel { color: #e8e8e8; font-size: 13pt; font-weight: 600;"
                " padding: 4px 2px 2px 2px; }")

#: Shell punctuation that must never reach a command line from a config value.
_UNSAFE = re.compile(r"[\"'`$;|&<>\\\n\r\t]")
_RELATIVE = re.compile(r"^[A-Za-z0-9._/-]+$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def one_line(value) -> str:
    """One clean line of text from whatever the form holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def safe_path(value, fallback: str = "") -> str:
    """A relative path that is safe to put into a shell command, or the fallback."""
    text = one_line(value).replace("..", "")
    text = _UNSAFE.sub("", text).strip("/ ")
    return text if text and _RELATIVE.match(text) else fallback


def clean_list(value) -> list[str]:
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


# ---------------------------------------------------------------------------
# Reading the form
# ---------------------------------------------------------------------------

def values_of() -> dict:
    """The form as a config-shaped dict."""
    out: dict = {}
    for key, field in FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        try:
            raw = api.tabs.widget_value(TAB, key,
                                         "checked" if kind == "bool" else "value")
        except AddonError:
            raw = None
        if raw is None:
            out[key] = config_get(DEFAULTS, key)
        elif kind == "list":
            out[key] = clean_list(raw)
        elif kind == "multiline":
            out[key] = [one_line(line) for line in str(raw or "").splitlines()
                        if one_line(line)]
        elif kind == "number":
            try:
                out[key] = int(raw)
            except (TypeError, ValueError):
                out[key] = int(field.get("minimum", 0))
        else:
            out[key] = raw
    return out


def merge_current() -> dict:
    """The saved config with whatever is in the form on top of it."""
    merged = merge_config(DEFAULTS, api.config.stored())
    return merge_config(merged, values_of())


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------

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


def tab_spec(values: dict) -> dict:
    """The whole tab: the settings on the left, what the job is doing on the right."""
    groups = []
    for title, keys in GROUPS:
        groups.append({
            "kind": "group", "text": title, "layout": "form",
            "children": [control_spec(key, values) for key in keys],
        })
    groups.append({
        "kind": "label", "id": "target", "text": "", "word_wrap": True,
        "style": "color: #9a9a9a; padding: 6px 4px;",
    })
    left = {"kind": "scroll", "children": [{"kind": "column", "children": groups,
                                            "stretch": True}]}
    right = {"kind": "column", "children": [
        {"kind": "label", "text": "What will be written",
         "style": "color: #c51a4a; font-weight: 600; padding: 2px 4px;"},
        {"kind": "table", "id": "plan",
         "columns": ["File", "Size", "On the device", "Action"],
         "tooltip": "The files this job will send, and what the device already "
                    "has of them"},
        {"kind": "multi", "id": "log", "readonly": True,
         "placeholder": "Every step of the last job, as it happened",
         "tooltip": "The same lines go into the program's own log"},
    ]}
    tips = (
        ("fetch", "Fetch files", "Get the tree from GitHub or from the local "
                                 "folder and work out what the device needs"),
        ("install", "Install", "Write those files into the remote folder over SFTP"),
        ("open", "Open dashboard", "The page the device is serving, in its own tab"),
        ("restart", "Restart web server", "Run the restart command on the device"),
        ("check", "Check page", "Ask the device for the dashboard and report what "
                              "came back"),
        ("push", "Save to config", "Keep these values in "
                                   "addons/_data/web_installer/config.json"),
        ("pull", "Reload from config", "Put the saved values back into the form"),
        ("stop", "Stop", "Give up on the job in hand. What has already gone "
                         "across stays on the device"),
    )
    return {"kind": "column", "children": [
        {"kind": "label", "text": "Rasconf web installer", "style": BANNER_STYLE},
        {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
         "style": "color: #23d18b; padding: 2px 4px;"},
        {"kind": "progress", "id": "bar", "maximum": 100, "value": 0},
        {"kind": "splitter", "children": [left, right]},
        {"kind": "row", "children": [
            {"kind": "button", "id": identifier, "text": text, "tooltip": tip,
             "on": ["clicked"]} for identifier, text, tip in tips],
        },
    ]}


def build_tab() -> None:
    """The tab as a spec, and its buttons as reports.

    Nothing here holds on to a widget. The app builds the controls in its own
    process, so the one route a press can take is the ``on_widget`` hook below -
    which is also how it arrives when the addon is imported into the app, so a
    compiled copy behaves exactly like a source one.
    """
    api.tabs.add_tab(TAB, spec=tab_spec(merge_config(DEFAULTS, api.config.stored())),
                     style=TAB_STYLE)
    try:
        # This tab wears the program's own mark and keeps wearing it, so the two
        # never disagree about what the app looks like.
        api.tabs.use_app_icon(TAB)
    except AddonError as error:             # an app with no icon of its own
        api.log(f"web installer tab icon: {error}")


def show_form(values: dict) -> None:
    for key, field in FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        value = config_get(values, key, config_get(DEFAULTS, key))
        if kind in ("list", "multiline"):
            items = clean_list(value) if kind == "list" else [one_line(line)
                                                             for line in (value or []) if one_line(line)]
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
    """Move the bar.

    While a job runs, ``done`` of ``total`` is the progress *inside* the stage
    that is going on now, and each stage owns a slice of the bar (see
    :func:`_bar_spans`). So the last file of a download lands at the end of the
    download's piece instead of at 95% of the whole job with a web server still
    to restart and a page still to check - which is where the bar used to sit
    while the job quietly carried on without it. With no job, which is how the
    two one-command buttons get here, the numbers are the whole bar.
    """
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
    """The step painted inside the bar itself.

    Qt still expands ``%p`` there, so the bar reads "writing files 62%" and says
    what it is doing as well as how far through it is. The status line under it
    keeps the long version.
    """
    api.tabs.set_widget(TAB, "bar", "format", f"{text}  %p%" if text else "%p%")


def show_plan(items: list[dict]) -> None:
    """The table, in the order the job works through it."""
    rows = []
    for item in items:
        seen = item.get("remote")
        rows.append([item.get("path", ""), human(item.get("size") or 0),
                     seen if seen else "-", item.get("action", "queued")])
    api.tabs.set_widget(TAB, "plan", "rows", rows)


def describe(values: dict) -> str:
    """One line saying where the files come from, in the user's own terms."""
    if is_local(values):
        return f"From the local folder {local_folder(values) or '(none given)'}"
    return (f"From {config_get(values, 'owner', '')}/"
            f"{config_get(values, 'repo', '')} at "
            f"{config_get(values, 'ref', '')}, the folder "
            f"{config_get(values, 'repo_path', '')}")


def show_target(values: dict) -> None:
    """Under the form: where this profile's files would land, right now."""
    status: dict = {}
    try:
        status = api.session.status() or {}
    except AddonError:
        pass
    root = str(status.get("remote_root") or "the profile's remote folder")
    sub = safe_path(config_get(values, "subdir", ""))
    whole = posixpath.join(root.rstrip("/"), sub) if sub else root
    lines = [describe(values), f"Goes to {whole}"]
    host = str(status.get("host") or "")
    if host:
        lines.append(f"On {host}" + ("  (SFTP is up)" if status.get("sftp_connected")
                                     else "  (not connected yet)"))
    try:
        api.tabs.set_widget(TAB, "target", "text", "\n".join(lines))
    except AddonError:
        pass


# ---------------------------------------------------------------------------
# The one bit of network this addon needs, with nothing but the standard library
# ---------------------------------------------------------------------------

class Fetcher:
    """A few HTTPS connections, kept open and reused for the whole download.

    Twenty-two files fetched with a fresh TLS handshake apiece take longer than
    the transfer itself, and an addon may not add a dependency of its own. One
    connection per host, with a retry when the server has quietly closed it, is
    all that is needed here.
    """

    def __init__(self, token: str = ""):
        self.token = str(token or "")
        self.connections: dict[str, http.client.HTTPSConnection] = {}

    def _connection(self, host: str) -> http.client.HTTPSConnection:
        connection = self.connections.get(host)
        if connection is None:
            connection = http.client.HTTPSConnection(
                host, timeout=25, context=ssl.create_default_context())
            self.connections[host] = connection
        return connection

    def get(self, host: str, path: str, accept: str = "application/octet-stream"):
        """``(status, body)`` for one GET, retried once on a dropped connection."""
        status, body = 0, b""
        for attempt in (0, 1):
            headers = {"User-Agent": "rasconf-manager", "Accept": accept}
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

    def json(self, host: str, path: str):
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


def _refused(status: int, body, path: str) -> str:
    """A failure worth showing to a user, in words that say what to do next."""
    detail = ""
    try:
        detail = str(json.loads(body.decode("utf-8", errors="replace")).get("message") or "")
    except (ValueError, AttributeError, TypeError):
        detail = ""
    if status == 404:
        return (f"GitHub has no such branch, tag or commit, or no such repository: "
                f"{path}. Check the owner, the repository and the ref on the form, "
                "or the token if the repository is private.")
    if status == 403:
        return (f"GitHub refused the request for {path} (403)"
                + (f": {detail}" if detail else
                   ". Anonymous requests are limited to sixty an hour, so put a "
                     "token in the config or try again later."))
    return f"GitHub answered {status} for {path}" + (f": {detail}" if detail else "")


# ---------------------------------------------------------------------------
# Reading the settings into the shapes the job needs
# ---------------------------------------------------------------------------

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
    except AddonError:
        status = {}
    return str(status.get("source") or "")


def job_paths(values: dict) -> dict:
    """Every path and pattern this job works with, cleaned once here."""
    sub = safe_path(config_get(values, "subdir", ""))
    entry = safe_path(config_get(values, "cgi_entry", DEFAULTS["cgi_entry"]),
                      DEFAULTS["cgi_entry"])
    try:
        port = int(config_get(values, "web_port", DEFAULTS["web_port"]) or 8989)
    except (TypeError, ValueError):
        port = 8989
    return {
        "subdir": sub,
        "entry": posixpath.join(sub, entry) if sub else entry,
        "skip": clean_list(config_get(values, "skip", DEFAULTS["skip"])),
        "keep": clean_list(config_get(values, "keep", DEFAULTS["keep"])),
        "repo_path": one_line(config_get(values, "repo_path",
                                         DEFAULTS["repo_path"])).strip("/ "),
        "web_path": "/" + str(config_get(values, "web_path",
                                         DEFAULTS["web_path"])).lstrip("/ "),
        "backup_dir": "/" + safe_path(config_get(values, "backup_dir",
                                                 DEFAULTS["backup_dir"]),
                                      "tmp/rasconf_web_backups"),
        "port": min(max(port, 1), 65535),
    }


def page_urls(paths: dict) -> list[str]:
    """The addresses the device can ask itself for its page at.

    Loopback first, since it needs no network and does not care which profile is
    connected; then the address in the connection panel, because a uhttpd bound to
    one interface answers on the LAN address and not on ``127.0.0.1``. Either of
    them replying is the proof wanted - that the dashboard is up.
    """
    out = [f"https://127.0.0.1:{paths['port']}{paths['web_path']}"]
    try:
        status = api.session.status() or {}
    except AddonError:
        status = {}
    host = one_line(str(status.get("host") or ""))
    if host and not any(host in text for text in out):
        out.append(f"https://{host}:{paths['port']}{paths['web_path']}")
    return out


def device_path(relative: str) -> str:
    """One absolute device path below the remote root, or nothing at all.

    Anything that is going to be typed into a shell command comes through here,
    so a name from the repository or from the device's own listing cannot escape
    the folder the profile deploys into.
    """
    clean = safe_path(relative)
    if not clean:
        return ""
    root = _remote_root().rstrip("/") or "/"
    joined = posixpath.normpath(posixpath.join(root, clean))
    if joined == "/" or (root != "/" and not joined.startswith(root.rstrip("/") + "/")):
        return ""
    return joined


def _ref(values: dict) -> str:
    """A branch, tag or commit as one URL-safe path piece."""
    return quote(one_line(config_get(values, "ref", "main")).strip("/"), safe="-./_")


def _ignored(relative: str) -> bool:
    """Junk that has no business on a web server, whatever the settings say."""
    for part in str(relative).split("/"):
        if part.startswith(".") or part.startswith("_"):
            return True
    return str(relative).endswith((".pyc", ".pyo", ".tmp", ".bak", ".swp", "~"))


# ---------------------------------------------------------------------------
# The job: one unit of work per tick, so the tab keeps painting
# ---------------------------------------------------------------------------

#: How much of the bar each stage gets, in units of no particular size. The two
#: that work file by file take most of it because that is where the time goes, and
#: every other stage gets a piece of its own so the bar moves while the device is
#: backing up, tidying, restarting and answering - the part of a job that used to
#: leave it stuck at 95%.
STAGE_WEIGHTS = {"tree": 4, "fetch": 40, "remote": 8, "ready": 4, "confirm": 2,
                 "backup": 12, "write": 40, "prune": 8, "after": 10, "verify": 8}

#: What the bar says while a stage runs. Short on purpose: it is painted inside a
#: thin widget, and the status line under the tab has the long version.
STAGE_WORDS = {"tree": "finding the files", "fetch": "downloading", "remote": "asking the device",
               "ready": "nothing to write", "confirm": "waiting for you",
               "backup": "backing up", "write": "writing files", "prune": "tidying away",
               "after": "restarting the web server", "verify": "checking the page"}


def route(values: dict, action: str) -> list[str]:
    """The stages this job walks. A local folder needs no download, and only an
    install writes, tidies up, sets the entry going and checks the page."""
    stages = ["tree"]
    if not is_local(values):
        stages.append("fetch")
    stages.append("remote")
    stages += (["confirm", "backup", "write", "prune", "after", "verify"]
               if action == "install" else ["ready"])
    return stages


def _bar_spans(stages: list[str]) -> tuple[dict, int]:
    """Slice the bar across this route: where each stage starts, how far it can
    go, and the total the bar is scaled to. The last stage's end is the maximum,
    so a job that finishes always lands on 100% and one that stops short does not
    pretend it finished."""
    spans: dict[str, tuple[int, int]] = {}
    total = 0
    for stage in stages:
        spans[stage] = (total, STAGE_WEIGHTS.get(stage, 4))
        total += STAGE_WEIGHTS.get(stage, 4)
    return spans, total


def _bar_stage(stage: str) -> None:
    """The bar as one stage begins: the ground it has already covered is filled
    in, and its word goes on the bar."""
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
    api.tabs.set_widget(TAB, "bar", "maximum", units)
    api.tabs.set_widget(TAB, "bar", "value", 0)
    if "fetch" in stages:
        _reset_staging()
    say(f"{label}...")
    api.log(f"{label}: {describe(values)}")
    _enter(stages[0])


def _enter(stage: str) -> None:
    """Begin a stage: its own count, its own progress, its own words."""
    if job is None:
        return
    job["stage"] = stage
    job["index"] = 0
    _bar_stage(stage)
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
    """Come back for the next unit of this stage on the next tick.

    Everything slow here is done a piece at a time for one reason: the app keeps
    painting and answering while a job runs. The same install done in one call
    would finish looking the same and be frozen the whole way through.
    """
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
    _close_session()
    _drop_http()
    working = False
    job = None
    _buttons(True)
    bar_text("stopped")
    say(text, bad=True)
    note(text)


def _done() -> None:
    """The end of a route, in one line that says what happened.

    The words are worked out while the job is still there: a summary is a small
    function, and one that looks something up in the job would find nothing at
    all once the job has been put away.
    """
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
    units = int((job or {}).get("units") or 100)
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
    """Log one thing that went wrong and keep the job moving.

    A file that will not upload should not take the twenty-one around it down
    with it, but it has to be said out loud rather than counted quietly at the
    end.
    """
    if job is not None:
        job.setdefault("errors", []).append(str(text))
    note(f"problem: {text}")
    api.error(text)


def _device(command: str, then, timeout: int = 90, step: str = "") -> None:
    """One SSH command for the job, its answer carried back into the route.

    The command runs in the app's pool and the callback arrives on the GUI
    thread, so nothing here waits on the device and the tab paints while it
    thinks.
    """
    if job is None:
        return
    if step:
        say(f"{job['label']} - {step}...")

    def answered(result: dict) -> None:
        if job is None:                   # stopped while the device was thinking
            return
        code = int(result.get("code") or 0)
        out = str(result.get("out") or "").strip()
        err = str(result.get("err") or "").strip()
        for line in out.splitlines()[:40]:
            note(line)
        if code != 0:
            _problem(f"{step or 'a command'} answered {code}"
                     + (f": {err.splitlines()[0]}" if err else ""))
        then(result)

    api.device.run_async(command, answered, timeout=timeout)


# ---------------------------------------------------------------------------
# Stage: the list of files
# ---------------------------------------------------------------------------

def _stage_tree() -> None:
    """Work out what there is to install, from GitHub or from a folder here."""
    values = job["values"]
    if is_local(values):
        _local_tree(values, job["paths"])
        return
    _github_tree(values, job["paths"])


def _github_tree(values: dict, paths: dict) -> None:
    """Ask GitHub for the tree of that folder; fall back to the built-in list.

    One call gives every path and its size, which is what the plan table wants
    before anything is fetched. The anonymous limit is sixty an hour, so a
    refusal is expected now and then and is not fatal: the files themselves come
    off a CDN that is not counted the same way.
    """
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


# ---------------------------------------------------------------------------
# Stage: the files themselves
# ---------------------------------------------------------------------------

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
        # The first file not being there means the address is wrong rather than
        # one file having gone away, and twenty-two more asks would only say it
        # louder. GitHub's own advice is the longer part of the message.
        stop(f"{one_line(config_get(values, 'owner', ''))}/"
             f"{one_line(config_get(values, 'repo', ''))} has no "
             f"{full} at {one_line(config_get(values, 'ref', 'main'))}. Check the "
             "branch, tag or commit, the folder inside the repository, and the "
             "token if the repository is private.")
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
    """The addon's own folder for what it downloads.

    Under ``api.paths.data``, so a built program writes beside itself rather than
    inside its own read-only bundle, and none of the user's files are involved.
    """
    folder = Path(api.paths.data) / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _reset_staging() -> None:
    """Empty the staging folder before a download, so no old file rides along."""
    import shutil

    for child in staging().iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Stage: what the device already has
# ---------------------------------------------------------------------------

def _stage_remote() -> None:
    """One command listing every file under the remote root, with its size.

    Metadata only: the plan is worked out from sizes, so nothing is downloaded to
    find out what is already there.
    """
    script = "\n".join([
        f'ROOT={quoted(_remote_root())}',
        'if [ ! -d "$ROOT" ]; then echo "NOTHING: that folder is not there yet"; '
        'exit 0; fi',
        """find "$ROOT" -type f -exec stat -c '%s|%n' {} + 2>/dev/null""",
    ])
    _device(script, on_remote, timeout=60, step="asking the device what it has")


def on_remote(result: dict) -> None:
    """Fill the plan table in: what is there, and what this job means to do."""
    if job is None:
        return
    root = _remote_root().rstrip("/") or "/"
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
    show_plan(job["items"])
    if not remote:
        note("the remote folder is empty or missing - this is a fresh install")
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


# ---------------------------------------------------------------------------
# Stage: the ask, the backup, and the writing
# ---------------------------------------------------------------------------

def _stage_confirm() -> None:
    """Ask once, in plain words, before anything on the device is touched."""
    items = job["items"]
    values, paths = job["values"], job["paths"]
    todo = todo_count()
    extra = _extras(items, paths)
    lines = [f"Write {todo} of {len(items)} file(s) into\n\n  {_remote_root()}\n"]
    if extra and bool(config_get(values, "remove_extra", False)):
        lines.append(f"{len(extra)} file(s) that are not in the download will be "
                     "deleted, except those the config keeps.\n")
    elif extra:
        lines.append(f"{len(extra)} file(s) on the device are not in the download; "
                     "they stay where they are.\n")
    lines.append("The folder is backed up on the device first.\n"
                 if bool(config_get(values, "backup_first", True))
                 else "No backup will be taken.\n")
    if not api.ui.question("Install the web interface",
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
    root = _remote_root()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{Path(root).name or 'remote'}-{stamp}.tgz"
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
    paths = job["paths"]
    queue = job.get("queue") or []
    index = int(job.get("index", 0))
    if index >= len(queue):
        _close_session()
        _next()
        return
    item = queue[index]
    target = posixpath.join(paths["subdir"], item["path"]) if paths["subdir"] else item["path"]
    _progress(index, len(queue),
              f"{job['label']} - {index + 1} of {len(queue)}: {item['path']}")
    try:
        _put(item.get("local") or "", target)
    except (AddonError, OSError) as error:
        _problem(f"{item['path']}: {error}")
    else:
        item["action"] = "written"
        job["written"] = int(job.get("written") or 0) + 1
        note(f"wrote {target} ({human(item.get('size') or 0)})")
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
        target = posixpath.join(paths["subdir"], relative) if paths["subdir"] else relative
        full = device_path(target)
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
    lines.append(f'ROOT={quoted(_remote_root())}')
    lines.append('find "$ROOT" -type d -empty -delete 2>/dev/null || true')
    _device("\n".join(lines), lambda _result: _next(), timeout=90,
            step="tidying away what is left over")


def _stage_after() -> None:
    """The exec bit on the CGI entry, then whatever restarts the web server."""
    values, paths = job["values"], job["paths"]
    lines: list[str] = []
    entry = device_path(paths["entry"])
    if bool(config_get(values, "make_executable", True)) and entry:
        lines += [
            'echo "== the CGI entry =="',
            f'if [ -f {quoted(entry)} ]; then chmod 755 {quoted(entry)} && '
            f'echo "{paths["entry"]} is executable"; '
            f'else echo "note: there is no {paths["entry"]} there to set"; fi',
        ]
    commands = [one_line(text) for text in
                (config_get(values, "restart_command", []) or [])]
    commands = [text for text in commands if text]
    if commands:
        lines.append('echo "== after =="')
        for one in commands:
            lines.append(f'{one} || {{ echo "note: {one} did not succeed"; }}')
    else:
        lines.append("echo '(no restart command configured)'")
    lines.append('echo "DONE"')
    _device("\n".join(lines), lambda _result: _next(), timeout=180,
            step="setting it up and restarting the web server")


def _page_probe(urls: list[str]) -> list[str]:
    """Shell that asks for the page at each address in turn and stops at the first
    answer. The dashboard's certificate is self-signed by design, so the check
    turns verification off: it is asking whether something answers, not whether
    this machine would trust the chain.
    """
    return [
        "for URL in %s; do" % " ".join(quoted(text) for text in urls),
        '  BODY=$(command -v curl >/dev/null 2>&1 && curl -s -k -m 10 "$URL" 2>/dev/null)',
        '  if [ -z "$BODY" ]; then'
        ' BODY=$(wget -q --no-check-certificate -O - "$URL" 2>/dev/null); fi',
        '  if [ -n "$BODY" ]; then echo "ANSWERED $URL";'
        ' echo "$BODY" | head -n 5; exit 0; fi',
        "done",
        f'echo "NO ANSWER from {urls[0]}"',
    ]


def answered_at(text: str) -> str:
    """Which address the device's own check got a page from, if any said so."""
    for line in str(text or "").splitlines():
        if line.startswith("ANSWERED "):
            return line[len("ANSWERED "):].strip()
    return ""


def _stage_verify() -> None:
    """Ask the device for its own page: the one proof that it is really working."""
    values, paths = job["values"], job["paths"]
    if not bool(config_get(values, "verify", True)):
        _next()
        return
    entry = device_path(paths["entry"])
    script = "\n".join([
        f'ENTRY={quoted(entry)}',
        'if [ -n "$ENTRY" ] && [ ! -f "$ENTRY" ]; then'
        '  echo "MISSING: there is no CGI entry file on the device"; fi',
        'echo "== the page =="',
    ] + _page_probe(page_urls(paths)))
    _device(script, on_verified, timeout=60, step="asking the device for its page")


def on_verified(result: dict) -> None:
    """Read the answer back: a login page means it worked, anything else does not."""
    if job is None:
        return
    body = str(result.get("out") or "").lower()
    written = int(job.get("written") or 0)
    port = job["paths"]["port"]
    if "no answer" in body:
        _problem("the dashboard did not answer. Check that uhttpd listens on that "
                 "port and that the folder it serves is the one this installed into")
    elif "missing" in body:
        _problem("there is no CGI entry file where the settings say there should be")
    elif any(marker in body for marker in ("<html", "doctype", "password", "login")):
        note("the dashboard answers")
        job["summary"] = lambda: (f"{written} file(s) written and the dashboard "
                                  f"answers on port {port}")
    else:
        note("the port answered, but not with what looks like the dashboard")
        job["summary"] = lambda: f"{written} file(s) written; the page did not look right"
    _next()


# ---------------------------------------------------------------------------
# One SFTP session, when the app will hand one out
# ---------------------------------------------------------------------------

def _remote_root() -> str:
    """The profile's deploy folder: every path in this job sits below it."""
    session = (job or {}).get("session")
    root = str(getattr(session, "remote_root", "") or "")
    if root:
        return root
    try:
        status = api.session.status() or {}
    except AddonError:
        return "/www_rasconf"
    return str(status.get("remote_root") or "/www_rasconf") or "/www_rasconf"


def _open_session() -> bool:
    """Hold one SSH session for the whole transfer, if this addon may.

    A Python addon inside the app can keep a session open, which is the
    difference between twenty files going up in one connection and twenty
    handshakes. A compiled addon is told in as many words that it cannot, and the
    same code then uses the file methods, each of which opens and closes its own.
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
        api.log(f"each file goes up on a connection of its own: {error}")
        return False
    note(f"one session held for the transfer to {_remote_root()}")
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


def _put(local: str, target: str) -> None:
    """Send one staged or local file to the device, below the remote root."""
    if not local or not Path(str(local)).is_file():
        raise AddonError(f"there is no local file to send for {target}")
    session = (job or {}).get("session")
    if session is not None:
        full = device_full(target)
        _remote_dirs(session.sftp, posixpath.dirname(full))
        session.sftp.put(str(local), full)
        return
    api.device.upload(str(local), target)


def device_full(target: str) -> str:
    """Where a relative path lands, for the session that is being held open."""
    root = _remote_root().rstrip("/") or "/"
    return posixpath.normpath(posixpath.join(root, str(target).lstrip("/")))


def _remote_dirs(sftp, directory: str) -> None:
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


# ---------------------------------------------------------------------------
# Two buttons that are a single command, not a job
# ---------------------------------------------------------------------------

def _quick(command: str, told, step: str, timeout: int = 90) -> None:
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
            say(f"{step} did not finish: the device answered "
                f"{result.get('code')}", bad=True)
            return
        bar_text("done")
        told(result)

    api.device.run_async(command, answered, timeout=timeout)


def restart_server(_checked: bool = False) -> None:
    """Run the restart command from the config, and nothing else."""
    if api is None or working:
        return
    values = merge_current()
    commands = [one_line(text) for text in
                (config_get(values, "restart_command", []) or []) if one_line(text)]
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
    values = merge_current()
    paths = job_paths(values)
    urls = page_urls(paths)
    script = "\n".join(['echo "== the page =="'] + _page_probe(urls))

    def told(result: dict) -> None:
        out = str(result.get("out") or "")
        body = out.lower()
        if "no answer" in body:
            say(f"nothing answers on {' or '.join(urls)}", bad=True)
        elif any(marker in body for marker in ("<html", "doctype", "password", "login")):
            said = answered_at(out)
            say(f"the dashboard answers on port {paths['port']}"
                + (f" ({said})" if said else ""))
        else:
            say(f"{answered_at(out) or urls[0]} answered, but not with the dashboard",
                bad=True)

    _quick(script, told, "asking the device for its page", timeout=60)


def open_dashboard(_checked: bool = False) -> None:
    """The page in a tab of its own, so an install can be looked at straight away."""
    if api is None:
        return
    values = merge_current()
    paths = job_paths(values)
    url = ""
    try:
        url = str(api.session.web_url({"ip": "", "port": paths["port"],
                                       "path": paths["web_path"],
                                       "scheme": "https"}) or "")
    except AddonError:
        url = ""
    if not url.startswith("https"):
        say("there is no address to open: put a host in the connection panel", bad=True)
        return
    try:
        if api.tabs.exists(PAGE):
            api.tabs.activate(PAGE)
            api.tabs.navigate(PAGE, url)
        else:
            api.tabs.add_web_tab(PAGE, url=url)
        say(f"opened {url}")
    except AddonError as error:
        say(f"the page could not be opened: {error}", bad=True)


# ---------------------------------------------------------------------------
# GitHub connections, made once and dropped when a job ends
# ---------------------------------------------------------------------------

_fetchers: dict[str, Fetcher] = {}


def _http(values: dict) -> Fetcher:
    """The shared fetcher, keeping its HTTPS connections open between calls.

    Keyed by token, so a job that changed it does not inherit a connection that
    would still present the old credentials.
    """
    key = str(config_get(values, "token", "") or "")
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = Fetcher(token=key)
        _fetchers[key] = fetcher
    return fetcher


def _drop_http() -> None:
    for fetcher in _fetchers.values():
        fetcher.close()
    _fetchers.clear()


# ---------------------------------------------------------------------------
# The buttons
# ---------------------------------------------------------------------------

def _buttons(usable: bool) -> None:
    """Grey the buttons out while a job is running, through the app.

    The Stop button is the other way round: it is only of any use while the rest
    of the tab is asleep.
    """
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
    """Ask the app for a session if there is none, and say that it was asked.

    Every device call opens one of its own when it has to, so this is only about
    the profile's password: without a saved one the app cannot connect at all,
    and it is better to have said so at the start than to fail twenty times.
    """
    try:
        if api.device.is_connected("sftp"):
            return
        api.device.ensure_connected("sftp")
        note("SFTP was not up, so the program is opening a session")
    except AddonError as error:
        note(f"SFTP is not connected: {error}")


def push_config(_checked: bool = False) -> None:
    if api is None:
        return
    api.config.update(values_of())
    say("saved to addons/_data/web_installer/config.json")
    api.log("config saved from the tab")


def pull_config(_checked: bool = False) -> None:
    if api is None:
        return
    values = api.config.all()
    show_form(values)
    say("form reloaded from the config")


def stop_job(_checked: bool = False) -> None:
    """Give up on the job in hand. Nothing half written is left running."""
    if working and job is not None:
        stop("stopped by hand - the files already written stay where they are")
        return
    say("there is nothing to stop")


#: What each button on the tab does, by its spec id.
HANDLERS.update({
    "fetch": lambda: _begin("fetch"),
    "install": lambda: _begin("install"),
    "open": open_dashboard,
    "restart": restart_server,
    "check": check_page,
    "push": push_config,
    "pull": pull_config,
    "stop": stop_job,
})

#: The stages a route is made of, and the per-file work each one repeats.
STAGES = {
    "tree": _stage_tree,
    "fetch": _stage_fetch,
    "remote": _stage_remote,
    "ready": _stage_ready,
    "confirm": _stage_confirm,
    "backup": _stage_backup,
    "write": _stage_write,
    "prune": _stage_prune,
    "after": _stage_after,
    "verify": _stage_verify,
}

UNITS = {"fetch": _unit_fetch, "write": _unit_write}


# ---------------------------------------------------------------------------
# The hooks the app calls
# ---------------------------------------------------------------------------

def focus_tab(_checked: bool = False) -> None:
    """The Tools menu entry: go to the tab."""
    if api is None:
        return
    api.tabs.activate(TAB)


def init(plugin_api) -> None:
    """Build the tab from the saved config."""
    global api
    api = plugin_api
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Rasconf web installer - addons/_data/web_installer/config.json")
    build_tab()
    show_form(values)
    api.ui.menu_item("Rasconf web installer", focus_tab, menu="Tools")


def start() -> None:
    """Say where this profile's files would go, as soon as the tab exists."""
    if api is not None:
        show_target(api.config.all())


def shutdown() -> None:
    global api, working, job
    _close_session()
    _drop_http()
    working = False
    job = None
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A control on the tab reported something: run what its button means.

    Every button in the spec says ``"on": ["clicked"]``, so the app calls this
    with the id it was pressed. It is the only way a click reaches an addon that
    has been compiled into a program of its own, because the widget itself lives
    in the app's process and cannot be handed over.
    """
    if str(tab) != TAB or str(event) != "clicked":
        return
    handler = HANDLERS.get(str(identifier))
    if handler is not None:
        handler()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: make the form match the file."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    values = api.config.all()
    show_form(values)
    say("config changed in Settings; the form now matches it")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
