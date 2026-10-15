#!/usr/bin/env python3
"""The Addon Dev tab: what an addon is, what it does, and how to make one.

The tab is switched from Settings like any other. In a source checkout an addon
is a ``.py`` file there to be read, edited, tested and compiled. In a packaged
program the same panels still list every addon, show its log lines and its
processes, and open the sources that ship inside it - but a file that came with
the build cannot be written back. Building and testing an addon run in a Python
of their own and import the app's protocol modules from disk, which is why a
build carries a plain copy of them; a program built without that copy says so
rather than start something that cannot finish.

Six things live here:

Details
    The manifest - name, version, author, description, api - edited in a form
    and written back where it belongs: into the ``ADDON = {`` literal of the
    addon's own file when it has one, otherwise into the ``<id>.json`` manifest
    beside it. Below that, a plain-text editor for ``addons/_data/<id>/config.json``.

Run
    The switches the Settings tab also has (enable, restart, show its tab,
    rescan) plus the choice that only matters once an addon has been compiled:
    run the source or run the program built from it.

Compile
    PyInstaller, started for one addon with a generated launcher, staging in
    ``addons/_build/<id>`` and putting the result at ``addons/<id>.exe`` (or an
    extension-less executable on Linux) so the runtime finds it as the same addon.

Test
    ``addons/_tests/test_<id>.py``, written from a template and run as its own
    process. It uses :mod:`addon_client`'s recording stub, so an addon can be
    checked without a device and without opening a window.

Docs
    The API reference and the guides. Every method, property and one-line
    summary in it is read off the protocol module as it runs, so the panel
    cannot describe an API the app no longer has.

Log / Processes
    The addon's own lines out of the app log, and what is running: the child
    process of every process addon (pid, state, exit code, command, uptime) and
    everything the app created for the in-process ones - tabs, timers, menu
    entries, event listeners and threads.

Nothing here can take the window down: the file writes are guarded, PyInstaller
and the test script run in their own processes, and every refresh reads the
runtime through ``AddonManager.describe``.
"""

from __future__ import annotations

import ast
import dis
import inspect
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
from string import Template
import struct
import tempfile
import threading
import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from addon_common import (
    AddonError,
    addon_config_path,
    build_mark,
    config_name,
    jsonable,
    kill_process_tree,
    popen_tree,
    read_config_json,
    release_process_tree,
    terminate_process_tree,
    write_build_mark,
)
from manager_const import (
    ADDON_BUILD_DIR,
    ADDON_EXECUTABLE_SUFFIX,
    ADDON_TEST_DIR,
    ADDONS_DIR,
    BASE_DIR,
    HARNESS_DIR,
    HARNESS_FILES,
    ICON_FILES,
    IS_COMPILED,
    LOG_FILE,
    PROJECT_GITHUB_URL,
    find_asset,
)
import manager_theme
from manager_widgets import tip_on_title

#: How wide the branches of the Docs tree are drawn, and how far their lines are
#: pushed in: the two numbers that make the list read as a folder tree.
DOCS_INDENT = 16
DOCS_MIN_W = 236

#: How often the tables ask the runtime what it knows.
REFRESH_MS = 2000

#: One line per row, and the rows the index shows before it scrolls itself.
ROW_H = 30
MAX_ROWS = 12

#: Floors for the panes; the tab scrolls rather than clipping text.
PANES_MIN_H = 380
VIEW_MIN_H = 150

#: State words per row; Qt's darkGreen and gray vanish on a dark background.
STATE_COLORS = {
    "running": "#23d18b",
    "loaded": "#23d18b",
    "closed": "#8f8f8f",
    "off": "#8f8f8f",
    "failed": "#ff6b6b",
}

#: The keys the Details form owns; the rest of a manifest file stays put.
DETAIL_KEYS = ("name", "version", "author", "description", "notes")

#: What the panels say about an addon that came with the program itself.
SHIPPED_NOTE = ("Shipped inside the program. Its source is shown as it is, and "
                "it can only be switched on and off in Settings.")

_MONO = ("Consolas", 10)


def monospace(view: QPlainTextEdit) -> QPlainTextEdit:
    font = QFont(_MONO[0], _MONO[1])
    font.setStyleHint(QFont.StyleHint.Monospace)
    view.setFont(font)
    # An output view keeps its lines; the tab scrolls instead of crushing it.
    view.setMinimumHeight(VIEW_MIN_H)
    return view


def note_label(label: QLabel, color: str = "#8f8f8f") -> QLabel:
    """A grey note under a control, holding the height its wrapped text needs."""
    label.setStyleSheet(f"color: {color};")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


def module_name(source: Path) -> str:
    """The import name of an addon file: dots and dashes cannot be imported."""
    return re.sub(r"[^A-Za-z0-9_]", "_", source.stem) or "addon"


def _display_path(value: Any) -> str:
    """A config path shown relative to the program, or whole if it is outside."""
    path = Path(value)
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def human_size(size: Any) -> str:
    text = float(size or 0)
    for unit in ("B", "KB", "MB"):
        if text < 1024:
            return f"{text:.0f} {unit}" if unit == "B" else f"{text:.1f} {unit}"
        text /= 1024.0
    return f"{text:.1f} MB"


def duration(seconds: Any) -> str:
    if seconds is None:
        return ""
    value = max(0, int(float(seconds)))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m {value % 60}s"
    return f"{value // 3600}h {(value % 3600) // 60}m"


def make_table(headers: list[str], stretch: int | None = None,
               widths: dict | None = None) -> QTableWidget:
    """A list the way this tab draws all of them: whole rows, nothing typed in."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    for index in range(len(headers)):
        header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    if stretch is not None:
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    for column, pixels in (widths or {}).items():
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(column, pixels)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_H)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setShowGrid(False)
    # Wide with the window, no taller than its rows; fit_rows() settles that.
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return table


def fit_rows(table: QTableWidget, cap: int = MAX_ROWS) -> None:
    """The exact height for the rows a table holds, up to ``cap``."""
    rows = min(max(table.rowCount(), 1), cap)
    head = table.horizontalHeader().sizeHint().height()
    edge = 2 * (table.frameWidth() + 1)
    height = head + rows * ROW_H + edge
    if table.minimumHeight() != height:
        table.setFixedHeight(height)


def state_color(state: str) -> QBrush:
    """The brush for a word in the state column."""
    return QBrush(QColor(STATE_COLORS.get(str(state), "#e0e0e0")))


# --- reading and writing a manifest ---

_ADDON_LITERAL = re.compile(r"^[ \t]*ADDON[ \t]*=[ \t]*\{", re.MULTILINE)


def has_addon_literal(text: str) -> bool:
    """Whether this source spells its manifest as an ``ADDON = {`` dict."""
    return bool(_ADDON_LITERAL.search(text or ""))


def manifest_target(addon: Any) -> Path:
    """The file the Details form writes: the addon itself, or a sidecar json."""
    if addon.kind == "python":
        try:
            text = addon.source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if has_addon_literal(text):
            return addon.source
    return addon.home / f"{addon.id}.json"


def manifest_values(addon: Any) -> dict:
    """What the Details form starts from; the file on disk wins."""
    values = {key: str(getattr(addon, key, "") or "") for key in DETAIL_KEYS}
    values["api"] = str(addon.manifest.get("api") or "")
    stored = file_manifest(addon)
    for key in DETAIL_KEYS:
        text = str(stored.get(key) or "").strip()
        if text:
            values[key] = text
    api = str(stored.get("api") or "").strip()
    if api:
        values["api"] = api
    return values


def format_literal(values: dict) -> str:
    """The ``ADDON = {...}`` block, one key per line; unowned keys ride along."""
    owned = DETAIL_KEYS + ("api",)
    lines = ["ADDON = {"]
    for key in owned + tuple(item for item in values if item not in owned):
        value = values.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "api":
            try:
                lines.append(f'    "api": {int(value)},')
            except (TypeError, ValueError):
                lines.append(f'    "api": {value!r},')
            continue
        lines.append(f'    "{key}": {value!r},')
    lines.append("}")
    return "\n".join(lines)


def _literal_span(text: str) -> tuple[int, int] | None:
    """Start and end of the ``ADDON = {...}`` assignment, braces matched."""
    match = _ADDON_LITERAL.search(text)
    if match is None:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    return None


def replace_addon_literal(text: str, values: dict) -> str | None:
    """Swap the ``ADDON`` dict for the edited one, keeping everything else."""
    span = _literal_span(text)
    if span is None:
        return None
    start, stop = span
    return text[:start] + format_literal(values) + text[stop:]


def literal_values(text: str) -> dict:
    """The ``ADDON = {...}`` dict read back, without importing the addon."""
    span = _literal_span(text or "")
    if span is None:
        return {}
    body = text[span[0]:span[1]].split("=", 1)[1].strip()
    try:
        data = ast.literal_eval(body)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def file_manifest(addon: Any) -> dict:
    """What the addon's own files say about it, whether or not it is loaded."""
    target = manifest_target(addon)
    if target == addon.source:
        try:
            return literal_values(target.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return {}
    return read_config_json(target)


#: The names an addon may keep its settings form, and its starting values, under.
_FIELDS_NAMES = ("FIELDS", "CONFIG_FIELDS")
_DEFAULTS_NAMES = ("DEFAULTS", "CONFIG_DEFAULTS")


def _simple_value(node: ast.AST, known: dict) -> Any:
    """One value as it is written in the file, a name the file gives one included.

    An addon may write ``"maximum": 50000`` or ``"max_rows": MAX_ROWS``; both are
    plain. Anything that only has a value once the addon runs - a comprehension,
    a call - is not read, and says so rather than importing anything.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in known:
            return known[node.id]
        raise ValueError(f"{node.id} is not given a plain value in the file")
    if isinstance(node, ast.List):
        return [_simple_value(item, known) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_simple_value(item, known) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_simple_value(item, known) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {_simple_value(key, known): _simple_value(value, known)
                for key, value in zip(node.keys, node.values) if key is not None}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _simple_value(node.operand, known)
        return -value if isinstance(node.op, ast.USub) else +value
    raise ValueError("this is not a value written out in the file")


def _top_assigns(tree: ast.AST) -> list:
    """The ``NAME = ...`` lines at the top of a parsed file, in the order they sit."""
    return [node for node in getattr(tree, "body", [])
            if isinstance(node, ast.Assign)]


def _assigned(tree: ast.AST, names: tuple) -> ast.AST | None:
    """What the first of ``names`` is given at the top of the file, as a node."""
    for node in _top_assigns(tree):
        if any(isinstance(target, ast.Name) and target.id in names
               for target in node.targets):
            return node.value
    return None


def _names_written(tree: ast.AST) -> dict:
    """Every name the file gives a plain value, in case a form reads one of them."""
    known: dict[str, Any] = {}
    for node in _top_assigns(tree):
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                known[target.id] = _simple_value(node.value, known)
            except (ValueError, TypeError):
                continue          # a name only worth something once the addon runs
    return known


def _read_entry(keys: list, values: list, known: dict) -> dict:
    """One dict of a form or a defaults map, with every key it can be given now."""
    entry: dict[str, Any] = {}
    for one, two in zip(keys, values):
        if not isinstance(one, ast.Constant) or not isinstance(one.value, str):
            continue
        try:
            entry[one.value] = _simple_value(two, known)
        except (ValueError, TypeError):
            continue          # this one key waits for the addon itself to say it
    return entry


def _read_form(node: ast.AST | None, known: dict) -> list[dict]:
    """The field dicts an addon declares, one for each entry that names its key.

    A field whose ``choices`` only exists once the addon runs is no reason to lose
    the field: its label, its kind and its hint are read all the same, and the
    addon fills in the rest when it is switched on.
    """
    if not isinstance(node, ast.List):
        return []
    return [entry for entry in (_read_entry(item.keys, item.values, known)
                                for item in node.elts if isinstance(item, ast.Dict))
            if entry.get("key")]


def _read_map(node: ast.AST | None, known: dict) -> dict:
    """The defaults an addon declares, dropping only what has no plain value."""
    if not isinstance(node, ast.Dict):
        return {}
    return _read_entry(node.keys, node.values, known)


def addon_metadata(source: Path) -> dict:
    """What an addon's source says about itself: its manifest and its settings.

    A program built from that source cannot answer either until it runs, and the
    app needs both before then: ``requires`` and ``connection`` are what let
    ``api.device`` use a transport or its session at all, and the declared
    fields are the form Settings shows. So the
    store reads them out of the file, with no import and no Qt, and keeps them
    beside the program and in the addon's own ``_data`` folder.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return {"manifest": {}, "fields": [], "defaults": {}}
    known = _names_written(tree)
    return {
        "manifest": literal_values(text),
        "fields": _read_form(_assigned(tree, _FIELDS_NAMES), known),
        "defaults": _read_map(_assigned(tree, _DEFAULTS_NAMES), known),
    }


def write_details(addon: Any, values: dict) -> Path:
    """Save the edited manifest where it is read from. Raises on failure."""
    if getattr(addon, "builtin", False):
        raise AddonError(SHIPPED_NOTE)
    clean = {key: str(values.get(key) or "").strip() for key in DETAIL_KEYS}
    api = str(values.get("api") or "").strip()
    target = manifest_target(addon)
    if target == addon.source:
        text = target.read_text(encoding="utf-8")
        edited = {**literal_values(text), **clean, "api": api}
        updated = replace_addon_literal(text, edited)
        if updated is None:
            raise AddonError(f"{target.name} has no ADDON dict to edit")
        _write_text(target, updated)
        return target
    data: dict = {}
    if target.is_file():
        try:
            stored = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data = stored
        except (OSError, ValueError):
            data = {}
    data.update({key: value for key, value in clean.items() if value})
    if api:
        try:
            data["api"] = int(api)
        except (TypeError, ValueError):
            data["api"] = api
    else:
        data.pop("api", None)
    _write_text(target, json.dumps(jsonable(data), indent=2, ensure_ascii=False) + "\n")
    return target


def _write_text(target: Path, text: str) -> None:
    """Write a file the user may have open: same name, one temp hop."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


# --- what the tab writes: an addon, its test, its launcher ---

_ADDON_SOURCE = Template('''#!/usr/bin/env python3
"""$title

$description

Written by the Addon Dev tab. Everything the addon needs from the app comes
through ``api``, and nothing here touches Qt, so the same file runs imported
into Berry Manager and compiled into a program of its own: the tab is built
from a declarative spec, and what its controls do is reported back through
``on_widget``.

Every call used below has a page of its own in Addon Dev > Docs, read off the
app as it runs. The four worth a new author first are ``api.tabs.add_tab``,
``api.settings_pages.add``, ``api.config.declare`` and ``api.events.on``.
"""

from __future__ import annotations

from typing import Any

from addon_common import AddonError, config_get

ADDON = {
    "name": "$name",
    "version": "$version",
    "author": "$author",
    "description": "$description",
    # The transports api.device may use; leave the key out if never. Each one
    # also gets a switch of its own on this addon's row in Settings > Addons,
    # and both the app-wide and the addon's own have to be on.
    "requires": ["ssh", "sftp"],
    # Whose connection the device calls use: "app" (the app's one main SSH+SFTP
    # session, which the user opens by button or at startup), "own" (a fresh
    # connection per call), "both" (the user chooses on this addon's Settings
    # row) or "none" - which says no more than leaving "requires" out above.
    "connection": "app",
    # A picture called <id>icon.png in the assets folder beside this file - in
    # assets/<id>/, or in the program's own assets - is found on
    # its own, both for the tab's mark and for its card in the addon store, so
    # only a file named differently needs this key.
    # "icon": "oldname.png",
    "api": 1,
}

#: The tab this addon adds, the page it adds to Settings, and what it has said.
TAB = "$tab"
PAGE = "$id-settings"
HISTORY: list[str] = []

#: The settings the user can change in Settings > Addons, and their defaults.
#: A key with a dot reads and writes inside a section of the config file.
FIELDS = [
    {"key": "greeting", "label": "Text to show", "kind": "text",
     "hint": "What the button on the tab prints", "placeholder": "Hello"},
    {"key": "lines", "label": "Lines to remember", "kind": "number",
     "hint": "How much of what it said the tab keeps on screen"},
    {"key": "show_page", "label": "Add a Settings page", "kind": "bool",
     "hint": "A page of this addon's own, outside the installed addons"},
]
DEFAULTS = {"greeting": "Hello from $id", "lines": 12, "show_page": True}

#: Set by init(); the app hands the addon its API there.
api = None


def value(key: str, fallback: Any = None) -> Any:
    """One config value, read the same way everywhere in this file."""
    return config_get(api.config.all(), key, DEFAULTS.get(key, fallback))


def remembered() -> int:
    """How many lines the history keeps; a hand-edited file can say anything."""
    try:
        return max(1, int(float(value("lines"))))
    except (TypeError, ValueError):
        return max(1, int(DEFAULTS["lines"]))


def show(identifier: str, prop: str, what: Any) -> None:
    """Write one control on the tab, thinking nothing of it when it is gone."""
    try:
        api.tabs.set_widget(TAB, identifier, prop, what)
    except AddonError:
        pass


def say(text: str) -> None:
    """One line on the tab, one in its history and one in the app log."""
    if api is None:
        return
    text = str(text)
    HISTORY.append(text)
    del HISTORY[:-remembered()]
    show("status", "text", text)
    show("history", "items", list(HISTORY))
    api.log(text)


def greeting() -> str:
    """What to say: the line on the tab first, then the config file."""
    text = ""
    if api is not None:
        try:
            text = str(api.tabs.widget_value(TAB, "greeting") or "").strip()
        except AddonError:
            text = ""
    return text or str(value("greeting", DEFAULTS["greeting"]))


def pressed() -> None:
    """The tab's button: read the form and report what it said."""
    if api is None:
        return
    say(f"you asked me to say: {greeting()}")


def about() -> None:
    """The Settings page's button: a dialog out of what the addon knows."""
    if api is None:
        return
    lines = [
        "$description",
        "",
        f"greeting: {greeting()}",
        f"starts so far: {api.settings.get('runs', 0)}",
        f"Berry Manager: {api.session.version()}",
        f"config file: {api.config.path()}",
    ]
    # chr(10) rather than a backslash: this file is written out of a template.
    api.ui.information("$name", chr(10).join(lines))


def build_tab(values: dict) -> None:
    """A spec, not widgets: a process addon can only describe what it wants.

    Two things a tab can ask for that this one does not: ``pinned=True`` with
    ``icon_only=True`` holds it in the short run beside Settings, showing the
    mark and no caption - and a mark has nothing but its tooltip left to say who
    it belongs to, which is why the app fills that in with this addon's name -
    while a second ``add_tab`` names a child tab, which
    the app puts to the left of the tab that made it.
    """
    api.tabs.add_tab(TAB, spec={
        "kind": "column",
        "children": [
            {"kind": "label", "text": "$title", "font_size": 12},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "style": "color: #8f8f8f;"},
            {"kind": "line", "id": "greeting",
             "placeholder": str(config_get(values, "greeting", DEFAULTS["greeting"]))},
            {"kind": "button", "id": "press", "text": "Say it", "on": ["clicked"]},
            {"kind": "label", "text": "what it said, newest last", "font_size": 8,
             "style": "color: #7a7a7a;"},
            {"kind": "list", "id": "history", "items": list(HISTORY)},
            {"kind": "spacer"},
        ],
    })
    # Three other shapes, each one a call away rather than a rewrite:
    #
    #   api.tabs.add_tab(TAB, spec={...}, pinned=True, icon_only=True)
    #       holds the tab in the short run beside Settings: its mark, no caption
    #   api.tabs.add_web_tab(TAB + ' page', url='http://192.168.1.1/')
    #       shows a page the device serves, with the app's own address bar; give
    #       ip / port / path instead of a url to follow the active profile
    #   api.tabs.add_tab('Child', spec={...})
    #       opens a second tab, which the app places to the left of this one


def build_page(values: dict) -> None:
    """A page of the app's own Settings window, outside Installed addons.

    ``add`` replaces whatever holds the name, so building the page again after
    a config change is safe. Its controls arrive at ``on_widget`` with ``tab``
    set to :data:`PAGE`, which is how one handler serves both of them.
    """
    if not bool(config_get(values, "show_page", DEFAULTS["show_page"])):
        api.settings_pages.remove(PAGE)
        return
    api.settings_pages.add(PAGE, title="$name", spec={
        "kind": "form",
        "children": [
            {"kind": "label", "label": "About", "text": "$description",
             "word_wrap": True},
            {"kind": "check", "id": "show_page", "label": "", "text":
             "Keep this page in Settings", "checked": True},
            {"kind": "button", "id": "about", "label": "", "text": "About $name",
             "on": ["clicked"]},
        ],
    })


def init(plugin_api: Any) -> None:
    """The entry point: declare the config, then build what the addon shows."""
    global api
    api = plugin_api
    values = api.config.declare(FIELDS, DEFAULTS,
                                title="$title - addons/_data/$id/config.json")
    HISTORY.clear()
    build_tab(values)
    build_page(values)
    api.ui.menu_item("$menu", focus, menu="Tools")
    api.events.on("ssh_connected", on_connected)
    # Work that paces itself: api.schedule.every(30, tick) is called on the GUI
    # thread, and the app stops the timer when the addon is switched off.
    # News worth interrupting someone for, even with this tab closed:
    # api.ui.popup("$name", "the run finished") - the status line, and a
    # notification from the app's tray icon while Settings allows them.
    api.log("ready")


def start() -> None:
    """Called once at startup, and whenever the addon is switched on."""
    if api is None:
        return
    runs = int(api.settings.get("runs", 0) or 0) + 1
    api.settings.set("runs", runs)          # the profile's own copy, per profile
    say(f"$title is running (start no. {runs})")


def shutdown() -> None:
    """Switched off: drop the API, so nothing here asks the app for anything."""
    global api
    api = None


def focus() -> None:
    """The Tools menu entry."""
    if api is not None:
        api.tabs.activate(TAB)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """What a spec-built control reported. Works in both kinds of addon."""
    if api is None or event not in ("clicked", "changed"):
        return
    if identifier == "press" and tab == TAB:
        pressed()
    elif identifier == "about":
        about()
    elif identifier == "show_page" and event == "changed":
        # The switch writes its own key; on_config then adds or drops the page.
        api.config.set("show_page", bool(payload.get("checked")))


def on_connected(payload: dict) -> None:
    """The device came up."""
    if api is not None:
        say("the device is reachable")


def on_config(payload: dict) -> None:
    """Called when addons/_data/$id/config.json changed: read it again."""
    if api is None:
        return
    values = api.config.all()
    build_tab(values)
    build_page(values)
    say("the config file was read again")


def on_event(name: str, payload: dict) -> None:
    """Every event the app broadcasts arrives here as well."""
    if api is not None and name == "operation_done":
        say('the app finished a ' + str(payload.get("operation", "job")))


def on_message(sender: str, data: Any) -> None:
    """Another addon wrote here; api.peers.list() says who answers a send."""
    if api is not None:
        say(f"{sender} sent {data!r}")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
''')

_TEST_SOURCE = Template('''#!/usr/bin/env python3
"""A test for addons/$id.py, written by the Addon Dev tab.

It runs the addon with the recording stub from :mod:`addon_client` instead of
the app: nothing is opened on screen, nothing is sent to a device, and every
call the addon makes is written down. So the test checks what the addon *asked
for*, which is the part an addon author owns.

Four questions are worth asking of any addon, and the checks below are grouped
by them:

- does ``init`` build what the addon promises, and name its controls?
- does a value the user set reach the code that uses it?
- does every button do the thing it sits there for?
- and does the addon go quiet once ``shutdown`` has taken the API back?

What the app answers is the test's to decide: ``api.answer("device.run", {"code": 1})``
makes a command look as if it failed, and ``api.asked("tabs.add_tab", "spec")``
reads back the spec the addon handed the app.

Run it from the Test panel, or on the command line::

    python addons/_tests/test_$id.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: The folder Berry Manager and addon_client live in, two levels up.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                            # noqa: E402

SOURCE = ROOT / "addons" / "$id.py"
TAB = "$tab"
PAGE = "$id-settings"
FAILURES: list[str] = []
CHECKS = 0


def check(condition: Any, text: str) -> None:
    """One thing the addon has to do; every one is reported, not just the first."""
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(text)


def nodes(spec: Any):
    """Every dict in a widget spec, so the ids it was built with can be counted."""
    if isinstance(spec, dict):
        yield spec
        for child in spec.get("children") or []:
            yield from nodes(child)
    elif isinstance(spec, (list, tuple)):
        for item in spec:
            yield from nodes(item)


def ids_of(spec: Any) -> set:
    return {str(node["id"]) for node in nodes(spec) if node.get("id")}


def shown(entry: tuple) -> str:
    """One recorded call on one line: a whole spec is not worth a screenful."""
    method, arguments, named = entry
    text = f"{method}{list(arguments)}"
    if named:
        text += " " + str(named)
    return text if len(text) <= 150 else text[:147] + "..."


def main() -> int:
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    # The values the config file would have had; a test never needs the file.
    api = addon_client.stub_api("$id", config={"greeting": "hello from the test"})
    addon = addon_client.load_addon(SOURCE)

    # --- init: what the addon asks the app to build ---
    addon.init(api)
    check(api.called("config.declare"), "init did not declare its config keys")
    check(api.asked("config.declare", "fields"), "the declared schema is empty")
    check(api.called("tabs.add_tab"), "init did not add a tab")
    named = api.asked("tabs.add_tab", "name")
    check(named == TAB, f"the tab is not named as the addon says: {named!r}")
    ids = ids_of(api.asked("tabs.add_tab", "spec"))
    check({"press", "status"} <= ids,
          f"the tab spec gives its controls no ids to write back to: {sorted(ids)}")
    check(api.called("ui.menu_item"), "init did not add its Tools menu entry")
    check(api.times("events.on") >= 1, "init did not listen for any event")
    if api.called("settings_pages.add"):
        page_ids = ids_of(api.asked("settings_pages.add", "spec"))
        check(bool(page_ids),
              f"the Settings page has no named controls: {sorted(page_ids)}")

    # --- what the user set reaches the code that uses it ---
    check(api.config.get("greeting") == "hello from the test",
          "the config value did not reach the addon")

    # --- start: a record of its own, kept per profile ---
    addon.start()
    check(api.times("settings.set") >= 1, "start recorded nothing in api.settings")

    # --- every control does the thing it is there for ---
    api.logs.clear()
    addon.on_widget(TAB, "press", "clicked", {})
    check(any("hello from the test" in line for line in api.logs),
          "pressing the button left nothing in the log to show for it")
    addon.on_widget(PAGE, "about", "clicked", {})
    check(api.called("ui.information"), "the page's button opened no dialog")

    # --- another addon speaking to this one, if it listens ---
    peer = getattr(addon, "on_message", None)
    if callable(peer):
        api.logs.clear()
        peer("tester", {"hello": 1})
        check(any("tester" in line for line in api.logs),
              "on_message took a peer's message and said nothing of it")

    # --- a config change is read again, not remembered from init ---
    mark = len(api.calls)
    addon.on_config({"addon": "$id", "source": "test", "file": "x"})
    check(len(api.calls) > mark, "on_config asked the app for nothing at all")

    # --- and shutdown is really the end of it ---
    addon.shutdown()
    mark = len(api.calls)
    addon.start()
    addon.on_widget(TAB, "press", "clicked", {})
    addon.on_config({"addon": "$id"})
    check(len(api.calls) == mark,
          "the addon still talks to the app after shutdown()")

    # --- what the addon did, for whoever has to read this ---
    tally: dict[str, int] = {}
    for method, _arguments, _named in api.calls:
        where = str(method).split(".")[0]
        tally[where] = tally.get(where, 0) + 1
    print(f"$id: {len(api.calls)} api call(s)"
          + (", " + ", ".join(f"{count} {where}"
                             for where, count in sorted(tally.items())) if tally else ""))
    for entry in api.calls[:20]:
        print(f"  {shown(entry)}")
    if len(api.calls) > 20:
        print(f"  ... and {len(api.calls) - 20} more")
    print("log lines written by the addon:")
    for line in api.logs:
        print(f"  {line}")
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        print(f"{CHECKS - len(FAILURES)} of {CHECKS} checks passed")
        return 1
    print()
    print(f"all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')

_LAUNCHER = Template('''#!/usr/bin/env python3
"""Runs $id as its own program. Written by the Addon Dev tab; do not edit."""

from __future__ import annotations

import os
import sys

for _descriptor, _mode in ((1, "w"), (2, "w")):
    # A --windowed build has no streams; reopen the pipe rather than lose it.
    if getattr(sys, "stdout" if _descriptor == 1 else "stderr", None) is None:
        try:
            stream = os.fdopen(_descriptor, _mode, buffering=1, encoding="utf-8")
        except OSError:
            continue
        setattr(sys, "stdout" if _descriptor == 1 else "stderr", stream)

import addon_client
import $module as addon

if __name__ == "__main__":
    raise SystemExit(addon_client.serve(addon))
''')


# --- PyInstaller ---

#: Which interpreter can build, and whether it has PyInstaller (costly to ask).
_READY: dict[str, tuple[bool, str]] = {}

#: What a Python may be started by, tried in this order. ``py`` is the launcher
#: the Windows installer puts on the path in its own right.
PYTHON_NAMES = ("python3", "python", "py")

#: The interpreter the path was asked for, once; nothing means it was not asked.
_INTERPRETER: tuple[str | None, str] | None = None


def pyinstaller_ready() -> tuple[bool, str]:
    """Whether the interpreter that would do the building has PyInstaller."""
    where, why = build_interpreter()
    if where is None:
        return False, why
    cached = _READY.get(str(where))
    if cached is not None:
        return cached
    found = _has_pyinstaller_here() if str(where) == sys.executable \
        else _has_pyinstaller(where)
    answer = (found, "" if found else f"{where} -m pip install pyinstaller")
    _READY[str(where)] = answer
    return answer


def _not_a_python(found: str) -> str:
    """Why this command on the path cannot build; nothing when it can.

    Windows leaves zero-length python and python3 files in its WindowsApps
    folder - the app execution aliases. Running one prints that Python was not
    found, points at the Microsoft Store and stops with code 9009, which
    otherwise reads as a failure of the build itself. An empty file is therefore
    refused outright rather than started, and whatever is left is asked to start
    once, so a command that is broken is not chosen either.
    """
    try:
        size = Path(found).stat().st_size
    except OSError as error:
        return f"a command that cannot be read ({error})"
    if size == 0:
        return ("a Windows app execution alias, standing for the Microsoft Store "
                "page instead of a Python" if os.name == "nt"
                else "an empty file that is not a Python")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        done = subprocess.run([found, "-c", "import sys"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as error:
        return f"a command that did not start ({error})"
    if done.returncode != 0:
        return f"a command that stopped with code {done.returncode}"
    return ""


def build_interpreter() -> tuple[str | None, str]:
    """The Python to build or test with: ours, or a working one found on the path.

    A packaged program carries no interpreter of its own, so ``python3``,
    ``python`` and the Windows ``py`` launcher are tried in turn on either
    system, and each is checked rather than trusted. The answer is kept, because
    asking costs a process and the panels ask often; :func:`forget_interpreter`
    puts it back so a Python installed since is noticed.
    """
    global _INTERPRETER
    if not IS_COMPILED:
        return sys.executable, ""
    if _INTERPRETER is not None:
        return _INTERPRETER
    seen: list[str] = []
    for name in PYTHON_NAMES:
        found = shutil.which(name)
        if found is None:
            continue
        why = _not_a_python(found)
        if why:
            seen.append(f"{name} at {found} is {why}")
            continue
        _INTERPRETER = (found, "")
        return _INTERPRETER
    _INTERPRETER = (None, "no working Python found on the path"
                    + (" (" + "; ".join(seen) + ")" if seen else "")
                    + ". Install Python, or on Windows untick the app execution "
                      "aliases under Settings > Apps > Advanced app settings, or "
                      "run this app from a source checkout to compile and test "
                      "addons")
    return _INTERPRETER


def forget_interpreter() -> None:
    """Ask the path again next time, and drop what was found along with it."""
    global _INTERPRETER
    _INTERPRETER = None
    _READY.clear()


def harness_dir() -> Path | None:
    """Where the modules an addon imports lie on disk, or nothing.

    ``addon_common`` and ``addon_client`` are what a launcher and a test script
    import to reach the app. In a checkout they sit in the app's own folder; in
    a built program they are inside the archive unless the build packed a plain
    copy, and the Python that builds cannot read the archive.
    """
    return HARNESS_DIR if all((HARNESS_DIR / name).is_file()
                              for name in HARNESS_FILES) else None


def app_modules_ready() -> tuple[bool, str]:
    """Whether the app's modules lie on disk, where a build and a test look."""
    if harness_dir() is not None:
        return True, ""
    return False, ("this program has no copy of the modules an addon imports "
                   "(addon_client, addon_common) where a build and a test can "
                   "read them. Run Berry Manager from a source checkout, use a "
                   "build that packed them, or install the addon from the "
                   "addon store, which fetches them")


def _has_pyinstaller_here() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("PyInstaller") is not None
    except (ImportError, ValueError):
        return False


def _has_pyinstaller(where: str) -> bool:
    """Ask another interpreter whether it can build."""
    try:
        done = subprocess.run([where, "-c", "import PyInstaller"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def buildable(addon: Any) -> tuple[bool, str]:
    """Whether this one addon could be compiled here, and why not if it cannot."""
    if not str(addon.source).endswith(".py"):
        return False, "this addon is already a program"
    if getattr(addon, "builtin", False):
        return False, ("the file ships inside the program; compile the copy in "
                       "the addons folder instead")
    ready, why = app_modules_ready()
    if not ready:
        return False, why
    return pyinstaller_ready()


def icon_for_build() -> Path | None:
    """The program's own mark as a file this build can wear, or nothing.

    The shipped icon is a PNG, so on Windows it is rewrapped here too: a program
    built by this tab or by the store then carries the berry rather than
    PyInstaller's own logo.
    """
    path = find_asset(ICON_FILES[0])
    return usable_icon(path) if path is not None else None


#: The ``--icon`` shapes PyInstaller reads as it stands on each platform. Any
#: other one it converts only when Pillow happens to be installed, and the Python
#: a user builds with has no reason to have it: requirements.txt does not name
#: Pillow anywhere, and the store installs nothing but PyInstaller itself.
ICON_FORMATS = {
    "win32": (".ico", ".exe"),
    "darwin": (".icns",),
}

#: The eight bytes that say a file is a PNG, and where its width and height sit.
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def icon_formats() -> tuple[str, ...]:
    """What may be handed to ``--icon`` here without being converted first."""
    return ICON_FORMATS.get(sys.platform, (".ico", ".icns", ".png"))


def png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height out of its own header; nothing when it is not one."""
    if not data.startswith(_PNG_HEAD) or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def ico_from_png(png: Path, target: Path) -> Path | None:
    """One PNG rewrapped as a Windows ``.ico`` with nothing but the standard library.

    An icon directory may hold PNG-compressed image data - Windows has read it
    since Vista - so the reserved word, the type, the one directory entry and the
    picture's own bytes follow each other here with nothing decoded in between. A
    file that is not a PNG, a side the directory cannot count (it holds one byte,
    and 0 means 256) and a disk that will not have the file all answer with
    nothing rather than a broken icon.
    """
    try:
        data = png.read_bytes()
    except OSError:
        return None
    size = png_size(data)
    if size is None or not all(1 <= side <= 256 for side in size):
        return None
    header = struct.pack("<HHH", 0, 1, 1)
    # A directory entry is 16 bytes, so the picture starts after both of them.
    dirent = struct.pack("<BBBBHHII", size[0] % 256, size[1] % 256, 0, 0,
                         1, 32, len(data), len(header) + 16)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header + dirent + data)
    except OSError:
        return None
    return target


#: How wide a Windows icon's directory can say a picture is: one byte per side,
#: and 0 stands for 256. Anything bigger is taken down to it before being packed.
ICO_MAX_SIDE = 256


def ico_scaled(png: Path, target: Path) -> Path | None:
    """One PNG too wide for an icon's directory, drawn down and packed by Qt.

    :func:`ico_from_png` cannot count past 256, and the marks addons publish are
    often 512 - too big for the directory and too dear to throw away. Qt is
    already here (this tab is built out of it) and its ICO writer takes the
    picture down whole. A Qt without the ICO plugin, a file it cannot read and a
    disk that will not have the answer all say nothing, which costs a build its
    icon and not its program.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage

        image = QImage(str(png))
        if image.isNull():
            return None
        if max(image.width(), image.height()) > ICO_MAX_SIDE:
            image = image.scaled(ICO_MAX_SIDE, ICO_MAX_SIDE,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        if not image.save(str(target), "ICO"):
            return None
    except Exception:                          # noqa: BLE001 - only a picture at stake
        return None
    return target if target.is_file() else None


def scratch_ico(png: Path) -> Path | None:
    """One PNG's ``.ico`` in a file nobody keeps.

    A build's icon is read once and thrown away, so writing it beside the picture
    would pack the same art into the program twice under two names - and a
    read-only bundle could not write anywhere at all.
    """
    try:
        handle = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
    except OSError:
        return None
    where = Path(handle.name)
    try:
        handle.close()
    except OSError:
        return None
    return ico_from_png(png, where) or ico_scaled(png, where)


def usable_icon(path: Path) -> Path | None:
    """One picture as a file this platform's PyInstaller can use; nothing if it cannot.

    This is where a store addon's ``<id>icon.png`` - a tab and card mark, made
    for the bar and not for a Windows executable - is settled: on Windows it is
    rewrapped rather than handed over raw, because PyInstaller would otherwise
    stop the whole build over a picture, and a build that cannot use it at all
    loses its icon rather than its program.
    """
    try:
        if not path.is_file():
            return None
        suffix = path.suffix.lower()
    except OSError:
        return None
    if suffix in icon_formats():
        return path
    if suffix == ".png" and sys.platform == "win32":
        return scratch_ico(path)
    return None


def icon_choice(icon: Any) -> Path | None:
    """The ``--icon`` file for a build: one PyInstaller can read, the app's, or none.

    ``icon`` is ``True`` for the program's own mark, ``False``/empty for none, or
    a path an addon brought with it (a store addon's own icon). A named icon that
    is missing or the wrong shape is rewrapped if it can be, and falls back on the
    app's if it cannot, so a build never dies over a picture.
    """
    if not icon:
        return None
    if icon is True:
        return icon_for_build()
    made = usable_icon(Path(icon))
    if made is not None:
        return made
    return icon_for_build()


def build_root(root: Path | None = None) -> Path:
    """The folder a build is staged in: ``addons/_build``, or a caller's own.

    An addon that builds things of its own (the store) keeps its work under its
    own ``_data`` folder rather than in the dev tab's, so nothing it makes lands
    among the files the Addon Dev tab made. Nothing is created here: whoever
    stages a build makes the folder it needs.
    """
    return ADDON_BUILD_DIR if root is None else Path(root)


def staged_name(addon_id: str, onefile: bool,
                root: Path | None = None) -> Path:
    """Where PyInstaller puts the program before it is copied into ``addons``."""
    folder = build_root(root) / addon_id / ("dist" if onefile else Path("dist") / addon_id)
    return folder / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}"


def build_command(launcher: Path, addon_id: str, options: dict,
                  source: Path | None = None,
                  root: Path | None = None,
                  paths: list | tuple = ()) -> list[str]:
    """The PyInstaller line for a build staged inside ``<root>/<id>``.

    ``paths`` adds folders holding the modules the launcher imports, for a
    caller that keeps its own copy of them: the store fetches the app's two
    when the program it runs inside has none on disk.
    """
    work = build_root(root) / addon_id
    where, _why = build_interpreter()
    command = [
        where or sys.executable, "-m", "PyInstaller",
        # No --clean here on purpose: it throws away PyInstaller's own
        # analysis cache, so every build - the dev tab's and the store's -
        # starts cold and re-reads every module it has already worked out.
        # Nothing stale can ride into a new program: each build stages in
        # its own ``<root>/<id>`` folder, which the caller empties first.
        "--noconfirm",
        "--distpath", str(work / "dist"),
        "--workpath", str(work / "build"),
        "--specpath", str(work),
        "--name", addon_id,
    ]
    command.append("--onefile" if options.get("onefile", True) else "--onedir")
    command.append("--console" if options.get("console", True) else "--windowed")
    icon = icon_choice(options.get("icon", True))
    if icon is not None:
        command += ["--icon", str(icon)]
    for local, dest in (options.get("add_data") or ()):      # files packed into the build
        command += ["--add-data", os.pathsep.join([str(local), str(dest)])]
    folders, seen = [], set()
    for folder in [harness_dir() or BASE_DIR, *paths,
                   *([source.parent] if source is not None else [])]:
        if str(folder) not in seen:
            seen.add(str(folder))
            folders.append(Path(folder))
    if source is not None:
        command += ["--hidden-import", module_name(source)]
    for folder in folders:
        command += ["--paths", str(folder)]
    for extra in str(options.get("hidden", "")).replace(",", " ").split():
        command += ["--hidden-import", extra]
    command.append(str(launcher))
    return command


class _Stream:
    """One child process, its output as lines, and a way to stop it."""

    def __init__(self, command: list[str], cwd: Path | None = None,
                 env: dict | None = None) -> None:
        self.command = list(command)
        self.cwd = str(cwd) if cwd is not None else str(BASE_DIR)
        self.env = env                                  # None keeps the parent's
        self.lines: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.finished = False
        self.code: int | None = None

    def start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            # A tree-killable child: a PyInstaller run spawns more processes
            # than the one started here, and all of them must be reachable.
            self.proc = popen_tree(
                self.command, cwd=self.cwd, env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except OSError as error:
            self.lines.put(f"could not start: {error}")
            self.finished = True
            self.code = -1
            return
        self.reader = threading.Thread(target=self._pump, daemon=True,
                                       name="addon-dev-out")
        self.reader.start()

    def _pump(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.code = self.proc.wait()
        release_process_tree(self.proc)
        self.finished = True
        self.lines.put(None)              # the tab stops draining on this

    def drain(self, limit: int = 400) -> list[Any]:
        out: list = []
        while len(out) < limit:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return out

    def running(self) -> bool:
        return not self.finished

    def cancel(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        terminate_process_tree(self.proc)

    def kill(self) -> None:
        """Force the whole tree down, for the app's closing path."""
        if self.proc is None or self.proc.poll() is not None:
            return
        kill_process_tree(self.proc)
        release_process_tree(self.proc)


def log_lines(text: str, addon_id: str, warnings_only: bool = False) -> list[str]:
    """The addon's own lines out of the app log, newest kept last."""
    marker = f"[{addon_id}]"
    out: list[str] = []
    for line in str(text or "").splitlines():
        if marker not in line:
            continue
        if warnings_only and "error:" not in line:
            continue
        out.append(line.strip())
    return out


# --- the "New addon..." dialog ---

class NewAddonDialog(QDialog):
    """Ask for the header, then write the addon and its test script."""

    #: What may become a file name that Python can also import.
    CLEAN = re.compile(r"[^A-Za-z0-9_]+")

    def __init__(self, parent: QWidget | None = None,
                 known: Any = ()) -> None:
        super().__init__(parent)
        self.known = {str(item) for item in known}
        self.setWindowTitle("New addon")
        self.resize(460, 380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("mytool - letters, digits and underscores")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My tool")
        self.version_input = QLineEdit("1.0")
        self.author_input = QLineEdit()
        self.description_input = QLineEdit()
        self.description_input.setPlaceholderText("One line the Addons list shows")
        self.tab_input = QLineEdit()
        self.tab_input.setPlaceholderText("My tool")
        self.menu_input = QLineEdit()
        self.menu_input.setPlaceholderText("Show My tool")
        form.addRow("Identifier", self.id_input)
        form.addRow("Name", self.name_input)
        form.addRow("Version", self.version_input)
        form.addRow("Author", self.author_input)
        form.addRow("Description", self.description_input)
        form.addRow("Tab title", self.tab_input)
        form.addRow("Menu entry", self.menu_input)
        layout.addLayout(form)

        self.test_check = QCheckBox("Write a test script in addons/_tests as well")
        self.test_check.setChecked(True)
        self.run_check = QCheckBox("Run it as soon as it is written")
        self.run_check.setChecked(True)
        layout.addWidget(self.test_check)
        layout.addWidget(self.run_check)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #8f8f8f;")
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for field in (self.id_input, self.name_input, self.tab_input):
            field.textChanged.connect(self._follow)
        self.id_input.setFocus()

    def _follow(self, _text: str = "") -> None:
        """An identifier typed, a sensible title everywhere else."""
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        for field, wanted in ((self.name_input, cleaned.replace("_", " ").strip()),
                              (self.tab_input, cleaned.replace("_", " ").strip()),
                              (self.menu_input, f"Show {cleaned.replace('_', ' ')}")):
            if not field.text().strip():
                field.setText(wanted)
        taken = cleaned.lower() in self.known
        self.note.setText(
            "That identifier is already used by an addon." if taken else
            f"Written to {ADDONS_DIR / (cleaned or '...')}.py"
            + (f" and {ADDON_TEST_DIR / ('test_' + cleaned + '.py')}" if cleaned else ""))
        self.note.setStyleSheet("color: #ffb020;" if taken else "color: #8f8f8f;")

    def usable(self) -> bool:
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        return bool(cleaned) and not cleaned[0].isdigit() \
            and cleaned.lower() not in self.known

    def try_accept(self) -> None:
        if not self.usable():
            self.note.setText("Give it an identifier that Python can import "
                              "(letters, digits, underscore; not a digit first) "
                              "and that no addon uses yet.")
            self.note.setStyleSheet("color: #ffb020;")
            return
        self.accept()

    def values(self) -> dict:
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        text = lambda field, fallback: (field.text().strip() or fallback)
        name = text(self.name_input, cleaned.replace("_", " "))
        return {
            "id": cleaned,
            "name": name,
            "version": text(self.version_input, "1.0"),
            "author": text(self.author_input, ""),
            "description": text(self.description_input, f"{name} addon"),
            "tab": text(self.tab_input, name),
            "menu": text(self.menu_input, f"Show {name}"),
            "test": self.test_check.isChecked(),
            "run": self.run_check.isChecked(),
        }


# --- the guides: what the Docs pane shows before the reference ---

#: The README as published. A packaged program carries no markdown, so the
#: guide's "whole manual" link falls back to this when ``README.md`` is not on
#: disk next to the running program.
README_URL = PROJECT_GITHUB_URL + "/blob/main/utils/update%20util/README.md"

#: The pane's reading order: a guide, its title, and the markdown it is written
#: in. Links of the form ``[text](api:tabs.add_tab)`` and ``[text](guide:tabs)``
#: move around inside the panel; anything else opens in the browser.
GUIDES: tuple[tuple[str, str, str], ...] = (
    ("start", "Start here", """
# Start here

An addon is one Python file in the ``addons`` folder beside Berry Manager, or a
program compiled from such a file. The app reads it, calls ``init(api)`` and
hands it the one object through which everything else is reached: ``api``. An
addon never touches the app's widgets directly - a tab is described in a dict
and the app builds it - which is why the same file runs both imported into the
app and as a program of its own.

## Three minutes

- Press **New addon...** above: it writes ``addons/<id>.py`` and, when asked,
  ``addons/_tests/test_<id>.py``.
- Switch it on - the checkbox above, or Settings > Addons. Its tab appears in
  the bar.
- Edit the file in any editor, then press **Restart** here. Only the addon is
  read again; the app does not move.

## Where things live

- ``addons`` - the addon's file, and the program built from it.
- ``addons/_data/<id>`` - its ``config.json``, shared by every profile.
- ``addons/_tests`` - its test script.
- ``config/berry_manager.log`` - what ``api.log`` writes; the Log pane above
  filters it to this one addon.

The reference in this panel is read off the running ``addon_api`` module, so it
cannot describe an API this program does not have.
"""),

    ("anatomy", "Anatomy of an addon", """
# Anatomy of an addon

    ADDON = {...}         the manifest: name, version, author, description,
                          requires, connection, icon, api
    TAB = "My addon"      the name of the tab the addon owns
    FIELDS, DEFAULTS      the settings the user can change
    def init(api)         the entry point: declare the config, then build
    def start()           called at startup and whenever the switch goes on
    def shutdown()        called before the app forgets the addon

Everything else is a hook the app calls when something happens:

- ``on_widget(tab, id, event, payload)`` - a control the addon asked for was
  clicked, typed in, picked from a list.
- ``on_config(payload)`` - ``config.json`` changed under its feet.
- ``on_event(name, payload)`` and ``on_<name>(payload)`` - an event arrived.
- ``on_message(sender, data)`` - another addon wrote to it through
  [api.peers.send](api:peers.send).

## The manifest

Only ``name`` is needed. ``api`` should stay at the version the app speaks, and
``requires`` names the transports ``api.device`` may use - ``["ssh", "sftp"]``,
or leave the key out for an addon that never connects. Each transport gets a
switch of its own on the addon's Settings row, and both the app-wide and the
addon's own have to be on. ``connection`` says whose session the calls go over:
``"app"`` for the program's one main connection, ``"own"`` for a connection the
addon opens for itself per call, ``"both"`` where the user picks with a switch on
the row, or ``"none"`` - which says no more than leaving ``requires`` out, and is
answered on the row and in **Connection status** as *no device connection*
rather than with a dot that could never go green. An addon that ships inside the
program always rides its session, whatever it declared. ``icon`` names the
picture the addon wears; a file named ``<id>icon.png`` in either of the two
folders the rule allows is found without being declared at all (see
[Notices and icons](guide:notice)).

## Mind the process boundary

A compiled addon is a separate program: everything it sends and receives has to
fit in JSON. So a tab is a spec dict rather than a widget, values come back as
plain data, and no module of the app is imported. Write it that way from the
start and the source and the build behave the same.
"""),

    ("tabs", "Tabs, pinned and plain", """
# Tabs, pinned and plain

[api.tabs.add_tab](api:tabs.add_tab) takes a spec dict - the language the app
turns into widgets - or any QWidget in an imported addon. A new tab does not
steal the view; [activate](api:tabs.activate) puts it on screen.

    api.tabs.add_tab("My addon", spec={
        "kind": "column",
        "children": [
            {"kind": "label", "text": "Hello", "font_size": 12},
            {"kind": "line", "id": "who", "placeholder": "name"},
            {"kind": "button", "id": "go", "text": "Go", "on": ["clicked"]},
            {"kind": "tree", "id": "rows", "columns": ["Thing", "State"]},
        ],
    })

Layouts: ``column`` / ``vbox``, ``row`` / ``hbox``, ``grid``, ``form``,
``group``, ``scroll``, ``splitter``, ``tabs``. Leaves: ``label``, ``button``,
``line``, ``color``, ``multi``, ``check``, ``combo``, ``list``, ``tree``,
``table``, ``progress``, ``spin``, ``slider``, ``spacer``, ``image``, ``web``,
``terminal``.

Anything with an ``id`` can be read and written afterwards with
[set_widget](api:tabs.set_widget) and [widget_value](api:tabs.widget_value).
The property names are the spec's own: ``text``, ``value``, ``rows``,
``items``, ``clear``, ``append``, ``placeholder``, ``tooltip``, ``style``,
``enabled``, ``visible``, ``checked``, ``highlight``, ``maximum``, ``format``,
``current``, ``font_size``.

An ``image`` leaf is a picture of its own: ``size`` caps how far it may measure
in either direction (at the interface zoom), and its ``value`` - or
``set_widget(tab, id, "image", ...)`` - is an image file's path or an SVG
document as text, the same two shapes [set_icon](api:tabs.set_icon) takes. A
picture that cannot be read leaves the leaf blank rather than showing the user a
path. A ``list``, ``tree`` or ``table`` entry can wear a mark as well: a list
item as ``{"text": ..., "icon": ...}``, a row as a dict with ``"icon"`` beside
``"cells"``, and that picture is drawn in front of the row's first cell - which
is how one list can carry a device's own mark next to its own colour.

## Where a tab lands

A tab an addon opens goes immediately to the left of the tab that addon already
owns, so a report or a helper sits beside its parent instead of at the far end
of the bar where the user has to go looking for it. Pass ``position`` to choose
otherwise.

## Pinned, icon-only tabs

``pinned=True`` holds the tab in the block at the right edge, beside Settings:
out of the row the user drags about, out of the saved tab order, and keeping its
place while other tabs come and go. ``icon_only=True`` draws it as a mark with
no caption - the name still identifies it to the addon, and the tooltip says it
aloud. Both work on an embedded page from
[add_web_tab](api:tabs.add_web_tab) as well, and can be changed afterwards with
[set_pinned](api:tabs.set_pinned). The addon store and the manager updater's own
tabs are made this way. A mark has nothing left but its tooltip to say who it
belongs to, so the app fills that in with the addon's name and its tab's - an
addon that asks to be drawn as a mark should send a picture of its own with it
(see [Notices and icons](guide:notice)).

## Pages added and taken away

A ``tabs`` container is not fixed at build time. ``set_widget(tab, id,
"add_page", {"label": "eth1", "spec": {...}})`` builds one more sub-page out of
the same spec language and puts it at the end - ``"position"`` says elsewhere,
``"current": False`` leaves the view where it is - ``"remove_page"`` takes one
away by index or by caption, and [widget_value](api:tabs.widget_value) with
``"pages"`` reads every caption back. A control that only ever exists while its
page does needs no cleaning up: taking the page away drops its controls out of
the tab's registry, and closes any terminal sitting on it.

## A terminal of your own

A ``terminal`` leaf is the app's own terminal screen - the widget that draws the
device's escape sequences and sends the keys typed into it - on a connection of
its own rather than the app's main session. So one tab can hold four of them and
four machines are being watched, each on its own thread, and closing one takes
nothing else down with it.

    {"kind": "terminal", "id": "shell", "on": ["ready", "closed", "failed"]}

    api.tabs.set_widget("My addon", "shell", "connect",
                        {"host": "192.168.1.1", "username": "root",
                         "password": typed})

``"disconnect"`` drops it, ``"command"`` types a line with the Enter added (a
list types several, so one field can reach every shell open), ``"send"`` types
raw bytes and ``"write"`` prints a note from the addon itself. Its ``value`` is
what is on the screen, ``"state"`` says what it is attached to - host, port,
username, and whether it is up; never the password - and ``"grid"`` its shape.
The screen says what happens to it: *Connecting to*, *Connected*, *SSH error:*
and *Connection closed.* are drawn where the user is looking, and the same news
arrives in ``on_widget`` as ``ready``, ``closed`` and ``failed`` - which is how a
sub-page can close itself the moment its link drops.

Because this is a connection of its own, the addon has to have asked for it: a
manifest with ``"requires": ["ssh"]`` and ``"connection": "own"``, and the same
switches [api.device.run](api:device.run) passes through. Reading a screen,
typing into one that is already up and letting go of one are never refused - the
permission was asked for once, when the connection was opened. A key file, if
the addon offers one, comes from [api.ui.pick_file](api:ui.pick_file); nothing is
uploaded behind anybody's back. A password is asked for in the same spirit:
[api.ui.input](api:ui.input) with ``secret=True`` draws dots while the user types
and answers ``None`` when the box is cancelled - and asking each time, rather than
keeping one in a config file that gets shared and backed up, is the design an
addon with several machines should reach for.

## Pages on the device

[add_web_tab](api:tabs.add_web_tab) embeds an address; from there
[set_html](api:tabs.set_html), [inject_css](api:tabs.inject_css) and
[inject_js](api:tabs.inject_js) shape what it shows, and
[view](api:tabs.view) hands over the whole page object - in an imported addon
only.
"""),

    ("notice", "Notices and icons", """
# Notices and icons

## Speaking when the tab is not on screen

    api.ui.popup("My addon", "the run finished - 3 packages installed")

[information](api:ui.information) stops the user with a dialog and wants an
answer; [popup](api:ui.popup) does neither. The app puts the line in its status
bar and, while **Pop up notifications** in Settings > Startup & defaults is on,
sends it to the system's notification area as well - which is how an addon can
report a long job to someone who has already gone back to another window.

The addon's own name is put on the title unless the addon wrote it in, so every
notice says who is talking: several addons sharing one notification area is only
readable if each line is named. The words always
reach the status line and the log, switch or no switch, so a notice that cannot
be shown is only quieter, never lost - and an addon has no way to reach the tray
or the window itself, because whether its words become a toast is the user's
setting rather than the addon's decision.

Use it for news the user would want even with the tab closed: a job that
finished, a device that stopped answering, an update that arrived. Anything that
only matters while they are watching belongs on the tab or in
[api.log](api:log). Keep it to a sentence or two - a notification is a nudge, and
the detail is one click away.

## Wearing a picture

One name, in either of two folders: the addon's id and then ``icon.png``, so
``mytool`` is worn by ``mytoolicon.png``. The file goes in an ``assets`` folder
beside the addon's own source, in a subfolder named after the addon -
``assets/mytool/mytoolicon.png`` under the folder the ``.py`` sits in, which for
a store addon is ``addon.store/assets/mytool/mytoolicon.png`` - and the program's
own ``assets/mytoolicon.png`` is tried too, which is where an addon shipped
inside the program keeps its picture. Neither name is written anywhere in the
addon's code: ``addon_common.find_addon_picture`` is the lookup that tries both
folders, and a square PNG - the marks the program ships are 128x128 - is what
the tab draws. A file named anything else is declared instead:

    ADDON = {..., "icon": "oldname.png"}

Nothing is ever required. A tab with no picture draws the SVG document it sends
with [set_icon](api:tabs.set_icon), and the store shows the program's own
``storedefaultaddon.png``, so an addon is never filed under a blank box and still
found by eye. A compiled build keeps its assets packed inside it and
[api.paths.assets](api:paths.assets) is where they are read from;
``addon_common.find_icon`` takes a folder and an id and is the same rule for an
addon drawing a picture into a widget of its own, and
[api.session.app_icon](api:session.app_icon) is the program's own mark when it
wants to sit beside it.
"""),

    ("settings-pages", "A page in Settings", """
# A page in Settings

``api.settings_pages`` builds a page of the app's Settings window: the same
controls as a tab, on a page that stands beside the program's own categories -
outside the shared **Installed addons** page, which keeps the switches and the
form built from the declared fields.

    api.settings_pages.add("planner", title="My planner", spec={
        "kind": "form",
        "children": [
            {"label": "Days ahead", "kind": "spin", "id": "days"},
        ],
    })

- The page is the app's furniture: it goes away by itself when the addon is
  switched off or removed, so an addon never has to clean it up.
- Its controls report to the same ``on_widget`` hook, with the page's name where
  a tab's would be.
- [activate](api:settings_pages.activate) opens Settings on the page,
  [set_title](api:settings_pages.set_title) renames it, and
  [widget](api:settings_pages.widget) / [set_widget](api:settings_pages.set_widget)
  and [widget_value](api:settings_pages.widget_value) read and write its
  controls exactly as a tab's.
- A page wears no icon: [set_icon](api:settings_pages.set_icon) says so and
  answers False.

For one or two switches use [api.ui.set_setting_widget](api:ui.set_setting_widget)
instead - the same controls, but under the addon's own row.
"""),

    ("themes", "Themes: the look of the app", """
# Themes: the look of the app

A look is one small JSON file - ``themes/<name>.theme``, beside the program. The
app reads the file it is drawn from when it starts, and writes
``themes/default.theme`` when the folder holds none, so there is always something
to copy and always something to fall back on.

    {
      "name": "deep sea",
      "author": "fligma",
      "blurb": "dark blues, soft edges",
      "colors": {"accent": "#2b8aa8", "base": "#0d1b2a"},
      "tabs": {"icon_px": 30, "radius_px": 8, "captions": false},
      "styles": "QToolTip { color: #e8f4f8; }"
    }

What the file does not say comes from the shipped look, so two colours in it make
a whole, usable theme. The three parts each own:

- ``colors`` - every colour the interface is painted with, by its token name.
- ``tabs`` - the shape of the tab bar: how big a tab's picture is, its air and
  corners, and whether it shows its name and its connection dots at all.
- ``styles`` - a few lines of sheet for whatever the first two do not reach.

## Who owns what

The theme owns the furniture; the addon owns its own page. A tab's icon size, its
colours and whether it carries a caption come from the theme and not from the
addon - [api.tabs.set_icon](api:tabs.set_icon) hands over a picture and the theme
decides how big it is drawn, so an addon that sizes its own mark is fighting the
look rather than wearing it.

What an addon does choose is the look of its tab's content, and the way to keep
that in step with the theme is to ask for the colours instead of writing them
down:

    colors = api.theme.colors()
    sheet = "QPushButton { background: %s; }" % colors["raise"]

## Reading and writing a look

- [names](api:theme.names), [exists](api:theme.exists) and
  [folder](api:theme.folder) are the files; ``folder()`` makes the folder on the
  way, so an addon always has somewhere to write.
- [read](api:theme.read) is one theme complete, every token filled in;
  [raw](api:theme.raw) is the file as written, which is what an editor shows.
- [colors](api:theme.colors) and [tabs](api:theme.tabs) are the two halves as
  dicts, [color](api:theme.color) one token by name, and
  [groups](api:theme.groups) and [token_knobs](api:theme.token_knobs) the same
  thing in the shapes an editor lists them in - colour blocks with their hints,
  and each tab knob marked as pixels or as a switch.
- [validate](api:theme.validate) says what is wrong with a candidate, one line
  each, before it is written; [save](api:theme.save) refuses anything it dislikes
  unless it is told to force.
- [use](api:theme.use) draws the window from a theme and keeps the choice for
  this profile. [copy](api:theme.copy), [rename](api:theme.rename) and
  [remove](api:theme.remove) move the files; the shipped ``default`` stays.
- [templates](api:theme.templates) are the looks a new theme may be begun from,
  and [template_text](api:theme.template_text) is one as the text of a file.
- [stylesheet](api:theme.stylesheet) is the whole sheet the app applies, and
  [sheet_of](api:theme.sheet_of) is what a draft still being edited would give.
- [reference](api:theme.reference) is this list, read off the running API, for an
  addon that wants to document the namespace the way the Theme Manager's Docs tab
  does.

## When the look moves

Applying a theme sends [theme_changed](api:events.on) with
``{"theme": worn, "wanted": asked}``: what the window is drawn from now, and the
name that was asked for, which differ only when that theme has no file. Anything
that painted itself with colours read earlier should read them again there; a tab
built from a spec and coloured by the app's own sheet needs nothing, because the
window repaints it under it.

The program's editor for all of this is the bundled **Theme Manager** addon. It
works on one draft at a time - a colour, a knob, or the JSON direct - and nothing
reaches the disk until Save, or the window until Apply, so a look can be ruined
without anyone noticing.
"""),

    ("config", "Config, settings and defaults", """
# Config, settings and defaults

Two stores, and picking the right one is most of it:

- [api.config](api:config.all) is the addon's own file,
  ``addons/_data/<id>/config.json``. It belongs to the installation rather than
  to one profile, is what the Settings form edits, and is a plain JSON file a
  user can open and change.
- [api.settings](api:settings.all) is the addon's private keys inside the
  profile's record - per profile, never shown to the user, good for counters
  and last-used values.

Declare what the user may change once, from ``init``:

    FIELDS = [{"key": "greeting", "label": "Text to show", "kind": "text",
               "hint": "What the button prints"}]
    DEFAULTS = {"greeting": "Hello"}

    values = api.config.declare(FIELDS, DEFAULTS, title="My addon")

``declare`` writes the file with the defaults when it is missing, keeps a side
copy of the schema so the form still shows while the addon is off, and returns
the merged values. Kinds: ``text``, ``number``, ``bool``, ``choice``, ``list``,
``multiline``. A dotted key - ``"window.width"`` - reads and writes inside that
section of the file.

- [get](api:config.get), [update](api:config.update) and
  [pop](api:config.pop) work on the merged view;
  [stored](api:config.stored) is the file with nothing filled in.
- [text](api:config.text) and [save_text](api:config.save_text) are the file as
  JSON, for an editor; anything invalid is refused.
- When the user changes the form the app calls ``on_config`` - rebuild what the
  tab shows there.
"""),

    ("events", "Events and hooks", """
# Events and hooks

    api.events.on("ssh_connected", lambda payload: ...)

[on](api:events.on) returns the callable that unregisters again;
[off](api:events.off) drops what the addon had for one event, and
[names](api:events.names) lists everything the app broadcasts. A listener runs on
the GUI thread and is caught if it raises - the failure is written to the log
against the addon's name rather than taking the window down.

The same message arrives two ways:

- ``on_event(name, payload)`` is told everything.
- ``on_<name>(payload)`` - ``on_ssh_connected`` - is told just that one.

Keep a hook short; it runs in the app's event loop. Longer work goes to
[api.schedule.after](api:schedule.after) or to a device call with
``block=False``, and anything that has to wait for the user goes through
[api.ui](api:ui.question) rather than a sleep.
"""),

    ("device", "The device: SSH and SFTP", """
# The device: SSH and SFTP

The manifest says which transports an addon needs, and both the app-wide switch
and the addon's own have to be on;
[is_connected](api:device.is_connected) says whether a call would go through
right now.

    api.device.run("cat /etc/openwrt_release")
    # {'code': 0, 'out': '...', 'err': ''}

Every call runs off the GUI thread. [run](api:device.run) waits and returns the
result; [run_async](api:device.run_async) hands it to ``on_done`` later and lets
the interface breathe - prefer that one in a button.

Files: [list](api:device.list), [exists](api:device.exists),
[stat](api:device.stat), [read_text](api:device.read_text),
[write_text](api:device.write_text), [append_text](api:device.append_text),
[mkdir](api:device.mkdir), [remove](api:device.remove),
[rename](api:device.rename), [upload](api:device.upload),
[download](api:device.download). Paths are relative to the profile's remote root
unless ``outside_root=True``, and a path is never allowed to delete the root
itself.

    with api.device.open() as conn:
        conn.run("opkg update")
        conn.run("opkg install foo")

[open](api:device.open) holds one session for several calls instead of making
one per call, and [shell_send](api:device.shell_send) types into the terminal
tab when the user should watch it happen.
"""),

    ("test", "Testing an addon", """
# Testing an addon

The Test panel runs ``addons/_tests/test_<id>.py`` as its own program, using the
recording stub in ``addon_client``: the addon is loaded and driven, nothing is
drawn, nothing is sent to a device, and every call it made is written down. So
the test checks what the addon *asked for*, which is the part an addon author
owns.

    api = addon_client.stub_api("myaddon", config={"greeting": "hi"})
    addon = addon_client.load_addon(SOURCE)
    addon.init(api)
    check(api.called("tabs.add_tab"), "init added no tab")
    addon.on_widget("My addon", "go", "clicked", {})
    check(any("hi" in line for line in api.logs), "the button said nothing")

- ``stub_api(id, config=..., settings=..., answers=...)``: ``config`` is what
  the config file would hold, ``settings`` the profile's keys, and ``answers``
  what ``ui.question`` / ``ui.choose`` reply.
- ``api.called(name)``, ``api.times(name)``, ``api.calls`` and ``api.logs`` are
  what the addon asked for. A stub records and changes no state of its own, so
  hand it the values the addon will read back.
- Check everything and print every failure: one run should say all is well, not
  stop at the first crack.
- The same script runs from the command line, which is what the store's index
  and the app's CI can do before an addon is published.
"""),

    ("build", "Compiling and publishing", """
# Compiling and publishing

**Compile** builds the addon with PyInstaller into ``addons/<id>.exe`` beside
its source, from a generated launcher that calls ``addon_client.serve``. The id
does not change, so exactly one of the two copies runs, and *Which copy runs*
above says which; the choice is kept in ``config/_build.json``.

A build carries a plain copy of the protocol modules it imports
(``addon_common``, ``addon_client``). A program built without that copy says so
rather than start a test that cannot finish.

The picture a program is told to wear follows the rule above: the addon's own
``<id>icon.png`` when it brought one, Berry Manager's mark otherwise. On Windows
either is rewrapped as an ``.ico`` first, because PyInstaller converts a PNG only
when Pillow happens to be installed and this project never asks for it. A
picture that cannot be made to fit costs a build its icon and not its program,
and an asset that will not download is said and left out of the install.

## Publishing on the addon store

The store reads one or more GitHub repositories, each with an ``index.json`` in
its store folder:

- ``tools/gen_addon_store_index.py`` writes the index from the addons in a
  checkout - their manifests, their icons, their test scripts.
- ``addons/_data/<id>/config.json`` and ``fields.json`` travel with the file, so
  an installed addon shows a settings form of its own.
- A source is a repository: owner, repository, branch or tag, and the store
  folder inside it. **Sources...** on the store's tab adds one, puts it first or
  drops it, and the first source wins an id that two of them publish.

Installing fetches the addon from the source its catalog row came out of, then
runs its test script against the harness before the app starts it.
"""),
)


# --- the API reference: read off the protocol module as it runs ---

#: Which guide an addon should read after each namespace, by the attribute the
#: namespace sits on. ``""`` is ``api`` itself, ``conn`` the connection object.
GUIDE_FOR_NAMESPACE = {
    "": "anatomy",
    "session": "start",
    "device": "device",
    "ui": "notice",
    "tabs": "tabs",
    "events": "events",
    "settings": "config",
    "settings_pages": "settings-pages",
    "theme": "themes",
    "config": "config",
    "schedule": "anatomy",
    "paths": "anatomy",
    "peers": "anatomy",
    "conn": "device",
}

#: The reference is built once per run: the module cannot change under a window
#: that is open, and rebuilding it walks a hundred docstrings for nothing.
_API_REFERENCE: list[dict] | None = None

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^) ]+)\)")
_CODE_RE = re.compile(r"``([^`]+)``")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _escape(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _inline(text: Any) -> str:
    """The inline markup a guide or a docstring is allowed to use."""
    out = _escape(text)
    out = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    return _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)


def _html(text: Any) -> str:
    """A page out of the small slice of markdown the guides and docstrings are
    written in: headings, bullets, indented code and ``inline`` markup."""
    out: list[str] = []
    bullets: list[str] = []
    indented: list[str] = []

    def close() -> None:
        if bullets:
            out.append("<ul>" + "".join(bullets) + "</ul>")
            bullets.clear()
        if indented:
            out.append("<pre>" + "\n".join(indented) + "</pre>")
            indented.clear()

    for raw in str(text or "").expandtabs().rstrip().splitlines():
        if raw.startswith(("    ", "\t")):
            # An indented run is code: keep it, and drop the list that was open.
            if bullets:
                out.append("<ul>" + "".join(bullets) + "</ul>")
                bullets.clear()
            indented.append(_escape(raw.strip()))
            continue
        close()
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            bullets.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if line.startswith("# "):
            out.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("## "):
            out.append(f"<h3>{_inline(line[3:])}</h3>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    close()
    return "\n".join(out)


def _addon_api_module() -> Any:
    """The protocol module the app itself runs, or ``None`` when it cannot be had."""
    try:
        import addon_api
    except Exception:                            # a program without its protocol
        return None
    return addon_api


def _api_namespaces() -> list[tuple[str, str]]:
    """``(attribute, class name)`` for every namespace on api, in the order the
    app builds them.

    Read out of ``AddonAPI.__init__``'s bytecode rather than its text: a packaged
    program carries no source to search, and the instructions are the same in a
    checkout and in a build.
    """
    module = _addon_api_module()
    if module is None:
        return []
    classes = {name for name, value in vars(module).items()
               if inspect.isclass(value) and name.endswith("API")}
    found: list[tuple[str, str]] = []
    wanted = ""
    for instruction in dis.get_instructions(module.AddonAPI.__init__):
        if instruction.opname == "LOAD_GLOBAL":
            wanted = str(instruction.argval or "")
        elif instruction.opname == "STORE_ATTR":
            if wanted in classes and wanted not in {one[1] for one in found}:
                found.append((str(instruction.argval), wanted))
            wanted = ""
    return found


def _signature_of(func: Any) -> str:
    """The call as an addon writes it, with the hidden ``self`` taken out."""
    try:
        text = str(inspect.signature(func))
    except (TypeError, ValueError):                # a built-in has no signature
        return "(...)"
    if text.startswith("(self, "):
        return "(" + text[len("(self, "):]
    if text.startswith("(self)"):
        return "()" + text[len("(self)"):]
    return text


def _api_entry(name: str, func: Any, kind: str) -> dict:
    """One method or property, with the summary lifted from its docstring."""
    doc = inspect.getdoc(func) or ""
    lines = [line.rstrip() for line in doc.splitlines()]
    return {
        "name": name,
        "kind": kind,
        "call": f"{name}{_signature_of(func)}" if kind == "method" else name,
        "summary": lines[0] if lines else "(nothing said about this one yet)",
        "detail": "\n".join(lines[1:]).strip(),
    }


def _namespace_page(addr: str, title: str, cls: Any) -> dict:
    """One namespace: its methods and properties, in the order the class defines
    them, with the private machinery left out."""
    entries: list[dict] = []
    for name, value in vars(cls).items():
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, property):
            if value.fget is None or name.startswith("_"):
                continue
            entries.append(_api_entry(name, value.fget, "property"))
        elif inspect.isfunction(value):
            if name.startswith("_"):
                continue
            entries.append(_api_entry(name, value, "method"))
    lines = (inspect.getdoc(cls) or "").splitlines()
    return {"addr": addr, "title": title, "cls": cls.__name__,
            "summary": lines[0].strip() if lines else "", "entries": entries}


def api_reference() -> list[dict]:
    """The whole API as the Docs panel lays it out.

    ``api`` itself first - it has three calls of its own - then every namespace
    in the order the app builds them, then the connection object that
    ``api.device.open()`` hands out, which an addon reads like any namespace.
    """
    global _API_REFERENCE
    if _API_REFERENCE is not None:
        return _API_REFERENCE
    module = _addon_api_module()
    pages: list[dict] = []
    if module is not None:
        pages.append(_namespace_page("", "api", module.AddonAPI))
        for attr, cls_name in _api_namespaces():
            cls = getattr(module, cls_name, None)
            if inspect.isclass(cls):
                pages.append(_namespace_page(attr, f"api.{attr}", cls))
        connection = getattr(module, "Connection", None)
        if inspect.isclass(connection):
            pages.append(_namespace_page(
                "conn", "connection - from api.device.open()", connection))
    _API_REFERENCE = pages
    return pages


def api_entry(key: str) -> tuple[dict, dict] | None:
    """``(namespace, entry)`` for a reference key such as ``tabs.add_tab``.

    A key with no dot is one of the calls on ``api`` itself (``log``, ``error``,
    ``call``); when nothing at that level answers to it, the namespaces are
    tried in order, so a link whose prefix was left off still opens a page.
    """
    head, sep, tail = str(key).partition(".")
    pages = api_reference()
    wanted = tail if sep else head
    if sep:
        ordered = [page for page in pages if page["addr"] == head]
    else:
        ordered = ([page for page in pages if page["addr"] == ""]
                   + [page for page in pages if page["addr"] != ""])
    for page in ordered:
        for entry in page["entries"]:
            if entry["name"] == wanted:
                return page, entry
    return None


def api_counts() -> tuple[int, int]:
    """How much of the API the panel has to show: namespaces and entries."""
    pages = api_reference()
    return len(pages), sum(len(page["entries"]) for page in pages)


# --- the tab itself ---

class AddonDevWidget(QWidget):
    """The Addon Dev panel: one addon selected at a time, six views of it."""

    #: What 'find' in the Docs pane reads beyond the row's own label: the key,
    #: the summary and the call, kept in one lower-case string per row.
    SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.mgr = window.addons
        self.selected = ""
        self.build: _Stream | None = None
        self.test: _Stream | None = None
        self._loading = False
        # The Docs tree, by the key each line carries, and the folds the tree had
        # before 'find' opened them. Both are filled by _fill_docs.
        self._docs_items: dict[str, QTreeWidgetItem] = {}
        self._docs_folds: dict[str, bool] | None = None

        # A table, five panes and many notes do not fit a short window, so all
        # of it lives in one scroll area and takes the height it needs.
        self.body = QWidget()
        root = QVBoxLayout(self.body)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_index())

        self.panes = QTabWidget()
        self.panes.setMinimumHeight(PANES_MIN_H)
        self.panes.addTab(self._build_details(), "Details")
        self.panes.addTab(self._build_compile(), "Compile")
        self.panes.addTab(self._build_test(), "Test")
        self.panes.addTab(self._build_docs(), "Docs")
        self.panes.addTab(self._build_log(), "Log")
        self.panes.addTab(self._build_processes(), "Processes")
        root.addWidget(self.panes, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.scroll)

        # One short timer for the child processes, one longer for the tables:
        # a build's output should feel live, a table list does not. The short
        # one only earns its keep while a child is actually running (see
        # _watch_streams), so a tab that builds nothing ticks for nothing.
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(400)
        self.poll_timer.timeout.connect(self._poll_streams)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh)

    # --- closing ---

    def shutdown(self) -> None:
        """The app is going: no build or test child, nor anything it spawned,
        may be left running behind it."""
        self.poll_timer.stop()
        for stream in (self.build, self.test):
            if stream is not None:
                stream.kill()

    # --- the widgets ---

    def _build_index(self) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.table = make_table(
            ["Addon", "Name", "Version", "Runs", "State", "Source"],
            stretch=1, widths={5: 260})
        self.table.itemSelectionChanged.connect(self._selected_row)
        fit_rows(self.table)
        column.addWidget(self.table, 0)

        row = QHBoxLayout()
        self.enabled_switch = QCheckBox("Enabled")
        self.enabled_switch.setToolTip(
            "The same switch as Settings > Addons: it starts the addon and keeps "
            "its tabs, or stops it and takes them away")
        self.enabled_switch.toggled.connect(self._set_enabled)
        for text, tip, slot in (
            ("Restart", "Load the file again, without restarting the app", self.mgr_restart),
            ("Show tab", "Jump to the tab this addon added", self.show_tab),
            ("Open source", "Edit the addon file itself", self.open_source),
            ("Rescan", "Read the addons folder again", self.rescan),
            ("New addon...", "Write a new addon, with a header and a test script",
             self.new_addon),
            ("Open folder", "The addons folder, where an addon is dropped", self.open_folder),
            ("Docs", "The API reference and the guides, read off the running app",
             self.show_docs),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addWidget(self.enabled_switch)
        row.addStretch(1)
        self.state_note = QLabel("")
        self.state_note.setStyleSheet("color: #8f8f8f;")
        row.addWidget(self.state_note, 1)
        column.addLayout(row)
        return holder

    def _build_details(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        box = QGroupBox("Addon details")
        tip_on_title(box,
                     "The manifest the app shows in Settings > Addons. It is saved "
                     "into the ADDON dict of the addon's own file when the file has "
                     "one, and otherwise into the <id>.json manifest beside it.")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 10, 12, 8)
        inner.setSpacing(6)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        self.id_label = QLabel("-")
        self.id_label.setStyleSheet("color: #aaaaaa;")
        self.name_input = QLineEdit()
        self.version_input = QLineEdit()
        self.author_input = QLineEdit()
        self.description_input = QLineEdit()
        self.notes_input = QLineEdit()
        self.api_input = QLineEdit()
        self.api_input.setPlaceholderText("1")
        self.api_input.setFixedWidth(70)
        for label, field in (("Name", self.name_input), ("Version", self.version_input),
                             ("Author", self.author_input),
                             ("Description", self.description_input),
                             ("Notes", self.notes_input), ("API", self.api_input)):
            form.addRow(label, field)
        inner.addLayout(form)
        self.target_note = note_label(QLabel(""))
        inner.addWidget(self.target_note)
        row = QHBoxLayout()
        for text, tip, slot in (
            ("Save details", "Write the manifest back to the file named above",
             self.save_details),
            ("Reload", "Fill the form again from what is on disk", self.reload_details),
            ("Open manifest", "Edit it outside Berry Manager", self.open_manifest),
            ("Open source", "The addon's own code", self.open_source),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.details_note = QLabel("")
        row.addWidget(self.details_note, 1)
        inner.addLayout(row)
        layout.addWidget(box)

        config_box = QGroupBox("Config file")
        tip_on_title(config_box,
                     "addons/_data/<id>/config.json: this addon's own settings, shared "
                     "by every profile. Written as plain JSON, so the addon can read it "
                     "and a user can edit it. The Addons section in Settings offers "
                     "the same thing as a form, once the addon has declared its keys.")
        column = QVBoxLayout(config_box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        self.config_path = note_label(QLabel(""), "#aaaaaa")
        self.config_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.config_path)
        self.config_edit = monospace(QPlainTextEdit())
        self.config_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        column.addWidget(self.config_edit, 1)
        row = QHBoxLayout()
        for text, tip, slot in (
            ("Save config", "Write the JSON above, and tell the addon it changed",
             self.save_config),
            ("Reload", "Read the file again, dropping unsaved edits", self.reload_config),
            ("Open file", "Edit it outside Berry Manager", self.open_config),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.config_note = QLabel("")
        row.addWidget(self.config_note, 1)
        column.addLayout(row)
        layout.addWidget(config_box, 1)
        return page

    def _build_compile(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        which = QGroupBox("Which copy runs")
        tip_on_title(which,
                     "A compiled addon keeps the same id as the source it was built "
                     "from, so exactly one of the two is started. The choice is kept "
                     "in config/_build.json.")
        row = QHBoxLayout(which)
        row.setContentsMargins(14, 10, 12, 8)
        row.setSpacing(14)
        self.source_radio = QRadioButton("the source (.py)")
        self.compiled_radio = QRadioButton("the compiled program")
        self.source_radio.setToolTip("Import the .py file into the app")
        self.compiled_radio.setToolTip("Start the built program as its own process")
        for button in (self.source_radio, self.compiled_radio):
            button.toggled.connect(self._prefer_changed)
            row.addWidget(button)
        row.addStretch(1)
        layout.addWidget(which)
        self.runs_note = note_label(QLabel(""))
        layout.addWidget(self.runs_note)

        box = QGroupBox("Compile to a release")
        tip_on_title(box,
                     "PyInstaller is started for this one addon with a generated "
                     "launcher that calls addon_client.serve. The staging folder is "
                     "addons/_build/<id> and the program lands beside the source, so "
                     "the runtime finds it under the same id.")
        column = QVBoxLayout(box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        options = QHBoxLayout()
        self.onefile_check = QCheckBox("one file")
        self.onefile_check.setChecked(True)
        self.onefile_check.setToolTip("--onefile; uncheck for a folder with the "
                                       "program and its libraries")
        self.console_check = QCheckBox("console")
        self.console_check.setChecked(True)
        self.console_check.setToolTip(
            "Keep the standard streams. The app starts an addon with no window "
            "either way, and a build without a console may lose the protocol pipe")
        self.icon_check = QCheckBox("app icon")
        self.icon_check.setChecked(True)
        self.icon_check.setToolTip("Use Berry Manager's icon, redrawn as an .ico "
                                   "on Windows when the shipped picture is a PNG")
        self.prefer_check = QCheckBox("run the compiled copy when it is ready")
        for button in (self.onefile_check, self.console_check, self.icon_check,
                       self.prefer_check):
            options.addWidget(button)
        options.addStretch(1)
        column.addLayout(options)

        extra = QHBoxLayout()
        extra.addWidget(QLabel("extra modules"))
        self.hidden_input = QLineEdit()
        self.hidden_input.setPlaceholderText(
            "anything the addon imports that PyInstaller cannot see, e.g. yaml, mylib")
        extra.addWidget(self.hidden_input, 1)
        column.addLayout(extra)

        actions = QHBoxLayout()
        self.compile_button = QPushButton("Compile addon")
        self.compile_button.setObjectName("primaryAction")
        self.compile_button.setToolTip("Start PyInstaller for this addon")
        self.compile_button.clicked.connect(self.compile_addon)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the build that is running")
        self.cancel_button.clicked.connect(self.cancel_build)
        self.cancel_button.setEnabled(False)
        self.clean_button = QPushButton("Clean staging")
        self.clean_button.setToolTip("Throw away addons/_build for this addon")
        self.clean_button.clicked.connect(self.clean_staging)
        self.reveal_button = QPushButton("Show the file")
        self.reveal_button.setToolTip("Open the addons folder, where the program is put")
        self.reveal_button.clicked.connect(self.open_folder)
        self.build_guide_button = QPushButton("Build guide")
        self.build_guide_button.setToolTip(
            "What a compiled addon carries, and how to publish it on the store")
        self.build_guide_button.clicked.connect(lambda: self._open_guide("build"))
        for button in (self.compile_button, self.cancel_button, self.clean_button,
                       self.reveal_button, self.build_guide_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.build_note = QLabel("")
        actions.addWidget(self.build_note, 1)
        column.addLayout(actions)

        self.build_log = monospace(QPlainTextEdit())
        self.build_log.setReadOnly(True)
        self.build_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        column.addWidget(self.build_log, 1)
        layout.addWidget(box, 1)
        return page

    def _build_test(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        box = QGroupBox("Test script")
        tip_on_title(box,
                     "addons/_tests/test_<id>.py: the addon is loaded and driven "
                     "through the recording stub in addon_client, so the test works "
                     "with no window open and no device connected. It prints every "
                     "call the addon made and fails on the checks it did not pass.")
        column = QVBoxLayout(box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        self.test_path = note_label(QLabel(""), "#aaaaaa")
        self.test_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.test_path)
        row = QHBoxLayout()
        self.create_test_button = QPushButton("Create test script")
        self.create_test_button.setToolTip("Write the template for this addon")
        self.create_test_button.clicked.connect(self.create_test)
        self.run_test_button = QPushButton("Run test")
        self.run_test_button.setObjectName("primaryAction")
        self.run_test_button.setToolTip("Run it with this interpreter and show its output")
        self.run_test_button.clicked.connect(self.run_test)
        self.open_test_button = QPushButton("Open test")
        self.open_test_button.setToolTip("Edit it outside Berry Manager")
        self.open_test_button.clicked.connect(self.open_test)
        self.test_folder_button = QPushButton("Open folder")
        self.test_folder_button.setToolTip("addons/_tests")
        self.test_folder_button.clicked.connect(self.open_test_folder)
        self.test_guide_button = QPushButton("Testing guide")
        self.test_guide_button.setToolTip(
            "What the stub records, and the checks a test script should make")
        self.test_guide_button.clicked.connect(lambda: self._open_guide("test"))
        for button in (self.create_test_button, self.run_test_button,
                       self.open_test_button, self.test_folder_button,
                       self.test_guide_button):
            row.addWidget(button)
        row.addStretch(1)
        self.test_note = QLabel("")
        row.addWidget(self.test_note, 1)
        column.addLayout(row)
        layout.addWidget(box)
        self.test_log = monospace(QPlainTextEdit())
        self.test_log.setReadOnly(True)
        self.test_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.test_log, 1)
        return page

    def _build_log(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("filter"))
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("only lines containing this")
        self.log_filter.textChanged.connect(lambda _=False: self.refresh_log())
        row.addWidget(self.log_filter, 1)
        self.log_warnings = QCheckBox("errors only")
        self.log_warnings.toggled.connect(lambda _=False: self.refresh_log())
        row.addWidget(self.log_warnings)
        self.log_follow = QCheckBox("follow")
        self.log_follow.setToolTip("Read the log again on every refresh")
        self.log_follow.setChecked(True)
        row.addWidget(self.log_follow)
        for text, tip, slot in (
            ("Refresh", "Read the log file again", self.refresh_log),
            ("Reset", "Start over from the end of the file", self.reset_log),
            ("Open log", "The whole log, in the system's text viewer", self.open_log),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.log_view = monospace(QPlainTextEdit())
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view, 1)
        note = QLabel(f"the lines the addon wrote with api.log, taken from {LOG_FILE}")
        note.setStyleSheet("color: #8f8f8f;")
        layout.addWidget(note)
        return page

    def _build_processes(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        first = QGroupBox("Addon processes")
        tip_on_title(first,
                     "Every addon the app started as a child program. An addon that "
                     "closed by itself keeps its exit code here.")
        column = QVBoxLayout(first)
        column.setContentsMargins(12, 10, 12, 8)
        self.process_table = make_table(
            ["Addon", "pid", "State", "Exit", "Uptime", "Requests", "Command"],
            stretch=6)
        column.addWidget(self.process_table)
        self.thread_note = note_label(QLabel(""))
        column.addWidget(self.thread_note)
        layout.addWidget(first, 1)

        second = QGroupBox("What the app holds for each addon")
        tip_on_title(second,
                     "Tabs, timers, menu entries and event listeners the app created "
                     "for an addon. They are all taken away again when the addon is "
                     "switched off, which is what this list is for checking.")
        column = QVBoxLayout(second)
        column.setContentsMargins(12, 10, 12, 8)
        self.holdings_table = make_table(
            ["Addon", "Kind", "Tabs", "Timers", "Menu", "Listeners"],
            stretch=5)
        column.addWidget(self.holdings_table)
        layout.addWidget(second, 1)
        return page

    # --- docs: the guides and the reference generated from the code ---

    def _build_docs(self) -> QWidget:
        page = QWidget()
        self.docs_pane = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        pages, entries = api_counts()
        self.docs_note = note_label(QLabel(
            f"{max(0, pages - 1)} namespaces, {entries} methods and properties, read "
            "from addon_api as this program runs it - and the docstrings under "
            "them are the whole story, so the panel cannot go out of date."))
        layout.addWidget(self.docs_note)

        row = QHBoxLayout()
        row.addWidget(QLabel("find"))
        self.docs_filter = QLineEdit()
        self.docs_filter.setPlaceholderText("tabs, pinned, config, peers...")
        self.docs_filter.textChanged.connect(self._filter_docs)
        row.addWidget(self.docs_filter, 1)
        for text, tip, slot in (
            ("Start here", "The first page an addon author should read",
             lambda: self._show_docs_key("guide:start")),
            ("README", "The program's own manual, in the system's viewer or online",
             self.open_readme),
            ("On GitHub", "The manual as published, with the addon chapters",
             self.open_online_docs),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        split = QSplitter()
        self.docs_tree = QTreeWidget()
        self.docs_tree.setHeaderHidden(True)
        self.docs_tree.setColumnCount(1)
        self.docs_tree.setRootIsDecorated(True)
        self.docs_tree.setIndentation(DOCS_INDENT)
        self.docs_tree.setUniformRowHeights(True)
        self.docs_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.docs_tree.currentItemChanged.connect(self._docs_moved)
        self.docs_tree.setMinimumWidth(DOCS_MIN_W)
        split.addWidget(self.docs_tree)
        self.docs_view = QTextBrowser()
        self.docs_view.setOpenLinks(False)
        self.docs_view.setMinimumWidth(380)
        # QTextBrowser wraps to its own width and takes html, which is what the
        # generated pages are; the links below are handled by hand.
        self.docs_view.anchorClicked.connect(self._docs_link)
        split.addWidget(self.docs_view)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 620])
        layout.addWidget(split, 1)

        self._fill_docs()
        return page

    def _add_docs_branch(self, key: str, text: str, tip: str,
                         expanded: bool = False) -> QTreeWidgetItem:
        """A group in the tree: it opens a page of its own and holds the lines
        under it, the way a folder holds the files it is named for.

        Bold and in the theme's accent, so the eye sorts the tree into its
        namespaces before it reads a single method name.
        """
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setToolTip(0, tip)
        item.setExpanded(expanded)
        item.setForeground(0, QBrush(QColor(
            manager_theme.color("accent", default="#c51a4a"))))
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.docs_tree.addTopLevelItem(item)
        self._docs_items[key] = item
        return item

    def _add_docs_leaf(self, branch: QTreeWidgetItem, key: str, text: str,
                       tip: str = "", search: str = "") -> QTreeWidgetItem:
        """A page in the tree, addressed by the same key its links carry."""
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setToolTip(0, tip)
        # What 'find' reads: the key and the call as well as the label and the
        # first line, so typing 'pinned' still turns up add_tab, whose signature
        # is the place the option is actually spelled out.
        item.setData(0, self.SEARCH_ROLE,
                     f"{key} {text} {tip} {search}".casefold())
        branch.addChild(item)
        self._docs_items[key] = item
        return item

    def _docs_branches(self) -> list[QTreeWidgetItem]:
        """Every group in the tree, in the order the tree shows them."""
        return [one for one in (self.docs_tree.topLevelItem(index)
                                for index in range(
                                    self.docs_tree.topLevelItemCount()))
                if one is not None]

    def _fill_docs(self) -> None:
        """The tree down the left: the guides first, then one group per
        namespace, every method and property a line under the name it is
        actually called through."""
        self.docs_tree.blockSignals(True)
        self.docs_tree.clear()
        self._docs_items = {}
        guides = self._add_docs_branch(
            "page:guides", f"Guides ({len(GUIDES)})",
            "The pages that read like a manual, from the first addon to the "
            "last option.", expanded=True)
        for key, title, text in GUIDES:
            self._add_docs_leaf(
                guides, f"guide:{key}", title,
                text.strip().splitlines()[0] if text.strip() else title,
                search=text)
        for page in api_reference():
            branch = self._add_docs_branch(
                f"page:{page['addr'] or 'api'}",
                f"{page['title']} ({len(page['entries'])})",
                page["summary"] or "The calls of this namespace.")
            for entry in page["entries"]:
                addr = f"{page['addr']}." if page["addr"] else ""
                self._add_docs_leaf(branch, f"api:{addr}{entry['name']}",
                                    entry["name"], entry["summary"],
                                    search=entry["call"])
        self.docs_tree.blockSignals(False)
        self._show_docs_key("guide:start")

    def _filter_docs(self, text: str) -> None:
        """Only the pages that answer to what was typed, and the groups that
        still hold one; a group opens itself so a hit is never folded away.

        Every word typed has to turn up somewhere in the line - label, summary
        or call - which lets 'tabs pinned' narrow the tree to the one entry
        asked for. Emptying the box folds the groups back as they were, since
        the opening was the filter's doing and not whoever was reading.
        """
        words = str(text or "").strip().casefold().split()
        if words and self._docs_folds is None:
            self._docs_folds = {self._branch_key(one): one.isExpanded()
                                for one in self._docs_branches()}
        folds = self._docs_folds or {}
        self.docs_tree.blockSignals(True)
        for branch in self._docs_branches():
            hits = 0
            for index in range(branch.childCount()):
                leaf = branch.child(index)
                if leaf is None:
                    continue
                haystack = str(leaf.data(0, self.SEARCH_ROLE) or "")
                match = all(word in haystack for word in words)
                leaf.setHidden(not match)
                hits += 1 if match else 0
            branch.setHidden(bool(words) and not hits)
            branch.setExpanded(folds.get(self._branch_key(branch), True)
                               if not words else hits > 0)
        self.docs_tree.blockSignals(False)
        if not words:
            self._docs_folds = None

    @staticmethod
    def _branch_key(item: QTreeWidgetItem) -> str:
        """The key a line of the tree is addressed by, empty for none."""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _show_docs_key(self, key: str) -> bool:
        """Jump the tree to one page, by the key its links carry: its group is
        opened, the line is picked and the page is scrolled into view."""
        item = self._docs_items.get(str(key))
        if item is None:
            return False
        branch = item.parent()
        self.docs_tree.blockSignals(True)
        if branch is not None:
            branch.setHidden(False)
            branch.setExpanded(True)
        self.docs_tree.setCurrentItem(item)
        self.docs_tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.docs_tree.blockSignals(False)
        self._render_docs(str(key))
        return True

    def _docs_moved(self, current: QTreeWidgetItem | None,
                   _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        self._render_docs(self._branch_key(current))

    def _open_guide(self, key: str) -> None:
        """Come to the Docs pane and show one guide: the way a Test or Compile
        pane hands a developer the page that explains it."""
        self.panes.setCurrentWidget(self.docs_pane)
        self.docs_filter.blockSignals(True)
        self.docs_filter.setText("")
        self.docs_filter.blockSignals(False)
        self._show_docs_key(f"guide:{key}")

    def _docs_link(self, url: QUrl) -> None:
        """A link clicked in the page: inside the panel, into a file, or out to
        the browser - the panel itself never leaves the tab."""
        text = url.toString()
        if text.startswith(("guide:", "api:")):
            self._show_docs_key(text)
            return
        if text.startswith("file:"):
            self.open_path(Path(text[len("file:"):]))
            return
        QDesktopServices.openUrl(url)

    def _readme_link(self) -> str:
        """Where the manual is: the file beside the program, or its page online."""
        where = find_asset("README.md")
        return f"file:{where.as_posix()}" if where is not None else README_URL

    def _guide_page(self, key: str) -> str:
        """One guide, with the way back out at the bottom."""
        text = next((body for one, _title, body in GUIDES if one == key), "")
        back = ('<p><a href="guide:start">Start here</a> \u00b7 '
                '<a href="api:tabs.add_tab">api.tabs.add_tab</a> \u00b7 '
                f'<a href="{self._readme_link()}">the full README</a></p>')
        return _html(text) + back

    def _entry_page(self, key: str) -> str:
        """One method or property: how it is called, what its docstring says, and
        the neighbours an addon would read next to it."""
        found = api_entry(key)
        if found is None:
            return "<p>nothing in the API answers to that.</p>"
        page, entry = found
        addr = f"api.{page['addr']}" if page["addr"] else "api"
        usage = f"{addr}.{entry['call']}" if entry["kind"] == "method" \
            else f"{addr}.{entry['name']}"
        names = [one["name"] for one in page["entries"]]
        where = names.index(entry["name"])
        parts = [f"<h2>{_escape(entry['name'])}</h2>",
                 f"<p><i>{_escape(page['title'])} \u00b7 {entry['kind']}</i></p>",
                 f"<pre>{_escape(usage)}</pre>",
                 f"<p>{_inline(entry['summary'])}</p>"]
        if entry["detail"]:
            parts.append(_html(entry["detail"]))
        walk = []
        if where:
            previous = names[where - 1]
            walk.append(f'<a href="api:{page["addr"] + "." if page["addr"] else ""}{previous}">'
                        f'\u2190 {previous}</a>')
        if where + 1 < len(names):
            following = names[where + 1]
            walk.append(f'<a href="api:{page["addr"] + "." if page["addr"] else ""}{following}">'
                        f'{following} \u2192</a>')
        guide = GUIDE_FOR_NAMESPACE.get(page["addr"])
        if guide:
            title = next((one for one, text, _body in GUIDES if one == guide), "")
            walk.append(f'<a href="guide:{guide}">{_escape(title)}</a>')
        if walk:
            parts.append("<p>" + " \u00b7 ".join(walk) + "</p>")
        return "".join(parts)

    def _branch_page(self, key: str) -> str:
        """The page a group shows: every line under it, linked, so the tree can
        be read from either side of the split."""
        branch = self._docs_items.get(key)
        if branch is None:
            return "<p>nothing in the docs answers to that.</p>"
        parts = [f"<h2>{_escape(branch.text(0))}</h2>"]
        note = str(branch.toolTip(0) or "")
        if note:
            parts.append(f"<p>{_inline(note)}</p>")
        lines = []
        for index in range(branch.childCount()):
            leaf = branch.child(index)
            label = leaf.text(0)
            summary = str(leaf.toolTip(0) or "")
            if len(summary) > 96:
                summary = summary[:96].rsplit(" ", 1)[0] + "\u2026"
            lines.append(
                f'<li><a href="{self._branch_key(leaf)}">{_escape(label)}</a>'
                + (f" \u2014 {_inline(summary)}" if summary else "") + "</li>")
        parts.append("<ul>" + "".join(lines) + "</ul>")
        return "".join(parts)

    def _render_docs(self, key: str) -> None:
        kind, _, rest = str(key).partition(":")
        if not rest:
            return
        if kind == "guide":
            page = self._guide_page(rest)
        elif kind == "api":
            page = self._entry_page(rest)
        else:
            page = self._branch_page(key)
        self.docs_view.setHtml(page)

    def show_docs(self) -> None:
        """The Docs button above: come to this pane and read the selected addon."""
        self.panes.setCurrentWidget(self.docs_pane)

    def open_readme(self) -> None:
        """The manual: the file when the program carries one, the published page
        when it does not - a packaged build ships no markdown."""
        where = find_asset("README.md")
        if where is not None:
            self.open_path(where)
            return
        QDesktopServices.openUrl(QUrl(README_URL))

    def open_online_docs(self) -> None:
        QDesktopServices.openUrl(QUrl(README_URL + "#addons"))

    # --- the index ---

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh_timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.refresh_timer.stop()
        super().hideEvent(event)

    def current(self) -> Any:
        return self.mgr.find(self.selected)

    def refresh(self) -> None:
        """Re-read the runtime, keeping the selection where it was."""
        wanted = self.selected
        rows = self.mgr.describe_all()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            process = row.get("process") or {}
            state = "failed" if row["error"] else ("running" if row["loaded"] else "off")
            runs = ("process" if row["kind"] == "process" else "imported")
            if row.get("builtin"):
                # Came with the program, not dropped in.
                runs += " shipped"
            if process:
                runs += f" pid {process.get('pid') or '?'}"
            values = (row["id"], row["name"], row["version"], runs, state,
                      row["source"])
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if column == 4:
                    item.setForeground(state_color(state))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.table.setItem(index, column, item)
        self.table.blockSignals(False)
        fit_rows(self.table)
        target = wanted if any(row["id"] == wanted for row in rows) \
            else (rows[0]["id"] if rows else "")
        if target and target == self.selected:
            # Same addon: only the parts that change on their own are touched.
            self.touch()
        else:
            self.select(target)
        self.refresh_processes(rows)

    def touch(self) -> None:
        addon = self.current()
        if addon is None:
            return
        info = self.mgr.describe(addon)
        self.state_note.setText(
            f"{info['id']} - {human_size(info['size'])}, "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(info['modified']))}")
        self.details_note.setText(info["error"] or "")
        self.details_note.setStyleSheet("color: #ffb020;" if info["error"] else "")
        self._load_run_panels(info, addon)
        if self.log_follow.isChecked():
            self.refresh_log()

    def select(self, addon_id: str) -> None:
        if addon_id and addon_id != self.selected:
            self.selected = addon_id
            for index in range(self.table.rowCount()):
                item = self.table.item(index, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == addon_id:
                    self.table.selectRow(index)
                    break
            self.load_panels()
        elif not addon_id:
            self.selected = ""
            self.load_panels()

    def _selected_row(self) -> None:
        index = self.table.currentRow()
        if index < 0:
            return
        item = self.table.item(index, 0)
        if item is None:
            return
        # The id travels in the first column as data, not as its text.
        addon_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        if addon_id != self.selected:
            self.selected = addon_id
            self.load_panels()

    def load_panels(self) -> None:
        """Fill every panel from the selected addon, without editing anything."""
        addon = self.current()
        self._loading = True
        try:
            if addon is None:
                self._clear_panels()
                return
            info = self.mgr.describe(addon)
            self.state_note.setText(
                f"{info['id']} - {human_size(info['size'])}, "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(info['modified']))}")
            self.id_label.setText(f"{info['name']}  ({info['id']})")
            self.enabled_switch.blockSignals(True)
            self.enabled_switch.setChecked(bool(info["enabled"]))
            self.enabled_switch.blockSignals(False)
            details = manifest_values(addon)
            self.name_input.setText(details["name"])
            self.version_input.setText(details["version"])
            self.author_input.setText(details["author"])
            self.description_input.setText(details["description"])
            self.notes_input.setText(details["notes"])
            self.api_input.setText(details["api"])
            target = manifest_target(addon)
            self.target_note.setText(SHIPPED_NOTE if info["builtin"]
                                     else f"Saved to {target}")
            self.details_note.setText(info["error"] or "")
            self.details_note.setStyleSheet("color: #ffb020;" if info["error"] else "")
            self.config_path.setText(
                _display_path(info["config"])
                + ("" if info["config_exists"] else "  (no file yet)")
                + (f" - {info['config_fields']} declared keys"
                   if info["config_fields"] else ""))
            values = read_config_json(addon_config_path(info["id"]))
            self.config_edit.setPlainText(
                json.dumps(jsonable(values), indent=2, ensure_ascii=False))
            self._load_run_panels(info, addon)
            self.refresh_log()
        finally:
            self._loading = False

    def _load_run_panels(self, info: dict, addon: Any) -> None:
        """The parts of the Run and Compile panels that depend on what is there."""
        shipped = bool(info.get("builtin"))
        copies = {str(item["kind"]): item for item in
                  [dict(kind=info["kind"], source=info["source"])] + list(info["alternatives"])}
        source, compiled = copies.get("python"), copies.get("process")
        both = bool(source and compiled) and not shipped
        want_compiled = both and info["prefer"] == "compiled"
        for button in (self.source_radio, self.compiled_radio):
            button.blockSignals(True)
        self.source_radio.setEnabled(both)
        self.compiled_radio.setEnabled(both)
        self.compiled_radio.setChecked(want_compiled)
        self.source_radio.setChecked(not want_compiled)
        for button in (self.source_radio, self.compiled_radio):
            button.blockSignals(False)
        self.runs_note.setText(SHIPPED_NOTE if shipped
                               else self._runs_text(info, compiled, source))
        # A shipped addon's file is inside the program: nothing to write back to.
        for field in (self.name_input, self.version_input, self.author_input,
                      self.description_input, self.notes_input, self.api_input):
            field.setReadOnly(shipped)
        busy = self.build is not None and self.build.running()
        can_build, why_build = buildable(addon)
        for field in (self.onefile_check, self.console_check, self.icon_check,
                      self.hidden_input, self.prefer_check):
            field.setEnabled(can_build and not busy)
        self.compile_button.setEnabled(can_build and not busy)
        self.cancel_button.setEnabled(busy)
        # One interpreter answer for both panels below: asking costs a call
        # and the two notes want the same words.
        where, why_python = build_interpreter()
        if busy:
            self.build_note.setText("building...")
            self.build_note.setStyleSheet("color: #8f8f8f;")
        elif not can_build:
            self.build_note.setText(why_build)
            self.build_note.setStyleSheet("color: #ffb020;")
        else:
            self.build_note.setText(f"PyInstaller from {where or sys.executable}")
            self.build_note.setStyleSheet("color: #8f8f8f;")

        # A test runs under its own Python, which must reach the addon protocol.
        path = self.test_file(addon)
        self.test_path.setText(str(path))
        self.create_test_button.setText("Rewrite test script" if path.is_file()
                                        else "Write test script")
        testing = self.test is not None and self.test.running()
        modules, why_modules = app_modules_ready()
        if not modules:
            why_run = why_modules
        elif where is None:
            why_run = why_python
        else:
            why_run = "" if path.is_file() else "write the test script first"
        self.create_test_button.setEnabled(bool(modules) and not testing)
        self.run_test_button.setEnabled(not why_run and not testing)
        if why_run and not testing:
            self.test_note.setText(why_run)
            self.test_note.setStyleSheet("color: #8f8f8f;")

    @staticmethod
    def _runs_text(info: dict, compiled: dict | None, source: dict | None) -> str:
        if not compiled:
            return (f"Only the source is there: {info['source']}. Compiling puts a "
                    "program beside it under the same id, and the choice appears here.")
        here = "the compiled program" if info["kind"] == "process" else "the source"
        return (f"Running {here}.\n"
                f"  source:  {source['source'] if source else '-'}\n"
                f"  program: {compiled['source']}\n"
                f"  built:   {info['build'].get('built', 'never')} "
                f"({info['build'].get('path', '-')}), preferred: {info['prefer']}")

    def _clear_panels(self) -> None:
        self.state_note.setText("nothing in the addons folder")
        for field in (self.name_input, self.version_input, self.author_input,
                      self.description_input, self.notes_input, self.api_input,
                      self.hidden_input):
            field.setText("")
        self.id_label.setText("-")
        self.target_note.setText("Pick an addon above.")
        self.config_path.setText("")
        self.config_edit.setPlainText("")
        self.runs_note.setText("")
        self.test_path.setText("")
        self.log_view.setPlainText("")
        self.process_table.setRowCount(0)
        self.holdings_table.setRowCount(0)

    # --- details ---

    def save_details(self) -> None:
        addon = self.current()
        if addon is None:
            return
        values = {
            "name": self.name_input.text(),
            "version": self.version_input.text(),
            "author": self.author_input.text(),
            "description": self.description_input.text(),
            "notes": self.notes_input.text(),
            "api": self.api_input.text(),
        }
        try:
            target = write_details(addon, values)
        except (OSError, ValueError, AddonError) as error:
            self.say_details(f"not saved: {error}", True)
            return
        self.say_details(f"saved {target.name}")
        self.mgr.win.log(f"[addons] manifest of {addon.id} saved to {target}")
        self.mgr.reload()

    def say_details(self, text: str, bad: bool = False) -> None:
        self.details_note.setText(text)
        self.details_note.setStyleSheet("color: #ffb020;" if bad else "color: #23d18b;")

    def reload_details(self) -> None:
        addon = self.current()
        if addon is None:
            return
        self.load_panels()
        self.say_details("reloaded from the file")

    def open_manifest(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(manifest_target(addon))

    def save_config(self) -> None:
        addon = self.current()
        if addon is None:
            return
        try:
            values = json.loads(self.config_edit.toPlainText() or "{}")
            if not isinstance(values, dict):
                raise ValueError("the config has to be one JSON object")
        except ValueError as error:
            self.say_config(f"not saved: {error}", True)
            return
        if self.mgr.save_config(addon.id, values, "Addon Dev"):
            self.say_config("saved")
        else:
            self.say_config("the file could not be written", True)

    def say_config(self, text: str, bad: bool = False) -> None:
        self.config_note.setText(text)
        self.config_note.setStyleSheet("color: #ffb020;" if bad else "color: #23d18b;")

    def reload_config(self) -> None:
        addon = self.current()
        if addon is None:
            return
        values = read_config_json(addon_config_path(addon.id))
        self.config_edit.setPlainText(
            json.dumps(jsonable(values), indent=2, ensure_ascii=False))
        self.say_config("reloaded from the file")

    def open_config(self) -> None:
        addon = self.current()
        if addon is not None:
            self.mgr.open_config_file(addon.id)

    # --- running one addon ---

    def _set_enabled(self, on: bool) -> None:
        if self._loading:
            return
        addon = self.current()
        if addon is not None:
            self.mgr.set_enabled(addon.id, on)
            self.refresh()

    def mgr_restart(self) -> None:
        addon = self.current()
        if addon is not None:
            self.mgr.restart(addon.id)
            self.refresh()

    def show_tab(self) -> None:
        addon = self.current()
        if addon is None or not addon.tabs:
            self.state_note.setText("this addon has opened no tab")
            return
        name = next(iter(addon.tabs))
        entry = addon.tabs.get(name) or {}
        page = entry.get("page")
        if page is not None:
            self.win.tabs.setCurrentWidget(page)

    def rescan(self) -> None:
        forget_interpreter()        # a Python installed since is noticed here
        self.mgr.reload()
        self.refresh()
        self.state_note.setText("the addons folder was read again")

    def open_folder(self) -> None:
        self.open_path(self.mgr.ensure_folder())

    def open_source(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(addon.source)

    def open_test_folder(self) -> None:
        ADDON_TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.open_path(ADDON_TEST_DIR)

    @staticmethod
    def open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # --- new addon ---

    def new_addon(self) -> None:
        dialog = NewAddonDialog(self, [row["id"] for row in self.mgr.describe_all()])
        if not dialog.exec():
            return
        values = dialog.values()
        try:
            written = self.write_addon(values)
        except OSError as error:
            QMessageBox.warning(self, "Could not write the addon", str(error))
            return
        self.mgr.reload()
        self.select(values["id"])
        self.refresh()
        if values["run"]:
            self.mgr.set_enabled(values["id"], True)
            self.refresh()
        extra = " and its test script" if len(written) > 1 else ""
        self.state_note.setText(f"created {written[0].name}{extra}")

    @staticmethod
    def write_addon(values: dict) -> list[Path]:
        """The addon file and, when it was asked for, its test script."""
        ADDONS_DIR.mkdir(parents=True, exist_ok=True)
        title = values["name"]
        source = _ADDON_SOURCE.substitute(
            id=values["id"], name=values["name"], title=title, tab=values["tab"],
            menu=values["menu"], version=values["version"], author=values["author"],
            description=values["description"])
        target = ADDONS_DIR / f"{values['id']}.py"
        if target.exists():
            raise OSError(f"{target} is already there")
        _write_text(target, source)
        written = [target]
        if values["test"]:
            written.append(write_test(values["id"], values["tab"]))
        return written

    @staticmethod
    def test_file(addon: Any) -> Path:
        return ADDON_TEST_DIR / f"test_{config_name(addon.id)}.py"

    # --- test ---

    def create_test(self) -> None:
        addon = self.current()
        if addon is None:
            return
        try:
            path = write_test(addon.id, addon.name or addon.id)
        except OSError as error:
            self.test_note.setText(f"could not write: {error}")
            self.test_note.setStyleSheet("color: #ffb020;")
            return
        self.load_panels()
        self.test_note.setText(f"wrote {path.name}")
        self.test_note.setStyleSheet("color: #23d18b;")

    def _watch_streams(self) -> None:
        """Keep the short poll alive while a build or a test child is running."""
        if not self.poll_timer.isActive():
            self.poll_timer.start()

    def run_test(self) -> None:
        addon = self.current()
        if addon is None:
            return
        modules, why_modules = app_modules_ready()
        where, why_python = build_interpreter()
        if not modules or where is None:
            self.test_log.setPlainText(why_modules or why_python)
            self.test_note.setText("the test cannot be run here")
            self.test_note.setStyleSheet("color: #ffb020;")
            return
        path = self.test_file(addon)
        if not path.is_file():
            path = write_test(addon.id, addon.name or addon.id)
        self.test_log.setPlainText("")
        self.test = _Stream([where, str(path)])
        self.test.start()
        self._watch_streams()
        self.run_test_button.setEnabled(False)
        self.test_note.setText("running...")
        self.test_note.setStyleSheet("color: #8f8f8f;")

    def open_test(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(self.test_file(addon))

    # --- compile ---

    def _prefer_changed(self, _on: bool = False) -> None:
        if self._loading:
            return
        addon = self.current()
        if addon is None:
            return
        kind = "compiled" if self.compiled_radio.isChecked() else "source"
        if self.mgr.preferred_kind(addon.id) == kind:
            return
        self.mgr.set_preferred_kind(addon.id, kind)
        self.refresh()
        self.runs_note.setText(f"the app will now run {kind}")

    def compile_addon(self) -> None:
        addon = self.current()
        if addon is None or not str(addon.source).endswith(".py"):
            return
        ready, why = buildable(addon)
        if not ready:
            self.build_log.setPlainText(
                f"This addon cannot be compiled here.\n\n{why}")
            return
        # Release the file the copy step overwrites, and any old process.
        if addon.loaded and addon.kind == "process":
            self.mgr.unload(addon, reason="rebuilding")
        identifier = config_name(addon.id)
        work = ADDON_BUILD_DIR / identifier
        try:
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)
            launcher = work / "launcher.py"
            _write_text(launcher, _LAUNCHER.substitute(
                id=identifier, module=module_name(addon.source)))
        except OSError as error:
            self.build_log.setPlainText(f"the staging folder could not be made: {error}")
            return
        options = {
            "onefile": self.onefile_check.isChecked(),
            "console": self.console_check.isChecked(),
            "icon": self.icon_check.isChecked(),
            "hidden": self.hidden_input.text(),
        }
        command = build_command(launcher, identifier, options, addon.source)
        self.build_log.setPlainText("$ " + " ".join(command) + "\n\n")
        self.build = _Stream(command, cwd=work)
        self.build.start()
        self._watch_streams()
        self.compile_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.build_note.setText("building...")

    def cancel_build(self) -> None:
        if self.build is not None:
            self.build.cancel()
            self.build_log.appendPlainText("\n(the build was asked to stop)")

    def clean_staging(self) -> None:
        addon = self.current()
        if addon is None:
            return
        work = ADDON_BUILD_DIR / config_name(addon.id)
        shutil.rmtree(work, ignore_errors=True)
        self.build_log.setPlainText(f"{work} removed")

    def _poll_streams(self) -> None:
        """Move a child process's output into its pane, and finish it off."""
        for kind, stream, view in (("build", self.build, self.build_log),
                                   ("test", self.test, self.test_log)):
            if stream is None:
                continue
            lines = [line for line in stream.drain() if line is not None]
            if lines:
                view.appendPlainText("\n".join(lines))
            if not stream.finished:
                continue
            if kind == "build":
                self.build_done(stream)
                self.build = None
            else:
                self.test_done(stream)
                self.test = None
            addon = self.current()
            if addon is not None:
                # Buttons and notes follow what the run just ended with.
                self._load_run_panels(self.mgr.describe(addon), addon)
        if self.build is None and self.test is None:
            # Nothing is in flight any more: the poll rests until the next
            # launch asks for it (_watch_streams).
            self.poll_timer.stop()

    def build_done(self, stream: _Stream) -> None:
        addon = self.current()
        code = stream.code
        if addon is None or code != 0:
            self.build_note.setText(f"the build stopped with code {code}")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        identifier = config_name(addon.id)
        staged = staged_name(identifier, self.onefile_check.isChecked())
        if not staged.is_file():
            self.build_note.setText("no program was produced; see the build log")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        try:
            target = install_build(staged, identifier, self.onefile_check.isChecked())
        except OSError as error:
            self.build_note.setText(f"could not be put in the addons folder: {error}")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        previous = build_mark(addon.id)
        write_build_mark(addon.id, {
            **{key: value for key, value in previous.items() if key != "prefer"},
            "built": time.strftime("%Y-%m-%d %H:%M"),
            "path": str(target),
            "onefile": self.onefile_check.isChecked(),
            "source": str(addon.source),
            "prefer": "compiled" if self.prefer_check.isChecked()
                      else previous.get("prefer", "source"),
        })
        self.mgr.reload()
        self.refresh()
        self.build_log.appendPlainText(f"\n{target} is ready")
        self.build_note.setText(f"wrote {target.name}")
        self.build_note.setStyleSheet("color: #23d18b;")

    def test_done(self, stream: _Stream) -> None:
        ok = stream.code == 0
        self.test_note.setText("passed" if ok else f"failed (code {stream.code})")
        self.test_note.setStyleSheet("color: #23d18b;" if ok else "color: #ffb020;")

    # --- log and processes ---

    def reset_log(self) -> None:
        self.log_view.setPlainText("")

    def refresh_log(self) -> None:
        addon_id = self.selected
        if not addon_id:
            return
        try:
            text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = log_lines(text, addon_id, self.log_warnings.isChecked())
        needle = self.log_filter.text().strip().lower()
        if needle:
            lines = [line for line in lines if needle in line.lower()]
        keep = lines[-400:]
        shown = self.log_view.toPlainText().splitlines()
        if shown == keep:
            return
        self.log_view.setPlainText("\n".join(keep))
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def open_log(self) -> None:
        self.open_path(LOG_FILE)

    def refresh_processes(self, rows: list[dict]) -> None:
        running = [row for row in rows if row.get("process")]
        self.process_table.blockSignals(True)
        self.process_table.setRowCount(len(running))
        for index, row in enumerate(running):
            process = row.get("process") or {}
            state = "running" if process.get("alive") else "closed"
            if row.get("error"):
                state = "failed"
            command = " ".join(str(part) for part in (process.get("command") or []))
            values = (row["id"], process.get("pid") or "", state,
                      "" if process.get("exit") is None else process.get("exit"),
                      duration(process.get("seconds")), process.get("requests") or 0,
                      command)
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if column == 2:
                    item.setForeground(state_color(state))
                self.process_table.setItem(index, column, item)
        self.process_table.blockSignals(False)
        fit_rows(self.process_table, cap=6)

        threads = [name for name in (getattr(thread, "name", "")
                                     for thread in threading.enumerate())
                   if name.startswith(("addon-", "addon"))]
        self.thread_note.setText("threads serving the addons: "
                                 + (", ".join(sorted(set(threads))) or "none"))

        held = [row for row in rows if row["kind"] == "python"]
        self.holdings_table.blockSignals(True)
        self.holdings_table.setRowCount(len(held))
        for index, row in enumerate(held):
            values = (row["id"], "imported", ", ".join(row["tabs"]) or "-",
                      row["timers"], row["menu_items"],
                      ", ".join(row["listeners"]) or "-")
            for column, text in enumerate(values):
                self.holdings_table.setItem(index, column, QTableWidgetItem(str(text)))
        self.holdings_table.blockSignals(False)
        fit_rows(self.holdings_table, cap=6)


def write_test(addon_id: str, tab: str = "") -> Path:
    """``addons/_tests/test_<id>.py``, from the template."""
    ADDON_TEST_DIR.mkdir(parents=True, exist_ok=True)
    target = ADDON_TEST_DIR / f"test_{config_name(addon_id)}.py"
    _write_text(target, _TEST_SOURCE.substitute(id=config_name(addon_id),
                                                tab=tab or addon_id))
    return target


def install_build(staged: Path, addon_id: str, onefile: bool) -> Path:
    """Move the build result where the runtime looks: ``addons/<id>.exe`` or ``addons/<id>/``.

    An older install of the same id goes first whichever shape it was left in:
    on a machine with no executable suffix a one-file program and a folder
    program both want the bare name ``addons/<id>``, so the build landing now
    replaces what stands there rather than arguing with it. The store has
    already stopped the addon before calling this, and the Addon Dev tab only
    ever writes an id it is building itself.
    """
    if not str(addon_id or "").strip() or str(addon_id).strip() in (".", ".."):
        raise OSError("the build has no addon id to install under")
    for stale in (ADDONS_DIR / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}",
                  ADDONS_DIR / addon_id):
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.is_file():
            stale.unlink()
    if onefile:
        target = ADDONS_DIR / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}"
        shutil.copy2(staged, target)
        return target
    target = ADDONS_DIR / addon_id
    shutil.copytree(staged.parent, target)
    program = target / staged.name
    if ADDON_EXECUTABLE_SUFFIX == "":
        program = target / "addon.bin"
        os.replace(target / staged.name, program)
        program.chmod(program.stat().st_mode | 0o111)
    return program
