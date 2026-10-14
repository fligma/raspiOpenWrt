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

Five things live here:

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
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
from string import Template
import threading
import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
    find_asset,
)
from manager_widgets import tip_on_title

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
from a declarative spec and its button is reported back through ``on_widget``.
"""

from __future__ import annotations

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
    # connection per call) or "both" (the user picks).
    "connection": "app",
    "api": 1,
}

#: The name of the tab this addon adds.
TAB = "$tab"

#: The settings the user can change in Settings > Addons, and their defaults.
#: A key with a dot reads and writes inside a section of the config file.
FIELDS = [
    {"key": "greeting", "label": "Text to show", "kind": "text",
     "hint": "What the button on the tab prints", "placeholder": "Hello"},
]
DEFAULTS = {"greeting": "Hello from $id"}

#: Set by init(); the app hands the addon its API there.
api = None


def say(text: str) -> None:
    """One line on the tab and one in the app log, always in both."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
    except AddonError:
        pass
    api.log(text)


def pressed() -> None:
    """The tab's button: read the form and report what it said."""
    if api is None:
        return
    try:
        greeting = str(api.tabs.widget_value(TAB, "greeting") or "").strip()
    except AddonError:
        greeting = ""
    if not greeting:
        greeting = str(config_get(api.config.all(), "greeting", DEFAULTS["greeting"]))
    say(f"you asked me to say: {greeting}")


def build_tab(values: dict) -> None:
    """A spec, not widgets: a process addon can only describe what it wants."""
    api.tabs.add_tab(TAB, spec={
        "kind": "column",
        "children": [
            {"kind": "label", "text": "$title", "font_size": 12},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "style": "color: #8f8f8f;"},
            {"kind": "line", "id": "greeting",
             "placeholder": str(config_get(values, "greeting", DEFAULTS["greeting"]))},
            {"kind": "button", "id": "press", "text": "Say it", "on": ["clicked"]},
            {"kind": "spacer"},
        ],
    })


def init(plugin_api: Any) -> None:
    """The entry point: declare the config, then build what the addon shows."""
    global api
    api = plugin_api
    values = api.config.declare(FIELDS, DEFAULTS,
                                title="$title - addons/_data/$id/config.json")
    build_tab(values)
    api.ui.menu_item("$menu", focus, menu="Tools")
    api.events.on("ssh_connected", on_connected)
    api.log("ready")


def start() -> None:
    """Called once at startup, and whenever the addon is switched on."""
    if api is None:
        return
    runs = int(api.settings.get("runs", 0) or 0) + 1
    api.settings.set("runs", runs)
    say(f"$title is running (start no. {runs})")


def shutdown() -> None:
    global api
    api = None


def focus() -> None:
    """The Tools menu entry."""
    if api is not None:
        api.tabs.activate(TAB)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """What a spec-built control reported. Works in both kinds of addon."""
    if identifier == "press" and event == "clicked":
        pressed()


def on_connected(payload: dict) -> None:
    """The device came up."""
    if api is not None:
        say("the device is reachable")


def on_config(payload: dict) -> None:
    """Called when addons/_data/$id/config.json changed: read it again."""
    if api is None:
        return
    build_tab(api.config.all())
    say("the config file was read again")


def on_event(name: str, payload: dict) -> None:
    """Every event the app broadcasts arrives here as well."""
    if api is not None and name == "operation_done":
        say('the app finished a ' + str(payload.get("operation", "job")))


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

Run it from the Test panel, or on the command line::

    python addons/_tests/test_$id.py
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The folder Berry Manager and addon_client live in, two levels up.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                            # noqa: E402

SOURCE = ROOT / "addons" / "$id.py"
FAILURES: list[str] = []


def check(condition: Any, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)


def main() -> int:
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    # The values the config file would have had; a test never needs the file.
    api = addon_client.stub_api("$id", config={"greeting": "hello from the test"})
    addon = addon_client.load_addon(SOURCE)

    addon.init(api)
    check(api.called("config.declare"), "init did not declare its config keys")
    check(api.called("tabs.add_tab"), "init did not add a tab")
    check(api.called("ui.menu_item"), "init did not add its Tools menu entry")
    check(api.called("events.on"), "init did not listen for any event")
    check(api.config.get("greeting") == "hello from the test",
          "the config value did not reach the addon")

    addon.start()
    check(api.times("settings.set") >= 1, "start recorded nothing in api.settings")

    addon.on_widget("$tab", "press", "clicked", {})
    check(any("hello from the test" in line for line in api.logs)
          or any("say" in line for line in api.logs),
          "pressing the button left no trace in the log")

    addon.on_config({"addon": "$id", "source": "test", "file": "x"})
    addon.shutdown()

    print(f"$id: {len(api.calls)} api calls")
    for method, arguments, named in api.calls[:12]:
        print(f"  {method}{arguments or ()}{' ' + str(sorted(named)) if named else ''}")
    print("log lines written by the addon:")
    for line in api.logs:
        print(f"  {line}")
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print()
    print("all checks passed")
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
    """The app icon, when PyInstaller can use it as-is."""
    path = find_asset(ICON_FILES[0])
    if path is not None and path.suffix.lower() in (".ico", ".icns"):
        return path
    return None


def icon_choice(icon: Any) -> Path | None:
    """The ``--icon`` file for a build: a named one that is usable, the app's, or none.

    ``icon`` is ``True`` for the program's own mark, ``False``/empty for none, or
    a path an addon brought with it (a store addon's own icon). A named icon that
    is missing or the wrong shape falls back to the app's, so a build never dies
    over a bad picture.
    """
    if not icon:
        return None
    if icon is True:
        return icon_for_build()
    path = Path(icon)
    if path.is_file() and path.suffix.lower() in (".ico", ".icns", ".png"):
        return path
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
        "--noconfirm", "--clean",
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


# --- the tab itself ---

class AddonDevWidget(QWidget):
    """The Addon Dev panel: one addon selected at a time, five views of it."""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.mgr = window.addons
        self.selected = ""
        self.build: _Stream | None = None
        self.test: _Stream | None = None
        self._loading = False

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
        # a build's output should feel live, a table list does not.
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(400)
        self.poll_timer.timeout.connect(self._poll_streams)
        self.poll_timer.start()
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
        self.icon_check.setToolTip("Use Berry Manager's icon when it is a format "
                                   "PyInstaller accepts (.ico / .icns)")
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
        for button in (self.compile_button, self.cancel_button, self.clean_button,
                       self.reveal_button):
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
        for button in (self.create_test_button, self.run_test_button,
                       self.open_test_button, self.test_folder_button):
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
        if busy:
            self.build_note.setText("building...")
            self.build_note.setStyleSheet("color: #8f8f8f;")
        elif not can_build:
            self.build_note.setText(why_build)
            self.build_note.setStyleSheet("color: #ffb020;")
        else:
            where, _why = build_interpreter()
            self.build_note.setText(f"PyInstaller from {where or sys.executable}")
            self.build_note.setStyleSheet("color: #8f8f8f;")

        # A test runs under its own Python, which must reach the addon protocol.
        path = self.test_file(addon)
        self.test_path.setText(str(path))
        self.create_test_button.setText("Rewrite test script" if path.is_file()
                                        else "Write test script")
        testing = self.test is not None and self.test.running()
        modules, why_modules = app_modules_ready()
        where, why_python = build_interpreter()
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
    """Move the build result where the runtime looks: ``addons/<id>.exe`` or ``addons/<id>/``."""
    if onefile:
        target = ADDONS_DIR / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}"
        if target.is_dir():
            raise OSError(f"{target} is a folder")
        if target.exists():
            target.unlink()
        shutil.copy2(staged, target)
        return target
    target = ADDONS_DIR / addon_id
    if target.exists():
        if not target.is_dir():
            raise OSError(f"{target} is a file, not the folder this build needs")
        shutil.rmtree(target)
    shutil.copytree(staged.parent, target)
    program = target / staged.name
    if ADDON_EXECUTABLE_SUFFIX == "":
        program = target / "addon.bin"
        os.replace(target / staged.name, program)
        program.chmod(program.stat().st_mode | 0o111)
    return program
