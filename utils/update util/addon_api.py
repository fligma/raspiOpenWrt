#!/usr/bin/env python3
"""The API every addon is handed - the only door from an addon into the app.

An addon is a ``.py`` file or an ``.exe`` sitting in the ``addons`` folder next
to the program. The app imports the Python ones, launches the executables, and
gives each of them one object: ``api``. Everything an addon may do goes through
that object, so a broken addon can only break itself - every call is wrapped,
and an exception is reported to the log instead of reaching the app.

The standard-library half of this contract - ``AddonError``, ``guard``,
``jsonable``, the manifest reading and the addon config/data path helpers - lives in
:mod:`addon_common` and is imported below, so it stays reachable under these
names for an addon running inside the app while an addon compiled into its own
program can import the same things without Qt or paramiko.

The namespaces on ``api``
----------------------------------

``api.id``, ``api.name``, ``api.log(text)``
    Identity and the shared log. Log lines are prefixed with the addon name.

``api.session``
    Everything the current Berry Manager session knows:
    ``profile()`` (active profile, never the password), ``settings()``,
    ``status()`` (host/port/user/remote root plus which protocols are up),
    ``tabs()``, ``current_tab()``, ``version()``, ``app_stylesheet()`` (already
    scaled to the interface zoom), ``zoom()`` (that percentage), ``app_icon()``
    (the logo the program wears itself).

``api.device``
    SSH and SFTP.
    ``run(command, timeout=30, device=None)`` -> ``{"code", "out", "err"}`` (runs in
    the background and waits; use ``run_async(command, on_done)`` from ``init``),
    ``shell_send(line)`` writes to the live terminal tab,
    ``ensure_connected("ssh"|"sftp")``, ``is_connected()``.
    ``device`` on ``run``/``run_async``/``open`` is a one-off dict of connection
    details (``host``, ``port``, ``username``, ``password``, ``key_file``,
    ``remote_root``): the call reaches that machine instead of the profile,
    for any addon that talks to several devices - one API call, so it stores
    nothing and never moves the app's own session; addons shipped with the
    program may use it too. ``bind()`` stays the drop-in-only door.
    Files: ``list()``, ``exists()``, ``stat()``, ``read_text()``,
    ``read_bytes()``, ``write_text()``, ``write_bytes()``, ``mkdir()``,
    ``remove()``, ``rename()``, ``upload()``, ``download()`` - each takes
    ``outside_root=True`` to reach above the profile's remote folder
    (``/etc/ssl`` and the like); without it a path stays inside that folder.
    ``open()`` is a context manager yielding a live ``Connection`` with
    ``.client`` / ``.sftp`` (raw paramiko) for anything not covered above.

``api.tabs``
    ``add_web_tab(name, url=None, ip="", port=80, path="/", scheme="http")``
    embeds a browser tab that belongs to no profile: it is not stored in the
    settings and never shows up in Settings > Custom tabs.
    ``add_tab(name, widget=None, spec=None)`` adds a tab holding any QWidget
    (Python) or a widget built from a declarative spec (see SPEC FORMAT).
    ``set_html``, ``inject_css``, ``inject_js``, ``set_style``, ``view``,
    ``widget``, ``set_widget``, ``widget_value``, ``activate``, ``close``,
    ``names``, ``exists``, ``set_icon`` (a mark beside the tab's label, the way
    a page's favicon sits beside its title), ``use_app_icon`` (wear the logo the
    program uses and keep following it when it is switched).

``api.ui``
    ``information/warning/error(title, text)``, ``question(title, text)`` ->
    bool, ``input(title, label, default)`` -> str, ``notify(text)``,
    ``menu_item(text, callback, menu="Tools", shortcut=None)``,
    ``dialog(spec)``, ``window()`` (the main window, for advanced use),
    ``set_setting_widget(spec_or_widget)`` (this addon's own extra settings).

``api.events``
    ``on(name, callback)`` -> unsubscribe callable. Events: ``startup``,
    ``shutdown``, ``sftp_connected``, ``sftp_disconnected``, ``ssh_connected``,
    ``ssh_disconnected``, ``operation_done``, ``tab_changed``,
    ``profile_changed``, ``settings_applied``, ``addon_enabled``,
    ``addon_disabled``, ``addon_config_changed``. A module-level ``on_event(name,
    payload)`` or ``on_<name>(payload)`` function is called too.

``api.settings``
    This addon's own persisted dict: ``get``, ``set``, ``update``, ``pop``,
    ``all``, ``remove``. Stored in config.json under ``addons -> <id> ->
    settings``, so it travels with the profile like every other setting.

``api.config``
    The addon's own settings *file*: ``addons/_data/<id>/config.json``, in the
    addon's own folder. Unlike
    ``api.settings`` it belongs to the installation rather than to one profile,
    it is plain JSON a user can edit in any editor, and the app shows it under
    the addon's name in Settings > Addons. ``declare(fields, defaults, title)``
    describes the keys so the app can build a form for them; then ``get``,
    ``set``, ``update``, ``all``, ``pop``, ``reset``, ``text``, ``save_text``,
    ``exists``, ``path``, ``file``, ``schema``, ``open_folder``.

``api.schedule``
    ``after(seconds, fn)``, ``every(seconds, fn)`` -> handle with ``.cancel()``.

``api.paths``
    ``addon`` (the folder this addon lives in), ``data`` (a private, created
    folder for this addon's files, which is also where its ``config.json``
    lives), ``config`` (this addon's own config file), ``configs`` (this addon's
    data folder - the same place), ``base``, ``addons``, ``assets`` (the pictures
    that ship inside the program, read-only), ``log``.

``api.peers``
    ``list()``, ``info(id)``, ``send(id, data)`` -> the target's
    ``on_message(sender, data)``.

SPEC FORMAT (for non-Qt addons, see ``manager_addons``)
------------------------------------------------------
A JSON object describing one widget, recursively:

``{"kind": "column", "children": [...]}`` - containers: ``column``/``row``,
``grid``, ``form``, ``group`` (``text`` = title), ``scroll``, ``splitter``,
``tabs`` (one sub-page per child, the child's ``label`` its caption; move
between the sub-pages with ``set_widget(tab, id, "current", n)`` and read the
visible one back with ``widget_value(tab, id, "index")``).
``label``, ``button``, ``line``, ``multi`` (plain text edit), ``check``,
``combo``, ``list``, ``tree``, ``table``, ``progress``, ``spin``, ``slider``,
``spacer``, ``web`` - all take ``id``, ``text``, ``tooltip``, ``style``
(a stylesheet, so an addon can style its own tab), ``enabled``, ``visible``,
``font_size`` (drawn at the interface zoom), ``items``, ``value``,
``placeholder``, ``checked``, ``minimum``,
``maximum``, ``columns``, ``word_wrap``, ``readonly``, ``multi``, ``expand``
(claim the width its row or column leaves over, so one status line can stretch
while its neighbours keep the room their text needs).
``value`` sets whatever the kind holds: text, the chosen entry, the number, or
the tick for a ``check``; ``widget_value`` reads it back the same way.
A ``multi`` may name a ``highlight`` language (``python``, ``json``, ``html``,
``css``, ``javascript``, ``ini``) for syntax colors; change it later with
``set_widget(tab, id, "highlight", lang)``.
``on`` lists the events sent back to the addon: ``clicked``, ``toggled``,
``changed``, ``returned``, ``selected`` and, for a ``table``/``tree``/``list``,
``context`` (a right-click, carrying the text of the row under the cursor).
``rows`` on a ``table``/``tree`` takes a list of cell lists, or of dicts:
``{"cells": [...], "color": "#rrggbb", "background": "...", "overlay": "..."}``
- the colours ride along with the row, so one line can wear the colour of its
own device, and an overlay (a search highlight) can wash over it without
hiding it. A table paints its rows through its own delegate: the app sheet
names ``::item`` colours, and a stylesheet colour would otherwise win over
every per-item brush and drop all of this on the floor.
"""

from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
import inspect
import json
import posixpath
from pathlib import Path
from urllib.parse import urlparse
import stat as stat_module
from typing import Any, Callable

from PyQt6.QtCore import (
    QEventLoop,
    QModelIndex,
    Qt,
    QTimer,
    QUrl,
    QThread,
    QRegularExpression,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QAction,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from addon_common import (
    API_VERSION,
    AddonError,
    addon_config_path,
    addon_data_dir,
    addon_schema_path,
    build_mark,
    config_get,
    config_name,
    guard,
    jsonable,
    manifest_from,
    merge_config,
    put_config_value,
    read_config_json,
    take_config_value,
    write_config_json,
)
from manager_config import web_tab_url
from manager_const import (
    APP_STYLESHEET,
    LOG_FILE,
    current_zoom,
    scaled_sheet,
    zoom_size,
)
from manager_net import create_ssh_client, exec_remote, remote_join, safe_relative_path
from manager_widgets import tab_caption

try:  # the web kinds are inert when QtWebEngine is not installed
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None


#: One shared pool, so twenty addons cannot open twenty SSH connections.
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="addon")


def _timeout(seconds: Any, default: float = 30.0) -> float:
    """Clamp an addon-supplied timeout."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1.0), 600.0)


def _await(future: concurrent.futures.Future, timeout: float) -> Any:
    """Wait for a pooled task without freezing the interface; on the GUI thread a private event loop runs while the task is in flight."""
    if on_gui_thread():
        loop = QEventLoop()
        future.add_done_callback(lambda _future: loop.quit())
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(int(timeout * 1000) + 2000)
        if not future.done():
            loop.exec()
        watchdog.stop()
    if not future.done():
        raise AddonError("the device did not answer in time")
    try:
        return future.result()
    except AddonError:
        raise
    except Exception as error:  # surface the real cause in the addon's words
        raise AddonError(str(error)) from error


def on_gui_thread() -> bool:
    """Whether the caller is the thread that owns the GUI."""
    app = QApplication.instance()
    return app is not None and QThread.currentThread() is app.thread()


# --- addon config files: the Qt-free helpers live in addon_common ---

# --- declarative widgets ---

_LAYPOUT_KINDS = {
    "column": QVBoxLayout, "vbox": QVBoxLayout, "row": QHBoxLayout, "hbox": QHBoxLayout,
}


#: Row shades for the table delegate, which paints rows by hand.
_ROW_BASE = QColor("#212121")
_ROW_ALT = QColor("#1a1a1a")
_ROW_HOVER = QColor("#2c2c2c")
_ROW_SELECTED = QColor("#6b1730")
_ROW_TEXT = QColor("#e0e0e0")
_ROW_TEXT_SELECTED = QColor("#ffffff")

#: Extra slot a row dict can carry: a search highlight over the background.
ROW_OVERLAY_ROLE = Qt.ItemDataRole.UserRole + 11


class _RowStyleDelegate(QStyledItemDelegate):
    """Paint table rows by hand so an addon's own colours survive the app sheet."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        alternate = bool(
            option.features & QStyleOptionViewItem.ViewItemFeature.Alternate)
        base = (_ROW_SELECTED if selected else _ROW_HOVER if hover
                else _ROW_ALT if alternate else _ROW_BASE)
        painter.setPen(Qt.PenStyle.NoPen)
        for brush in (QBrush(base), index.data(Qt.ItemDataRole.BackgroundRole),
                      index.data(ROW_OVERLAY_ROLE)):
            if isinstance(brush, QBrush):
                painter.setBrush(brush)
                painter.drawRect(rect)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if text:
            fore = index.data(Qt.ItemDataRole.ForegroundRole)
            colour = (fore.color() if isinstance(fore, QBrush)
                      else _ROW_TEXT_SELECTED if selected else _ROW_TEXT)
            painter.setPen(colour)
            painter.setFont(option.font)
            box = rect.adjusted(zoom_size(4), 0, -zoom_size(4), 0)
            elided = painter.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, box.width())
            painter.drawText(
                box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                elided)
        painter.restore()


def _swatch_icon(colour: QColor) -> QIcon:
    """The chip at the end of a ``color`` line; grey when the hex is bad."""
    side = max(12, zoom_size(16))
    canvas = QPixmap(side, side)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = colour if colour.isValid() else QColor("#808080")
    painter.setPen(QPen(fill.darker(160), max(1, side // 12)))
    painter.setBrush(fill)
    painter.drawRoundedRect(1, 1, side - 2, side - 2,
                            side * 0.28, side * 0.28)
    painter.end()
    icon = QIcon(canvas)
    return icon


def build_spec(spec: dict, registry: dict[str, QWidget] | None = None,
               events: Callable[[str, str, dict], None] | None = None
               ) -> tuple[QWidget, dict[str, QWidget]]:
    """Turn a spec dict into a QWidget, filling `registry` with id -> widget."""
    registry = {} if registry is None else registry
    widget = _build_one(spec, registry, events)
    if widget is None:
        raise AddonError(f"unknown widget kind: {spec.get('kind')!r}")
    return widget, registry


def _apply_common(widget: QWidget, spec: dict, registry: dict,
                  events: Callable[[str, str, dict], None] | None) -> None:
    identifier = spec.get("id")
    if identifier:
        registry[str(identifier)] = widget
    if spec.get("text") is not None and hasattr(widget, "setText"):
        widget.setText(str(spec["text"]))
    if spec.get("placeholder") is not None and hasattr(widget, "setPlaceholderText"):
        widget.setPlaceholderText(str(spec["placeholder"]))
    if spec.get("word_wrap") is not None and hasattr(widget, "setWordWrap"):
        widget.setWordWrap(bool(spec["word_wrap"]))
    if spec.get("expand") and hasattr(widget, "setSizePolicy"):
        # Take the slack of the row or column it sits in: a long line then wraps
        # inside the room that is actually left, instead of being squeezed by a
        # spacer and cut off.
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(policy.Policy.Expanding)
        widget.setSizePolicy(policy)
    if spec.get("checked") is not None and hasattr(widget, "setChecked"):
        widget.setChecked(bool(spec["checked"]))
    # Fill a combo or list first so "value" can pick from it.
    if spec.get("items") is not None and hasattr(widget, "addItems"):
        widget.clear()
        widget.addItems([str(item) for item in (spec["items"] or [])])
    if spec.get("value") is not None:
        apply_widget_property(widget, "value", spec["value"])
    if spec.get("tooltip") is not None:
        widget.setToolTip(str(spec["tooltip"]))
    if spec.get("style") is not None:
        widget.setStyleSheet(str(spec["style"]))
    if spec.get("object_name") is not None:
        widget.setObjectName(str(spec["object_name"]))
    if "enabled" in spec:
        widget.setEnabled(bool(spec["enabled"]))
    if "visible" in spec:
        widget.setVisible(bool(spec["visible"]))
    if spec.get("font_size"):
        font = widget.font()
        # The addon's size drawn at the interface zoom.
        font.setPointSize(zoom_size(int(spec["font_size"])))
        widget.setFont(font)
    wanted = spec.get("on")
    if events is not None and wanted is not None:
        _connect_events(widget, wanted, str(identifier or ""), events)


def _connect_events(widget: QWidget, wanted: Any, identifier: str,
                    events: Callable[[str, str, dict], None]) -> None:
    """Send ``{tab, id, event}`` back to the addon for each listed interaction."""
    names = [str(name) for name in (wanted if isinstance(wanted, list) else [wanted])]
    emit = lambda event: events(identifier, event, {})
    if "clicked" in names and isinstance(widget, QPushButton):
        widget.clicked.connect(lambda _=False: emit("clicked"))
    if "toggled" in names and isinstance(widget, QCheckBox):
        widget.toggled.connect(lambda on: events(identifier, "toggled", {"checked": bool(on)}))
    if "returned" in names and isinstance(widget, QLineEdit):
        widget.returnPressed.connect(lambda: emit("returned"))
    if "changed" in names:
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(lambda text: events(identifier, "changed", {"text": text}))
        elif isinstance(widget, QPlainTextEdit):
            widget.textChanged.connect(lambda: events(identifier, "changed",
                                                     {"text": widget.toPlainText()}))
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(lambda text: events(identifier, "changed", {"text": text}))
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(lambda value: events(identifier, "changed", {"value": int(value)}))
        elif isinstance(widget, QSlider):
            widget.valueChanged.connect(lambda value: events(identifier, "changed", {"value": int(value)}))
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(lambda on: events(identifier, "changed", {"checked": bool(on)}))
    if "context" in names:
        # Right-click: the row's text is sent along so the addon needs no
        # second round trip; it shows its menu with ``api.ui.choose``.
        if isinstance(widget, QTreeWidget):
            widget.customContextMenuRequested.connect(
                lambda pos: events(identifier, "context", {"text": _context_text(
                    widget.itemAt(pos))}))
        elif isinstance(widget, QListWidget):
            widget.customContextMenuRequested.connect(
                lambda pos: events(identifier, "context", {"text": _context_text(
                    widget.itemAt(pos))}))
    if "selected" in names:
        if isinstance(widget, QListWidget):
            widget.itemSelectionChanged.connect(
                lambda: events(identifier, "selected",
                               {"rows": [widget.row(item) for item in widget.selectedItems()],
                                "texts": [item.text() for item in widget.selectedItems()]}))
        elif isinstance(widget, QTreeWidget):
            widget.itemSelectionChanged.connect(
                lambda: events(identifier, "selected", {"count": len(widget.selectedItems())}))
            widget.itemDoubleClicked.connect(
                lambda item, _col=0: events(identifier, "selected", {"text": item.text(0)}))


def _context_text(item: Any) -> str:
    """The first-column label of the row a right-click landed on, or nothing."""
    if item is None:
        return ""
    getter = getattr(item, "text", None)
    if callable(getter):
        return str(getter(0) or "")
    return str(getattr(item, "text", "") or "")


# --- syntax highlighting for a ``multi``: an addon only names a language ---

def _hl_format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


def _py_keywords() -> str:
    """Python keyword pattern."""
    words = ("False None True and as assert async await break class continue def "
             "del elif else except finally for from global if import in is lambda "
             "nonlocal not or pass raise return try while with yield match case")
    return r"\b(" + "|".join(words.split()) + r")\b"


def _js_keywords() -> str:
    """JavaScript keyword pattern."""
    words = ("break case catch class const continue debugger default delete do else "
             "export extends finally for function if import in instanceof let new "
             "return super switch this throw try typeof var void while with yield")
    return r"\b(" + "|".join(words.split()) + r")\b"


_LANG_RULES: dict[str, list[tuple[str, str, bool, bool]]] = {
    "python": [
        (r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "#ce9178", False, False),
        (r"\b[rbuf]?\"[^\"]*\"|\b[rbuf]?'[^']*'", "#ce9178", False, False),
        (_py_keywords(), "#569cd6", True, False),
        (r"\b(self|cls)__\w+__|__\w+__\b", "#9cdcfe", False, False),
        (r"^\s*def\s+([A-Za-z_]\w*)", "#dcdcaa", True, False),
        (r"^\s*class\s+([A-Za-z_]\w*)", "#4ec9b0", True, False),
        (r"@[A-Za-z_][\w.]*", "#d7ba7d", False, False),
        (r"\b\d+(\.\d+)?\b", "#b5cea8", False, False),
        (r"#.*$", "#6a9955", False, True),
    ],
    "json": [
        (r'"(?:\\.|[^"\\])*"\s*:', "#9cdcfe", True, False),   # a key
        (r'"(?:\\.|[^"\\])*"', "#ce9178", False, False),        # a string
        (r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "#b5cea8", False, False),
        (r"\b(?:true|false|null)\b", "#569cd6", True, False),
        (r"[{}\[\],]", "#d4d4d4", False, False),
    ],
    "html": [
        (r"<!--[\s\S]*?-->", "#6a9955", False, True),
        (r"</?[A-Za-z][^>]*?>", "#569cd6", False, False),
        (r"[A-Za-z-]+(?==)", "#9cdcfe", False, False),          # attribute name
        (r'="[^"]*"', "#ce9178", False, False),                 # attribute value
        (r"&[a-zA-Z#0-9]+;", "#d7ba7d", False, False),
    ],
    "css": [
        (r"/\*[\s\S]*?\*/", "#6a9955", False, True),
        (r"[.#]?[A-Za-z_-][\w-]*(?=\s*\{)", "#d7ba7d", True, False),   # selector
        (r"[-A-Za-z]+(?=\s*:\s*)", "#9cdcfe", False, False),           # property
        (r"#[0-9a-fA-F]{3,8}\b", "#b5cea8", False, False),
        (r'"[^"\s]*"|\'[^\']*\'', "#ce9178", False, False),
        (r"\b\d+(\.\d+)?(px|em|rem|%|vh|vw|s|ms)?\b", "#b5cea8", False, False),
    ],
    "javascript": [
        (r"//.*$", "#6a9955", False, True),
        (r"/\*[\s\S]*?\*/", "#6a9955", False, True),
        (r'"[^\"]*"|\'[^\']*\'|`[^`]*`', "#ce9178", False, False),
        (_js_keywords(), "#569cd6", True, False),
        (r"\b(?:console|document|window|Math|JSON|require)\b", "#4ec9b0", False, False),
        (r"\b\d+(\.\d+)?\b", "#b5cea8", False, False),
    ],
    "ini": [
        (r"^\s*[#;].*$", "#6a9955", False, True),
        (r"^\s*\[[^\]]*\]", "#4ec9b0", True, False),            # section
        (r"^\s*[^=\s:]+(?=\s*[=:])", "#9cdcfe", False, False),  # key
        (r"\b(?:true|false|yes|no|on|off)\b", "#569cd6", False, False),
        (r"\b\d+(\.\d+)?\b", "#b5cea8", False, False),
    ],
    "bash": [
        # function definition: "name()" or "function name".
        (r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*(\(\)|\{)", "#dcdcaa", False, False),
        # control-flow and declaration keywords.
        (r"\b(?:if|then|else|elif|fi|for|while|until|do|done|case|esac|in|select|"
         r"return|break|continue|exit|local|export|readonly|declare|typeset|unset|"
         r"shift|source|eval|exec|trap|set|function)\b", "#569cd6", True, False),
        # common builtins and command-line tools.
        (r"\b(?:echo|printf|read|cd|ls|cat|tac|less|more|cp|mv|rm|mkdir|rmdir|touch|"
         r"grep|sed|awk|cut|sort|uniq|tr|head|tail|find|xargs|test|true|false|kill|"
         r"killall|sleep|date|mount|umount|opkg|uci|service|reboot|wget|curl|chmod|"
         r"chown|ln|tar|gzip|gunzip)\b", "#4ec9b0", False, False),
        (r"\b\d+(\.\d+)?\b", "#b5cea8", False, False),          # numbers
        (r"(?<!\S)-{1,2}[A-Za-z][\w-]*", "#d7ba7d", False, False),  # options/flags
        (r'"(?:\\.|[^"\\])*"|\'[^\']*\'', "#ce9178", False, False),  # strings
        (r"\$\{[^}]*\}|\$[A-Za-z_]\w*|\$[0-9@*#?$!-]", "#9cdcfe", False, False),  # variables
        (r"#.*$", "#6a9955", False, True),                       # comments (last, wins)
    ],
}

# Extensions an editor tab maps onto a language.
_EXT_LANG = {
    ".py": "python", ".pyw": "python",
    ".json": "json", ".js": "javascript", ".mjs": "javascript",
    ".html": "html", ".htm": "html", ".xml": "html", ".svg": "html",
    ".css": "css",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".editorconfig": "ini",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ksh": "bash",
    ".bashrc": "bash", ".profile": "bash", ".shrc": "bash",
}


def language_for_name(path: str) -> str:
    """Guess a highlighter language from a file's extension ('' if unknown)."""
    suffix = posixpath.splitext(str(path or ""))[1].lower()
    return _EXT_LANG.get(suffix, "")


class RegexHighlighter(QSyntaxHighlighter):
    """A QSyntaxHighlighter driven by the regular-expression rules above."""

    def __init__(self, document: Any, language: str = "") -> None:
        super().__init__(document)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self.set_language(language)

    def set_language(self, language: str) -> None:
        """Swap the rule set and re-shade every line already in the document."""
        self._rules = []
        for pattern, color, bold, italic in _LANG_RULES.get(str(language or ""), []):
            expression = QRegularExpression(pattern)
            if not expression.isValid():          # a bad rule must not throw up on the console
                continue
            expression.setPatternOptions(
                QRegularExpression.PatternOption.DotMatchesEverythingOption
                | QRegularExpression.PatternOption.CaseInsensitiveOption
                if str(language) in ("json", "ini")
                else QRegularExpression.PatternOption.DotMatchesEverythingOption)
            self._rules.append((expression, _hl_format(color, bold, italic)))
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for expression, fmt in self._rules:
            match = expression.globalMatch(text or "")
            while match.hasNext():
                found = match.next()
                self.setFormat(found.capturedStart(), found.capturedLength(), fmt)


def _attach_highlighter(widget: QPlainTextEdit, language: str) -> None:
    """Give a ``multi`` its highlighter, once, then keep the language current."""
    highlighter = getattr(widget, "_addon_highlighter", None)
    if highlighter is None:
        highlighter = RegexHighlighter(widget.document(), language)
        widget._addon_highlighter = highlighter
    else:
        highlighter.set_language(language)


def _make_layout(parent: QWidget, kind: str) -> QLayout:
    if kind in ("column", "vbox"):
        layout = QVBoxLayout(parent)
    elif kind in ("row", "hbox"):
        layout = QHBoxLayout(parent)
    elif kind == "grid":
        layout = QGridLayout(parent)
    elif kind == "form":
        layout = QFormLayout(parent)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    else:
        layout = QVBoxLayout(parent)
    layout.setContentsMargins(*(zoom_size(8) for _ in range(4)))
    layout.setSpacing(zoom_size(8))
    return layout


def _build_one(spec: dict, registry: dict,
               events: Callable[[str, str, dict], None] | None) -> QWidget | None:
    kind = str(spec.get("kind") or "").lower()
    children = spec.get("children") or []

    if kind in ("grid", "form") or kind in _LAYPOUT_KINDS:
        holder = QWidget()
        layout = _make_layout(holder, kind)
        _fill_children(layout, children, kind, spec, registry, events)
        if kind in ("row", "hbox"):
            # A row hugs its contents so free space falls to the table below.
            holder.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Fixed)
        _apply_common(holder, spec, registry, events)
        return holder

    if kind == "group":
        box = QGroupBox(str(spec.get("text") or ""))
        layout = _make_layout(box, spec.get("layout", "column"))
        box.setStyleSheet(scaled_sheet(
            "QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px;"
            " margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin;"
            " left: 10px; padding: 0 4px; color: #c51a4a; }"))
        _fill_children(layout, children, str(spec.get("layout") or "column"), spec, registry, events)
        _apply_common(box, {key: value for key, value in spec.items() if key != "text"},
                     registry, events)
        return box

    if kind == "scroll":
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        body = children[0] if children and isinstance(children[0], dict) else {"kind": "column"}
        layout = _make_layout(inner, body.get("kind", "column"))
        _fill_children(layout, body.get("children") or [], str(body.get("kind") or "column"),
                       spec, registry, events)
        area.setWidget(inner)
        _apply_common(area, spec, registry, events)
        return area

    if kind == "splitter":
        splitter = QSplitter(Qt.Orientation.Vertical if spec.get("vertical")
                             else Qt.Orientation.Horizontal)
        for child in children:
            widget = _build_one(child, registry, events)
            if widget is not None:
                splitter.addWidget(widget)
        if spec.get("sizes"):               # starting shares, e.g. [3, 2]
            splitter.setSizes([int(s) for s in spec["sizes"]])
        _apply_common(splitter, spec, registry, events)
        return splitter

    if kind == "tabs":
        tabs = QTabWidget()
        for child in children:
            if not isinstance(child, dict):
                continue
            child = dict(child)
            label = str(child.pop("label", child.get("text", "Tab")))
            widget = _build_one(child, registry, events)
            if widget is not None:
                tabs.addTab(widget, label)
        _apply_common(tabs, spec, registry, events)
        return tabs

    widget = _make_leaf(kind, spec)
    if widget is None:
        return None
    _apply_common(widget, spec, registry, events)
    return widget


def _make_leaf(kind: str, spec: dict) -> QWidget | None:
    if kind == "label":
        label = QLabel()
        label.setWordWrap(bool(spec.get("word_wrap", False)))
        return label
    if kind == "button":
        return QPushButton()
    if kind == "line":
        return QLineEdit()
    if kind == "color":
        # A hex line with the colour wheel behind its swatch; still a plain
        # QLineEdit, so "value" reads and writes it like any other line.
        line = QLineEdit()
        action = line.addAction(_swatch_icon(QColor("#808080")),
                                QLineEdit.ActionPosition.TrailingPosition)
        action.setToolTip("Open the colour wheel - the hex fills itself")

        def open_wheel() -> None:
            picked = ""
            try:
                from manager_widgets import ColorWheelDialog

                picked = ColorWheelDialog.get_colour(line.text(), line,
                                                     "Choose a colour")
            except Exception:                    # no wheel, no fuss
                pass
            if picked:
                line.setText(picked)

        action.triggered.connect(open_wheel)
        line.textChanged.connect(lambda text: action.setIcon(_swatch_icon(
            QColor(str(text).strip() or "not a colour"))))
        return line
    if kind == "multi":
        edit = QPlainTextEdit()
        edit.setReadOnly(bool(spec.get("readonly", False)))
        font = edit.font()
        font.setFamily("Consolas")
        font.setStyleHint(font.StyleHint.Monospace)
        edit.setFont(font)
        if spec.get("highlight"):
            _attach_highlighter(edit, str(spec["highlight"]))
        return edit
    if kind == "check":
        return QCheckBox()
    if kind == "combo":
        combo = QComboBox()
        combo.setEditable(bool(spec.get("editable", False)))
        return combo
    if kind == "list":
        listing = QListWidget()
        listing.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
            if spec.get("multi") else QListWidget.SelectionMode.SingleSelection)
        return listing
    if kind == "tree":
        tree = QTreeWidget()
        tree.setHeaderLabels([str(name) for name in spec.get("columns") or ["Name"]])
        tree.setAlternatingRowColors(True)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        return tree
    if kind == "table":
        table = QTreeWidget()
        table.setRootIsDecorated(False)
        names = [str(name) for name in spec.get("columns") or [""]]
        table.setHeaderLabels(names)
        table.setAlternatingRowColors(True)
        header = table.header()
        # By default every column shares the width equally; "stretch" hands
        # the slack to one column (or several) and "col_widths" gives the rest
        # starting widths, so a long log line is not elided into nothing.
        stretch = spec.get("stretch")
        if stretch is None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        else:
            flexible = {int(i) for i in
                        (stretch if isinstance(stretch, (list, tuple)) else [stretch])}
            widths = list(spec.get("col_widths") or [])
            for column in range(len(names)):
                if column in flexible:
                    header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
                    continue
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                if column < len(widths):
                    table.setColumnWidth(column, zoom_size(int(widths[column])))
        table.setMouseTracking(True)          # so a row knows the mouse is over it
        table.setItemDelegate(_RowStyleDelegate(table))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        return table
    if kind == "progress":
        bar = QProgressBar()
        bar.setRange(0, int(spec.get("maximum", 100)))
        bar.setValue(int(spec.get("value", 0)))
        return bar
    if kind == "spin":
        spin = QSpinBox()
        spin.setRange(int(spec.get("minimum", 0)), int(spec.get("maximum", 9999)))
        spin.setValue(int(spec.get("value", 0)))
        return spin
    if kind == "slider":
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(spec.get("minimum", 0)), int(spec.get("maximum", 100)))
        slider.setValue(int(spec.get("value", 0)))
        return slider
    if kind == "spacer":
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().Policy.Expanding,
                             spacer.sizePolicy().Policy.Expanding)
        return spacer
    if kind in ("web", "webview"):
        if QWebEngineView is None:
            return QLabel("QtWebEngine is not available in this installation.")
        return QWebEngineView()
    return None


def _fill_children(layout: QLayout, children: list, kind: str, spec: dict,
                   registry: dict, events: Callable[[str, str, dict], None] | None
                   ) -> None:
    """Add every child spec to `layout`, honouring grid cells and form labels."""
    if layout is None:
        return
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        child = dict(child)
        label = child.pop("label", None)
        widget = _build_one(child, registry, events)
        if widget is None:
            continue
        if kind == "grid":
            layout.addWidget(widget, int(child.get("row", index)), int(child.get("col", 0)))
        elif kind == "form" and label is not None:
            layout.addRow(str(label), widget)
        else:
            layout.addWidget(widget)
    if spec.get("stretch") and hasattr(layout, "addStretch"):
        layout.addStretch(1)


def apply_widget_property(widget: QWidget, prop: str, value: Any) -> None:
    """One ``set_widget`` operation, shared by the Python and the process API."""
    text = "" if value is None else str(value)
    if prop in ("text", "html"):
        if hasattr(widget, "setText"):
            widget.setText(text)
        elif hasattr(widget, "setPlainText"):
            # A plain text edit has no setText.
            widget.setPlainText(text)
    elif prop == "append":
        if hasattr(widget, "appendPlainText"):
            widget.appendPlainText(text)
        elif hasattr(widget, "addItem"):
            widget.addItem(text)
        elif hasattr(widget, "setPlainText"):
            widget.setPlainText(widget.toPlainText() + "\n" + text)
    elif prop in ("placeholder",):
        widget.setPlaceholderText(text)
    elif prop == "tooltip":
        widget.setToolTip(text)
    elif prop == "style":
        widget.setStyleSheet(text)
    elif prop == "enabled":
        widget.setEnabled(bool(value))
    elif prop == "highlight":
        # Syntax colors: naming a language is all an addon has to do.
        if hasattr(widget, "document"):
            _attach_highlighter(widget, str(value or ""))
    elif prop == "visible":
        widget.setVisible(bool(value))
    elif prop == "checked":
        widget.setChecked(bool(value))
    elif prop == "value":
        # "value" is the state of the control, whatever kind it is.
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, (QSpinBox, QSlider, QProgressBar)):
            widget.setValue(int(value or 0))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value or 0))
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(text)
        elif hasattr(widget, "setPlainText"):
            widget.setPlainText(text)
        elif hasattr(widget, "setText"):
            widget.setText(text)
    elif prop == "maximum":
        limit = int(value or 0)
        if hasattr(widget, "setMaximum"):
            widget.setMaximum(limit)
        elif hasattr(widget, "setRange"):
            # ``minimum`` is a method, not a number.
            floor = widget.minimum() if callable(getattr(widget, "minimum", None)) else 0
            widget.setRange(min(floor, limit), limit)
    elif prop == "format":
        # Qt expands ``%p``, ``%v`` and ``%m`` in a progress bar's text.
        if hasattr(widget, "setFormat"):
            widget.setFormat(text)
        elif hasattr(widget, "setText"):
            widget.setText(text)
    elif prop == "items":
        items = [str(item) for item in (value or [])]
        if hasattr(widget, "setHeaderLabels"):
            widget.setHeaderLabels(items)
        elif hasattr(widget, "clear") and hasattr(widget, "addItems"):
            # A combo keeps its choice; a list widget has no currentText.
            current = widget.currentText() if hasattr(widget, "currentText") else None
            widget.clear()
            widget.addItems(items)
            if current is not None and current in items:
                widget.setCurrentText(current)
        elif hasattr(widget, "clear"):
            widget.clear()
            widget.addItems(items)
    elif prop in ("current", "index"):
        # Which page a ``tabs`` container shows, or a list's picked row.
        if hasattr(widget, "setCurrentRow"):
            widget.setCurrentRow(int(value or 0))
        elif hasattr(widget, "setCurrentIndex"):
            widget.setCurrentIndex(int(value or 0))
    elif prop == "rows":
        if hasattr(widget, "clear"):
            widget.clear()
        for row in value or []:
            # A row is a list of cells, or a dict carrying colours:
            # ``{"cells": [...], "color": "#rrggbb", "background": "...",
            #   "overlay": "..."}`` - painted by the table's delegate.
            cells, fore, back, over = row, None, None, None
            if isinstance(row, dict):
                cells = row.get("cells", row.get("row", ""))
                fore = row.get("color", row.get("foreground"))
                back = row.get("background", row.get("bg"))
                over = row.get("overlay", row.get("highlight"))
            cells = cells if isinstance(cells, (list, tuple)) else [cells]
            item = QTreeWidgetItem([str(cell) for cell in cells])
            for column, cell in enumerate(cells):
                # Qt elides a wide cell; the tooltip carries the whole text.
                item.setToolTip(column, str(cell))
            for colour, paint in ((fore, item.setForeground),
                                  (back, item.setBackground)):
                if not colour:
                    continue
                tint = QColor(str(colour))
                if not tint.isValid():
                    continue
                brush = QBrush(tint)
                for column in range(len(cells)):
                    paint(column, brush)
            if over:
                tint = QColor(str(over))
                if tint.isValid():
                    brush = QBrush(tint)
                    for column in range(len(cells)):
                        item.setData(column, ROW_OVERLAY_ROLE, brush)
            widget.addTopLevelItem(item)
    elif prop == "clear":
        if hasattr(widget, "clear"):
            widget.clear()


def read_widget_value(widget: QWidget, what: str = "value") -> Any:
    """The counterpart of :func:`apply_widget_property`."""
    numeric = (QSpinBox, QDoubleSpinBox, QSlider, QProgressBar)
    if what == "text":
        return widget.text() if hasattr(widget, "text") else widget.toPlainText()
    if what == "html":
        return widget.toHtml() if hasattr(widget, "toHtml") else None
    if what == "checked":
        return bool(widget.isChecked()) if hasattr(widget, "isChecked") else None
    if what == "items":
        if hasattr(widget, "takeItem"):
            return [widget.takeItem(0).text() for _ in range(widget.count())]
        if hasattr(widget, "item"):
            return [widget.item(i).text() for i in range(widget.count())]
        return None
    if what == "rows":
        out = []
        root = widget.invisibleRootItem() if hasattr(widget, "invisibleRootItem") else None
        if root is not None:
            for i in range(root.childCount()):
                item = root.child(i)
                out.append([item.text(col) for col in range(widget.columnCount())])
        return out
    if what == "selected":
        if hasattr(widget, "selectedItems"):
            out = []
            for item in widget.selectedItems():
                text = getattr(item, "text", None)
                out.append(text(0) if callable(text) else str(item))
            return out
        return None
    if what in ("index", "current"):
        # The page a ``tabs`` container shows, or a combo's entry.
        current = getattr(widget, "currentIndex", None)
        return int(current()) if callable(current) else None
    # Spin boxes and progress bars answer to text() with the formatted label.
    if isinstance(widget, numeric):
        return int(widget.value())
    for name in ("currentText", "text", "toPlainText", "value"):
        accessor = getattr(widget, name, None)
        if callable(accessor):
            try:
                return accessor()
            except (TypeError, RuntimeError):
                continue
    return None


# --- the namespaces on api ---

class SessionAPI:
    """What the app currently knows: profile, settings, connection state."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _win(self) -> QWidget:
        return self._api.win

    def profile(self) -> dict:
        """The active profile. The password is never handed to an addon."""
        cfg = dict(self._win.app_config)
        for key in ("password",):
            cfg.pop(key, None)
        cfg["profile"] = self._win.app_config.get("name", "")
        return jsonable(cfg)

    def settings(self) -> dict:
        """Every application setting except the per-addon stores."""
        cfg = dict(self._win.app_config)
        cfg.pop("addons", None)
        return jsonable(cfg)

    def status(self) -> dict:
        shell = self._win.shell_worker
        shared = getattr(self._win, "shared_conn", None)
        live = bool(shared.active()) if shared is not None else False
        return jsonable({
            "profile": self._win.app_config.get("name", ""),
            "host": self._win.host_input.text().strip(),
            "port": self._win.port_input.value(),
            "username": self._win.username_input.text().strip(),
            "remote_root": self._win.remote_root_input.text().strip(),
            "source": str(self._win.app_config.get("source", "")),
            "sftp_connected": bool(self._win.sftp_connected or live),
            "ssh_connected": bool((shell is not None and shell.isRunning()) or live),
            # The master switch, and whether the SharedConnection is open now.
            "connections_enabled": bool(getattr(
                self._win, "connections_enabled", lambda: True)()),
            "main_connection_open": live,
            "web_port": int(self._win.app_config.get("web_port", 8989)),
            "busy": bool(getattr(self._win, "worker", None) is not None
                         and self._win.worker.isRunning()),
        })

    def web_url(self, tab: dict | None = None) -> str:
        """Build the address a web tab would open, following the profile host."""
        return web_tab_url(tab or {}, self._win.app_config.get("host", "192.168.1.1"))

    def tabs(self) -> list[str]:
        """Every open tab by its full name, not the elided caption."""
        widget = self._win.tabs
        reader = getattr(widget, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is None:
            return [widget.tabText(index) for index in range(widget.count())]
        return [tab_caption(bar, index) for index in range(widget.count())]

    def current_tab(self) -> str:
        """The tab on show by its full name; empty while none is."""
        widget = self._win.tabs
        index = widget.currentIndex()
        if index < 0:
            return ""
        reader = getattr(widget, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is None:
            return widget.tabText(index)
        return tab_caption(bar, index)

    def version(self) -> str:
        return "1.9"

    def app_stylesheet(self) -> str:
        """The app's dark theme, already scaled to the interface zoom."""
        return scaled_sheet(APP_STYLESHEET)

    def zoom(self) -> int:
        """The interface density in percent; 100 is as shipped."""
        return current_zoom()

    def app_icon(self) -> str:
        """The logo the program wears now, as a path; empty if none."""
        return app_icon_path(self._win)

    def rescan_addons(self) -> bool:
        """Look at the addons folder again, on the next turn of the event loop.

        A file an addon installed or deleted only reaches the app through a
        rescan. It is queued rather than run here, because a rescan shuts down
        and starts again every addon it re-reads - the caller among them - and
        answering this request first is what lets a process addon hear about it
        and close itself down politely instead of being cut off mid-call.
        """
        QTimer.singleShot(0, self._api._manager.reload)
        return True

    def set_addon_enabled(self, addon_id: str, enabled: bool) -> dict:
        """Write one addon's switch into the active profile's settings.

        Nothing is started or stopped here: only the record is written, so the
        addon comes up, or stays down, at the next rescan - which is what an
        installer does straight after putting a program in the folder.
        """
        manager = self._api._manager
        record = manager.record_for(str(addon_id))
        record["enabled"] = bool(enabled)
        manager.save_records()
        return jsonable({"enabled": bool(record["enabled"]),
                         "settings": dict(record.get("settings") or {})})


class Connection:
    """A live SSH/SFTP pair, handed out by ``api.device.open()``."""

    def __init__(self, client: Any, sftp: Any, remote_root: str,
                 gate: Callable[[str], None] | None = None) -> None:
        self.client = client
        self.sftp = sftp
        self.remote_root = remote_root
        self._gate = gate

    def run(self, command: str, timeout: int = 30) -> dict:
        if self._gate is not None:
            self._gate("ssh")
        return exec_remote(self.client, command, timeout)

    def remote(self, path: str = "", outside_root: bool = False) -> str:
        return resolve_remote(self.remote_root, path, outside_root)


def resolve_remote(root: str, path: Any, outside_root: bool = False) -> str:
    """Map an addon path onto the device, keeping it under the remote root."""
    text = "" if path is None else str(path)
    root = root.rstrip("/") or "/"
    if not text:
        return root
    if text.startswith("/"):
        normalised = posixpath.normpath(text)
        if outside_root or normalised == root or normalised.startswith(root + "/"):
            return normalised
        raise AddonError(f"{text} is outside the remote directory {root}")
    return remote_join(root, safe_relative_path(text))


class DeviceAPI:
    """SSH commands and SFTP files, always off the GUI thread."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api
        # A device this addon bound for itself, or None to follow the profile.
        self._bound: dict | None = None

    @property
    def _win(self) -> QWidget:
        return self._api.win

    def _settings(self, override: dict | None = None) -> dict:
        """Connection details: profile, bound device and override, in order."""
        base = self._win.current_settings(silent=True)
        settings = dict(base) if base else {}
        if self._bound:
            settings.update(self._bound)
        for key, value in (override or {}).items():
            if value is not None and value != "":
                settings[str(key)] = value
        if not str(settings.get("host", "")).strip():
            raise AddonError(
                "no device to connect to: the profile has no host and nothing "
                "was bound with device.bind()"
            )
        if not str(settings.get("username", "")).strip():
            raise AddonError("no username to connect with")
        if not settings.get("password") and not settings.get("key_file"):
            raise AddonError(
                "no credentials to use: connect once and tick 'Remember password', "
                "or pass a password/key_file with bind() or open(device=...)"
            )
        return settings

    def _check_device(self, device: Any) -> None:
        """Validate a one-off ``device`` dict; it steers only this call."""
        if not device:
            return
        if not isinstance(device, dict):
            raise AddonError("device must be a dict of connection details "
                             "(host, port, username, password, key_file, "
                             "remote_root)")

    def _trace(self, text: str) -> None:
        self._api.log(text)

    def _addon(self) -> Any:
        """The runtime record for the addon calling, or ``None``."""
        return getattr(self._api, "_addon", None)

    def _guard(self, transport: str) -> None:
        """Refuse a transport unless the master switch and ``requires`` allow."""
        win = self._win
        check = getattr(win, "connections_enabled", None)
        if callable(check) and not check():
            raise AddonError(
                "connections are switched off (Settings > Connection)")
        addon = self._addon()
        declared = tuple(getattr(addon, "requires", ()) or ()) if addon is not None else ()
        if transport not in declared:
            wanted = sorted(set(declared) | {transport})
            raise AddonError(
                f"this addon may not use {transport.upper()}: it has to declare "
                f"its connection services, e.g. \"requires\": {wanted} "
                "in its ADDON manifest")

    def _shared(self) -> Any:
        """The app's main connection when this addon shares it, else ``None``."""
        addon = self._addon()
        if addon is not None and not getattr(addon, "is_bundled", False) \
                and getattr(addon, "own_connection", False):
            return None
        return getattr(self._win, "shared_conn", None)

    def _report_shared(self) -> None:
        """Tell the window the main session came up from a pool thread."""
        mark = getattr(self._win, "mark_shared_connected", None)
        if mark is None:
            return
        try:
            self._api._manager.bus.post(mark)
        except Exception:
            pass

    def _open_shared(self, settings: dict) -> Any:
        """Bring the main session up if it is down, and report its state."""
        shared = self._shared()
        if shared is None:
            return None
        if not shared.active():
            try:
                shared.open(settings)
            except Exception as error:
                raise AddonError(f"the main connection could not open: {error}")
            self._report_shared()
        return shared

    def _mark(self, transport: str) -> None:
        """Note the transport for the Settings dot; never fails the call."""
        try:
            self._api._manager.mark_transport(self._api._addon, transport)
        except Exception:
            pass

    # --- one-off SSH commands ---

    def _exec(self, command: str, timeout: Any, device: dict | None = None) -> dict:
        self._check_device(device)
        settings = self._settings(device)
        self._trace(f"$ {command}"
                    + (f" @{settings.get('host')}" if device else ""))
        # Re-check the gates in the worker thread; they may have moved.
        self._guard("ssh")
        if device:
            # A one-off device never uses the app's session.
            shared = None
        else:
            shared = self._open_shared(settings)
        if shared is not None:
            # Main connection: one command on the app's open session.
            code, out, err = shared.run(command, int(_timeout(timeout)))
            return {"code": code, "out": out, "err": err}
        client = None
        try:
            client = create_ssh_client(settings)
            code, out, err = exec_remote(client, command, int(_timeout(timeout) ))
        finally:
            if client is not None:
                client.close()
        return {"code": code, "out": out, "err": err}

    def run(self, command: str, timeout: Any = 30, block: bool = True,
            device: dict | None = None) -> Any:
        """Run one command as {"code","out","err"}, or a Future if not block; device names a one-off connection instead of the app's session."""
        if not str(command).strip():
            raise AddonError("empty command")
        self._check_device(device)
        self._guard("ssh")
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout, device)
        if not block:
            return future
        return _await(future, _timeout(timeout))

    def run_async(self, command: str, on_done: Callable[[dict], None] | None = None,
                  timeout: Any = 30, device: dict | None = None) -> Any:
        """``run`` without waiting: ``on_done(result)`` arrives on the GUI thread."""
        self._check_device(device)
        self._guard("ssh")
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout, device)
        if on_done is not None:
            def deliver(done: Any) -> None:
                try:
                    result = done.result()
                except Exception as error:
                    result = {"code": -1, "out": "", "err": str(error)}
                self._api._manager.bus.post(
                    lambda: guard(on_done, result, reporter=self._api.error))
            future.add_done_callback(deliver)
        return future

    def shell_send(self, line: str) -> bool:
        """Type into the live terminal tab. False when no shell is open."""
        self._guard("ssh")
        self._mark("ssh")
        worker = self._win.shell_worker
        if worker is None or not worker.isRunning():
            return False
        worker.send_line(str(line))
        self._trace(f"shell> {line}")
        return True

    def is_connected(self, protocol: str | None = None) -> bool:
        """Whether a connection this addon could use is up right now."""
        win = self._win
        check = getattr(win, "connections_enabled", None)
        if callable(check) and not check():
            return False
        shared = getattr(win, "shared_conn", None)
        live = bool(shared.active()) if shared is not None else False
        worker = win.shell_worker
        states = {
            "sftp": bool(win.sftp_connected or live),
            "ssh": bool((worker is not None and worker.isRunning()) or live),
        }
        if protocol is None:
            return any(states.values())
        return states.get(str(protocol).lower(), False)

    def mode(self) -> str:
        """``"main"`` shares the app's session, ``"own"`` connects per call."""
        addon = self._addon()
        if addon is not None and not getattr(addon, "is_bundled", False) \
                and getattr(addon, "own_connection", False):
            return "own"
        return "main"

    def set_mode(self, mode: str) -> None:
        """Switch a drop-in addon between the main and its own connection."""
        wanted = str(mode).strip().lower()
        if wanted not in ("main", "own"):
            raise AddonError("mode must be 'main' or 'own'")
        addon = self._addon()
        if addon is None:
            raise AddonError("no addon runtime to store the choice on")
        if getattr(addon, "is_bundled", False):
            raise AddonError("an addon shipped with the program always uses "
                             "the app's main connection")
        manager = self._api._manager
        # The record and the Settings rows live on the GUI thread.
        manager.bus.post(lambda: manager.set_own_connection(addon.id,
                                                            wanted == "own"))

    def bind(self, host: str = "", port: Any = None, username: str = "",
             password: str = "", key_file: str = "",
             remote_root: str = "") -> dict:
        """Point this addon's own connection at another device (drop-in only)."""
        addon = self._addon()
        if addon is not None and getattr(addon, "is_bundled", False):
            raise AddonError("an addon shipped with the program always uses "
                             "the app's main connection; it cannot bind a "
                             "different device")
        if addon is not None and not getattr(addon, "own_connection", False):
            raise AddonError("bind only applies to an addon using its own "
                             "connection: switch it on in Settings, or call "
                             "api.device.set_mode('own') first")
        wanted = str(host or "").strip()
        if not wanted:
            raise AddonError("bind needs a host to connect to")
        bound: dict = {"host": wanted}
        if port is not None and str(port).strip():
            bound["port"] = int(port)
        for key, value in (("username", username), ("password", password),
                           ("key_file", key_file), ("remote_root", remote_root)):
            text = str(value or "").strip()
            if text:
                bound[key] = text
        self._bound = bound
        self._trace(f"bound to {bound['host']}")
        return jsonable(dict(bound))

    def unbind(self) -> None:
        """Drop a bound device; the own connection follows the profile again."""
        self._bound = None
        self._trace("device binding cleared")

    def ensure_connected(self, protocol: str = "sftp") -> bool:
        """Ask the app to connect; returns whether something was already up."""
        kind = str(protocol).lower()
        if kind not in ("sftp", "ssh"):
            raise AddonError("protocol must be 'sftp' or 'ssh'")
        self._guard(kind)
        already = self.is_connected(kind)
        if not already:
            if kind == "sftp":
                self._win.connect_remote()
            else:
                self._win.connect_shell()
        return already

    # --- files ---

    def _sftp(self, operation: str, action: Callable, timeout: Any = 30) -> Any:
        """Run `action(client, sftp)` in the pool, on the shared session or a fresh one."""
        self._guard("sftp")
        self._mark("sftp")
        settings = self._settings()

        def work() -> Any:
            self._guard("sftp")      # the gates may have moved since submitting
            self._trace(f"sftp {operation}")
            shared = self._open_shared(settings)
            if shared is not None:
                with shared.lock:
                    return action(shared.client, shared.sftp,
                                  settings["remote_root"])
            client = None
            sftp = None
            try:
                client = create_ssh_client(settings)
                sftp = client.open_sftp()
                return action(client, sftp, settings["remote_root"])
            finally:
                if sftp is not None:
                    sftp.close()
                if client is not None:
                    client.close()

        return _await(POOL.submit(work), _timeout(timeout))

    @contextmanager
    def open(self, timeout: Any = 30, device: dict | None = None) -> Any:
        """with api.device.open() as conn: a session you keep open; always its own connection, device overrides the details for it alone."""
        self._check_device(device)
        self._guard("sftp")
        self._mark("sftp")
        settings = self._settings(device)
        client = None
        sftp = None
        try:
            client = _await(POOL.submit(create_ssh_client, settings), _timeout(timeout))
            sftp = client.open_sftp()
            yield Connection(client, sftp, settings["remote_root"], gate=self._guard)
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()

    def list(self, path: str = "", outside_root: bool = False) -> list[dict]:
        """The device's listing of one directory, relative to the remote root."""

        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root)
            entries = []
            for attribute in sftp.listdir_attr(target):
                if attribute.filename in {".", ".."}:
                    continue
                entries.append({
                    "name": attribute.filename,
                    "path": posixpath.join(target, attribute.filename),
                    "directory": bool(stat_module.S_ISDIR(attribute.st_mode)),
                    "link": bool(stat_module.S_ISLNK(attribute.st_mode)),
                    "size": int(getattr(attribute, "st_size", 0) or 0),
                    "mtime": int(getattr(attribute, "st_mtime", 0) or 0),
                    "mode": int(getattr(attribute, "st_mode", 0) or 0),
                })
            return sorted(entries, key=lambda item: (not item["directory"], item["name"].lower()))

        return self._sftp("list", work)

    def exists(self, path: str, outside_root: bool = False) -> bool:
        def work(client: Any, sftp: Any, root: str) -> Any:
            try:
                sftp.stat(resolve_remote(root, path, outside_root))
            except OSError:
                return False
            return True

        return self._sftp("stat", work)

    def stat(self, path: str, outside_root: bool = False) -> dict | None:
        def work(client: Any, sftp: Any, root: str) -> Any:
            try:
                info = sftp.stat(resolve_remote(root, path, outside_root))
            except OSError:
                return None
            return {
                "size": int(info.st_size or 0),
                "mtime": int(info.st_mtime or 0),
                "mode": int(info.st_mode or 0),
                "directory": bool(stat_module.S_ISDIR(info.st_mode or 0)),
            }

        return self._sftp("stat", work)

    def read_bytes(self, path: str, outside_root: bool = False) -> bytes:
        def work(client: Any, sftp: Any, root: str) -> Any:
            with sftp.open(resolve_remote(root, path, outside_root), "rb") as handle:
                return handle.read()

        return self._sftp("read", work)

    def read_text(self, path: str, encoding: str = "utf-8",
                  outside_root: bool = False) -> str:
        return self.read_bytes(path, outside_root).decode(encoding, errors="replace")

    def write_bytes(self, path: str, payload: bytes, mkdir: bool = True,
                    outside_root: bool = False) -> int:
        data = bytes(payload)

        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root)
            if mkdir:
                _make_dirs(sftp, posixpath.dirname(target))
            with sftp.open(target, "wb") as handle:
                handle.write(data)
            return len(data)

        return self._sftp("write", work)

    def write_text(self, path: str, text: Any, encoding: str = "utf-8", mkdir: bool = True,
                   outside_root: bool = False) -> int:
        return self.write_bytes(path, str(text).encode(encoding), mkdir, outside_root)

    def append_text(self, path: str, text: Any, outside_root: bool = False) -> int:
        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root)
            _make_dirs(sftp, posixpath.dirname(target))
            existing = b""
            try:
                with sftp.open(target, "rb") as handle:
                    existing = handle.read()
            except OSError:
                pass
            payload = existing + (str(text) + "\n").encode("utf-8")
            with sftp.open(target, "wb") as handle:
                handle.write(payload)
            return len(payload)

        return self._sftp("append", work)

    def mkdir(self, path: str) -> str:
        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root=True)
            _make_dirs(sftp, target)
            return target

        return self._sftp("mkdir", work)

    def remove(self, path: str, recursive: bool = False,
               outside_root: bool = False) -> bool:
        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root)
            if target.rstrip("/") == (root.rstrip("/") or "/"):
                raise AddonError("refusing to remove the remote root itself")
            info = sftp.stat(target)
            if stat_module.S_ISDIR(info.st_mode or 0):
                if not recursive:
                    raise AddonError(f"{path} is a directory (pass recursive=True)")
                _remove_tree(sftp, target)
            else:
                sftp.remove(target)
            return True

        return self._sftp("remove", work)

    def rename(self, path: str, new_path: str, outside_root: bool = False) -> str:
        def work(client: Any, sftp: Any, root: str) -> Any:
            source = resolve_remote(root, path, outside_root)
            target = resolve_remote(root, new_path, outside_root)
            sftp.rename(source, target)
            return target

        return self._sftp("rename", work)

    def upload(self, local: Any, remote_path: str, outside_root: bool = False) -> str:
        source = Path(str(local)).expanduser()
        if not source.is_file():
            raise AddonError(f"no such local file: {source}")

        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, remote_path or source.name, outside_root)
            _make_dirs(sftp, posixpath.dirname(target))
            sftp.put(str(source), target)
            return target

        return self._sftp("upload", work)

    def download(self, remote_path: str, local: Any = None,
                 outside_root: bool = False) -> str:
        target = Path(str(local or Path(str(remote_path)).name)).expanduser()

        def work(client: Any, sftp: Any, root: str) -> Any:
            source = resolve_remote(root, remote_path, outside_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(source, str(target))
            return str(target)

        return self._sftp("download", work)


def _make_dirs(sftp: Any, path: str) -> None:
    """mkdir -p on the device, ignoring the root itself."""
    from manager_net import make_remote_directories

    if path and path != "/":
        make_remote_directories(sftp, path)


def _remove_tree(sftp: Any, directory: str) -> None:
    """rm -r on the device, deepest entries first."""
    for attribute in sftp.listdir_attr(directory):
        child = posixpath.join(directory, attribute.filename)
        if stat_module.S_ISDIR(attribute.st_mode or 0):
            _remove_tree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(directory)


class UIAPI:
    """Dialogs, the menu bar and the status line."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _win(self) -> QWidget:
        return self._api.win

    def information(self, title: str, text: str) -> None:
        QMessageBox.information(self._win, self._titled(title), str(text))

    def warning(self, title: str, text: str) -> None:
        QMessageBox.warning(self._win, self._titled(title), str(text))

    def error(self, title: str, text: str) -> None:
        QMessageBox.critical(self._win, self._titled(title), str(text))

    def question(self, title: str, text: str, default: bool = False) -> bool:
        button = QMessageBox.StandardButton.Yes if default else QMessageBox.StandardButton.No
        answer = QMessageBox.question(
            self._win, self._titled(title), str(text),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, button,
        )
        return answer == QMessageBox.StandardButton.Yes

    def input(self, title: str, label: str, default: str = "", secret: bool = False) -> str | None:
        from PyQt6.QtWidgets import QInputDialog

        mode = QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal
        text, accepted = QInputDialog.getText(self._win, self._titled(title), str(label), mode, str(default))
        return text if accepted else None

    def choose(self, items: list[str]) -> str | None:
        """A small menu at the mouse pointer; returns the picked entry."""
        menu = QMenu(self._win)
        chosen: list[str] = []
        for item in items or []:
            action = menu.addAction(str(item))
            action.triggered.connect(lambda _=False, text=str(item): chosen.append(text))
        from PyQt6.QtGui import QCursor

        menu.exec(QCursor.pos())
        return chosen[0] if chosen else None

    def pick_color(self, title: str = "Choose a colour", initial: str = "") -> str:
        """The app's colour wheel; ``#rrggbb`` or ``""`` when nothing was picked."""
        try:
            from manager_widgets import ColorWheelDialog

            return ColorWheelDialog.get_colour(initial, self._win,
                                               self._titled(title))
        except Exception:                        # a wheel-less app still asks
            pass
        from PyQt6.QtWidgets import QColorDialog

        start = QColor(str(initial))
        if not str(initial or "").strip() or not start.isValid():
            start = QColor("#ffb020")
        colour = QColorDialog.getColor(start, self._win, self._titled(title))
        return colour.name() if colour.isValid() else ""

    def popup(self, title: str, text: str) -> None:
        """A tray-style notice: the status line plus the log."""
        self._win.statusBar().showMessage(str(text), 6000)

    def notify(self, text: str, ms: int = 5000) -> None:
        self._win.statusBar().showMessage(str(text), max(500, int(ms)))

    def window(self) -> QWidget:
        """The main window. Use sparingly - it is the app itself."""
        return self._win

    def menu_item(self, text: str, callback: Callable[[], None],
                  menu: str = "Tools",
                  shortcut: str | None = None) -> QAction:
        """Add an entry to the menu bar for as long as the addon stays enabled."""
        target = self._find_menu(menu)
        action = target.addAction(str(text))
        action.triggered.connect(lambda _checked=False: callback())
        if shortcut:
            from PyQt6.QtGui import QKeySequence

            action.setShortcut(QKeySequence(str(shortcut)))
        self._api._addon.actions.append((target, action))
        return action

    def remove_menu_item(self, action: QAction) -> None:
        for stored_menu, stored in list(self._api._addon.actions):
            if stored is action:
                stored_menu.removeAction(action)
                self._api._addon.actions.remove((stored_menu, stored))

    def _find_menu(self, title: str) -> QMenu:
        wanted = str(title).replace("&", "").strip().lower()
        bar = self._win.menuBar()
        for action in bar.actions():
            if action.text().replace("&", "").strip().lower() == wanted:
                menu = action.menu()
                if menu is not None:
                    return menu
        menu = bar.addMenu(str(title))
        return menu

    def dialog(self, spec: dict, title: str = "Input", ok: bool = True) -> dict | None:
        """A modal dialog built from a widget spec; returns its values by id."""
        from PyQt6.QtWidgets import QDialogButtonBox

        box = QDialog(self._win)
        box.setWindowTitle(self._titled(title))
        layout = QVBoxLayout(box)
        body, registry = build_spec(spec if isinstance(spec, dict) else {"kind": "column", "children": spec})
        layout.addWidget(body, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(box.accept)
        buttons.rejected.connect(box.reject)
        layout.addWidget(buttons)
        if not box.exec():
            return None
        values = {}
        for identifier, widget in registry.items():
            values[identifier] = read_widget_value(widget)
        if not ok:
            return values
        return values

    def set_setting_widget(self, widget_or_spec: Any) -> None:
        """Extra Settings-tab controls for this addon, under its own switch."""
        self._api._manager.set_setting_widget(self._api._addon, widget_or_spec)

    def _titled(self, text: str) -> str:
        """Prefix dialog titles so it is obvious which addon is speaking."""
        label = str(text or "")
        name = self._api.name
        return f"{label} - {name}" if name and name.lower() not in label.lower() else label


def _is_web_view(widget: Any) -> bool:
    """True for an embedded page; QtWebEngine is optional."""
    return QWebEngineView is not None and isinstance(widget, QWebEngineView)


def app_icon_path(win: Any) -> str:
    """The app's current logo as a path; the window is what knows it."""
    getter = getattr(win, "current_icon_path", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "")
    except Exception:                        # a window with no icon is a window
        return ""


def build_icon(source: Any, home: Path | None = None) -> QIcon | None:
    """A tab's mark out of an SVG document (as text) or a picture file (absolute, or beside the addon's folder); anything unreadable returns None."""
    from PyQt6.QtCore import QByteArray

    text = str("" if source is None else source).strip()
    if not text:
        return None
    if text.startswith(("<svg", "<?xml")):
        image = QPixmap()
        if not image.loadFromData(QByteArray(text.encode("utf-8")), "SVG"):
            return None
        image = image.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        return None if image.isNull() else QIcon(image)
    path = Path(text)
    if not path.is_absolute() and home is not None:
        path = Path(home) / text
    if not path.is_file():
        return None
    icon = QIcon(str(path))
    return None if icon.isNull() else icon


class TabsAPI:
    """Tabs the addon owns: embedded pages and custom Qt panels."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _win(self) -> QWidget:
        return self._api.win

    @property
    def _store(self) -> dict:
        return self._api._addon.tabs

    def _before_settings(self) -> int:
        # The window pins a gap + Settings at the far right; new tabs go just
        # to the left of that pair.
        helper = getattr(self._win, "_content_insert_index", None)
        if callable(helper):
            try:
                return int(helper())
            except Exception:
                pass
        page = getattr(self._win, "settings_page", None)
        index = self._win.tabs.indexOf(page) if page is not None else -1
        return index if index >= 0 else self._win.tabs.count()

    def _index(self, position: Any) -> int:
        """Where to put a new tab: before Settings, unless the addon says otherwise."""
        if position is None:
            return self._before_settings()
        return max(0, min(int(position), self._win.tabs.count()))

    def names(self) -> list[str]:
        return list(self._store)

    def exists(self, name: str) -> bool:
        return str(name) in self._store

    def add_web_tab(self, name: str, url: str = "", ip: str = "", port: int = 0,
                    path: str = "", scheme: str = "", position: int | None = None) -> str:
        """An embedded page belonging to no profile: not stored, not rebuilt; give either a full url or ip/port/path/scheme."""
        name = str(name).strip()
        if not name:
            raise AddonError("a tab needs a name")
        if name in self._store:
            self.close(name)
        tab = dict(_tab_definition(url, ip, port, path, scheme))
        tab["name"] = name
        entry = self._win.add_transient_web_tab(tab, self._index(position))
        self._store[name] = {
            "page": entry["page"], "url_input": entry["url_input"],
            "view": entry["view"], "tab": tab, "web": True, "registry": {},
        }
        self._api.log(f"opened web tab '{name}' -> {entry['url_input'].text()}")
        return name

    def add_tab(self, name: str, widget: QWidget | None = None, spec: dict | None = None,
                style: str = "", position: int | None = None) -> str:
        """A custom tab: any QWidget, or one built from a spec dict; the new tab does not steal the view, use activate to show it."""
        name = str(name).strip()
        if not name:
            raise AddonError("a tab needs a name")
        if widget is None and spec is None:
            raise AddonError("add_tab needs a widget or a spec")
        self.close(name)
        registry: dict[str, QWidget] = {}
        if widget is None:
            events = lambda identifier, event, payload, tab_name=name: (
                self._api._manager.push_widget_event(self._api.id, tab_name, identifier, event, payload))
            widget, registry = build_spec(spec, events=events)
        if style:
            widget.setStyleSheet(str(style))
        self._win.tabs.insertTab(self._index(position), widget, name)
        self._store[name] = {
            "page": widget, "url_input": None, "view": widget if _is_web_view(widget) else None,
            "tab": {"name": name}, "web": False, "registry": registry,
        }
        self._refresh_tab_dots()
        self._api.log(f"opened tab '{name}'")
        return name

    def close(self, name: str) -> bool:
        entry = self._store.pop(str(name), None)
        if entry is None:
            return False
        self._win.remove_transient_tab(entry["page"])
        index = self._win.tabs.indexOf(entry["page"])
        if index >= 0:
            self._win.tabs.removeTab(index)
        entry["page"].deleteLater()
        self._refresh_tab_dots()
        return True

    def _refresh_tab_dots(self) -> None:
        """Re-put the transport dots after this addon's tab moved in or out."""
        sync = getattr(self._win, "sync_tab_transport_dots", None)
        if callable(sync):
            try:
                sync()
            except Exception:                # a decoration must never break a tab
                pass

    def rename(self, name: str, new_name: str) -> bool:
        entry = self._store.get(str(name))
        if entry is None:
            return False
        self._win.tabs.setTabText(self._win.tabs.indexOf(entry["page"]), str(new_name))
        self._store[str(new_name)] = self._store.pop(str(name))
        return True

    def activate(self, name: str) -> bool:
        entry = self._store.get(str(name))
        if entry is None:
            return False
        self._win.tabs.setCurrentWidget(entry["page"])
        return True

    def url(self, name: str) -> str:
        entry = self._store.get(str(name))
        if entry is None or entry["url_input"] is None:
            return ""
        return entry["url_input"].text()

    def navigate(self, name: str, url: str) -> None:
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        if entry["url_input"] is not None:
            entry["url_input"].setText(str(url))
        if entry["view"] is not None:
            entry["view"].setUrl(QUrl(str(url)))

    def reload(self, name: str) -> None:
        entry = self._store.get(str(name))
        if entry is None:
            return
        if entry["view"] is not None:
            entry["view"].reload()
        elif entry["url_input"] is not None:
            self.navigate(name, entry["url_input"].text())

    def view(self, name: str) -> Any:
        """The QWebEngineView behind a tab, for the full page API."""
        entry = self._store.get(str(name))
        return None if entry is None else entry["view"]

    def widget(self, name: str, identifier: str | None = None) -> QWidget | None:
        entry = self._store.get(str(name))
        if entry is None:
            return None
        if identifier is None:
            return entry["page"]
        return entry["registry"].get(str(identifier))

    def set_widget(self, name: str, identifier: str, prop: str = "text",
                   value: Any = None) -> bool:
        widget = self.widget(name, identifier)
        if widget is None:
            return False
        apply_widget_property(widget, str(prop), value)
        return True

    def widget_value(self, name: str, identifier: str, what: str = "value") -> Any:
        widget = self.widget(name, identifier)
        if widget is None:
            raise AddonError(f"{name!r} has no widget with id {identifier!r}")
        return jsonable(read_widget_value(widget, what))

    def set_html(self, name: str, html: str) -> None:
        """Render markup in a web tab without a server - a fully custom page."""
        view = self.view(name)
        if view is None:
            raise AddonError(f"{name!r} is not an embedded page")
        page = str(html)
        if "<html" not in page.lower():
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{background:#121212;color:#e0e0e0;"
                "font-family:'Segoe UI',sans-serif;margin:18px}</style></head><body>"
                + page + "</body></html>"
            )
        self._api._manager.bus.post(lambda: view.setHtml(page))

    def inject_css(self, name: str, css: str) -> None:
        """Style a page the device serves - the addon's own look on top."""
        view = self.view(name)
        if view is None:
            raise AddonError(f"{name!r} is not an embedded page")
        script = (
            "var s=document.getElementById('addon-style');"
            "if(!s){s=document.createElement('style');s.id='addon-style';"
            "document.head.appendChild(s);}"
            "s.innerHTML=" + _js_string(str(css)) + ";"
        )
        self._api._manager.bus.post(lambda: view.page().runJavaScript(script))

    def inject_js(self, name: str, code: Any,
                  callback: Callable[[Any], None] | None = None) -> None:
        view = self.view(name)
        if view is None:
            raise AddonError(f"{name!r} is not an embedded page")
        self._api._manager.bus.post(
            lambda: view.page().runJavaScript(str(code), callback) if callback
            else view.page().runJavaScript(str(code)))

    def set_style(self, name: str, css: str) -> None:
        """A Qt stylesheet on a custom tab (for web tabs this themes the frame)."""
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        entry["page"].setStyleSheet(str(css))

    def set_icon(self, name: str, icon: Any = None) -> bool:
        """A mark beside the tab's label (SVG text or a picture file); None clears; an embedded page keeps following its favicon."""
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        index = self._win.tabs.indexOf(entry["page"])
        if index < 0:
            return False
        wanted = str("" if icon is None else icon).strip()
        mark = None
        if wanted:
            mark = build_icon(wanted, self._api._addon.home)
            if mark is None:
                self._api.log("that tab icon could not be read, so nothing changed")
                return False
        entry.pop("app_icon", None)
        if wanted:
            entry["icon_mark"] = wanted
        else:
            entry.pop("icon_mark", None)
        self._win.tabs.setTabIcon(index, mark or QIcon())
        # The bar hands out the default picture and re-measures captions:
        # re-fit after this one.
        reader = getattr(self._win.tabs, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is not None and hasattr(bar, "refit"):
            bar.refit()
        return True

    def icon(self, name: str) -> bool:
        """Whether this addon's tab carries a mark of its own."""
        entry = self._store.get(str(name))
        if entry is None:
            return False
        return bool(entry.get("icon_mark") or entry.get("app_icon"))

    def use_app_icon(self, name: str, on: bool = True) -> bool:
        """Let this tab wear the app's logo and keep following it when it swaps; an embedded page is not marked, it shows the device's favicon."""
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        if entry.get("web"):
            return False
        entry["app_icon"] = bool(on)
        if not on:
            index = self._win.tabs.indexOf(entry["page"])
            if index >= 0:
                self._win.tabs.setTabIcon(index, QIcon())
            return True
        if not app_icon_path(self._win):
            self._api.log("the app has no icon of its own to follow on this machine")
        self._api._manager.refresh_app_icons()
        return True


def _js_string(text: str) -> str:
    """Safely quote a Python string as a JavaScript literal."""
    import json

    return json.dumps(text)


def _tab_definition(url: Any, ip: Any, port: Any, path: Any,
                    scheme: Any) -> dict:
    """Split a tab request into the shape the browser code expects."""
    text = str(url or "").strip()
    if text:
        parsed = urlparse(text if "//" in text else "//" + text)
        return {
            "name": "",
            "ip": parsed.hostname or str(ip or ""),
            "port": int(parsed.port or 0) or (443 if (parsed.scheme or "http") == "https" else 80),
            "path": parsed.path or str(path or "/"),
            "scheme": (parsed.scheme or str(scheme or "http")).lower(),
        }
    return {
        "name": "",
        "ip": str(ip or ""),
        "port": int(port or (443 if str(scheme or "http") == "https" else 80)),
        "path": str(path or "/"),
        "scheme": str(scheme or "http").lower(),
    }


class EventsAPI:
    """Subscribe to what the app does."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def on(self, name: str, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Register a listener; returns the callable that unregisters it."""
        return self._api._manager.add_listener(str(name), self._api.id, callback)

    def off(self, name: str, callback: Callable[[dict], None] | None = None) -> int:
        return self._api._manager.remove_listener(str(name), self._api.id, callback)

    def names(self) -> list[str]:
        return list(self._api._manager.EVENT_NAMES)


class SettingsAPI:
    """The addon's own keys in config.json, saved with the profile."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def _record(self) -> dict:
        return self._api._manager.record_for(self._api.id)

    def all(self) -> dict:
        return jsonable(dict(self._record().get("settings") or {}))

    def get(self, key: str, default: Any = None) -> Any:
        return jsonable((self._record().get("settings") or {}).get(str(key), default))

    def set(self, key: str, value: Any) -> None:
        self.update({key: value})

    def update(self, values: dict) -> None:
        record = self._record()
        store = dict(record.get("settings") or {})
        store.update({str(k): v for k, v in (values or {}).items()})
        record["settings"] = store
        self._api._manager.save_records()

    def pop(self, key: str, default: Any = None) -> Any:
        record = self._record()
        store = dict(record.get("settings") or {})
        gone = store.pop(str(key), default)
        record["settings"] = store
        self._api._manager.save_records()
        return jsonable(gone)

    def remove(self) -> None:
        record = self._record()
        record.pop("settings", None)
        self._api._manager.save_records()


class ConfigAPI:
    """The addon's own file in ``addons/_data/<id>``, shared by every profile.

    Declare the keys once from ``init`` and the app shows them in
    Settings > Addons."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api
        self._addon = api._addon

    # --- the file ---

    @property
    def file(self) -> Path:
        return addon_config_path(self._api.id)

    def path(self) -> str:
        """The same location as text, which is what crosses a process boundary."""
        return str(self.file)

    def exists(self) -> bool:
        return self.file.is_file()

    def open_folder(self) -> None:
        """Show the addon's own ``addons/_data/<id>`` folder."""
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(addon_data_dir(self._api.id))))

    # --- what the keys mean ---

    def declare(self, fields: list | None = None, defaults: dict | None = None,
                title: str = "") -> dict:
        """Register the schema and write the file with defaults if missing; kind is text/number/bool/choice/list/multiline, a dotted key reads and writes inside that section."""
        clean = []
        for field in fields or []:
            if isinstance(field, dict) and field.get("key"):
                clean.append(dict(field))
        self._addon.config_fields = clean
        self._addon.config_defaults = jsonable(dict(defaults or {}))
        self._addon.config_title = str(title or "")
        if clean:
            # A side file keeps the form showing while the addon is off.
            guard(lambda: write_config_json(
                addon_schema_path(self._api.id),
                {"title": self._addon.config_title,
                 "fields": clean,
                 "defaults": self._addon.config_defaults}))
        if not self.exists():
            guard(lambda: write_config_json(self.file, self._addon.config_defaults))
        return self.all()

    def schema(self) -> list:
        """The fields declared by ``declare``, for the Settings form."""
        fields = getattr(self._addon, "config_fields", None)
        if fields:
            return jsonable(list(fields))
        return jsonable(read_config_json(addon_schema_path(self._api.id)).get("fields") or [])

    def defaults(self) -> dict:
        stored = getattr(self._addon, "config_defaults", None)
        if stored:
            return jsonable(dict(stored))
        # Switched off: the side file is all the app has.
        return jsonable(read_config_json(addon_schema_path(self._api.id)).get("defaults") or {})

    # --- the values ---

    def stored(self) -> dict:
        """Exactly what is in the file, with nothing filled in."""
        return jsonable(read_config_json(self.file))

    def all(self) -> dict:
        """The file over the declared defaults, read fresh from disk."""
        return jsonable(merge_config(self.defaults(), self.stored()))

    def get(self, key: str, default: Any = None) -> Any:
        return jsonable(config_get(self.all(), str(key), default))

    def set(self, key: str, value: Any) -> None:
        self.update({key: value})

    def update(self, values: dict) -> dict:
        """Write the given keys and return the whole merged view."""
        def apply() -> dict:
            merged = merge_config(self.defaults(), read_config_json(self.file))
            for key, value in (values or {}).items():
                put_config_value(merged, str(key), value)
            write_config_json(self.file, merged)
            return jsonable(merged)

        return guard(apply, reporter=self._api.error) or {}

    def pop(self, key: str, default: Any = None) -> Any:
        """Remove a key so the default applies again."""
        def apply() -> Any:
            stored = read_config_json(self.file)
            gone = take_config_value(stored, str(key))
            write_config_json(self.file, stored)
            return jsonable(default if gone is None else gone)

        return guard(apply, reporter=self._api.error) or jsonable(default)

    def reset(self) -> dict:
        """Throw the file away and go back to the addon's defaults."""
        def apply() -> dict:
            try:
                self.file.unlink()
            except OSError:
                pass
            merged = self._addon.config_defaults
            write_config_json(self.file, merged)
            return jsonable(merged)

        return guard(apply, reporter=self._api.error) or {}

    def text(self) -> str:
        """The file as written, for an editor."""
        def read() -> str:
            if self.file.is_file():
                return self.file.read_text(encoding="utf-8")
            return json.dumps(self.all(), indent=2, ensure_ascii=False) + "\n"

        return guard(read, reporter=self._api.error) or ""

    def save_text(self, raw: str) -> dict:
        """Replace the file with hand-written JSON; refuses anything invalid."""
        def apply() -> dict:
            data = json.loads(str(raw))
            if not isinstance(data, dict):
                raise AddonError("a config file has to be one JSON object")
            write_config_json(self.file, jsonable(data))
            return jsonable(merge_config(self.defaults(), data))

        return guard(apply, reporter=self._api.error) or {}


class SchedulerAPI:
    """Timers that stop themselves when the addon is switched off."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def after(self, seconds: Any, callback: Callable[[], None]) -> QTimer:
        return self._start(float(seconds) * 1000.0, callback, False)

    def every(self, seconds: Any, callback: Callable[[], None]) -> QTimer:
        return self._start(max(100.0, float(seconds) * 1000.0), callback, True)

    def _start(self, ms: float, callback: Callable[[], None], repeat: bool) -> QTimer:
        timer = QTimer()
        timer.setSingleShot(not repeat)
        timer.timeout.connect(lambda: _call(callback))
        timer.start(int(ms))
        self._api._addon.timers.append(timer)
        return timer


def _call(callback: Callable[[], None]) -> None:
    callback()


class PathsAPI:
    """Folders an addon may read and write."""

    def __init__(self, api: "AddonAPI") -> None:
        self._addon = api._addon
        self._api = api

    @property
    def addon(self) -> Path:
        return self._addon.home

    @property
    def data(self) -> Path:
        folder = self._addon.data_dir
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def config(self) -> Path:
        """This addon's own file, in its ``addons/_data/<id>`` folder."""
        return addon_config_path(self._api.id)

    @property
    def configs(self) -> Path:
        """The addon's own folder, where its config and data live together."""
        folder = addon_data_dir(self._api.id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def base(self) -> Path:
        from manager_const import BASE_DIR

        return BASE_DIR

    @property
    def addons(self) -> Path:
        from manager_const import ADDONS_DIR

        return ADDONS_DIR

    @property
    def assets(self) -> Path:
        """The pictures shipped inside the program; read-only in spirit."""
        from manager_const import ASSETS_DIR

        return ASSETS_DIR

    @property
    def log(self) -> Path:
        return LOG_FILE


class PeersAPI:
    """Look at, and talk to, the other addons."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def list(self) -> list[str]:
        return [addon.id for addon in self._api._manager.loaded()]

    def info(self, addon_id: str) -> dict | None:
        addon = self._api._manager.find(str(addon_id))
        if addon is None:
            return None
        return jsonable({"id": addon.id, "name": addon.name, "version": addon.version,
                         "enabled": addon.enabled, "kind": addon.kind})

    def send(self, addon_id: str, data: Any = None) -> bool:
        return self._api._manager.deliver(self._api.id, str(addon_id), data)


class AddonAPI:
    """The object every addon receives: ``def init(api)``."""

    def __init__(self, manager: Any, addon: Any) -> None:
        from manager_const import BASE_DIR

        self._manager = manager
        self._addon = addon
        self.win = manager.window
        self.id = addon.id
        self.name = addon.name
        self.api_version = API_VERSION
        self.base_dir = BASE_DIR
        self.session = SessionAPI(self)
        self.device = DeviceAPI(self)
        self.ui = UIAPI(self)
        self.tabs = TabsAPI(self)
        self.events = EventsAPI(self)
        self.settings = SettingsAPI(self)
        self.config = ConfigAPI(self)
        self.schedule = SchedulerAPI(self)
        self.paths = PathsAPI(self)
        self.peers = PeersAPI(self)

    def log(self, text: Any) -> None:
        """Write to the app log under this addon's name."""
        self._manager.log(self.id, str(text))

    def error(self, text: Any) -> None:
        self._manager.log(self.id, f"error: {text}", warning=True)

    def call(self, method: str, arguments: dict | None = None) -> Any:
        """Invoke an API method by name - the entry point for process addons."""
        parts = str(method).split(".")
        target = self
        for part in parts[:-1]:
            target = getattr(target, part, None)
            if target is None:
                raise AddonError(f"no such api namespace: {method}")
        function = getattr(target, parts[-1], None)
        if not callable(function):
            raise AddonError(f"no such api method: {method}")
        values = {str(key): value for key, value in (arguments or {}).items()}
        # A JSON addon names the spec key "id"; Python uses "identifier".
        if "id" in values and "identifier" not in values:
            try:
                parameters = inspect.signature(function).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "identifier" in parameters and "id" not in parameters:
                values["identifier"] = values.pop("id")
        return function(**values)
