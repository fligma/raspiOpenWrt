#!/usr/bin/env python3
"""Berry addon store: pull addon sources from GitHub and build them here.

Adds an ``Addon store`` tab. The catalog of what is available is read from
``index.json`` in the ``addon.store`` folder of this project's repository, so the
list of installable addons lives on GitHub and is never baked into the program.
Each catalog entry names a plain ``.py`` addon sitting beside that index, and an
optional list of asset files under ``addon.store/assets/<id>/``. ``index.json``
is generated from the store folder on build, so it carries every addon's name,
version, author and assets, with new and updated entries marked.

Installing one mirrors what the web installer does with its files: the addon's
``.py`` and any assets it ships are fetched over HTTPS into the addon's own
staging folder under ``addons/_data/addon_store``, then compiled into a program
of its own by PyInstaller - the assets packed into the executable's ``assets``
folder so ``api.paths.assets`` finds them - and dropped into ``addons/`` exactly
where the runtime looks, the same result the Addon Dev tab's Compile button
produces. The build is staged in a folder of the store's own beside the
downloads, under ``addons/_data/addon_store/build``, and both the downloads and
the build are thrown away as soon as the program is in ``addons/``: only the
installed file and the addon's own settings are left behind.

A freshly installed addon is written into the active profile's settings as
switched off, so nothing of it runs until it is turned on in Settings > Addons,
where its row appears as soon as the app has rescanned the folder.

Before the compile, when the store ships a ``test_<id>.py`` for the addon, it is
fetched too and run against the freshly downloaded ``.py``: a test that does not
pass stops the install there, so a broken addon is never built. An addon with no
test published is compiled as it is.

Deleting one takes that compiled program (or folder) back out of ``addons/`` and
rescans, leaving the addon's own ``_data`` settings where they are.

Compiling needs a Python on this machine and PyInstaller within it. The
interpreter is looked for as ``python3``, then ``python``, then the Windows ``py``
launcher, and a name that turns out to be the Microsoft Store's stand-in rather
than a Python is passed over. When PyInstaller is missing the store installs it
itself with ``pip`` - for this user alone when the plain install is refused -
and the user is not asked to. The repository, branch, folder and token are all
editable in ``addons/_data/addon_store/config.json``.

The tab is built from a declarative spec and its buttons report back through
``on_widget``, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Qt-free contract half: runs standalone and in tests.
from addon_common import AddonError, config_get, merge_config

ADDON = {
    "name": "Berry addon store",
    "version": "1.0",
    "author": "fligma",
    "description": "Lists the addons published in the repository's addon.store "
                   "folder, downloads a chosen one into a staging folder and "
                   "compiles it for this machine with PyInstaller, then installs "
                   "or removes it from the addons folder. Installs PyInstaller "
                   "itself when it is not already present.",
    "requires": [],
    "api": 1,
}

TAB = "Addon store"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the job in progress, or nothing
entries: list[dict] = []    # the catalog read last time

#: A module name PyInstaller can import: anything else becomes an underscore.
_SAFE = re.compile(r"[^A-Za-z0-9_]")

# --- what can be configured, and what it starts out as ---

DEFAULTS = {
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "store_path": "utils/update util/addon.store",
    "token": "",
}

FIELDS = [
    {"key": "owner", "label": "GitHub owner", "kind": "text",
     "hint": "The account or organisation the repository belongs to"},
    {"key": "repo", "label": "Repository", "kind": "text"},
    {"key": "ref", "label": "Branch, tag or commit", "kind": "text",
     "hint": "main, a tag like v1.4, or a full commit sha"},
    {"key": "store_path", "label": "Store folder inside the repository",
     "kind": "text",
     "hint": "The folder holding index.json and the addon .py files"},
    {"key": "token", "label": "GitHub token", "kind": "text",
     "hint": "Only needed for a private repository, or when anonymous requests "
             "are refused",
     "placeholder": "blank for a public repository"},
]

GROUPS = [("Where addons come from", ["owner", "repo", "ref", "store_path", "token"])]

BUTTONS = (("refresh", "Refresh"), ("install", "Install"), ("delete", "Delete"))

# --- small text helpers ---

def one_line(value: Any) -> str:
    """One clean line of text from whatever the form or file holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def clean_id(value: Any) -> str:
    """A catalog id turned into a name Python and PyInstaller can both use."""
    return _SAFE.sub("_", one_line(value))


def safe_segment(value: Any) -> str:
    """A repository name or path piece with the shell-junk and traversal gone."""
    text = one_line(value).replace("..", "")
    return re.sub(r"[^A-Za-z0-9._/ -]", "", text).strip(" /")


def clean_token(value: Any) -> str:
    """A token stripped of the spaces a copy-paste tends to add."""
    return one_line(value).replace(" ", "")


# --- the tab ---

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QTreeWidget, QPlainTextEdit { font-family: Consolas, monospace; }
QPushButton { padding: 7px 14px; }
"""

BANNER_STYLE = ("QLabel { color: #e8e8e8; font-size: 13pt; font-weight: 600;"
                " padding: 4px 2px 2px 2px; }")

_STATUS_STYLE = ("QLabel { color: #9a9a9a; padding: 2px 6px;"
                 " border: 1px solid #3c3c3c; border-radius: 9px; }")


def tab_spec() -> dict:
    """The whole tab: one header line, the catalog and its log, its buttons.

    The status line sits in the header between the title and the repository chip,
    so nothing of its own steals height from the table. It takes the width the
    two leave over and wraps inside that, so a long report stays readable and the
    chip stays put. The table and the log share a horizontal splitter, so the
    line between them drags to give either more room; the table starts with the
    larger share.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry addon store", "style": BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": "color: #23d18b; padding: 2px 4px;"},
            {"kind": "label", "id": "source", "text": "", "style": _STATUS_STYLE},
        ]},
        {"kind": "splitter", "sizes": [3, 2], "children": [
            {"kind": "table", "id": "list", "label": "Available",
             "columns": ["ID", "Version", "Name", "Author", "Installed"],
             "stretch": [2, 3], "col_widths": [120, 60, 0, 0, 100],
             "tooltip": "What the store folder lists; pick one and use the buttons "
                        "below"},
            {"kind": "multi", "id": "log", "readonly": True,
             "placeholder": "Every step of the last install, as it happened",
             "tooltip": "The same lines go into the program's own log"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": identifier, "text": text,
             "on": ["clicked"]} for identifier, text in BUTTONS],
        },
    ]}


def build_tab() -> None:
    """The tab as a spec; nothing here holds a widget."""
    api.tabs.add_tab(TAB, spec=tab_spec(), style=TAB_STYLE)
    try:
        api.tabs.use_app_icon(TAB)
    except AddonError as error:
        api.log(f"addon store tab icon: {error}")


def say(text: str, bad: bool = False) -> None:
    """One line on the tab's status, and nothing once this copy has been reloaded."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style",
                            "color: #ffb020; padding: 2px 4px;" if bad
                            else "color: #23d18b; padding: 2px 4px;")
    except AddonError:
        pass


def note(text: str) -> None:
    """One line into the tab's own log and the program's log, the same words."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "log", "append", str(text))
    except AddonError:
        pass
    api.log(text)


def show_source(values: dict) -> None:
    """The header chip: which owner/repo/ref/folder the catalog is read from."""
    text = (f"{config_get(values, 'owner', '')}/{config_get(values, 'repo', '')}"
            f" @ {config_get(values, 'ref', 'main')}")
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "source", "text", text)
    except AddonError:
        pass


def _buttons(usable: bool) -> None:
    if api is None:
        return
    for identifier, _text in BUTTONS:
        try:
            api.tabs.set_widget(TAB, identifier, "enabled", bool(usable))
        except AddonError:
            pass


# --- network: standard library only ---

class Fetcher:
    """A few HTTPS connections, kept open and reused for one job."""

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

    def close(self, host: str = "") -> None:
        for name in ([host] if host else list(self.connections)):
            connection = self.connections.pop(name, None)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass


_fetchers: dict[str, Fetcher] = {}


def _http(values: dict) -> Fetcher:
    key = clean_token(config_get(values, "token", ""))
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = Fetcher(token=key)
        _fetchers[key] = fetcher
    return fetcher


def _drop_http() -> None:
    for fetcher in _fetchers.values():
        fetcher.close()
    _fetchers.clear()


def raw_address(values: dict, filename: str) -> str:
    """The raw.githubusercontent path for one file in the store folder."""
    owner = quote(safe_segment(config_get(values, "owner", DEFAULTS["owner"])))
    repo = quote(safe_segment(config_get(values, "repo", DEFAULTS["repo"])))
    ref = quote(one_line(config_get(values, "ref", "main")).strip("/"), safe="-./_")
    folder = safe_segment(config_get(values, "store_path",
                                     DEFAULTS["store_path"])).strip("/")
    whole = "/".join(part for part in (folder, safe_segment(filename)) if part)
    return f"/{owner}/{repo}/{ref}/{quote(whole)}"


def _refused(status: int, path: str) -> str:
    if status == 404:
        return (f"GitHub has no such file at {path}: check the owner, repository, "
                "branch and store folder in the settings")
    if status == 403:
        return (f"GitHub refused the request for {path} (403). Anonymous requests "
                "are limited, so add a token or try again later")
    return f"GitHub answered {status} for {path}"


# --- reading the catalog ---

def parse_catalog(body: Any) -> list[dict]:
    """index.json as a tidy list of entries; a bare list is accepted too."""
    if isinstance(body, dict):
        rows = body.get("addons") or body.get("entries") or []
    elif isinstance(body, list):
        rows = body
    else:
        rows = []
    out: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        ident = clean_id(raw.get("id") or raw.get("name") or "")
        if not ident:
            continue
        filename = one_line(raw.get("filename") or f"{ident}.py")
        listed = raw.get("assets") or []
        assets = [one_line(name) for name in listed if one_line(name)] \
            if isinstance(listed, list) else []
        out.append({
            "id": ident,
            "name": one_line(raw.get("name") or ident),
            "version": one_line(raw.get("version") or ""),
            "author": one_line(raw.get("author") or ""),
            "description": one_line(raw.get("description") or ""),
            "filename": filename,
            "assets": assets,
            "icon": one_line(raw.get("icon") or ""),
            "test": one_line(raw.get("test") or ""),
            "status": one_line(raw.get("status") or ""),
        })
    return out


def load_catalog(values: dict) -> list[dict]:
    """Fetch and read ``index.json`` from the store folder."""
    path = raw_address(values, "index.json")
    try:
        status, body = _http(values).get(
            "raw.githubusercontent.com", path, accept="application/json")
    except (http.client.HTTPException, OSError) as error:
        raise AddonError(f"the catalog could not be reached: {error}")
    if status != 200:
        raise AddonError(_refused(status, path))
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise AddonError(f"index.json was not the JSON expected: {error}")
    return parse_catalog(data)


# --- what is installed, and where downloads land ---

def addons_dir() -> Path:
    return Path(api.paths.addons)


def installed(id_value: str) -> bool:
    """Whether an addon id already sits in the addons folder in any shape."""
    folder = addons_dir()
    name = clean_id(id_value)
    if not name:
        return False
    for candidate in (folder / f"{name}.exe", folder / f"{name}.bin", folder / name):
        if candidate.exists():
            return True
    return False


def staging() -> Path:
    """The addon's own folder under ``api.paths.data`` for what it downloads."""
    folder = Path(api.paths.data) / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def work_dir() -> Path:
    """The addon's own folder under ``api.paths.data`` for the build's work.

    PyInstaller writes a spec, a ``build`` folder and a ``dist`` folder for every
    program it makes. All of that belongs to the store, not to the Addon Dev tab
    beside it, so it is staged here, under ``addons/_data/addon_store``.
    """
    folder = Path(api.paths.data) / "build"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _reset_staging() -> None:
    for child in staging().iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def _tidy(id_value: str) -> None:
    """Throw the downloads and the build away once the program is in ``addons``.

    The source, its assets, its test and everything PyInstaller made on the way
    were only a means to the one file that got installed, so they are cleared;
    what the addon itself keeps in its ``_data`` folder is left alone.
    """
    try:
        shutil.rmtree(work_dir() / id_value, ignore_errors=True)
    except OSError as error:
        note(f"the build folder could not be cleared: {error}")
    _reset_staging()


# --- the table ---

def show_entries() -> None:
    """The catalog as rows: last column installed or not, first marks new or updated."""
    if api is None:
        return
    rows = []
    marks = {"new": "  \u25cf new", "updated": "  \u21bb updated"}
    for entry in entries:
        done = installed(entry["id"])
        mark = marks.get(entry.get("status", ""), "")
        row = {"cells": [entry["id"], entry["version"], entry["name"] + mark,
                         entry["author"],
                         "installed" if done else "not installed"]}
        if done:
            row["color"] = "#23d18b"
        elif entry.get("status") in marks:
            row["color"] = "#ffb020"
        else:
            row["color"] = "#9a9a9a"
        rows.append(row)
    try:
        api.tabs.set_widget(TAB, "list", "rows", rows)
    except AddonError:
        pass


def selected_id() -> str:
    """The id in the first column of the chosen row, or nothing."""
    try:
        chosen = api.tabs.widget_value(TAB, "list", "selected")
    except AddonError:
        return ""
    if not chosen:
        return ""
    first = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
    parts = str(first).split("\t")
    return clean_id(parts[0])


def entry_for(id_value: str) -> dict | None:
    for entry in entries:
        if entry["id"] == clean_id(id_value):
            return entry
    return None


def refresh(_checked: bool = False) -> None:
    """Read the catalog from GitHub and repaint the table."""
    global entries
    if api is None or working:
        return
    values = merge_config(DEFAULTS, api.config.stored())
    show_source(values)
    say("reading the store catalog...")
    try:
        entries = load_catalog(values)
    except AddonError as error:
        say(f"{error}", bad=True)
        note(f"catalog: {error}")
        return
    show_entries()
    word = "no addons are published here yet" if not entries \
        else f"{len(entries)} addon(s) in the store"
    say(f"catalog loaded: {word}")


# --- the job: install and delete, without holding the tab still ---

def _dev() -> Any:
    """The Addon Dev machinery, imported only when a real build is asked for."""
    import manager_addondev

    return manager_addondev


def _begin(verb: str) -> dict:
    """Start a job: the buttons grey out and one dict holds its whole state."""
    global working, job
    values = merge_config(DEFAULTS, api.config.stored())
    working = True
    job = {"verb": verb, "values": values, "stream": None, "on_ok": None,
           "fail": ""}
    _buttons(False)
    try:
        api.tabs.set_widget(TAB, "log", "value", "")
    except AddonError:
        pass
    return job


def _finish(text: str, bad: bool = False) -> None:
    """End the job in hand: the buttons come back and one line is left up."""
    global working, job
    job = None
    working = False
    _buttons(True)
    say(text, bad=bad)
    note(text)


def _stream(command: list[str], cwd: Path | None, on_ok: Any,
            env: dict | None = None, fail: str = "",
            on_bad: Any = None) -> None:
    """Run one child process and call ``on_ok`` when it ends clean.

    ``env`` is the environment for this one process (``None`` keeps the app's),
    and ``fail`` the line left up when it stops non-zero, in place of the plain
    exit-code message, so a failed test says what it was. ``on_bad``, when given,
    is called instead of ending the job, so a step can try another way first.
    """
    dev = _dev()
    stream = dev._Stream(command, cwd=cwd, env=env)
    stream.start()
    if job is not None:
        job["stream"] = stream
        job["on_ok"] = on_ok
        job["fail"] = fail
        job["on_bad"] = on_bad
    _poll_stream()


def _poll_stream() -> None:
    """Drain the running job's process; come back on the next tick until it ends."""
    if job is None:
        return
    stream = job.get("stream")
    if stream is None:
        return
    for line in stream.drain(400):
        if line is not None:
            note(line)
    if not stream.finished:
        if api is None:
            return
        api.schedule.after(0.2, _poll_stream)
        return
    code = int(stream.code or 0)
    job["stream"] = None
    if code != 0:
        on_bad = job.pop("on_bad", None)
        if callable(on_bad):
            on_bad()
            return
        _finish(job.get("fail") or f"the job stopped with code {code}", bad=True)
        return
    job.pop("on_bad", None)
    on_ok = job.pop("on_ok", None)
    if callable(on_ok):
        on_ok()


def _clear_ready() -> None:
    """Put away what the dev machinery remembered: the interpreter and PyInstaller."""
    try:
        _dev().forget_interpreter()
    except Exception:
        pass


def _ensure_python() -> str:
    """The interpreter that would build, or empty with the reason already shown."""
    where, why = _dev().build_interpreter()
    if where is None:
        _finish(why, bad=True)
        return ""
    note(f"building with {where}")
    return str(where)


def _fetch_file(values: dict, remote: str, target: Path) -> str:
    """One store file down into ``target``; empty text on success, else why it stopped."""
    path = raw_address(values, remote)
    try:
        status, body = _http(values).get("raw.githubusercontent.com", path)
    except (http.client.HTTPException, OSError) as error:
        return f"the download stopped: {error}"
    if status != 200:
        return _refused(status, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError as error:
        return f"the download could not be written here: {error}"
    note(f"{len(body)} bytes into {target}")
    return ""


def _compile(id_value: str) -> None:
    """Fetch the addon's .py and any assets into staging, then build it here."""
    if job is None:
        return
    entry = entry_for(id_value)
    values = job["values"]
    if entry is None:
        _finish(f"the store no longer lists {id_value}", bad=True)
        return
    _reset_staging()
    source = staging() / f"{id_value}.py"
    say(f"downloading {entry['filename']}...")
    why = _fetch_file(values, entry["filename"], source)
    if why:
        _finish(why, bad=True)
        return
    names = list(entry.get("assets") or [])
    if entry.get("icon") and entry["icon"] not in names:
        names.append(entry["icon"])
    assets = staging() / "assets"
    for index, name in enumerate(names, 1):
        say(f"downloading asset {name} ({index} of {len(names)})...")
        why = _fetch_file(values, f"assets/{id_value}/{name}", assets / name)
        if why:
            _finish(f"an asset could not be fetched ({name}): {why}", bad=True)
            return
    test_path = None
    test_name = entry.get("test") or ""
    if test_name:
        say(f"downloading {test_name} to check the addon first...")
        why = _fetch_file(values, f"_tests/{test_name}", staging() / test_name)
        if why:
            _finish(f"the addon's test could not be fetched: {why}", bad=True)
            return
        test_path = staging() / test_name
    _maybe_test(id_value, source, test_path)


def _maybe_test(id_value: str, source: Path, test_path: Path | None) -> None:
    """Run the addon's own test before spending a build on it; skip it when none.

    The test is handed the downloaded ``.py`` through ``RASCONF_ADDON_SOURCE`` and
    the app's modules through ``PYTHONPATH``, so it runs against the very copy
    that is about to be compiled. A non-zero result ends the job; the compile
    only happens when the test passes, or when there is no test to run.
    """
    if job is None:
        return
    if test_path is None or not test_path.is_file():
        say("no test is published for this addon; compiling as it is")
        _build(id_value, source)
        return
    dev = _dev()
    where, why = dev.build_interpreter()
    if where is None:
        _finish(why, bad=True)
        return
    env = dict(os.environ)
    had = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(dev.BASE_DIR) + (os.pathsep + had if had else "")
    env["RASCONF_ADDON_SOURCE"] = str(source)      # the copy just downloaded
    env["QT_QPA_PLATFORM"] = "offscreen"           # a test may build real widgets
    say(f"running {test_path.name} before compiling...")
    _stream([str(where), str(test_path)], dev.BASE_DIR,
            lambda: _build(id_value, source), env=env,
            fail="the addon's own test did not pass, so it was not compiled")


def _build(id_value: str, source: Path) -> None:
    """Write a launcher and run PyInstaller over the staged source and its assets."""
    if job is None:
        return
    dev = _dev()
    root = work_dir()
    work = root / id_value
    try:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        launcher = work / "launcher.py"
        dev._write_text(launcher, dev._LAUNCHER.substitute(
            id=id_value, module=dev.module_name(source)))
    except OSError as error:
        _finish(f"the build folder could not be made: {error}", bad=True)
        return
    entry = entry_for(id_value) or {}
    assets = staging() / "assets"
    options = {"onefile": True, "console": True, "icon": True, "hidden": ""}
    if assets.is_dir() and any(assets.iterdir()):
        options["add_data"] = [(assets, "assets")]       # packed to the exe's assets folder
        icon_name = entry.get("icon") or ""
        icon = assets / icon_name if icon_name else None
        options["icon"] = str(icon) if (icon is not None and icon.is_file()) else True
    command = dev.build_command(launcher, id_value, options, source, root=root)
    say(f"compiling {id_value} with PyInstaller...")
    note("$ " + " ".join(command))
    _stream(command, work, lambda: _install_step(id_value))


def _stay_off(id_value: str) -> None:
    """Write the new addon into the active profile as switched off.

    The app starts only what the profile enables, so an addon that has just
    arrived runs nothing: it appears in Settings > Addons with its switch down
    and waits to be turned on. The row shows up in the rescan that follows.
    """
    if api is None:
        return
    try:
        api.session.set_addon_enabled(addon_id=id_value, enabled=False)
    except Exception as error:              # a settings hiccup never loses an install
        note(f"{id_value} could not be written to the profile as switched off: "
             f"{error}")


def _install_step(id_value: str) -> None:
    """Move the fresh program into ``addons/``, then leave it switched off.

    The rescan that lets the app see the new file re-loads this addon as well,
    which takes the module this code runs in out of the app: the api is gone the
    moment ``_reload()`` starts. So everything that still needs it - clearing the
    build away, marking the addon off, the table and the status line - is done
    first, and the rescan is the last thing here.
    """
    if job is None:
        return
    dev = _dev()
    staged = dev.staged_name(id_value, True, root=work_dir())
    if not staged.is_file():
        _tidy(id_value)
        _finish("no program was produced; see the log above", bad=True)
        return
    try:
        target = dev.install_build(staged, id_value, True)
    except OSError as error:
        _tidy(id_value)
        _finish(f"it could not be put in the addons folder: {error}", bad=True)
        return
    _tidy(id_value)
    _stay_off(id_value)
    show_entries()
    _finish(f"installed {target.name}, switched off until you turn it on in "
            "Settings > Addons")
    _reload()


def _pip_install(where: str, user: bool = False) -> None:
    """Install PyInstaller into the interpreter that would do the building.

    Plain first and ``--user`` second: a machine-wide Python on Windows refuses
    a normal install without elevation, and a distribution's Python on Linux
    refuses it altogether unless the packages are for this user alone.
    """
    if job is None:
        return
    who = " for this user" if user else ""
    say(f"getting PyInstaller ready (pip install{who})...")
    command = [where, "-m", "pip", "install", "--disable-pip-version-check"]
    if user:
        command.append("--user")
    command.append("pyinstaller")
    _stream(command, None, lambda: _after_pip(where),
            on_bad=lambda: _pip_again(where, user),
            fail="pip could not install PyInstaller; the lines above say why")


def _pip_again(where: str, user: bool) -> None:
    """The pip that failed: once more for this user, and then it is reported."""
    if user or job is None:
        _finish("PyInstaller could not be installed with pip, for this user "
                "either; the lines above say why. A distribution's Python on "
                "Linux may need a virtualenv instead", bad=True)
        return
    note("that install did not go through; trying again for this user alone")
    _pip_install(where, user=True)


def _after_pip(where: str) -> None:
    _clear_ready()
    ready, hint = _dev().pyinstaller_ready()
    if not ready:
        _finish(f"PyInstaller still cannot be found: {hint}", bad=True)
        return
    id_value = job.get("id") if job is not None else ""
    _compile(str(id_value))


def install_selected(_checked: bool = False) -> None:
    """Download and compile the chosen addon, installing PyInstaller first if needed."""
    global working
    if api is None or working:
        return
    id_value = selected_id()
    if not id_value:
        say("pick an addon in the list first", bad=True)
        return
    if entry_for(id_value) is None:
        say("the list is empty - press Refresh first", bad=True)
        return
    if installed(id_value):
        say(f"{id_value} is already installed; use Delete to remove it", bad=True)
        return
    job_local = _begin("install")
    job_local["id"] = id_value
    where = _ensure_python()
    if not where:
        return
    ready, _hint = _dev().pyinstaller_ready()
    if not ready:
        _pip_install(where)
        return
    _compile(id_value)


def _delete_paths(id_value: str) -> list[Path]:
    folder = addons_dir()
    return [candidate for candidate in
            (folder / f"{id_value}.exe", folder / f"{id_value}.bin", folder / id_value)
            if candidate.exists()]


def delete_selected(_checked: bool = False) -> None:
    """Take the compiled addon back out of ``addons/`` and rescan."""
    if api is None or working:
        return
    id_value = selected_id()
    if not id_value:
        say("pick an installed addon in the list first", bad=True)
        return
    removed = _delete_paths(id_value)
    if not removed:
        say(f"{id_value} is not installed", bad=True)
        return
    try:
        for path in removed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
    except OSError as error:
        say(f"{id_value} could not be removed: {error}", bad=True)
        return
    _tidy(id_value)
    show_entries()
    say(f"deleted {id_value} from the addons folder")
    note(f"deleted {id_value}")
    _reload()


def _reload() -> None:
    """Ask the running app to look at ``addons/`` again and pick up what changed.

    The rescan shuts this addon down on its way to starting it again from the
    file on disk - and in a compiled store it ends this process too - so it is
    the last thing a job does and nothing after it reaches for the api.
    """
    if api is None:
        return
    try:
        api.session.rescan_addons()
    except Exception as error:                 # never let a rescan break the tab
        try:
            api.log(f"addon rescan after a store job: {error}")
        except Exception:
            pass


# --- the hooks the app calls ---

def focus_tab(_checked: bool = False) -> None:
    """The Tools menu entry: go to the tab."""
    if api is None:
        return
    api.tabs.activate(TAB)


HANDLERS: dict = {
    "refresh": refresh,
    "install": install_selected,
    "delete": delete_selected,
}


def init(plugin_api: Any) -> None:
    """Build the tab from the saved config and read the catalog once."""
    global api
    api = plugin_api
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Berry addon store - addons/_data/addon_store/config.json")
    build_tab()
    show_source(values)
    api.ui.menu_item("Berry addon store", focus_tab, menu="Tools")
    api.schedule.after(0.2, refresh)


def start() -> None:
    """Nothing to do beyond what init queued."""
    return None


def shutdown() -> None:
    global api, working, job
    _drop_http()
    working = False
    job = None
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A button on the tab was clicked: run what its id means."""
    if str(tab) != TAB or str(event) != "clicked":
        return
    handler = HANDLERS.get(str(identifier))
    if handler is not None:
        handler()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: point the header and reload."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    values = api.config.all()
    show_source(values)
    if not working:
        refresh()


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
