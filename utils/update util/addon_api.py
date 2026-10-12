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
    Everything the current rasconf Manager session knows:
    ``profile()`` (active profile, never the password), ``settings()``,
    ``status()`` (host/port/user/remote root plus which protocols are up),
    ``tabs()``, ``current_tab()``, ``version()``, ``app_stylesheet()`` (already
    scaled to the interface zoom), ``zoom()`` (that percentage), ``app_icon()``
    (the logo the program wears itself).

``api.device``
    SSH and SFTP.
    ``run(command, timeout=30)`` -> ``{"code", "out", "err"}`` (runs in the
    background and waits; use ``run_async(command, on_done)`` from ``init``),
    ``shell_send(line)`` writes to the live terminal tab,
    ``ensure_connected("ssh"|"sftp")``, ``is_connected()``.
    Files: ``list()``, ``exists()``, ``stat()``, ``read_text()``,
    ``read_bytes()``, ``write_text()``, ``write_bytes()``, ``mkdir()``,
    ``remove()``, ``rename()``, ``upload()``, ``download()`` - each takes
    ``outside_root=True`` to reach above the profile's deploy directory
    (``/etc/ssl`` and the like); without it a path stays inside that folder.
    ``open()`` is a context manager yielding a live ``Connection`` with
    ``.client`` / ``.sftp`` (raw paramiko) for anything not covered above.
    ``deploy(dry_run=False)`` runs the app's own deploy so the UI updates.

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
``tabs`` (``text`` per child is the tab label).
``label``, ``button``, ``line``, ``multi`` (plain text edit), ``check``,
``combo``, ``list``, ``tree``, ``table``, ``progress``, ``spin``, ``slider``,
``spacer``, ``web`` - all take ``id``, ``text``, ``tooltip``, ``style``
(a stylesheet, so an addon can style its own tab), ``enabled``, ``visible``,
``font_size`` (drawn at the interface zoom), ``items``, ``value``,
``placeholder``, ``checked``, ``minimum``,
``maximum``, ``columns``, ``word_wrap``, ``readonly``, ``multi``.
``value`` sets whatever the kind holds: text, the chosen entry, the number, or
the tick for a ``check``; ``widget_value`` reads it back the same way.
A ``multi`` may name a ``highlight`` language (``python``, ``json``, ``html``,
``css``, ``javascript``, ``ini``) for syntax colors; change it later with
``set_widget(tab, id, "highlight", lang)``.
``on`` lists the events sent back to the addon: ``clicked``, ``toggled``,
``changed``, ``returned``, ``selected`` and, for a ``table``/``tree``/``list``,
``context`` (a right-click, carrying the text of the row under the cursor).
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

from PyQt6.QtCore import QEventLoop, Qt, QTimer, QUrl, QThread, QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
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
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
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

try:  # the web kinds are inert when QtWebEngine is not installed
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None


#: One shared pool, so twenty addons cannot open twenty SSH connections.
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="addon")


def _timeout(seconds, default: float = 30.0) -> float:
    """Clamp an addon-supplied timeout to something sane."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1.0), 600.0)


def _await(future: concurrent.futures.Future, timeout: float):
    """Wait for a pooled task without freezing the interface.

    On the GUI thread a private event loop runs while the task is in flight -
    the same trick a modal dialog uses - so the window keeps painting. From any
    other thread a plain blocking wait is right.
    """
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


# ---------------------------------------------------------------------------
# Addon configuration files: addons/_data/<id>/config.json
# ---------------------------------------------------------------------------
# Read, write, merge and dotted-key helpers for those files live in
# addon_common, together with the path of the file itself, so the same code
# serves the app, a compiled addon and an addon's own test script. They are
# imported at the top of this module and stay reachable as addon_api.*.


# ---------------------------------------------------------------------------
# Declarative widgets
# ---------------------------------------------------------------------------

_LAYPOUT_KINDS = {
    "column": QVBoxLayout, "vbox": QVBoxLayout, "row": QHBoxLayout, "hbox": QHBoxLayout,
}


def build_spec(spec: dict, registry: dict[str, QWidget] | None = None,
               events: Callable[[str, str, dict], None] | None = None):
    """Turn a spec dict into a QWidget, filling `registry` with id -> widget.

    `events(name, event, payload)` is wired to whatever the spec asked for with
    ``on``, which is how a non-Qt addon learns that its button was pressed.
    """
    registry = {} if registry is None else registry
    widget = _build_one(spec, registry, events)
    if widget is None:
        raise AddonError(f"unknown widget kind: {spec.get('kind')!r}")
    return widget, registry


def _apply_common(widget: QWidget, spec: dict, registry: dict, events) -> None:
    identifier = spec.get("id")
    if identifier:
        registry[str(identifier)] = widget
    if spec.get("text") is not None and hasattr(widget, "setText"):
        widget.setText(str(spec["text"]))
    if spec.get("placeholder") is not None and hasattr(widget, "setPlaceholderText"):
        widget.setPlaceholderText(str(spec["placeholder"]))
    if spec.get("word_wrap") is not None and hasattr(widget, "setWordWrap"):
        widget.setWordWrap(bool(spec["word_wrap"]))
    if spec.get("checked") is not None and hasattr(widget, "setChecked"):
        widget.setChecked(bool(spec["checked"]))
    # A combo or a list is filled first so "value" can then pick from it.
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
        # The addon's own size, drawn at the zoom the interface is using.
        font.setPointSize(zoom_size(int(spec["font_size"])))
        widget.setFont(font)
    wanted = spec.get("on")
    if events is not None and wanted is not None:
        _connect_events(widget, wanted, str(identifier or ""), events)


def _connect_events(widget, wanted, identifier: str, events) -> None:
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
        # Right-click. The listing's policy is set where it is built, so this
        # signal fires; the text of the row under the cursor is sent along so
        # the addon knows what was clicked without a second round trip. A menu
        # is then shown from the addon with ``api.ui.choose``.
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


def _context_text(item) -> str:
    """The first-column label of the row a right-click landed on, or nothing."""
    if item is None:
        return ""
    getter = getattr(item, "text", None)
    if callable(getter):
        return str(getter(0) or "")
    return str(getattr(item, "text", "") or "")


# ---------------------------------------------------------------------------
# Syntax highlighting for a ``multi`` (an editor tab)
#
# The highlighter lives here, in the app process, next to the QPlainTextEdit it
# decorates. An addon - imported or compiled - only names a language in its spec
# (``"highlight": "python"``) or changes it later (``set_widget(tab, id,
# "highlight", "json")``); the colors are chosen for the dark editor background
# the app uses. Rules are matched line by line, which is enough to shade
# keywords, strings, comments and numbers for the handful of languages a config
# file, a shell script or a web page is written in.
# ---------------------------------------------------------------------------

def _hl_format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


def _py_keywords() -> str:
    words = ("False None True and as assert async await break class continue def "
             "del elif else except finally for from global if import in is lambda "
             "nonlocal not or pass raise return try while with yield match case")
    return r"\b(" + "|".join(words.split()) + r")\b"


def _js_keywords() -> str:
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

# Extensions an editor tab maps onto a language, so opening a file picks the
# right colors by itself.
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

    def __init__(self, document, language: str = ""):
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


def _attach_highlighter(widget, language: str) -> None:
    """Give a ``multi`` its highlighter, once, then keep the language current."""
    highlighter = getattr(widget, "_addon_highlighter", None)
    if highlighter is None:
        highlighter = RegexHighlighter(widget.document(), language)
        widget._addon_highlighter = highlighter
    else:
        highlighter.set_language(language)


def _make_layout(parent: QWidget, kind: str):
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


def _build_one(spec: dict, registry: dict, events) -> QWidget | None:
    kind = str(spec.get("kind") or "").lower()
    children = spec.get("children") or []

    if kind in ("grid", "form") or kind in _LAYPOUT_KINDS:
        holder = QWidget()
        layout = _make_layout(holder, kind)
        _fill_children(layout, children, kind, spec, registry, events)
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
        table.setHeaderLabels([str(name) for name in spec.get("columns") or [""]])
        table.setAlternatingRowColors(True)
        table.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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


def _fill_children(layout, children, kind, spec, registry, events) -> None:
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


def apply_widget_property(widget: QWidget, prop: str, value) -> None:
    """One ``set_widget`` operation, shared by the Python and the process API."""
    text = "" if value is None else str(value)
    if prop in ("text", "html"):
        if hasattr(widget, "setText"):
            widget.setText(text)
        elif hasattr(widget, "setPlainText"):
            # A plain text edit has no setText, so without this an addon could
            # read its viewer back but never fill it.
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
        # Syntax colors for an editor. The highlighter lives beside the widget in
        # the app process, so naming a language here is all an addon has to do -
        # imported or compiled alike.
        if hasattr(widget, "document"):
            _attach_highlighter(widget, str(value or ""))
    elif prop == "visible":
        widget.setVisible(bool(value))
    elif prop == "checked":
        widget.setChecked(bool(value))
    elif prop == "value":
        # "value" means the state of the control, whatever kind it is: ticked,
        # the number, the chosen entry or the text.
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
            # ``minimum`` is a method, not a number: asking for the attribute
            # hands out the bound function, which setRange then refuses.
            floor = widget.minimum() if callable(getattr(widget, "minimum", None)) else 0
            widget.setRange(min(floor, limit), limit)
    elif prop == "format":
        # What a progress bar writes inside itself. Qt expands ``%p``, ``%v`` and
        # ``%m`` in the text, so an addon can say what it is doing as well as how
        # far through it is.
        if hasattr(widget, "setFormat"):
            widget.setFormat(text)
        elif hasattr(widget, "setText"):
            widget.setText(text)
    elif prop == "items":
        items = [str(item) for item in (value or [])]
        if hasattr(widget, "setHeaderLabels"):
            widget.setHeaderLabels(items)
        elif hasattr(widget, "clear") and hasattr(widget, "addItems"):
            current = widget.currentText()
            widget.clear()
            widget.addItems(items)
            if current in items:
                widget.setCurrentText(current)
        elif hasattr(widget, "clear"):
            widget.clear()
            widget.addItems(items)
    elif prop == "current":
        if hasattr(widget, "setCurrentRow"):
            widget.setCurrentRow(int(value or 0))
        elif hasattr(widget, "setCurrentIndex"):
            widget.setCurrentIndex(int(value or 0))
    elif prop == "rows":
        if hasattr(widget, "clear"):
            widget.clear()
        for row in value or []:
            cells = row if isinstance(row, (list, tuple)) else [row]
            item = QTreeWidgetItem([str(cell) for cell in cells])
            widget.addTopLevelItem(item)
    elif prop == "clear":
        if hasattr(widget, "clear"):
            widget.clear()


def read_widget_value(widget: QWidget, what: str = "value"):
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
    # A spin box and a progress bar answer to text() as well, and that is the
    # formatted label ("3", "5%") rather than the number the addon set.
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


# ---------------------------------------------------------------------------
# The namespaces on api
# ---------------------------------------------------------------------------

class SessionAPI:
    """What the app currently knows: profile, settings, connection state."""

    def __init__(self, api: "AddonAPI"):
        self._api = api

    @property
    def _win(self):
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
        return jsonable({
            "profile": self._win.app_config.get("name", ""),
            "host": self._win.host_input.text().strip(),
            "port": self._win.port_input.value(),
            "username": self._win.username_input.text().strip(),
            "remote_root": self._win.remote_root_input.text().strip(),
            "source": str(self._win.app_config.get("source", "")),
            "sftp_connected": bool(self._win.sftp_connected),
            "ssh_connected": bool(shell is not None and shell.isRunning()),
            "web_port": int(self._win.app_config.get("web_port", 8989)),
            "busy": bool(getattr(self._win, "worker", None) is not None
                         and self._win.worker.isRunning()),
        })

    def web_url(self, tab: dict | None = None) -> str:
        """Build the address a web tab would open, following the profile host."""
        return web_tab_url(tab or {}, self._win.app_config.get("host", "192.168.1.1"))

    def tabs(self) -> list[str]:
        widget = self._win.tabs
        return [widget.tabText(index) for index in range(widget.count())]

    def current_tab(self) -> str:
        return self._win.tabs.currentText()

    def version(self) -> str:
        return "1.4"

    def app_stylesheet(self) -> str:
        """The dark theme the app runs under, for an addon that wants to match.

        Handed over already multiplied by the interface zoom, so an addon that
        pastes it into its own widgets gets the sizes the window is drawing."""
        return scaled_sheet(APP_STYLESHEET)

    def zoom(self) -> int:
        """The density the interface is drawn at, in percent (100 is as shipped).

        View > Zoom in/out changes it. An addon that sizes anything by hand can
        multiply it by ``zoom() / 100`` to keep pace with the rest of the window.
        """
        return current_zoom()

    def app_icon(self) -> str:
        """The logo the program wears right now, as a path; empty if it has none.

        View > Alternate icon switches between the two pictures the build ships,
        and the toolbar, the window, the taskbar and the About box all ask the
        window for the current one. This is that answer, so an addon can use the
        same mark - or call ``api.tabs.use_app_icon`` and have it followed for
        it.
        """
        return app_icon_path(self._win)


class Connection:
    """A live SSH/SFTP pair, handed out by ``api.device.open()``."""

    def __init__(self, client, sftp, remote_root: str):
        self.client = client
        self.sftp = sftp
        self.remote_root = remote_root

    def run(self, command: str, timeout: int = 30):
        return exec_remote(self.client, command, timeout)

    def remote(self, path: str = "", outside_root: bool = False) -> str:
        return resolve_remote(self.remote_root, path, outside_root)


def resolve_remote(root: str, path, outside_root: bool = False) -> str:
    """Map an addon path onto the device, keeping it inside the deploy folder.

    A relative path is always below the profile's remote root. An absolute one
    is accepted only when it stays under that root, unless the caller passes
    ``outside_root=True``.
    """
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

    def __init__(self, api: "AddonAPI"):
        self._api = api

    @property
    def _win(self):
        return self._api.win

    def _settings(self) -> dict:
        settings = self._win.current_settings(silent=True)
        if settings is None:
            raise AddonError("the profile has no complete connection details")
        if not settings.get("password") and not settings.get("key_file"):
            raise AddonError(
                "no credentials to use: connect once and tick 'Remember password', "
                "or save the password in the connection panel first"
            )
        return settings

    def _trace(self, text: str) -> None:
        self._api.log(text)

    def _mark(self, transport: str) -> None:
        """Tell the manager which transport this call uses, for its Settings dot.

        Never lets a bookkeeping failure reach the addon: the dot is a display
        convenience, not part of the call's job.
        """
        try:
            self._api._manager.mark_transport(self._api._addon, transport)
        except Exception:
            pass

    # --- one-off SSH commands -------------------------------------------

    def _exec(self, command: str, timeout) -> dict:
        settings = self._settings()
        self._trace(f"$ {command}")
        client = None
        try:
            client = create_ssh_client(settings)
            code, out, err = exec_remote(client, command, int(_timeout(timeout) ))
        finally:
            if client is not None:
                client.close()
        return {"code": code, "out": out, "err": err}

    def run(self, command: str, timeout=30, block=True):
        """Run one non-interactive command. Returns ``{"code","out","err"}``,
        or a Future when ``block`` is False (call ``run_async`` instead)."""
        if not str(command).strip():
            raise AddonError("empty command")
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout)
        if not block:
            return future
        return _await(future, _timeout(timeout))

    def run_async(self, command: str, on_done=None, timeout=30):
        """``run`` without waiting: ``on_done(result)`` arrives on the GUI thread."""
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout)
        if on_done is not None:
            def deliver(done):
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
        self._mark("ssh")
        worker = self._win.shell_worker
        if worker is None or not worker.isRunning():
            return False
        worker.send_line(str(line))
        self._trace(f"shell> {line}")
        return True

    def is_connected(self, protocol: str | None = None) -> bool:
        worker = self._win.shell_worker
        states = {
            "sftp": bool(self._win.sftp_connected),
            "ssh": bool(worker is not None and worker.isRunning()),
        }
        if protocol is None:
            return any(states.values())
        return states.get(str(protocol).lower(), False)

    def ensure_connected(self, protocol: str = "sftp") -> bool:
        """Ask the app to open a session, the same way the buttons do. Returns
        whether something was already up (so an addon knows whether to wait)."""
        kind = str(protocol).lower()
        if kind == "sftp":
            already = bool(self._win.sftp_connected)
            if not already:
                self._win.connect_remote()
            return already
        if kind == "ssh":
            worker = self._win.shell_worker
            already = worker is not None and worker.isRunning()
            if not already:
                self._win.connect_shell()
            return already
        raise AddonError("protocol must be 'sftp' or 'ssh'")

    # --- files -----------------------------------------------------------

    def _sftp(self, operation: str, action: Callable, timeout=30):
        """Open a session, run `action(client, sftp)` in the pool, wait for it."""
        self._mark("sftp")
        settings = self._settings()

        def work():
            client = None
            sftp = None
            try:
                client = create_ssh_client(settings)
                sftp = client.open_sftp()
                self._trace(f"sftp {operation}")
                return action(client, sftp, settings["remote_root"])
            finally:
                if sftp is not None:
                    sftp.close()
                if client is not None:
                    client.close()

        return _await(POOL.submit(work), _timeout(timeout))

    @contextmanager
    def open(self, timeout=30):
        """``with api.device.open() as conn:`` - a session you keep open.

        Blocks while it connects, so use it from a callback or a thread rather
        than in ``init``.
        """
        self._mark("sftp")
        settings = self._settings()
        client = None
        sftp = None
        try:
            client = _await(POOL.submit(create_ssh_client, settings), _timeout(timeout))
            sftp = client.open_sftp()
            yield Connection(client, sftp, settings["remote_root"])
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()

    def list(self, path: str = "", outside_root: bool = False) -> list[dict]:
        """The device's listing of one directory, relative to the remote root."""

        def work(client, sftp, root):
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
        def work(client, sftp, root):
            try:
                sftp.stat(resolve_remote(root, path, outside_root))
            except OSError:
                return False
            return True

        return self._sftp("stat", work)

    def stat(self, path: str, outside_root: bool = False) -> dict | None:
        def work(client, sftp, root):
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
        def work(client, sftp, root):
            with sftp.open(resolve_remote(root, path, outside_root), "rb") as handle:
                return handle.read()

        return self._sftp("read", work)

    def read_text(self, path: str, encoding: str = "utf-8",
                  outside_root: bool = False) -> str:
        return self.read_bytes(path, outside_root).decode(encoding, errors="replace")

    def write_bytes(self, path: str, payload: bytes, mkdir: bool = True,
                    outside_root: bool = False) -> int:
        data = bytes(payload)

        def work(client, sftp, root):
            target = resolve_remote(root, path, outside_root)
            if mkdir:
                _make_dirs(sftp, posixpath.dirname(target))
            with sftp.open(target, "wb") as handle:
                handle.write(data)
            return len(data)

        return self._sftp("write", work)

    def write_text(self, path: str, text, encoding: str = "utf-8", mkdir: bool = True,
                   outside_root: bool = False) -> int:
        return self.write_bytes(path, str(text).encode(encoding), mkdir, outside_root)

    def append_text(self, path: str, text, outside_root: bool = False) -> int:
        def work(client, sftp, root):
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
        def work(client, sftp, root):
            target = resolve_remote(root, path, outside_root=True)
            _make_dirs(sftp, target)
            return target

        return self._sftp("mkdir", work)

    def remove(self, path: str, recursive: bool = False,
               outside_root: bool = False) -> bool:
        def work(client, sftp, root):
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
        def work(client, sftp, root):
            source = resolve_remote(root, path, outside_root)
            target = resolve_remote(root, new_path, outside_root)
            sftp.rename(source, target)
            return target

        return self._sftp("rename", work)

    def upload(self, local, remote_path: str, outside_root: bool = False) -> str:
        source = Path(str(local)).expanduser()
        if not source.is_file():
            raise AddonError(f"no such local file: {source}")

        def work(client, sftp, root):
            target = resolve_remote(root, remote_path or source.name, outside_root)
            _make_dirs(sftp, posixpath.dirname(target))
            sftp.put(str(source), target)
            return target

        return self._sftp("upload", work)

    def download(self, remote_path: str, local=None,
                 outside_root: bool = False) -> str:
        target = Path(str(local or Path(str(remote_path)).name)).expanduser()

        def work(client, sftp, root):
            source = resolve_remote(root, remote_path, outside_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(source, str(target))
            return str(target)

        return self._sftp("download", work)


def _make_dirs(sftp, path: str) -> None:
    """mkdir -p on the device, ignoring the root itself."""
    from manager_net import make_remote_directories

    if path and path != "/":
        make_remote_directories(sftp, path)


def _remove_tree(sftp, directory: str) -> None:
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

    def __init__(self, api: "AddonAPI"):
        self._api = api

    @property
    def _win(self):
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

    def popup(self, title: str, text: str) -> None:
        """A tray-style notice: the status line plus the log."""
        self._win.statusBar().showMessage(str(text), 6000)

    def notify(self, text: str, ms: int = 5000) -> None:
        self._win.statusBar().showMessage(str(text), max(500, int(ms)))

    def window(self):
        """The main window. Use sparingly - it is the app itself."""
        return self._win

    def menu_item(self, text: str, callback, menu: str = "Tools", shortcut: str | None = None):
        """Add an entry to the menu bar for as long as the addon stays enabled."""
        target = self._find_menu(menu)
        action = target.addAction(str(text))
        action.triggered.connect(lambda _checked=False: callback())
        if shortcut:
            from PyQt6.QtGui import QKeySequence

            action.setShortcut(QKeySequence(str(shortcut)))
        self._api._addon.actions.append((target, action))
        return action

    def remove_menu_item(self, action) -> None:
        for stored_menu, stored in list(self._api._addon.actions):
            if stored is action:
                stored_menu.removeAction(action)
                self._api._addon.actions.remove((stored_menu, stored))

    def _find_menu(self, title: str):
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

    def set_setting_widget(self, widget_or_spec) -> None:
        """Extra Settings-tab controls for this addon, under its own switch."""
        self._api._manager.set_setting_widget(self._api._addon, widget_or_spec)

    def _titled(self, text: str) -> str:
        """Prefix dialog titles so it is obvious which addon is speaking."""
        label = str(text or "")
        name = self._api.name
        return f"{label} - {name}" if name and name.lower() not in label.lower() else label


def _is_web_view(widget) -> bool:
    """True for an embedded page. QtWebEngine is optional, so the type may be
    missing and ``isinstance`` would refuse ``None`` as its second argument."""
    return QWebEngineView is not None and isinstance(widget, QWebEngineView)


def app_icon_path(win) -> str:
    """The path of the app's own logo, or nothing at all.

    Asked through the window rather than read from the settings, because the
    window is what knows which of the two pictures is current and where the
    assets of a program packed with PyInstaller ended up.
    """
    getter = getattr(win, "current_icon_path", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "")
    except Exception:                        # a window with no icon is a window
        return ""


def build_icon(source, home: Path | None = None):
    """A tab's mark out of whatever an addon offered as its icon, or ``None``.

    Two forms are understood, told apart by what the text looks like:

    * an SVG document (``<svg ...>``) - plain text all the way, so an addon
      compiled into a program of its own, with no Qt and nothing beside it, can
      still draw its own icon and send it across the pipe;
    * a picture file - an absolute path, or a name beside the addon's own
      folder, which is what an addon that ships ``icon.png`` uses.

    Anything that cannot be read comes back as ``None`` rather than as a blank
    icon: a tab whose addon misspoke is still a tab.
    """
    from PyQt6.QtCore import QByteArray, Qt
    from PyQt6.QtGui import QIcon, QPixmap

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

    def __init__(self, api: "AddonAPI"):
        self._api = api

    @property
    def _win(self):
        return self._api.win

    @property
    def _store(self):
        return self._api._addon.tabs

    def _before_settings(self) -> int:
        # The window keeps a gap + Settings pinned at the far right; new tabs
        # go to the left of that pair. Fall back to "before Settings" (then the
        # count) for a window or stub that has no such helper.
        helper = getattr(self._win, "_content_insert_index", None)
        if callable(helper):
            try:
                return int(helper())
            except Exception:
                pass
        page = getattr(self._win, "settings_page", None)
        index = self._win.tabs.indexOf(page) if page is not None else -1
        return index if index >= 0 else self._win.tabs.count()

    def _index(self, position) -> int:
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
        """An embedded browser page that belongs to no profile.

        It is not written to config.json, does not appear in Settings > Custom
        tabs and is not rebuilt when the profile changes; it lives as long as
        the addon is enabled. Give either a full ``url`` or ip/port/path/scheme.
        """
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
        """A custom tab: any QWidget, or a widget built from a spec dict.

        Python addons can build whatever they like - pass the widget and set
        ``style`` with a stylesheet. Process addons use ``spec`` (see the module
        docstring) and address its parts by ``id`` afterwards.

        The new tab does not steal the view: being switched on, or adding a
        tab in passing, should not drag the window away from whatever the user
        was looking at. ``activate`` is there for the moment an addon really
        does mean to show a tab - a menu entry, a "go there" button.
        """
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
        """Re-put the 's'/'f' transport dots after this addon's tab moved in or
        out of the bar. The window owns the layout; an addon started before it is
        complete (or a stub in a test) simply has nothing to refresh."""
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

    def view(self, name: str):
        """The QWebEngineView behind a tab, for the full page API."""
        entry = self._store.get(str(name))
        return None if entry is None else entry["view"]

    def widget(self, name: str, identifier: str | None = None):
        entry = self._store.get(str(name))
        if entry is None:
            return None
        if identifier is None:
            return entry["page"]
        return entry["registry"].get(str(identifier))

    def set_widget(self, name: str, identifier: str, prop: str = "text", value=None) -> bool:
        widget = self.widget(name, identifier)
        if widget is None:
            return False
        apply_widget_property(widget, str(prop), value)
        return True

    def widget_value(self, name: str, identifier: str, what: str = "value"):
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

    def inject_js(self, name: str, code, callback=None) -> None:
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

    def set_icon(self, name: str, icon=None) -> bool:
        """A mark beside the tab's label, as a page's favicon sits in a browser.

        ``icon`` is an SVG document as text or a picture file - see
        :func:`build_icon` - and nothing at all clears it again. An embedded
        page keeps doing what the page tells it (the favicon follows every
        load), so this is for the tabs an addon builds itself. Choosing a mark
        here also ends any follow of the app's own icon: the addon has said what
        it wants.
        """
        from PyQt6.QtGui import QIcon

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
        self._win.tabs.setTabIcon(index, mark or QIcon())
        return True

    def icon(self, name: str) -> bool:
        """Whether this addon's tab is carrying a mark of its own."""
        entry = self._store.get(str(name))
        if entry is None:
            return False
        index = self._win.tabs.indexOf(entry["page"])
        return index >= 0 and not self._win.tabs.tabIcon(index).isNull()

    def use_app_icon(self, name: str, on: bool = True) -> bool:
        """Let this tab wear the app's own logo, and go on wearing it.

        View > Alternate icon switches between the two pictures the program
        ships, and every place one is shown asks the window for the current
        path. A tab marked here joins them: the window re-marks it in the same
        call that re-marks its own toolbar, so the addon follows the switch
        without watching for it. ``on=False`` gives the tab back its own mark or
        none.

        An embedded page is not marked: what it shows is the device's favicon.
        """
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        if entry.get("web"):
            return False
        entry["app_icon"] = bool(on)
        if not on:
            from PyQt6.QtGui import QIcon

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


def _tab_definition(url, ip, port, path, scheme) -> dict:
    """Split a tab request into the shape the browser code expects.

    A full URL is taken apart rather than kept as-is so the tab's Home button
    and a profile switch still know the device it points at.
    """
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

    def __init__(self, api: "AddonAPI"):
        self._api = api

    def on(self, name: str, callback) -> Callable[[], None]:
        """Register a listener; returns the callable that unregisters it."""
        return self._api._manager.add_listener(str(name), self._api.id, callback)

    def off(self, name: str, callback=None) -> int:
        return self._api._manager.remove_listener(str(name), self._api.id, callback)

    def names(self) -> list[str]:
        return list(self._api._manager.EVENT_NAMES)


class SettingsAPI:
    """The addon's own keys in config.json, saved with the profile."""

    def __init__(self, api: "AddonAPI"):
        self._api = api

    def _record(self) -> dict:
        return self._api._manager.record_for(self._api.id)

    def all(self) -> dict:
        return jsonable(dict(self._record().get("settings") or {}))

    def get(self, key: str, default=None):
        return jsonable((self._record().get("settings") or {}).get(str(key), default))

    def set(self, key: str, value) -> None:
        self.update({key: value})

    def update(self, values: dict) -> None:
        record = self._record()
        store = dict(record.get("settings") or {})
        store.update({str(k): v for k, v in (values or {}).items()})
        record["settings"] = store
        self._api._manager.save_records()

    def pop(self, key: str, default=None):
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
    """The addon's own file in its ``addons/_data/<id>`` folder, shared by every
    profile.

    ``api.settings`` keeps an addon's small per-profile notes; this is for the
    things a user would want to look at and edit themselves - where the
    certificate lives, what command restarts the web server. Declare the keys
    once from ``init`` and the app shows them under the addon's name in
    Settings > Addons.
    """

    def __init__(self, api: "AddonAPI"):
        self._api = api
        self._addon = api._addon

    # --- the file --------------------------------------------------------

    @property
    def file(self) -> Path:
        return addon_config_path(self._api.id)

    def path(self) -> str:
        """The same location as text, which is what crosses a process boundary."""
        return str(self.file)

    def exists(self) -> bool:
        return self.file.is_file()

    def open_folder(self) -> None:
        """Show the addon's own ``addons/_data/<id>`` folder in the file browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(addon_data_dir(self._api.id))))

    # --- what the keys mean ---------------------------------------------

    def declare(self, fields: list | None = None, defaults: dict | None = None,
                title: str = "") -> dict:
        """Register the schema, and write the file with the defaults if missing.

        ``fields`` is a list of ``{"key", "label", "kind", "hint", ...}``, where
        ``kind`` is ``text``, ``number``, ``bool``, ``choice`` (with ``choices``),
        ``list`` (one comma-separated line) or ``multiline`` (a box of lines).
        A nested ``key`` with a dot - ``"subject.CN"`` - reads and writes inside
        that section. Returns the values the addon should start from.
        """
        clean = []
        for field in fields or []:
            if isinstance(field, dict) and field.get("key"):
                clean.append(dict(field))
        self._addon.config_fields = clean
        self._addon.config_defaults = jsonable(dict(defaults or {}))
        self._addon.config_title = str(title or "")
        if clean:
            # Kept in a side file so the form still shows while the addon is
            # switched off, and so the user's own file stays free of the schema.
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
        # Switched off: the copy in the side file is all the app has.
        return jsonable(read_config_json(addon_schema_path(self._api.id)).get("defaults") or {})

    # --- the values ------------------------------------------------------

    def stored(self) -> dict:
        """Exactly what is in the file, with nothing filled in."""
        return jsonable(read_config_json(self.file))

    def all(self) -> dict:
        """The file over the declared defaults, read fresh from disk."""
        return jsonable(merge_config(self.defaults(), self.stored()))

    def get(self, key: str, default=None):
        return jsonable(config_get(self.all(), str(key), default))

    def set(self, key: str, value) -> None:
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

    def pop(self, key: str, default=None):
        """Remove a key so the default applies again."""
        def apply():
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

    def __init__(self, api: "AddonAPI"):
        self._api = api

    def after(self, seconds, callback) -> QTimer:
        return self._start(float(seconds) * 1000.0, callback, False)

    def every(self, seconds, callback) -> QTimer:
        return self._start(max(100.0, float(seconds) * 1000.0), callback, True)

    def _start(self, ms: float, callback, repeat: bool) -> QTimer:
        timer = QTimer()
        timer.setSingleShot(not repeat)
        timer.timeout.connect(lambda: _call(callback))
        timer.start(int(ms))
        self._api._addon.timers.append(timer)
        return timer


def _call(callback) -> None:
    callback()


class PathsAPI:
    """Folders an addon may read and write."""

    def __init__(self, api: "AddonAPI"):
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
        """The pictures that ship inside the program (logos, tab icons).

        Read-only in spirit: a build unpacks them into a temporary folder, so
        writing there is lost on the next run. An addon that wants a picture of
        its own keeps it beside its own file.
        """
        from manager_const import ASSETS_DIR

        return ASSETS_DIR

    @property
    def log(self) -> Path:
        return LOG_FILE


class PeersAPI:
    """Look at, and talk to, the other addons."""

    def __init__(self, api: "AddonAPI"):
        self._api = api

    def list(self) -> list[str]:
        return [addon.id for addon in self._api._manager.loaded()]

    def info(self, addon_id: str) -> dict | None:
        addon = self._api._manager.find(str(addon_id))
        if addon is None:
            return None
        return jsonable({"id": addon.id, "name": addon.name, "version": addon.version,
                         "enabled": addon.enabled, "kind": addon.kind})

    def send(self, addon_id: str, data=None) -> bool:
        return self._api._manager.deliver(self._api.id, str(addon_id), data)


class AddonAPI:
    """The object every addon receives: ``def init(api)``."""

    def __init__(self, manager, addon):
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

    def log(self, text) -> None:
        """Write to the app log under this addon's name."""
        self._manager.log(self.id, str(text))

    def error(self, text) -> None:
        self._manager.log(self.id, f"error: {text}", warning=True)

    def call(self, method: str, arguments: dict | None = None):
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
        # A JSON addon names the spec key "id"; Python uses "identifier"
        # because id() is a builtin, so the two spellings mean the same thing.
        if "id" in values and "identifier" not in values:
            try:
                parameters = inspect.signature(function).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "identifier" in parameters and "id" not in parameters:
                values["identifier"] = values.pop("id")
        return function(**values)
