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
    ``tabs()``, ``current_tab()``, ``version()``, ``app_stylesheet()`` (the theme
    in force, already scaled to the interface zoom), ``zoom()`` (that
    percentage), ``app_icon()`` (the logo the program wears itself).
    The installed addons as the app sees them - what the store's Addon manager
    page is built from: ``addons()``, ``addon_card(id)``, ``set_addon_enabled``,
    ``addon_state``, ``set_addon_running``, ``rescan_addons``, and what the app
    owns beside the enable switch: ``set_addon_popout``/``addon_popout``,
    ``set_addon_git_token``, ``set_addon_shortcut``, ``set_addon_transport`` and
    ``set_addon_own_connection``, plus ``show_addon_window`` to bring an addon's
    own window up. Another addon's own settings file is read and written here too -
    ``addon_config(id)`` for its declared fields and current values and
    ``set_addon_config(id, values)`` to save them - which is how the manager can
    edit an addon it did not build. ``app_setting(key)`` reads any profile
    setting; ``set_app_setting(key, value)`` writes only the handful an addon may
    touch (``addon_dev_enabled``), so a profile's connection details stay the
    user's.
    One GitHub token per profile, in Settings > Connection: ``git_token()``
    answers this addon's copy of it - empty unless the addon is switched on for
    it in the Addon manager, and empty when the field is - and
    ``github_headers()`` gives the two headers ``http.client`` wants for a
    private repository and the higher rate limit, or ``{}`` to ask anonymously.

``api.device``
    SSH and SFTP. The program keeps one main connection to the device and it
    carries both transports. Every call passes the same gates, in this order:
    the master switch in Settings > Connection, that transport's app-wide switch
    (``ssh_enabled`` / ``sftp_enabled``), this addon's own switch on its row in
    the store's Addon manager, the same addon's switch for *that transport* on its
    row,
    the transport listed under ``"requires"`` in the manifest, and the
    connection it reaches - which must be the one the manifest declared under
    ``"connection"``: ``"app"`` (the default - only the app's main session,
    which must already be open: an addon never brings it up by itself, it asks
    with ``ensure_connected()``), ``"own"`` (only a connection of its own, which
    the addon opens and closes per call, main session or not) or ``"both"``
    (either, the user's Settings switch picks). An addon shipped with the
    program always rides the app's session.
    ``run(command, timeout=30, device=None)`` -> ``{"code", "out", "err"}`` (runs in
    the background and waits; use ``run_async(command, on_done)`` from ``init``),
    ``shell_send(line)`` types into the terminal tab, which is a channel of the
    main connection,
    ``ensure_connected("ssh"|"sftp")`` asks the app to open its main connection,
    ``is_connected()`` reports what this addon may use right now, ``mode()`` and
    ``set_mode("main"|"own")`` report and switch a ``"both"`` addon.
    ``device`` on ``run``/``run_async``/``open`` is a one-off dict of connection
    details (``host``, ``port``, ``username``, ``password``, ``key_file``,
    ``remote_root``): the call reaches that machine instead of the profile -
    a connection of its own, so it needs ``"connection"`` to allow one; one
    API call, so it stores nothing and never moves the app's own session;
    addons shipped with the program may use it too. ``bind()`` stays the
    drop-in-only door.
    Files: ``list()``, ``exists()``, ``stat()``, ``read_text()``,
    ``read_bytes()``, ``write_text()``, ``write_bytes()``, ``mkdir()``,
    ``remove()``, ``rename()``, ``upload()``, ``download()`` - each takes
    ``outside_root=True`` to reach above the profile's remote folder
    (``/etc/ssl`` and the like); without it a path stays inside that folder.
    ``open()`` is a context manager yielding a live ``Connection`` with
    ``.client`` / ``.sftp`` (raw paramiko) for anything not covered above;
    it opens a connection of its own, so it needs the same declaration.

``api.tabs``
    ``add_web_tab(name, url="", ip="", port=0, path="", scheme="",
    position=None, pinned=False, icon_only=False, menu=None)`` embeds a browser
    tab that belongs to no profile: it is not stored in the settings and never
    shows up in Settings > Custom tabs. ``menu`` is a list of
    ``{"id": ..., "label": ...}`` entries shown on a small button floating over
    the corner of the page; picking one comes back as ``on_widget(name, id,
    "clicked", {})``.
    ``add_tab(name, widget=None, spec=None, style="", position=None,
    pinned=False, icon_only=False)`` adds a tab holding any QWidget (Python) or a
    widget built from a declarative spec (see SPEC FORMAT). With ``position`` left
    out the tab goes immediately left of the leftmost movable tab this addon
    already owns - a helper or a report sits beside the tab that opened it, not at
    the far end of the bar - and an addon with nothing open starts at the end of
    the movable row. ``pinned=True`` holds the tab in the run at the right edge
    beside Settings: it cannot be dragged out of that run, it keeps its place in
    it while the tabs around it come and go, and it is saved in the pinned order
    rather than in the order of the movable tabs - within the run it may be slid
    about like any other tab. ``icon_only=True`` draws it as its mark
    with no caption (the name still addresses it and is its tooltip); both apply to
    a web tab as well, and ``set_pinned(name, pinned=True, icon_only=None)``
    changes either afterwards - ``pinned=False`` lets the tab back into the row
    with its caption restored.
    ``set_html``, ``inject_css``, ``inject_js``, ``set_style``, ``view``,
    ``widget``, ``set_widget``, ``widget_value``, ``activate``, ``close``,
    ``rename``, ``names``, ``exists``, ``set_icon`` (a mark beside the tab's label,
    the way a page's favicon sits beside its title), ``use_app_icon`` (wear the logo
    the program uses and keep following it when it is switched).

``api.window``
    The window this addon's tabs live in, when they live in one of their own.
    ``popout(tab, on=True)`` undocks one of this addon's tabs into a top-level
    window wearing the addon's name and mark, and docks it back into the place in
    the bar it came from; ``popped(tab)`` and ``state(tab)`` say where a tab
    stands now. While it is out, the addon steers the frame: ``open``, ``close``,
    ``raise_window``, ``show(tab, on)``, ``center``, ``set_title``, ``set_size``,
    ``set_minimum``, ``set_position``, ``set_resizable``, ``set_closable``,
    ``set_always_on_top``, ``set_style``, ``set_icon``. A tab that never left the
    bar has no window to steer, so those calls say so. ``set_shortcut(on)`` asks
    for a Desktop link that starts this one addon in its own window - a copy
    launched that way loads nothing else besides it, its api and, when the addon
    rides it, the profile's main connection - and ``shortcut_path()`` says where it
    is. What the window does comes back through ``api.events.on("window")`` as
    ``{"addon", "tab", "event", ...}`` for ``opened``, ``closing``, ``closed``,
    ``docked``, ``undocked``, ``resized`` and ``moved``.

``api.ui``
    ``information/warning/error(title, text)``, ``question(title, text)`` ->
    bool, ``input(title, label, default, secret=False)`` -> str or ``None``,
    ``notify(text)``,
    ``menu_item(text, callback, menu="Tools", shortcut=None)``,
    ``dialog(spec)``, ``pick_color()`` -> a hex colour, ``pick_file()`` -> a path
    on this machine, ``window()`` (the main window, for advanced use),
    ``set_setting_widget(spec_or_widget)`` (this addon's own extra settings).

``api.events``
    ``on(name, callback)`` -> unsubscribe callable. Events: ``startup``,
    ``shutdown``, ``sftp_connected``, ``sftp_disconnected``, ``ssh_connected``,
    ``ssh_disconnected``, ``operation_done``, ``tab_changed``,
    ``profile_changed``, ``settings_applied``, ``addon_enabled``,
    ``addon_disabled``, ``addon_config_changed``, ``transport_changed`` (one
    transport gate moved: ``scope`` says whether the app-wide switch or an
    addon's own did, with ``transport``, ``addon`` and ``enabled`` beside it),
    ``theme_changed`` (the look moved: ``theme`` is what is worn now, ``wanted``
    what was asked for, and the two differ when a file was missing).
    A module-level ``on_event(name, payload)`` or ``on_<name>(payload)``
    function is called too.

``api.theme``
    The window's own look, read from ``themes/<name>.theme`` - one JSON object
    of named colours, the shape of the tabs, and a few lines of stylesheet of
    its own. An addon's *page* keeps its own styling; the tab bar, the window's
    surfaces and its text are the theme's business, so an addon that wants to
    match the window asks rather than assumes. Reading: ``name``, ``names``,
    ``exists``, ``folder``, ``read``, ``raw``, ``colors``, ``color``, ``tabs``,
    ``metric``, ``icon_px``, ``chrome_px``, ``shows_captions``, ``shows_dots``,
    ``stylesheet``, ``groups``, ``token_knobs``, ``reference``, ``templates``,
    ``template_text``, ``validate``. Changing: ``use(name)`` applies one and
    keeps the choice in the profile, ``save(name, theme)`` writes a file (and
    redraws when it is the one in force), ``copy``, ``rename`` and ``remove``
    look after the rest, and ``open_appearance()`` stands the app on
    Settings > Appearance, where the theme in force is picked. A theme never
    breaks the window: an unknown token or a
    colour that is not a colour is reported by ``validate`` and the shipped
    value is kept for it.

``api.settings``
    This addon's own persisted dict: ``get``, ``set``, ``update``, ``pop``,
    ``all``, ``remove``. Stored in config.json under ``addons -> <id> ->
    settings``, so it travels with the profile like every other setting.

``api.settings_pages``
    A page of the app's own Settings window that this addon owns, standing with
    the program's categories and outside the shared **Installed addons** page:
    ``add(name, title="", widget=None, spec=None, style="")`` (re-adding a name
    replaces that page rather than stacking a second), ``remove``, ``names``,
    ``exists``, ``title``, ``set_title``, ``activate``, ``widget``,
    ``set_widget``, ``widget_value``. The spec is the same language as a tab's and
    its controls report to the addon's ``on_widget`` with the page's name where a
    tab's would be; the page is the app's furniture and goes away by itself when
    the addon is switched off or removed. ``set_icon`` answers False - a Settings
    page wears no mark of its own.

``api.config``
    The addon's own settings *file*: ``addons/_data/<id>/config.json``, in the
    addon's own folder. Unlike
    ``api.settings`` it belongs to the installation rather than to one profile,
    it is plain JSON a user can edit in any editor, and the app shows it under
    the addon's name in the store's Addon manager - as its own Settings page for
    an addon that came inside the program, and through ``api.session.addon_config``
    and ``set_addon_config`` for one that was installed later. ``declare(fields, defaults, title)``
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
    that ship inside the program, read-only), ``themes`` (the ``.theme`` files
    the window is painted from), ``log``.

``api.peers``
    ``list()``, ``info(id)``, ``send(id, data)`` -> the target's
    ``on_message(sender, data)``.

SPEC FORMAT (for non-Qt addons, see ``core.addon``)
------------------------------------------------------
A JSON object describing one widget, recursively:

``{"kind": "column", "children": [...]}`` - containers: ``column``/``row``,
``grid``, ``form``, ``group`` (``text`` = title), ``scroll``, ``splitter``,
``tabs`` (one sub-page per child, the child's ``label`` its caption; move
between the sub-pages with ``set_widget(tab, id, "current", n)`` and read the
visible one back with ``widget_value(tab, id, "index")``). A ``tabs`` container
can also be filled while it is on show: ``set_widget(tab, id, "add_page",
{"label": ..., "spec": ...})`` builds one more sub-page (``"position"`` puts it
somewhere other than the end, ``"current": False`` leaves the view where it is),
``"remove_page"`` takes one away by index or caption, and
``widget_value(tab, id, "pages")`` reads every caption back - which is how an
addon opens a sub-page per connection and shuts it again. An ``overlay``
stacks its children in one area: the first is the background and fills it, every
one after it keeps to its own size and floats on top at the place named by its
``align`` (``center`` - the default - ``top``, ``bottom``, ``left``, ``right``),
and is re-centred when the pane is resized. This is how a job's console can show
what it is doing while the addon it is working on sits in the middle of it.
``label``, ``button``, ``line``, ``multi`` (plain text edit), ``check``,
``combo``, ``list``, ``tree``, ``table``, ``progress``, ``spin``, ``slider``,
``spacer``, ``image``, ``web``, ``color`` (a hex line with the colour wheel
behind its swatch), ``terminal`` - all take ``id``, ``text``, ``tooltip``,
``style`` (a stylesheet, so an addon can style its own tab), ``enabled``,
``visible``, ``font_size`` (drawn at the interface zoom), ``items``, ``value``,
``placeholder``, ``checked``, ``minimum``,
``maximum``, ``columns``, ``word_wrap``, ``readonly``, ``multi``, ``icon`` (a mark
worn beside a widget's words - a button's picture - from a file path, an SVG
document or a base64 data URI), ``expand``
(claim the width its row or column leaves over, so one status line can stretch
while its neighbours keep the room their text needs).
``value`` sets whatever the kind holds: text, the chosen entry, the number, the
tick for a ``check``, or the picture for an ``image``; ``widget_value`` reads it
back the same way.
An ``image`` is a picture of its own: ``size`` is the most it may measure in
either direction (at the interface zoom), and its ``value``, or
``set_widget(..., "image", ...)``, is an image file's path or an SVG document as
text - the same two shapes a tab's ``set_icon`` takes. Nothing there is an error:
the leaf goes blank rather than showing a path to the user.
A ``list`` entry may be a bare string or ``{"text": ..., "icon": ...}``, so one
row can wear a mark of its own.
A ``multi`` may name a ``highlight`` language (``python``, ``json``, ``html``,
``css``, ``javascript``, ``ini``) for syntax colors; change it later with
``set_widget(tab, id, "highlight", lang)``.
A ``terminal`` is the app's own terminal widget - the screen that draws the
device's escape sequences and sends the keys typed into it - driven by a
connection of its own rather than the app's main session, so one tab can hold
as many as it likes and each is a separate machine. ``set_widget(tab, id,
"connect", {...})`` brings it up and takes ``"host"``, ``"port"``,
``"username"`` and either ``"password"`` or ``"key_file"`` (a plain
``"user@host:port"`` string says the same thing), ``"disconnect"`` drops it,
``"send"`` types raw bytes, ``"command"`` types a line with the Enter added and
``"write"`` prints a note on the screen from the addon. Its ``value``, or
``widget_value(tab, id, "text")``, is what is showing; ``"state"`` says what it
is attached to (never the password) and ``"grid"`` its shape. It needs
``"requires": ["ssh"]`` and ``"connection": "own"`` in the manifest, like any
other work done over a connection of its own.
``on`` lists the events sent back to the addon: ``clicked``, ``toggled``,
``changed``, ``returned``, ``selected`` and, for a ``table``/``tree``/``list``,
``context`` (a right-click, carrying the text of the row under the cursor). A
``terminal`` answers to ``ready`` (its connection came up), ``closed`` (it went
down, asked for or lost) and ``failed`` (carrying the reason), and to ``bell``
and ``title`` (the screen's own news) when it is asked for them. A ``web`` leaf
answers to ``loaded``, carrying ``{"ok": ..., "url": ...}``, and to ``icon``,
carrying ``{"icon": <PNG data URI>}`` when the page it holds reports a favicon of
its own - the way an addon puts a served page's mark on one of its buttons.
A ``web`` leaf is the same page view an embedded tab gets, in a pane of the
addon's own tab: its ``url``, or ``set_widget(tab, id, "url", ...)``, is the
address, and an address given to a leaf that is not on show is held until the
moment it is, while one it is showing already is not fetched again. ``back``,
``forward``, ``reload`` and ``stop`` are asked the same way, and
``widget_value`` reads ``"url"``, ``"title"``, ``"pending"`` and ``"loading"``
back. Without QtWebEngine the leaf is a one-line notice and a ``"url"`` is
refused with the reason; ``api.ui.browse`` is the machine's own browser for an
addon that has to offer the page somewhere it will open.
``rows`` on a ``table``/``tree`` takes a list of cell lists, or of dicts:
``{"cells": [...], "color": "#rrggbb", "background": "...", "overlay": "...",
"icon": "..."}`` - the colours ride along with the row, so one line can wear the
colour of its own device, and an overlay (a search highlight) can wash over it
without hiding it. ``icon`` puts the addon's own picture in front of the first
cell. A ``tree`` row may also carry ``"children"``, itself a list of the same
shape, so one line can hold the lines under it - a file tree of documentation,
a folder of keys - and ``"expanded"`` says whether it starts open. A table
paints its rows through its own delegate: the app sheet names ``::item``
colours, and a stylesheet colour would otherwise win over every per-item brush
and drop all of this on the floor.
Every column of a ``table`` or ``tree`` is dragged to the width it is wanted at,
and its headers are dragged across to a new order. ``col_widths`` pins a
starting width per column and ``stretch`` names the column (or columns) that
take the room the others leave over, so the table fills its pane and still
answers to a hand on its edge; a column left out of ``col_widths`` starts as
wide as its own heading reads, so a tab is never opened with a caption cut
short. Nothing is drawn narrower than it can be read at - a pane too narrow
for all of them grows a bar to scroll instead of cutting words off - and
the slack column gives itself back on a double-click of its header once it has
been dragged.

PANES THE PERSON CAN MOVE. A ``splitter`` lays its children side by side with a
fold line between each pair, and ``sizes`` are the shares each is promised (a
``[3, 1]`` is three parts of the room to the first pane and one to the second).
A child that says ``"collapsed": True`` starts folded away: nothing of it shows
but its fold line, which is dragged open or double-clicked, and which comes back
to the share it was promised. The same folding comes to a ``column`` or ``row``
from ``"resizable": True`` - the same stack of panes, only with the room between
them movable - and a pane the addon did not say may fold cannot be dragged out
of sight at all, so no row of words can be squeezed into nothing.
``set_widget(tab, id, "sizes", [3, 1])`` lays a splitter's panes out again by
hand, a ``0`` folding that pane away, and ``widget_value(tab, id, "panes")``
reads back ``{"sizes": [...], "open": [...], "ids": [...]}`` to see how they
stand. The shares are kept as the window moves, until somebody drags a line -
which is them saying they know better - and the addon laying them out again
takes the promise back up.
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
    QEvent,
    QEventLoop,
    QModelIndex,
    QObject,
    QSize,
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
    QFontMetrics,
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
    QListWidgetItem,
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
    QTableView,
    QTreeView,
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
    config_get,
    guard,
    jsonable,
    merge_config,
    put_config_value,
    read_config_json,
    take_config_value,
    write_config_json,
)
from core.ui import web_tab_url
from core.const import (
    APP_VERSION,
    LOG_FILE,
    current_zoom,
    scaled_sheet,
    zoom_size,
)
from core.net import (create_ssh_client, exec_remote, remote_join,
                      safe_relative_path, OwnShellWorker)
import core.ui
from core.ui import tab_caption

try:  # the web kinds are inert when QtWebEngine is not installed
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None


def quiet_web_view() -> Any:
    """A web view that never asks the window for more room than it has.

    The same ``core.views.QuietWebView`` the app's own browser tabs are
    built from: when the render process boots, a plain view's size hints
    jump with the page's preferred size and the layout minimums drag the
    window after them. Read late rather than imported at the top: a bare
    addon harness builds specs without ``core.views`` in sight, and an
    addon that never asks for a page should not be the reason it fails.
    With no app beside the leaf, the plain view is all there is.
    """
    try:
        from core.views import QuietWebView
    except Exception:
        QuietWebView = None
    return (QuietWebView or QWebEngineView)()


#: One shared pool, so twenty addons cannot open twenty SSH connections.
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="addon")


def _timeout(seconds: Any, default: float = 30.0) -> float:
    """Clamp an addon-supplied timeout."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1.0), 600.0)


#: How long a call that has to cross from an addon's thread to the interface waits
#: for its answer, and the same wait the other way for a timer handle below. The
#: interface answers in milliseconds when it is running its event loop at all, so a
#: wait measured in whole minutes means the window is stuck - and the addon deserves
#: to hear that as an error in its own log rather than to sit there until the app
#: closes.
GUI_ASK_TIMEOUT = 120.0


def _await(future: concurrent.futures.Future, timeout: float) -> Any:
    """Wait for a pooled task without freezing the interface; on the GUI thread a private event loop runs while the task is in flight.

    Off the GUI thread the wait is a plain one: an addon's own thread owns no
    event loop and has nobody to serve while it stands here, so blocking on the
    future is both the cheapest and the honest answer - and it is what lets a
    threaded addon still get a real result from a blocking ``device`` call.
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
    elif not future.done():
        try:
            future.result(timeout=float(timeout) + 2.0)
        except concurrent.futures.TimeoutException:
            pass
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


def _there(api: Any, callback: Callable, *marks: Any) -> Callable[[Any], None]:
    """Wrap one callback the app owes an addon, so it arrives on the addon's thread.

    An answer that comes in from a pool - a command that finished, a script's
    return value - used to be handed to the interface and run there, which meant
    an addon's own callback could hold the window up like any other job. The
    addon's code is one thread now, so the answer is queued there instead: the
    callback is given the addon's queue and the same guard it always had, and an
    addon that runs no thread of its own is called on this thread, as before.
    """
    def deliver(value: Any) -> None:
        worker = getattr(getattr(api, "_addon", None), "worker", None)
        job = lambda: guard(callback, value, *marks, reporter=api.error)
        if worker is None:
            job()
        else:
            # Refused means the addon is being stopped: its answer is gone rather
            # than run on the wrong thread out of spite.
            worker.post(job)

    return deliver


# --- addon config files: the Qt-free helpers live in addon_common ---

# --- declarative widgets ---

_LAYPOUT_KINDS = {
    "column": QVBoxLayout, "vbox": QVBoxLayout, "row": QHBoxLayout, "hbox": QHBoxLayout,
}

#: Where a floating child of an ``overlay`` is laid on top of what is under it.
#: Only the first child fills the whole area; every one after it hugs its own
#: contents and sits at the place named here, centred when nothing is said.
OVERLAY_ALIGN = {
    "center": Qt.AlignmentFlag.AlignCenter,
    "top": Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
    "bottom": Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
    "left": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    "right": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}

#: The strip a person drags to move the line between two panes. Qt's own is a
#: few pixels of nothing at all, which reads as a border rather than as
#: something to grab - and a pane folded away is only worth having if the line
#: to pull it back is to be found.
PANE_HANDLE = 7

#: How narrow a table column may be drawn, and where one starts when the addon
#: pinned no width for it. Both are only a beginning: every column is dragged
#: wider from there, and nothing is cut off below the first of the two - a
#: cramped table grows a horizontal bar instead of eating its own words.
MIN_SECTION = 36
DEFAULT_SECTION = 110

#: Room left over beside the word a column's header shows, when the addon
#: pinned no width to start from: the frame, the little gap before the drag
#: mark and a character or two of what sits under the label. A header cut to
#: "Mess..." is the table saying its own words do not fit, so the width is
#: measured from the font rather than guessed at.
SECTION_PADDING = 28


#: Row shades for the table delegate, which paints rows by hand. They are read
#: from the theme in force rather than written down, so an addon's table sits in
#: the same room as the rest of the window whatever is being worn; the shipped
#: look gives the colours that were hard-coded here.
_ROW_TOKENS = {"base": "surface", "alternate": "sunken", "hover": "row_hover",
               "selected": "selection", "text": "text", "text_selected": "bright"}
_row_cache: tuple[int, dict[str, QColor]] | None = None


def row_colours() -> dict[str, QColor]:
    """The table's row shades, from the theme in force; rebuilt when the look moves."""
    global _row_cache
    revision = core.ui.revision()
    if _row_cache is None or _row_cache[0] != revision:
        _row_cache = (revision, {key: QColor(core.ui.color(token, default=""))
                                 for key, token in _ROW_TOKENS.items()})
    return _row_cache[1]

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
        shades = row_colours()
        base = QColor(shades["selected"] if selected else shades["hover"] if hover
                      else shades["alternate"] if alternate else shades["base"])
        painter.setPen(Qt.PenStyle.NoPen)
        for brush in (QBrush(base), index.data(Qt.ItemDataRole.BackgroundRole),
                      index.data(ROW_OVERLAY_ROLE)):
            if isinstance(brush, QBrush):
                painter.setBrush(brush)
                painter.drawRect(rect)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        box = rect.adjusted(zoom_size(4), 0, -zoom_size(4), 0)
        check = index.data(Qt.ItemDataRole.CheckStateRole)
        if check is not None:
            # The row wears a tick. This delegate paints the whole cell by
            # hand, so Qt's own check indicator never shows unless it is drawn
            # here too - a box at the cell's left, and the text after it.
            from PyQt6.QtCore import QRect
            from PyQt6.QtWidgets import QApplication, QStyleOptionButton

            side = max(zoom_size(14), rect.height() - zoom_size(6))
            opt = QStyleOptionButton()
            opt.state = (QStyle.StateFlag.State_Enabled
                         | (QStyle.StateFlag.State_On
                            if check == Qt.CheckState.Checked
                            else QStyle.StateFlag.State_Off))
            opt.rect = QRect(rect.left() + zoom_size(4),
                             rect.center().y() - side // 2, side, side)
            # A delegate has no style() of its own to ask - it is not a widget -
            # so the view wearing this row answers, and the app's own style is
            # the fallback for a paint that came from no widget at all.
            style = (option.widget.style() if option.widget is not None
                     else QApplication.style())
            if style is not None:
                style.drawPrimitive(
                    QStyle.PrimitiveElement.PE_IndicatorCheckBox, opt, painter)
            box = box.adjusted(side + zoom_size(6), 0, 0, 0)
        mark = index.data(Qt.ItemDataRole.DecorationRole)
        if mark is not None:
            # A row wearing an icon of its own: the picture keeps its shape and
            # the row's height, and the text starts after it.
            side = max(zoom_size(14), rect.height() - zoom_size(6))
            got = mark.pixmap(QSize(side, side)) if isinstance(mark, QIcon) else None
            if got is not None and not got.isNull():
                painter.drawPixmap(
                    rect.left() + zoom_size(4),
                    rect.center().y() - got.height() // 2, got)
                box = box.adjusted(got.width() + zoom_size(6), 0, 0, 0)
        if text:
            fore = index.data(Qt.ItemDataRole.ForegroundRole)
            colour = (fore.color() if isinstance(fore, QBrush)
                      else shades["text_selected"] if selected else shades["text"])
            painter.setPen(colour)
            painter.setFont(option.font)
            elided = painter.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, box.width())
            painter.drawText(
                box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                elided)
        painter.restore()


def _table_header(view: QTreeView | QTableView) -> QHeaderView:
    """The headings across the top of a table, whichever kind of table it is.

    A tree names its header ``header()`` and the app's own ``QTableWidget``
    lists name it ``horizontalHeader()``; saying it once here is what lets the
    same rules about dragging and slack cover both, instead of the addon's
    tables being laid out one way and the program's another.
    """
    getter = getattr(view, "header", None) or getattr(view, "horizontalHeader", None)
    if getter is None:
        raise TypeError("a table without a header cannot be laid out")
    return getter()


class _SlackColumn(QObject):
    """Keeps the room a table leaves over with the columns that were told to take it.

    ``QHeaderView.ResizeMode.Stretch`` hands out the slack all by itself, but a
    stretched column cannot be dragged, so a table that fills its pane could not
    be resized by hand - and a table that can be dragged leaves a strip of empty
    ground at its right when the pane widens. This is both at once: every column
    stays interactive, the slack goes to the columns the spec named with
    ``stretch`` each time the view is laid out, and the first drag of one of
    those edges stops that for that column, because it is somebody saying they
    meant that width. Double-clicking its header hands the slack back.
    """

    def __init__(self, view: QTreeView | QTableView, columns: list[int]) -> None:
        super().__init__(view)
        self._view = view
        self._columns = [int(column) for column in columns]
        self._owned: set[int] = set()      # taken by hand, so left alone
        self._fitting = False
        header = _table_header(view)
        header.sectionResized.connect(self._taken)
        header.sectionDoubleClicked.connect(self._given_back)
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.fit()
        return False

    def _taken(self, column: int, _old: int, _new: int) -> None:
        if not self._fitting and column in self._columns:
            self._owned.add(column)

    def _given_back(self, column: int) -> None:
        if column in self._columns:
            self._owned.discard(column)
            self.fit()

    def open(self, column: int) -> bool:
        """Whether this column still answers to the space it is given."""
        return int(column) in self._columns and int(column) not in self._owned

    def fit(self) -> None:
        """Share what the other columns leave between the ones that take slack."""
        header = _table_header(self._view)
        wanted = [column for column in self._columns if self.open(column)]
        if not wanted:
            return
        free = self._view.viewport().width() - 2 * self._view.frameWidth()
        for column in range(header.count()):
            if column not in self._columns:
                free -= header.sectionSize(column)
        share = max(header.minimumSectionSize(), free // len(wanted))
        self._fitting = True
        try:
            for column in wanted:
                header.resizeSection(column, share)
        finally:
            self._fitting = False


def _slack_of(view: QTreeView | QTableView, columns: list[int]) -> _SlackColumn:
    """The one slack-taker a table has, made on the first ask and found again
    after: two of them fighting over the same columns would only argue."""
    held = getattr(view, "_addon_slack", None)
    if held is None:
        held = _SlackColumn(view, columns)
        view._addon_slack = held
    return held


class _PaneFitter(QObject):
    """Keeps a splitter's panes in the shares the addon promised them.

    ``setSizes`` asks in pixels, and a pane asking for three parts of the room
    against its neighbour's one only gets them if the numbers are of the order
    the room is in - ``[3, 1]`` is swallowed and both panes end up even, which
    is why the shares named in a spec have never really divided the panes the
    way they read. So the shares are kept here and worked out against whatever
    length the splitter has, every time it is laid out - until somebody drags a
    fold line, which is them saying they know better than the addon; the line
    then stays where they left it. An addon laying the panes out again through
    ``set_widget(tab, id, "sizes", ...)`` takes the promise back up.
    """

    def __init__(self, splitter: QSplitter, shares: list[int],
                 shut: list[bool]) -> None:
        super().__init__(splitter)
        self._splitter = splitter
        # A pane promised nothing still keeps a ratio of one, so folding it and
        # opening it again hands back the room it had rather than a pixel.
        self._shares = [max(1, int(share)) for share in shares]
        self._shut = [bool(one) for one in shut]
        self._taken = False                      # a fold line has been dragged
        self._laying = False
        splitter.splitterMoved.connect(self._dragged)
        splitter.installEventFilter(self)
        self.lay()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and not self._taken:
            self.lay()
        return False

    def _dragged(self, _index: int, _size: int) -> None:
        """A fold line moved under a hand: the room is the person's business now."""
        if self._laying:
            return
        self._taken = True
        self._shut = [int(size) <= 0 for size in _splitter_sizes(self._splitter)]
        held = getattr(self._splitter, "_addon_panes", None)
        if held:
            held["shut"] = list(self._shut)

    def promise(self, shares: list[int]) -> None:
        """Lay the panes out to these shares, and keep doing so as the room moves.

        A zero is the pane folded rather than a ratio of nothing: what it was
        promised before it went is kept, so it comes back at that size.
        """
        for index, share in enumerate(shares):
            if index >= len(self._shares) or index >= len(self._shut):
                break
            share = max(0, int(share))
            if share:
                self._shares[index] = share
            self._shut[index] = share == 0
        self._taken = False
        self.lay()

    def fold(self, index: int) -> None:
        """This pane goes away, or comes back to the room it was promised.

        What a fold line's double-click does: the shares are taken up again, so
        the rest of the splitter moves with it the way the spec said it would.
        """
        index = int(index)
        if not 0 <= index < len(self._shut):
            return
        self._shut[index] = not self._shut[index]
        self._taken = False
        self.lay()

    def lay(self) -> None:
        """Turn the shares into the pixels the splitter has in hand."""
        splitter = self._splitter
        count = splitter.count()
        if count < 1:
            return
        room = (splitter.height() if splitter.orientation() == Qt.Orientation.Vertical
                else splitter.width())
        room = max(0, room - splitter.handleWidth() * (count - 1))
        open_it = [index for index in range(count) if not self._shut[index]]
        total = sum(self._shares[index] for index in open_it) or max(1, len(open_it))
        sizes = [0] * count
        for index in open_it:
            sizes[index] = max(1, self._shares[index] * room // total)
        # The pixels rounding left over go to the widest pane, so the sum is the
        # room and Qt is not left to hand the odd one to whichever it likes.
        spare = room - sum(sizes)
        if open_it and spare:
            sizes[max(open_it, key=lambda index: sizes[index])] += spare
        self._laying = True
        try:
            splitter.setSizes(sizes)
        finally:
            self._laying = False
        held = getattr(splitter, "_addon_panes", None)
        if held:
            held["shut"] = list(self._shut)


class _WrappedHug(QObject):
    """Keeps a word-wrapped line to the height its text actually needs.

    A label that wraps can grow in both directions, and a column shares its
    free room out between everything that can grow: a one-line status note put
    above a table then takes half the page and the table is left as a strip at
    the bottom of it. So the line is pinned to the height its text needs at the
    width it has been given, worked out again whenever the pane widens or
    narrows and whenever the addon writes new words into it. The line keeps its
    wrap - a narrow pane still gets every line, in a taller box - but it never
    eats the room the widgets under it asked for.
    """

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self._label = label
        policy = label.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        label.setSizePolicy(policy)
        label.installEventFilter(self)
        self.cap(label.width())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            size, was = event.size(), event.oldSize()
            if not was.isValid() or size.width() != was.width():
                self.cap(size.width())
        return False

    def cap(self, width: int) -> None:
        """Allow the line exactly the height this much text needs at that width."""
        try:
            need = self._label.heightForWidth(int(width)) if int(width) > 0 else 0
        except (TypeError, ValueError):
            need = 0
        if need <= 0:
            # Nothing laid out to work from yet - a hidden pane, a width of
            # zero - so the label's own guess about its text is the measure.
            need = self._label.sizeHint().height()
        if need > 0:
            self._label.setMaximumHeight(need)


def _hug_wrapped(widget: QWidget) -> None:
    """The one hug a wrapped line has, made on the first ask and found again."""
    if getattr(widget, "_addon_hug", None) is None:
        widget._addon_hug = _WrappedHug(widget)


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


#: Pictures read off disk or parsed out of an SVG, kept by the source that made
#: them: a table redrawn every couple of seconds asks for the same marks over
#: and over, and one read answers them all. A QPixmap copies by reference, so a
#: caller scaling or wearing one never touches what sits here; the cap keeps a
#: long session with many different pictures from growing the memory unawares.
#: Nothing is kept when the picture could not be read: a plain miss costs no
#: parse, and a file that turns up later is noticed the moment it does.
_PICTURE_CACHE: dict[str, QPixmap] = {}
_PICTURE_CACHE_MAX = 128


def _decode_data_uri(text: str) -> bytes | None:
    """The bytes a ``data:...;base64,`` URI holds, or nothing when it is malformed.

    This is how a picture that has no file behind it - a page's own favicon, read
    off the device and handed back to an addon - is carried as text, the one shape
    that survives a trip to a process addon over JSON.
    """
    import base64

    head, split, tail = text.partition(",")
    if not split or "base64" not in head.lower():
        return None
    try:
        return base64.b64decode(tail)
    except (ValueError, TypeError):
        return None


def load_picture(source: Any) -> QPixmap | None:
    """A picture out of an SVG document (as text), an image file at a path, or a
    base64 ``data:`` URI; nothing readable is ``None``, so a missing file leaves a
    blank, not a traceback."""
    from PyQt6.QtCore import QByteArray

    text = str("" if source is None else source).strip()
    if not text:
        return None
    held = _PICTURE_CACHE.get(text)
    if held is not None:
        return held
    image: QPixmap | None = None
    if text.startswith("data:"):
        raw = _decode_data_uri(text)
        if raw is None:
            return None
        made = QPixmap()
        if not made.loadFromData(QByteArray(raw)):
            return None
        image = None if made.isNull() else made
    elif text.startswith(("<svg", "<?xml")):
        made = QPixmap()
        if not made.loadFromData(QByteArray(text.encode("utf-8")), "SVG"):
            return None
        image = None if made.isNull() else made
    else:
        path = Path(text)
        if not path.is_file():
            return None
        made = QPixmap(str(path))
        image = None if made.isNull() else made
    if image is not None:
        if len(_PICTURE_CACHE) >= _PICTURE_CACHE_MAX:
            # The oldest answer goes; insertion order is good enough as age.
            _PICTURE_CACHE.pop(next(iter(_PICTURE_CACHE)), None)
        _PICTURE_CACHE[text] = image
    return image


def widget_pixmap(source: Any, side: int) -> QPixmap | None:
    """A picture sized to fit inside `side` pixels, its shape kept; a picture
    already small enough is drawn as it is."""
    image = load_picture(source)
    if image is None:
        return None
    wanted = max(1, int(side))
    if image.width() > wanted or image.height() > wanted:
        image = image.scaled(wanted, wanted, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return None if image.isNull() else image


def set_widget_picture(widget: QLabel, source: Any) -> None:
    """Draw, or clear, the picture an ``image`` leaf holds.

    An empty source takes the picture away and lets the placeholder text, if
    the addon set one, show again.
    """
    side = int(getattr(widget, "_addon_picture_side", 0) or 0)
    if not side:
        image = load_picture(source)
    else:
        image = widget_pixmap(source, side)
    if image is None:
        widget.clear()
        return
    widget.setPixmap(image)


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
        if bool(spec["word_wrap"]) and isinstance(widget, QLabel):
            # A wrapped line hugs its own height, so the room a column has over
            # falls to the table or text under it rather than being eaten here.
            # Only a label wraps a line of its own; a check box that wraps its
            # caption is still a single row of the form it sits in.
            _hug_wrapped(widget)
    if spec.get("expand") and hasattr(widget, "setSizePolicy"):
        # Take the slack of the row or column it sits in: a long line then wraps
        # inside the room that is actually left, instead of being squeezed by a
        # spacer and cut off.
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(policy.Policy.Expanding)
        widget.setSizePolicy(policy)
    if spec.get("checked") is not None and hasattr(widget, "setChecked"):
        widget.setChecked(bool(spec["checked"]))
    if spec.get("url") is not None and getattr(widget, "_addon_web_load", None) is not None:
        # A ``web`` leaf born with an address. It is still only loaded when it
        # is first seen, so naming one in a spec costs nothing up front.
        apply_widget_property(widget, "url", spec["url"])
    # Fill a combo or list first so "value" can pick from it.
    if spec.get("items") is not None and hasattr(widget, "addItems"):
        apply_widget_property(widget, "items", spec["items"])
    if spec.get("value") is not None:
        apply_widget_property(widget, "value", spec["value"])
    if spec.get("tooltip") is not None:
        widget.setToolTip(str(spec["tooltip"]))
    if spec.get("icon") is not None:
        # A button (or any mark-wearing widget) born with its picture: an SVG
        # document, a file path or a data URI, the shapes ``set_icon`` takes.
        apply_widget_property(widget, "icon", spec["icon"])
    if spec.get("style") is not None:
        style_sheet(widget, str(spec["style"]))
    if spec.get("object_name") is not None:
        widget.setObjectName(str(spec["object_name"]))
    if spec.get("locked") is not None and isinstance(widget, QTabWidget):
        # A container can be born held: the job pane that swaps a tab over is
        # built with "locked": True and never has to remember to ask.
        apply_widget_property(widget, "locked", spec["locked"])
    if spec.get("tab_colors") is not None and isinstance(widget, QTabWidget):
        apply_widget_property(widget, "tab_colors", spec["tab_colors"])
    if "enabled" in spec:
        widget.setEnabled(bool(spec["enabled"]))
    if "visible" in spec:
        widget.setVisible(bool(spec["visible"]))
    if spec.get("font_size"):
        font = widget.font()
        # The addon's size drawn at the interface zoom.
        font.setPointSize(zoom_size(int(spec["font_size"])))
        # A terminal counts its cells from the font it holds itself, so the
        # font has to go through its own door rather than Qt's blanket one.
        if getattr(widget, "_addon_terminal", None) is not None:
            widget.set_font(font)
        else:
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
    if "toggled" in names and isinstance(widget, QTreeWidget):
        # A row's tick flipped by hand: the row's label and whole line come
        # back, so an addon acts on the entry without a second round trip. A
        # repaint sets ticks behind the silent flag and is never reported.
        def _row_toggled(item, changed, _col=getattr(widget, "_addon_check_column", 0)):
            if changed != _col or getattr(widget, "_addon_check_silent", False):
                return
            events(identifier, "toggled", {
                "text": str(item.text(0) or ""),
                "checked": item.checkState(_col) == Qt.CheckState.Checked,
                "cells": _row_cells(item)})
        widget.itemChanged.connect(_row_toggled)
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
        elif isinstance(widget, QTabWidget):
            # Which sub-page the person is looking at: the caption comes back
            # beside the index, so an addon can tell one dynamically built page
            # from another without keeping its own list in step with Qt's.
            widget.currentChanged.connect(
                lambda index: events(identifier, "changed", {
                    "index": int(index),
                    "text": str(widget.tabText(index) or "")
                    if 0 <= index < widget.count() else ""}))
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
                lambda: events(identifier, "selected", _tree_selection(widget)))
            widget.itemDoubleClicked.connect(
                lambda item, _col=0: events(identifier, "selected", {
                    "text": item.text(0), "cells": _row_cells(item)}))
    if "loaded" in names and getattr(widget, "_addon_web_load", None) is not None:
        # A page's own news: whether it came up and what it ended up at, since
        # a redirect or a refused certificate moves it off the address that was
        # asked for. An addon that embeds a page needs this: a web view that
        # shows nothing says nothing on its own.
        widget.loadFinished.connect(
            lambda ok, _view=widget: events(identifier, "loaded",
                                            {"ok": bool(ok),
                                             "url": _web_leaf_shown(_view)}))
    if "icon" in names and getattr(widget, "_addon_web_load", None) is not None:
        # The page's own favicon, as the device serves one. It is handed back as a
        # PNG data URI - the one shape that also reaches an addon running in a
        # process of its own - so an addon can put the dashboard's mark on a button
        # without ever having to fetch the file itself.
        def _page_icon(icon, _id=identifier, _emit=events):
            uri = _icon_data_uri(icon)
            if uri:
                _emit(_id, "icon", {"icon": uri})
        _page = getattr(widget, "page", None)
        _page = _page() if callable(_page) else None
        if _page is not None and hasattr(_page, "iconChanged"):
            _page.iconChanged.connect(_page_icon)
    terminal = getattr(widget, "_addon_terminal", None)
    if terminal is not None:
        # A terminal's news comes from its shell thread and from the screen
        # itself. The addon lists in ``on`` which of it it wants to hear; the
        # shell reports through the same route when its connection comes up,
        # goes down or fails, so one hook serves all of it.
        asked = set(names)

        def report(event: str, payload: Any = None) -> None:
            if event in asked:
                events(identifier, event, dict(payload or {}))

        terminal["report"] = report
        if "bell" in asked:
            widget.bell.connect(lambda: report("bell"))
        if "title" in asked:
            widget.title_changed.connect(
                lambda text: report("title", {"text": str(text)}))


def _row_cells(item: QTreeWidgetItem) -> list[str]:
    """Every column of one row, as the tree shows it."""
    return [str(item.text(column) or "") for column in range(item.columnCount())]


def _tree_selection(widget: QTreeWidget) -> dict:
    """What is picked in a ``tree``: how many, and the first one's label and cells.

    The label alone cannot tell two rows of the same name apart once a tree
    nests, so the whole line rides along: an addon's documentation tree puts one
    word per row and reads it back here.
    """
    picked = widget.selectedItems()
    first = picked[0] if picked else None
    return {"count": len(picked),
            "text": str(first.text(0)) if first is not None else "",
            "cells": _row_cells(first) if first is not None else []}


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
    ".theme": "json",
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


class _PaneHandle(QObject):
    """One fold line, answering for one pane: a double-click folds it or opens it.

    Qt only toggles a pane its own drag happened to collapse, so a pane the addon
    folded at build time - a job's log, to leave the work the whole of the tab -
    would not answer a double-click at all. This does the same job from the line,
    whichever way the pane came to be shut.
    """

    def __init__(self, splitter: QSplitter, index: int) -> None:
        super().__init__(splitter)
        self._splitter = splitter
        self._index = int(index)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.MouseButtonDblClick:
            return False
        held = getattr(self._splitter, "_addon_panes", None) or {}
        fitter = held.get("fit")
        if fitter is None:
            return False
        fitter.fold(self._index)
        event.accept()
        return True


def _pane_shares(count: int, wanted: Any) -> list[int]:
    """The shares a splitter's panes are promised, one per pane, all over zero.

    An addon names them as ratios - ``[3, 1]`` is three parts of the room to the
    first pane and one to the second - and a row or column made resizable with
    nothing said gets an even share, which is what a layout gives today.
    """
    shares = [int(item) for item in (wanted or []) if str(item).strip() != ""]
    if len(shares) != count:
        shares = [1] * count
    if not any(share > 0 for share in shares):
        shares = [1] * count
    return shares


def _make_splitter(children: list, vertical: bool, spec: dict,
                   registry: dict, events) -> QSplitter | None:
    """One pane beside another, with a line between them that moves.

    This is what a ``splitter`` is, and also what a ``column`` or ``row`` becomes
    when the addon says ``"resizable": True`` - the same row of panes, only with
    the freedom to give one of them more of the room. Two things are remembered
    on the widget for that: the shares each pane was promised, so a pane folded
    away can be opened back to what it was, and which pane a fold line belongs
    to, so ``set_widget(tab, id, "sizes", ...)`` can be read back.
    """
    splitter = QSplitter(Qt.Orientation.Vertical if vertical
                         else Qt.Orientation.Horizontal)
    ids: list[str] = []
    foldable: list[bool] = []
    shut: list[bool] = []
    for child in children:
        widget = _build_one(child, registry, events)
        if widget is None:
            continue
        splitter.addWidget(widget)
        ids.append(str((child or {}).get("id") or ""))
        # Only a pane the addon folds on purpose, or one that was asking for
        # room anyway, may be dragged out of sight: the rest keep the words they
        # hold, however hard the line between them is pulled.
        shut.append(bool((child or {}).get("collapsed")))
        foldable.append(shut[-1] or bool((child or {}).get("collapsible")))
    if not splitter.count():
        return None
    shares = _pane_shares(splitter.count(), spec.get("sizes"))
    for index, fold in enumerate(foldable):
        splitter.setCollapsible(index, fold)
    splitter._addon_panes = {"ids": ids, "open": shares, "shut": list(shut)}
    _style_panes(splitter, foldable)
    # The fold lines are as wide as they are going to be, so the shares are
    # worked out against the room that is really left.
    splitter._addon_panes["fit"] = _PaneFitter(splitter, shares, shut)
    return splitter


def _lay_panes(splitter: QSplitter, sizes: list[int]) -> None:
    """Give every pane its share, and the same say in what happens after.

    ``setSizes`` is only a request: the room it leaves over is handed out by each
    pane's stretch factor, so shares named as ratios - ``[3, 1]`` - need the
    stretch to be said in the same ratios or the two panes end up even. A pane
    folded away is given no stretch at all, so nothing drifts back into it.
    """
    for index, share in enumerate(sizes):
        splitter.setStretchFactor(index, max(0, int(share)))
    splitter.setSizes(sizes)


def _style_panes(splitter: QSplitter, foldable: list[bool] | None = None) -> None:
    """Draw the fold lines so they can be seen, and named so they can be found.

    Qt's own handle is a couple of pixels that match the background exactly, and
    a pane folded to nothing leaves nothing but that line to pull it back, so the
    line gets the theme's handle colour and a width worth aiming at. A line next
    to a pane that may be folded is also double-clicked, and says so.
    """
    splitter.setHandleWidth(zoom_size(PANE_HANDLE))
    shade = core.ui.color("handle", default="#5a5a5a")
    held = getattr(splitter, "_addon_panes", None)
    # Which fold lines already answer a double-click, and for which pane: laying
    # the same splitter out twice must not hand it two answers, or the second
    # would open what the first just closed, and a line taken over by another
    # pane has to change its mind rather than gain a second opinion.
    dressed = held.setdefault("lines", {}) if held is not None else {}
    handles: dict[int, int] = {}
    for index, fold in enumerate(foldable or []):
        if fold and splitter.count() > 1:
            # The line under the pane answers for it; a line between two foldable
            # panes is given to the later one, which is the one being closed.
            handles[index if index > 0 else 1] = index
    for index in range(1, splitter.count()):
        handle = splitter.handle(index)
        if handle is None:
            continue
        handle.setStyleSheet(scaled_sheet(f"background-color: {shade};"))
        pane = handles.get(index)
        if pane is None:
            continue
        keeper = dressed.get(index)
        if keeper is None:
            dressed[index] = _PaneHandle(splitter, pane)
            handle.setToolTip("Drag to resize; double-click to fold this pane "
                              "away, and again to open it back up")
            handle.installEventFilter(dressed[index])
        else:
            keeper._index = pane


def fit_panes(splitter: QSplitter, shares: list | None = None,
              foldable: list | None = None) -> QSplitter:
    """Give a splitter the app builds for itself the same say as one a spec builds.

    Some panes are laid out in Qt rather than in a spec - the Addon Dev tab's
    compile-and-test split, the reference over the log under it. Left alone
    those lines are Qt's two pixels of background, and a size asked for there is
    answered once and then dropped at the next resize. This draws the line in
    the theme's handle colour and wide enough to find and grab, keeps the shares
    in `shares` as the window moves until somebody drags it, and lets a pane
    named in `foldable` be folded out of the way and pulled back - the three
    things a ``"splitter"`` gets for nothing. Calling it again on the same
    splitter only lays it out to the new shares.
    """
    count = splitter.count()
    if count < 2:
        return splitter
    given = _pane_shares(count, shares)
    held = getattr(splitter, "_addon_panes", None)
    if held is None:
        held = {"ids": [""] * count, "open": given, "shut": [False] * count}
        splitter._addon_panes = held
    else:
        held["open"] = given
    fold = [bool(one) for one in (foldable or [])][:count]
    fold += [False] * (count - len(fold))
    for index, may in enumerate(fold):
        splitter.setCollapsible(index, may)
    _style_panes(splitter, fold)
    fitter = held.get("fit")
    if fitter is None:
        held["fit"] = _PaneFitter(splitter, given, held["shut"])
    else:
        fitter.promise(given)
    return splitter


def _splitter_sizes(splitter: QSplitter) -> list[int]:
    """How much room each pane has now, in the shares they were given."""
    return [int(size) for size in splitter.sizes()]


def _header_text(view: QTreeView | QTableView, column: int) -> str:
    """The word a column's header shows, read the same off either kind of table."""
    model = view.model()
    if model is None:
        return ""
    try:
        value = model.headerData(column, Qt.Orientation.Horizontal,
                                 Qt.ItemDataRole.DisplayRole)
    except (TypeError, RuntimeError):
        return ""
    return str(value or "")


def _column_room(view: QTreeView | QTableView, column: int) -> int:
    """Where a column nobody pinned starts: as wide as its own words need.

    The label over it and whatever it holds now are both measured, and the wider
    wins, so a table is never opened with its own headings cut short - which is
    what sizing to contents used to buy, before it made every column impossible
    to drag.
    """
    room = zoom_size(DEFAULT_SECTION)
    text = _header_text(view, column)
    if text:
        room = max(room, QFontMetrics(view.font()).horizontalAdvance(text)
                   + zoom_size(SECTION_PADDING))
    try:
        room = max(room, int(view.sizeHintForColumn(column)))
    except (TypeError, ValueError, RuntimeError, AttributeError):
        pass
    return max(zoom_size(MIN_SECTION), room)


def fit_table_columns(view: QTreeView | QTableView, flexible: list[int],
                      widths: list) -> None:
    """Give a table's columns room enough for their words and a drag that works.

    Qt's own way to fill a pane is to stretch every section, which shares the
    room out nicely and will not be resized by hand; the way that can be
    resized leaves an empty strip at the right once the pane widens. So every
    column stays interactive, the ones named in `flexible` are handed what the
    others leave over (see :class:`_SlackColumn`), and the rest start at the
    width the addon pinned for them - or, where it pinned none, as wide as their
    own heading reads, which is what a table stretched by contents used to give
    without taking the drag away. Nothing may go under ``MIN_SECTION``, and a
    table that cannot show all its columns at that width grows a bar to scroll
    rather than cutting its own words off.
    """
    header = _table_header(view)
    header.setStretchLastSection(False)
    header.setSectionsClickable(True)
    header.setSectionsMovable(True)
    header.setMinimumSectionSize(zoom_size(MIN_SECTION))
    columns = view.columnCount()
    for column in range(columns):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        pinned = int(widths[column]) if column < len(widths) else 0
        if pinned > 0:
            view.setColumnWidth(column, zoom_size(pinned))
        elif column not in flexible:
            view.setColumnWidth(column, _column_room(view, column))
    wanted = [column for column in flexible if 0 <= column < columns]
    if wanted:
        _slack_of(view, wanted)


def _apply_pane_sizes(splitter: QSplitter, value: Any) -> None:
    """Hand each pane its share, folding away the ones told to have nothing.

    The shares are the same ratios a spec's ``sizes`` gives, and a zero is the
    pane shut: it keeps its fold line, which is dragged back open or
    double-clicked, and the room it was promised before it went.
    """
    held = getattr(splitter, "_addon_panes", None) or {}
    try:
        shares = [max(0, int(item)) for item in (value or [])]
    except (TypeError, ValueError):
        shares = []
    if len(shares) != splitter.count():
        # Nothing usable said: go back to the promise the spec made.
        shares = [int(one) for one in (held.get("open") or [])]
        if len(shares) != splitter.count():
            shares = [1] * splitter.count()
    for index, share in enumerate(shares):
        if share == 0:
            splitter.setCollapsible(index, True)
    fitter = held.get("fit")
    if fitter is not None:
        fitter.promise(shares)
    else:
        _lay_panes(splitter, shares)
    if held:
        held["shut"] = [share == 0 for share in shares]
        # A pane folded here has to answer its fold line the same as one the
        # spec folded at build time, or the box that opened it would be the only
        # way back. Dressing is idempotent, so the lines already answering are
        # left with the one filter they have.
        _style_panes(splitter, [splitter.isCollapsible(index)
                                for index in range(splitter.count())])


def _build_one(spec: dict, registry: dict,
               events: Callable[[str, str, dict], None] | None) -> QWidget | None:
    kind = str(spec.get("kind") or "").lower()
    children = spec.get("children") or []

    if kind in ("grid", "form") or kind in _LAYPOUT_KINDS:
        if spec.get("resizable") and kind in ("column", "vbox", "row", "hbox"):
            splitter = _make_splitter(children, kind in ("column", "vbox"),
                                      spec, registry, events)
            if splitter is not None and kind in ("row", "hbox"):
                # A row of panes still hugs its contents, as a plain row does:
                # the room it is handed is for the panes to split between them.
                splitter.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Fixed)
            if splitter is not None:
                _apply_common(splitter, spec, registry, events)
            return splitter
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
        # The room left under a scrolled column is the column's own saying: an
        # addon that wants its form pinned to the top of the pane writes
        # ``"stretch": True`` on the column inside the scroll, and a pane that
        # is only a wrapper keeps whatever the scroll itself promised.
        holder = dict(spec)
        if body.get("stretch") is not None:
            holder["stretch"] = body["stretch"]
        _fill_children(layout, body.get("children") or [], str(body.get("kind") or "column"),
                       holder, registry, events)
        area.setWidget(inner)
        _apply_common(area, spec, registry, events)
        return area

    if kind == "splitter":
        splitter = _make_splitter(children, bool(spec.get("vertical")),
                                  spec, registry, events)
        if splitter is None:
            return None
        _apply_common(splitter, spec, registry, events)
        return splitter

    if kind == "overlay":
        # One pane laid over another. The first child is the background and fills
        # the area; each child after it keeps to its own size and floats where
        # "align" says, which is how a job's console carries the addon it is
        # working on in the middle of the screen. A grid cell holding two widgets
        # with an alignment on the second is exactly that, and Qt moves the
        # floating one when the pane is resized rather than the addon having to.
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        for order, child in enumerate(children):
            widget = _build_one(child, registry, events)
            if widget is None:
                continue
            if order == 0:
                grid.addWidget(widget, 0, 0)
                continue
            place = OVERLAY_ALIGN.get(str(child.get("align") or "").lower(),
                                      OVERLAY_ALIGN["center"])
            grid.addWidget(widget, 0, 0, place)
        _apply_common(holder, spec, registry, events)
        return holder

    if kind == "tabs":
        tabs = QTabWidget()
        # Keep how to build more: a container can be handed a sub-page after
        # the tab is on screen, and that page has to write into the same
        # registry and route its events the same way as the rest of the tab.
        # ``ids`` runs beside the pages, holding the ids each one owns, so
        # taking a page away can drop its controls out of the registry and
        # close any terminal inside it.
        tabs._addon_pages = {"registry": registry, "events": events, "ids": []}
        for child in children:
            if not isinstance(child, dict):
                continue
            child = dict(child)
            label = str(child.pop("label", child.get("text", "Tab")))
            owned = set(registry)
            widget = _build_one(child, registry, events)
            if widget is not None:
                tabs.addTab(widget, label)
                tabs._addon_pages["ids"].append(
                    [key for key in registry if key not in owned])
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
    if kind == "image":
        # A picture of its own - an addon's mark, a store item's icon - named by
        # a file path or drawn from an SVG document. "size" is the widest (or
        # tallest) it is allowed to be, so a badge and a banner are the same
        # kind; the size is taken at the interface zoom, like everything else.
        view = QLabel()
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            side = int(spec.get("size", 48))
        except (TypeError, ValueError):
            side = 48
        view._addon_picture_side = max(8, zoom_size(side))
        view.setText("")
        return view
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
                from core.ui import ColorWheelDialog

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
        # The first column is the one that takes what the rest leave; it stays
        # draggable, which a stretched column is not.
        fit_table_columns(tree, [0], list(spec.get("col_widths") or []))
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        return tree
    if kind == "table":
        table = QTreeWidget()
        table.setRootIsDecorated(False)
        names = [str(name) for name in spec.get("columns") or [""]]
        table.setHeaderLabels(names)
        table.setAlternatingRowColors(True)
        # By default every column shares the width out and the last of them
        # takes what is left over; "stretch" names the ones that take it
        # instead, and "col_widths" pins the rest, so a long line has the room
        # its words need and every column can still be dragged. See
        # :func:`fit_table_columns`.
        stretch = spec.get("stretch")
        if stretch is None:
            flexible = [len(names) - 1]
        else:
            asked = stretch if isinstance(stretch, (list, tuple)) else [stretch]
            flexible = []
            for item in asked:
                try:
                    column = int(item)
                except (TypeError, ValueError):
                    continue
                flexible.append(column if column >= 0 else len(names) + column)
        fit_table_columns(table, flexible, list(spec.get("col_widths") or []))
        table.setMouseTracking(True)          # so a row knows the mouse is over it
        if spec.get("multi"):
            # More than one row picked at once: ctrl- and shift-click work the
            # list, so an addon can offer a batch over everything selected.
            table.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        if spec.get("checkbox") is not None:
            # The column that wears a tick per row; a row that carries a
            # "check" becomes user-tickable here, and its changes come back as
            # a "toggled" event. See _place_row and the row delegate.
            table._addon_check_column = int(spec["checkbox"])
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
        view = quiet_web_view()
        # The leaf starts empty and only is given an address by the ``url``
        # property, which is held until the leaf is first on show: see
        # :class:`_WebLeafLoad`. An addon that builds a dashboard into a tab
        # nobody has opened yet should not be paying a renderer for it.
        view._addon_web_load = _WebLeafLoad(view)
        return view
    if kind == "terminal":
        # The app's own terminal screen, for an addon that wants a shell of its
        # own. Read late rather than imported at the top: a bare addon test
        # harness can build specs without the terminal module in sight, and an
        # addon that never asks for a shell should not be the reason it fails.
        try:
            from core.net import TerminalScreen
        except Exception as error:            # a terminal-less app is still an app
            return QLabel(f"No terminal available here: {error}")
        screen = TerminalScreen()
        # Everything the shell thread and the ``connect`` property share.
        # ``report`` is put there by ``_connect_events`` when the spec asks for
        # the terminal's events; ``last`` is what a closed shell was attached
        # to, so ``state`` still answers after the connection has gone.
        screen._addon_terminal = {
            "shell": None,
            "report": None,
            "last": {"connected": False, "host": "", "port": 22, "username": ""},
        }
        return screen
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


def style_sheet(widget: QWidget, text: Any) -> QWidget:
    """Lay one addon sheet on a widget so it follows the theme and the zoom.

    The sheet is kept as written - pixels at 100% - and ``core.ui`` remembers
    it against the widget, re-pouring it scaled every time the interface zoom
    or the theme changes; ``$token`` words in it are read as the theme's own.
    With no app beside the widget there is nobody to re-pour, so the sheet is
    scaled once here, which is the best a bare process can do.
    """
    text = str(text or "")
    try:
        from core import ui as _ui
    except ImportError:                        # an addon running with no app
        _ui = None
    if _ui is not None:
        try:
            _ui.theme_style(widget, text)
            if not text:
                widget.setStyleSheet("")       # an empty sheet is a cleared one
            return widget
        except (AttributeError, RuntimeError, TypeError):
            pass
    widget.setStyleSheet(scaled_sheet(text) if text else "")
    return widget


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
        style_sheet(widget, text)
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
        if getattr(widget, "_addon_picture_side", None) is not None:
            # An ``image`` leaf's value is the picture itself.
            set_widget_picture(widget, value)
        elif isinstance(widget, QCheckBox):
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
        raw = list(value or [])
        names = [str(entry.get("text", "") if isinstance(entry, dict) else entry)
                 for entry in raw]
        if hasattr(widget, "setHeaderLabels"):
            widget.setHeaderLabels(names)
        elif isinstance(widget, QListWidget):
            # A list entry may wear a mark of its own: naming it in a dict
            # ({"text": ..., "icon": ...}) puts the picture before the words.
            widget.clear()
            for entry, name in zip(raw, names):
                item = QListWidgetItem(name)
                mark = entry.get("icon") if isinstance(entry, dict) else None
                icon = build_icon(mark) if mark else None
                if icon is not None:
                    item.setIcon(icon)
                widget.addItem(item)
        elif hasattr(widget, "clear") and hasattr(widget, "addItems"):
            # A combo keeps its choice; a list widget has no currentText.
            current = widget.currentText() if hasattr(widget, "currentText") else None
            widget.clear()
            widget.addItems(names)
            if current is not None and current in names:
                widget.setCurrentText(current)
        elif hasattr(widget, "clear"):
            widget.clear()
            widget.addItems(names)
    elif prop == "image":
        if isinstance(widget, QLabel):
            set_widget_picture(widget, value)
    elif prop == "icon":
        # A mark worn beside a widget's own words - a button's picture - named by
        # a file path, an SVG document or a base64 data URI, the three shapes a
        # tab's mark already takes. An empty source clears it again.
        if hasattr(widget, "setIcon"):
            mark = build_icon(value)
            widget.setIcon(mark if mark is not None else QIcon())
    elif prop in ("current", "index"):
        # Which page a ``tabs`` container shows, or a list's picked row.
        if hasattr(widget, "setCurrentRow"):
            widget.setCurrentRow(int(value or 0))
        elif hasattr(widget, "setCurrentIndex"):
            widget.setCurrentIndex(int(value or 0))
    elif prop == "rows":
        silent = getattr(widget, "_addon_check_column", None) is not None
        if silent:
            # Laying rows down sets each tick, which fires itemChanged; the
            # table must not report those as the user flipping them.
            widget._addon_check_silent = True
        try:
            if hasattr(widget, "clear"):
                widget.clear()
            for row in value or []:
                _place_row(widget, row, None)
        finally:
            if silent:
                widget._addon_check_silent = False
    elif prop == "clear":
        if hasattr(widget, "clear"):
            widget.clear()
    elif prop in ("connect", "disconnect", "send", "command", "write"):
        # A terminal's own connection, and the note an addon prints on it. The
        # first three reach the device, so they pass the gates of
        # :func:`_authorize_terminal`, called by the two ``set_widget``
        # implementations - the only places that hold an api.
        if prop == "connect":
            _terminal_connect(widget, value)
        elif prop == "disconnect":
            _terminal_disconnect(widget)
        elif prop == "write":
            if _is_terminal(widget):
                widget.write_local(text)
            else:
                raise AddonError("'write' prints on a terminal screen")
        elif prop == "send":
            _terminal_send(widget, value)
        else:
            _terminal_send(widget, value, line=True)
    elif prop in WEB_LEAF_COMMANDS:
        # A ``web`` leaf's address, and the moves inside the page it holds.
        _web_leaf_command(widget, prop, value)
    elif prop == "sizes":
        # A splitter's panes: the shares each is given, where a 0 folds that
        # pane away and leaves its fold line to pull it back.
        if not isinstance(widget, QSplitter):
            raise AddonError("'sizes' opens and folds the panes of a splitter")
        _apply_pane_sizes(widget, value)
    elif prop in ("add_page", "remove_page"):
        # Sub-pages built after the tab is on screen: how an addon opens one
        # page per connection and takes it away again when the link drops.
        if _tabs_store(widget) is None:
            raise AddonError(f"'{prop}' needs a tabs container")
        if prop == "add_page":
            _tabs_add_page(widget, value)
        else:
            _tabs_remove_page(widget, value)
    elif prop == "locked":
        # Hold a tabs container on the page it is showing: no click on its own
        # sub-bar, and no keyboard or wheel move, until it is let go. Used for a
        # job that must not be browsed away from; the app's main bar is held by
        # the same idea through ``api.tabs.lock``.
        if not isinstance(widget, QTabWidget):
            raise AddonError("'locked' holds the pages of a tabs container")
        from core.ui import set_tab_lock

        if not set_tab_lock(widget, bool(value)) and bool(value):
            raise AddonError("'locked' needs a tabs container")
    elif prop == "bar_visible":
        # Stow a tabs container's caption bar while its pages stay exactly
        # where they are: what lets a job's pane own the whole of a container
        # with nothing to click back to. The window's own bar answers to the
        # same idea through ``api.tabs.bar_visible``.
        if not isinstance(widget, QTabWidget):
            raise AddonError("'bar_visible' stows the bar of a tabs container")
        widget.tabBar().setVisible(bool(value))
    elif prop == "tab_colors":
        # One caption colour per page, given as a list of colours where an
        # empty entry means "as the theme draws it". A page that is switched
        # off is greyed this way rather than disabled, so it can still be
        # clicked open and read.
        if not isinstance(widget, QTabWidget):
            raise AddonError("'tab_colors' paints the captions of a tabs container")
        bar = widget.tabBar()
        for index, colour in enumerate(value or []):
            if not (0 <= index < widget.count()) or bar is None:
                continue
            tint = str(colour or "").strip()
            if not tint:
                bar.setTabTextColor(index, QColor())
                continue
            paint = QColor(tint)
            bar.setTabTextColor(index, paint if paint.isValid() else QColor())
    elif prop == "font_size":
        font = widget.font()
        font.setPointSize(zoom_size(int(value or 10)))
        # A terminal counts its cells from the font it holds itself.
        if _is_terminal(widget):
            widget.set_font(font)
        else:
            widget.setFont(font)
    hug = getattr(widget, "_addon_hug", None)
    if hug is not None:
        # A wrapped line's own height is worked out from the words it carries,
        # so any property that can move those words - ``text``, ``html``,
        # ``value``, a new ``font_size`` - has to be asked again how much room
        # the line needs now. Qt does not say so through a resize, and a status
        # line left capped at its first message would be cut off mid-sentence.
        hug.cap(widget.width())


def _make_row(row: Any) -> tuple[QTreeWidgetItem, Any, bool]:
    """One ``rows`` entry as an item, with whatever is nested under it.

    A row is a list of cells, or a dict carrying colours, a mark and - for a
    ``tree`` - the rows under it, so one line can hold a whole branch: a file
    tree of documentation, a folder of keys. Returns the item, the children to
    hang below it, and whether it starts open.
    """
    cells, fore, back, over = row, None, None, None
    mark = None
    kids: Any = None
    open_it = False
    if isinstance(row, dict):
        cells = row.get("cells", row.get("row", ""))
        fore = row.get("color", row.get("foreground"))
        back = row.get("background", row.get("bg"))
        over = row.get("overlay", row.get("highlight"))
        mark = row.get("icon")
        kids = row.get("children", row.get("rows"))
        open_it = bool(row.get("expanded", row.get("open")))
    cells = cells if isinstance(cells, (list, tuple)) else [cells]
    item = QTreeWidgetItem([str(cell) for cell in cells])
    for column, cell in enumerate(cells):
        # Qt elides a wide cell; the tooltip carries the whole text.
        item.setToolTip(column, str(cell))
    for colour, paint in ((fore, item.setForeground), (back, item.setBackground)):
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
    if mark:
        icon = build_icon(mark)
        if icon is not None:
            item.setIcon(0, icon)
    return item, kids, open_it


def _place_row(widget: QWidget, row: Any, parent: QTreeWidgetItem | None) -> None:
    """Put one row - and the rows under it - into a ``table`` or ``tree``.

    The icon rides in front of the first cell, which is what lets a list of
    addons show each one's own picture; colours ride the row itself, painted by
    the table's delegate rather than the app sheet.
    """
    item, kids, open_it = _make_row(row)
    column = getattr(widget, "_addon_check_column", None)
    if column is not None and isinstance(row, dict) and "check" in row:
        # Only a row that says so wears a tick: an addon's list puts a switch
        # beside the entries it can act on and leaves the rest plain.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(column, Qt.CheckState.Checked if row.get("check")
                           else Qt.CheckState.Unchecked)
    if parent is None:
        widget.addTopLevelItem(item)
    else:
        parent.addChild(item)
    for child in (kids or []):
        _place_row(widget, child, item)
    if open_it:
        item.setExpanded(True)


# --- a terminal's own connection, and sub-pages built after the fact ---

#: The terminal properties that reach a machine over SSH, and so may only be
#: used once the gates ``device.run`` passes through have passed for this addon.
TERMINAL_PRIVILEGED = ("connect", "send", "command")


def _is_terminal(widget: Any) -> bool:
    """Whether this widget is an addon's ``terminal`` screen."""
    return getattr(widget, "_addon_terminal", None) is not None


def _terminal_settings(value: Any) -> dict:
    """The machine a terminal is pointed at, from either shape an addon uses:
    a dict of host/port/username/password, or a ``user@host:port`` string."""
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    text = str(value or "").strip()
    settings: dict = {}
    if "@" in text:
        settings["username"], _, text = text.partition("@")
    if ":" in text and text.count(":") == 1:     # host:port, not an IPv6 literal
        settings["host"], _, settings["port"] = text.rpartition(":")
    else:
        settings["host"] = text
    return settings


def _terminal_report(widget: QWidget, event: str, payload: Any = None) -> None:
    """Tell the addon one of its terminals has news, if it asked to hear it."""
    report = (getattr(widget, "_addon_terminal", None) or {}).get("report")
    if callable(report):
        try:
            report(event, payload)
        except Exception:                        # a dead tab must not kill a shell
            pass


def _terminal_connect(widget: QWidget, value: Any) -> None:
    """Bring this terminal's own connection up and hand the shell to the screen."""
    state = getattr(widget, "_addon_terminal", None)
    if state is None:
        raise AddonError("that widget is not a terminal")
    shell = state.get("shell")
    if shell is not None and shell.isRunning():
        return                     # already up: a second call would drop the screen
    settings = _terminal_settings(value)
    if not str(settings.get("host", "")).strip():
        raise AddonError("a terminal needs a host to connect to")
    cols, rows = widget.grid_size()
    shell = OwnShellWorker(settings, cols=cols, rows=rows, parent=widget)
    state["shell"] = shell
    state["last"] = dict(shell.state)
    who = shell.state["username"]
    where = f"{shell.state['host']}:{shell.state['port']}"
    widget.write_local(f"Connecting to {who}@{where}\n" if who
                       else f"Connecting to {where}\n")
    shell.received.connect(widget.feed)
    shell.connected.connect(lambda: _terminal_connected(widget))
    shell.failed.connect(lambda message: _terminal_failed(widget, message))
    shell.disconnected.connect(lambda: _terminal_closed(widget))
    widget.send_data.connect(shell.send)
    widget.resized.connect(shell.resize)
    shell.start()
    widget.setFocus()


def _terminal_connected(widget: QWidget) -> None:
    """The shell thread is talking to the device."""
    state = getattr(widget, "_addon_terminal", None) or {}
    shell = state.get("shell")
    details = dict(shell.state) if shell is not None else dict(state.get("last") or {})
    details["connected"] = True
    state["last"] = details
    widget.write_local("Connected.\n")
    _terminal_report(widget, "ready", details)


def _terminal_failed(widget: QWidget, message: str) -> None:
    """The connection did not come up, or died mid-session; the reason shows on
    the screen, where the user is looking, as well as in the addon's event."""
    widget.write_local(f"SSH error: {message}\n")
    _terminal_report(widget, "failed", {"error": str(message)})


def _terminal_closed(widget: QWidget) -> None:
    """The shell thread has finished, however it ended: unhook it, say so on the
    screen and let the addon know, which is how a sub-page closes itself when its
    connection drops."""
    state = getattr(widget, "_addon_terminal", None)
    if state is None:
        return
    shell = state.pop("shell", None)
    details = dict(shell.state) if shell is not None else dict(state.get("last") or {})
    details["connected"] = False
    state["last"] = details
    if shell is not None:
        # Until this thread ends, the screen's keystrokes belong to it.
        try:
            widget.send_data.disconnect(shell.send)
            widget.resized.disconnect(shell.resize)
        except (TypeError, RuntimeError):
            pass                      # a connect that failed never wired them
        shell.deleteLater()
    widget.write_local("Connection closed.\n")
    _terminal_report(widget, "closed", details)


def _terminal_disconnect(widget: QWidget) -> None:
    """Ask for this terminal's connection to be dropped; nothing if it is not up."""
    state = getattr(widget, "_addon_terminal", None)
    if state is None:
        raise AddonError("that widget is not a terminal")
    shell = state.get("shell")
    if shell is not None and shell.isRunning():
        shell.stop()                  # ``closed`` reports when the thread is done


def _terminal_stop(widget: QWidget, patience: int = 3000) -> None:
    """End a terminal's shell and wait for the thread, for a page that is about
    to be thrown away: a running QThread deleted under Qt is a crash, not a leak."""
    state = getattr(widget, "_addon_terminal", None)
    if state is None:
        return
    shell = state.pop("shell", None)
    if shell is None:
        return
    try:
        widget.send_data.disconnect(shell.send)
        widget.resized.disconnect(shell.resize)
    except (TypeError, RuntimeError):
        pass
    shell.stop()
    shell.wait(patience)
    state["last"] = {**(state.get("last") or {}), "connected": False}


def _terminal_send(widget: QWidget, value: Any, line: bool = False) -> None:
    """Type into a terminal; a list is sent one after another, so one broadcast
    field can hand the same command to several shells in a single call."""
    state = getattr(widget, "_addon_terminal", None)
    if state is None:
        raise AddonError("that widget is not a terminal")
    shell = state.get("shell")
    if shell is None or not shell.isRunning():
        raise AddonError("this terminal has no connection to write to")
    pieces = value if isinstance(value, (list, tuple)) else [value]
    for piece in pieces:
        text = str(piece if piece is not None else "")
        if not text:
            continue
        shell.send_line(text) if line else shell.send(text)


def _terminal_text(widget: QWidget) -> str:
    """What is on a terminal's screen now: the visible rows, one per line, each
    without the blanks it carries to stay as wide as the grid."""
    try:
        rows = ["".join(cell[0] for cell in row).rstrip() for row in widget.lines]
    except (AttributeError, TypeError, IndexError):
        return ""
    return "\n".join(rows)


# --- a ``web`` leaf: an address held until it is seen, and the page's news ---

#: What ``set_widget`` can ask of a ``web`` leaf beyond the properties every
#: control answers to: an address to show, and the moves inside the page.
WEB_LEAF_COMMANDS = ("url", "reload", "back", "forward", "stop")


class _WebLeafLoad(QObject):
    """Holds a ``web`` leaf's address until the leaf is actually looked at.

    The same bargain a web tab is built on, in :mod:`core.views`: an addon that
    points a page at a tab nobody has opened yet should pay a widget and no
    renderer, so the address waits for the leaf's first Show event, which never
    comes while the tab is hidden. Once a leaf has been shown the address is
    loaded at once, and an address already showing is not loaded again - a page
    that reloaded every time an addon hid its own form over it would throw the
    user out of whatever they had reached.
    """

    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self._view = view
        self._asked = ""          # the address held back for a first Show
        self._shown = ""          # the address the view was last sent to
        view.installEventFilter(self)

    def pending(self) -> str:
        """The address held and not yet loaded, which is empty more often than not."""
        return self._asked

    def shown(self) -> str:
        """The address this leaf was last sent to, loaded or not."""
        return self._shown

    def goto(self, address: Any) -> bool:
        """Show ``address``: now when the leaf is on screen, else at its first Show.

        Returns whether a load was sent off this time round; an address the
        leaf is already showing is answered without one.
        """
        text = str(address or "").strip()
        if not text:
            return False
        if text in (self._shown, self._asked):
            return False          # already there, or already queued to go there
        self._asked = text
        if self._view.isVisible():
            return self.fire()
        return False

    def fire(self) -> bool:
        """Load what is held; ``False`` when there is nothing waiting."""
        if not self._asked:
            return False
        self._shown, self._asked = self._asked, ""
        self._view.load(QUrl(self._shown))
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._asked and event.type() == QEvent.Type.Show:
            try:
                self.fire()
            except Exception:                    # a page that will not start is quiet
                self._asked = ""
        return False


def _is_web_leaf(widget: Any) -> bool:
    """Whether this widget is an addon's ``web`` leaf, and so takes an address.

    Only a real view: with QtWebEngine missing the kind is a label, and a label
    is asked for its address the way any other control is - which is to say it
    has none, and says so.
    """
    return getattr(widget, "_addon_web_load", None) is not None


def _web_leaf_shown(widget: QWidget) -> str:
    """The address a ``web`` leaf is at, or the one it is waiting to be taken to.

    A leaf that has not been shown yet has no address of its own - Qt answers an
    empty view with an empty url - so the one held back for it is what an addon
    is told, which is how a tab that has never been opened still reports where
    it is going.
    """
    hold = getattr(widget, "_addon_web_load", None)
    try:
        address = str(widget.url().toString() or "")
    except (AttributeError, RuntimeError, TypeError):
        address = ""
    if address or hold is None:
        return address
    return hold.pending() or hold.shown()


def _web_leaf_command(widget: QWidget, prop: str, value: Any) -> None:
    """One ``url`` / ``reload`` / ``back`` / ``forward`` / ``stop`` on a web leaf."""
    hold = getattr(widget, "_addon_web_load", None)
    if hold is None:
        raise AddonError(f"'{prop}' shows a page in a web leaf")
    if prop == "url":
        hold.goto(value)
        return
    try:
        if prop == "reload":
            widget.reload()
        elif prop == "stop":
            widget.stop()
        elif prop == "back":
            widget.back()
        elif prop == "forward":
            widget.forward()
    except (AttributeError, RuntimeError) as error:
        raise AddonError(f"the page would not {prop}: {error}") from error


def _read_web_leaf(widget: QWidget, what: str) -> Any:
    """What a ``web`` leaf answers to ``widget_value``, none of it a text widget's.

    A view has no ``text()`` at all, so without this a ``"text"`` ask would fall
    through to a call that does not exist and raise inside the app.
    """
    hold = getattr(widget, "_addon_web_load", None)
    if what in ("url", "value", "text"):
        return _web_leaf_shown(widget)
    if what == "pending":
        return hold.pending() if hold is not None else ""
    if what == "title":
        try:
            return str(widget.title() or "")
        except (AttributeError, RuntimeError):
            return ""
    if what == "loading":
        try:
            return bool(widget.isLoading())
        except (AttributeError, RuntimeError):
            return False
    return None


def _tabs_store(widget: QWidget) -> dict | None:
    """The build information a ``tabs`` container kept for itself, or ``None``."""
    return getattr(widget, "_addon_pages", None)


def _tabs_add_page(widget: QWidget, value: Any) -> int:
    """Build one more sub-page and put it in the container; returns its index."""
    store = _tabs_store(widget)
    if store is None:
        raise AddonError("that widget is not a tabs container")
    entry = value if isinstance(value, dict) else {"spec": value}
    spec = entry.get("spec")
    if not isinstance(spec, dict):
        raise AddonError("add_page needs a spec dict for the new sub-page")
    spec = dict(spec)
    label = str(entry.get("label", spec.pop("label", spec.get("text", "Tab"))))
    registry, events = store["registry"], store["events"]
    owned = set(registry)
    page = _build_one(spec, registry, events)
    if page is None:
        raise AddonError(f"add_page could not build that spec: {spec.get('kind')!r}")
    index = widget.count()
    if entry.get("position") is not None:
        index = max(0, min(int(entry["position"]), widget.count()))
    widget.insertTab(index, page, label)
    store["ids"].insert(index, [key for key in registry if key not in owned])
    if entry.get("current", True):
        widget.setCurrentIndex(index)
    return index


def _tabs_remove_page(widget: QWidget, value: Any) -> bool:
    """Take one sub-page away: its controls leave the tab's registry, any
    terminal inside it is closed, and the page itself goes."""
    store = _tabs_store(widget)
    if store is None:
        raise AddonError("that widget is not a tabs container")
    index = -1
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        index = int(value)
    else:
        wanted = str(value or "")
        for candidate in range(widget.count()):
            if widget.tabText(candidate) == wanted:
                index = candidate
                break
    if index < 0 or index >= widget.count():
        return False
    page = widget.widget(index)
    ids = store["ids"].pop(index) if index < len(store["ids"]) else []
    for identifier in ids:
        control = store["registry"].pop(identifier, None)
        if _is_terminal(control):
            _terminal_stop(control)
    widget.removeTab(index)
    if page is not None:
        page.deleteLater()
    return True


def _authorize_terminal(api: "AddonAPI", prop: str, widget: QWidget) -> None:
    """Refuse a terminal action that reaches a machine until the addon has said
    it may use SSH and has a connection of its own to do it on.

    The same gates ``device.run`` passes through: the master switch, this
    addon's switch, that addon's SSH switch, its ``requires`` list, and - since
    a terminal is never a view of the app's main session - a manifest that
    declared a connection of its own. Reading a screen, typing into one that is
    already up, printing a note on it and letting go of it are not gated: the
    permission was asked for once, when the connection was opened.
    """
    device = api.device
    device._guard("ssh", uses_main=False)
    device._allow_own()
    device._mark("ssh")


def read_widget_value(widget: QWidget, what: str = "value") -> Any:
    """The counterpart of :func:`apply_widget_property`."""
    numeric = (QSpinBox, QDoubleSpinBox, QSlider, QProgressBar)
    if what in ("text", "value") and _is_terminal(widget):
        # A terminal holds no text widget at all: the answer is the screen.
        return _terminal_text(widget)
    if _is_web_leaf(widget):
        # Nor does a web leaf hold text of its own; its state is the address.
        return _read_web_leaf(widget, what)
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
    if what == "pages":
        # Every caption a ``tabs`` container holds, in order.
        return [str(widget.tabText(index))
                for index in range(widget.count())] if hasattr(widget, "tabText") else None
    if what == "sizes" and isinstance(widget, QSplitter):
        return _splitter_sizes(widget)
    if what == "panes" and isinstance(widget, QSplitter):
        # How the panes are laid now: the room each has, whether it is showing,
        # and the id each one answers to.
        held = getattr(widget, "_addon_panes", None) or {}
        sizes = _splitter_sizes(widget)
        return {"sizes": sizes,
                "ids": [str(one) for one in (held.get("ids") or [])],
                "open": [int(size) > 0 for size in sizes]}
    if what == "state" and _is_terminal(widget):
        # What a terminal is attached to, password left out of it by design.
        state = widget._addon_terminal
        shell = state.get("shell")
        if shell is not None:
            return dict(shell.state)
        return dict(state.get("last") or {"connected": False})
    if what == "grid" and _is_terminal(widget):
        return list(widget.grid_size())
    # Spin boxes and progress bars answer to text() with the formatted label.
    if isinstance(widget, numeric):
        return int(widget.value())
    if what == "value" and isinstance(widget, QCheckBox):
        # A check box's value is its tick, which is the one thing "value" writes to
        # it; its words are only ever asked for as ``text``, and are what a dialog
        # would otherwise read back instead of the answer.
        return bool(widget.isChecked())
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
        """What the app looks like from the inside now: profile, host, ports,
        and which connections stand up."""
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
            "main_connected": bool(getattr(self._win, "main_connected", False) or live),
            # The two transport keys stay, and say the same thing: the one
            # main connection carries both, so it is either up or it is not.
            "sftp_connected": bool(getattr(self._win, "main_connected", False) or live),
            "ssh_connected": bool(getattr(self._win, "main_connected", False) or live),
            # The master switch, and whether the SharedConnection is open now.
            "connections_enabled": bool(getattr(
                self._win, "connections_enabled", lambda: True)()),
            "main_connection_open": live,
            # A terminal channel is attached to the main connection or none.
            "shell_open": bool(shell is not None and shell.isRunning()),
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
        """The program's own version, as ``core.const.APP_VERSION`` names it."""
        return APP_VERSION

    def app_stylesheet(self) -> str:
        """The theme the window is drawn from, already scaled to the interface zoom."""
        return scaled_sheet(core.ui.stylesheet())

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

    def addon_state(self, addon_id: str) -> dict:
        """Where one addon stands in this profile now.

        Three answers, none of which an addon can read for itself once it is
        written out to disk: whether the program is in the folder at all
        (``installed``), whether the profile has its switch up (``enabled``),
        and whether it is loaded and running this moment (``running``). The
        store reads this before it replaces a program, so it can put the switch
        back where it found it afterwards.
        """
        manager = self._api._manager
        ident = str(addon_id)
        addon = manager.find(ident)
        record = manager.record_for(ident)
        return jsonable({
            "installed": addon is not None,
            "enabled": bool(record.get("enabled", False)),
            "running": bool(addon is not None and addon.loaded),
        })

    def set_addon_running(self, addon_id: str, running: bool) -> dict:
        """Switch one addon on or off, and start or stop it to match.

        Where :meth:`set_addon_enabled` only writes the record for a rescan to
        pick up later, this acts at once: turning on loads the addon now, and
        turning off takes a running one down - stopping its process, so the
        files it was built from can be replaced - before writing its switch
        off. The store uses it to kill and disable an addon it is about to
        install over, and to bring an installed addon to life from a toggle in
        its own list. The new :meth:`addon_state` comes back.
        """
        manager = self._api._manager
        manager.set_enabled(str(addon_id), bool(running))
        return self.addon_state(str(addon_id))

    # --- the addon manager, and the profile's own switches ---

    #: The only app settings an addon may write. Everything else in the profile is
    #: somebody's connection, password or tab layout, and an addon has no business
    #: moving it; reading is unrestricted.
    _WRITABLE_SETTINGS = ("addon_dev_enabled",)

    def _addon_row(self, addon: Any) -> dict:
        """One addon, in the shape the Addon manager's table and form are built from."""
        manager = self._api._manager
        return {
            "id": addon.id,
            "name": addon.name,
            "version": addon.version,
            "author": addon.author,
            "description": addon.description,
            "kind": addon.kind,
            "installed": True,
            "enabled": bool(addon.enabled),
            "running": bool(addon.loaded),
            "bundled": bool(addon.is_bundled),
            "error": addon.error,
            "requires": list(addon.requires),
            "connection": addon.connection,
            "own_connection": bool(addon.own_connection),
            # The two gates the addon holds over its own traffic, and whether it
            # may hold either: an addon that declared no transport is offered no
            # switch, and only one that said "both" gets to pick a connection.
            "ssh_allowed": bool(addon.transport_allowed("ssh")),
            "sftp_allowed": bool(addon.transport_allowed("sftp")),
            "own_switchable": bool(addon.connection == "both"
                                   and not addon.is_bundled),
            "popout_declared": str(addon.popout or ""),
            "popout_switch": bool(addon.popout_record),
            "popout_switchable": bool(addon.popout_switchable),
            "popout": bool(addon.effective_popout),
            "git_token": bool(addon.git_token),
            "tabs": sorted(addon.tabs),
            "window_open": bool(manager.popped(addon.id)),
            "shortcut": self._addon_shortcut(addon),
            # Which of the two pages the addon belongs to: Settings shows a
            # shipped addon and the Addon manager shows every other one, so the
            # store asks the app rather than guessing from ``bundled``, which
            # means something slightly different in a source checkout.
            "settings_page": bool(getattr(manager, "owns_settings_page",
                                          lambda _a: False)(addon)),
        }

    def _addon_shortcut(self, addon: Any) -> str:
        """The path of this addon's Desktop link, or nothing when it has none.

        The Addon manager's **Desktop shortcut** button is drawn from the file on
        disk rather than from what was clicked last, so a link made by
        ``api.window.set_shortcut`` - or deleted by hand - reads the same either
        way. Nothing is created here: this only asks the popout module where the
        link would live and whether it is there.
        """
        try:
            from core import popout
        except ImportError:                      # no window support on this build
            return ""
        try:
            if not popout.has_shortcut(addon):
                return ""
            return str(popout.shortcut_file(addon))
        except Exception as error:               # a missing folder is not an error
            self._api.log(f"desktop shortcut for {getattr(addon, 'id', '?')}: {error}")
            return ""

    def addons(self) -> list[dict]:
        """Every addon this profile knows, the way the Addon manager lists them.

        One call builds the whole page: a row per addon in the folder, named in
        the order the manager found them, each saying whether the program is
        there (``installed``), whether the profile switches it on (``enabled``)
        and whether it is loaded this moment (``running``), whether it came
        inside the program (``bundled``), what it needs and which connection it
        rides, and the three window answers - what its manifest ``popout_declared``
        (``"always"``, ``"never"`` or ``""`` for nothing said), whether the app's
        ``popout_switch`` is up, whether that switch is offered at all
        (``popout_switchable``), and what actually holds (``popout``) - beside its
        tab names, whether its own window is ``window_open``, the path of its
        Desktop ``shortcut`` when it has one (empty when it has not) and whether
        the application's own Settings page lists it (``settings_page``, true for
        the addons that came inside the program, which the Addon manager leaves
        out). Its two traffic gates come back too, as ``ssh_allowed`` and
        ``sftp_allowed`` for the switches on the addon's own row, and
        ``own_switchable`` says whether the *own connection* choice is this addon's
        to make - true only for one that declared ``"connection": "both"`` and did
        not come inside the program.
        """
        manager = self._api._manager
        return jsonable([self._addon_row(addon) for addon in manager.addons])

    def addon_card(self, addon_id: str) -> dict:
        """One addon, in the same shape as :meth:`addons` puts them in.

        ``installed`` says False when the folder holds nothing under that id, and
        the rest of the answer is the defaults a page can draw without testing for
        a missing row.
        """
        addon = self._api._manager.find(str(addon_id))
        if addon is None:
            return jsonable({
                "id": str(addon_id), "name": str(addon_id), "version": "",
                "author": "", "description": "", "kind": "", "installed": False,
                "enabled": False, "running": False, "bundled": False,
                "error": "", "requires": [], "connection": "app",
                "own_connection": False, "ssh_allowed": True,
                "sftp_allowed": True, "own_switchable": False,
                "popout_declared": "",
                "popout_switch": False, "popout_switchable": False,
                "popout": False, "git_token": False, "tabs": [],
                "window_open": False, "shortcut": "", "settings_page": False})
        return jsonable(self._addon_row(addon))

    def set_addon_popout(self, addon_id: str, on: bool) -> bool:
        """Move the app's **force popout** switch for one addon, and move its tabs.

        On, every page the addon owns leaves the main window's bar for a window of
        its own; off, they go back to the slots they left. Only an addon that said
        nothing about it in its manifest, and that did not come inside the program,
        has a switch to move - anything else answers False and says why in the log.
        An addon wanting its *own* tab out uses ``api.window.popout(tab)``, which is
        allowed for any addon.
        """
        return bool(self._api._manager.set_popout(str(addon_id), bool(on)))

    def addon_popout(self, addon_id: str) -> bool:
        """Whether this addon's pages belong in a window of their own right now."""
        addon = self._api._manager.find(str(addon_id))
        return bool(addon is not None and addon.effective_popout)

    def set_addon_git_token(self, addon_id: str, on: bool) -> bool:
        """Let one addon read the profile's global git token, or not.

        This is the switch the Addon manager puts beside an addon's enable switch:
        on, ``api.session.git_token()`` and ``api.session.github_headers()`` answer
        for that addon with what the profile holds; off, they answer empty and the
        addon is anonymous, which is the default for everybody.
        """
        return bool(self._api._manager.set_git_token(str(addon_id), bool(on)))

    def show_addon_window(self, addon_id: str) -> bool:
        """Bring one addon's own window up, popping its tabs out of the bar first.

        The row's **Raise window**: an addon whose window was closed is still
        running, so this is how the user gets it back without touching the switch.
        """
        return bool(self._api._manager.show_addon_window(str(addon_id)))

    def set_addon_shortcut(self, addon_id: str, on: bool) -> bool:
        """Put, or take away, the Desktop link that starts one addon in its own window.

        The link launches this program with ``--popout <id>``, which loads that one
        addon and nothing else, and opens the profile's main connection only when
        the addon rides it. Returns whether the link is there afterwards.
        """
        addon = self._api._manager.find(str(addon_id))
        if addon is None:
            return False
        try:
            from core import popout
        except ImportError as error:
            self._api.log(f"no desktop shortcut support here: {error}")
            return False
        made, where = (popout.write_shortcut(addon) if bool(on)
                       else popout.remove_shortcut(addon))
        if not made:
            self._api.log(f"desktop shortcut for {addon_id}: {where}")
        return bool(made)

    def set_addon_transport(self, addon_id: str, transport: str, on: bool) -> bool:
        """Move one addon's own gate over SSH or SFTP, and say whether it has one.

        This is the switch that used to sit under the addon's name in Settings and
        now sits on its tab in the store's Addon manager: off, the addon is refused
        that transport even with the main connection open and the app-wide switch
        on. Only a transport its manifest asked for is switchable - anything else
        answers False, because the addon never declared it and would not use it
        either way.
        """
        name = str(transport).strip().lower()
        if name not in ("ssh", "sftp"):
            return False
        manager = self._api._manager
        addon = manager.find(str(addon_id))
        if addon is None or name not in (addon.requires or ()):
            self._api.log(f"{addon_id} has no {name.upper()} switch to move - "
                          f"its manifest does not ask for that transport")
            return False
        manager.set_transport_enabled(str(addon_id), name, bool(on))
        return True

    def set_addon_own_connection(self, addon_id: str, on: bool) -> bool:
        """Put one addon on a connection of its own, or back on the app's session.

        Offered only for an addon whose manifest said ``"connection": "both"`` -
        every other kind has already decided, and the app would refuse the switch.
        On, the addon opens its own connection per call and works whether the main
        one is up or not; off, it shares the app's session and waits for Connect.
        """
        manager = self._api._manager
        addon = manager.find(str(addon_id))
        if addon is None:
            self._api.log(f"{addon_id} is not installed, so it has no "
                          f"connection of its own to be given")
            return False
        if addon.is_bundled or addon.connection != "both":
            self._api.log(f"{addon_id} has no own-connection switch: it declared "
                          f"\"connection\": \"{addon.connection}\""
                          + (", and came inside the program" if addon.is_bundled
                             else ""))
            return False
        manager.set_own_connection(str(addon_id), bool(on))
        return True

    def addon_config(self, addon_id: str) -> dict:
        """One addon's own settings file, as the form it declared and the values it holds.

        Everything the store's Addon manager needs to edit another addon is in the
        one answer: ``fields`` (the schema the addon passed to ``api.config.declare``,
        or the copy kept in ``addons/_data/<id>/fields.json`` while it is switched
        off), ``values`` (the file over those defaults, which is what the addon
        would read now), ``defaults``, ``title``, the file's ``path`` and whether it
        ``exists`` yet. An addon that declared nothing and has no file answers with
        empty ``fields``, so a page can say there is nothing to set rather than
        showing a blank box.
        """
        manager = self._api._manager
        name = str(addon_id)
        title, fields, defaults = manager.config_layout(name)
        path = addon_config_path(name)
        return jsonable({"id": name, "title": title, "path": str(path),
                         "exists": path.is_file(), "fields": fields,
                         "defaults": defaults,
                         "values": manager.config_values(name)})

    def set_addon_config(self, addon_id: str, values: dict) -> dict:
        """Write keys into another addon's ``config.json``, and tell it they changed.

        The file is read first and only the named keys put over it, so a key the
        form never showed survives the save; dotted keys write inside their
        section, the way ``api.config`` reads them. The addon is then sent the same
        ``on_config`` call the app's own Settings page sends, so a change lands
        while it runs rather than at the next start. Comes back with the whole
        merged view - empty when the file could not be written.
        """
        manager = self._api._manager
        name = str(addon_id)
        stored = read_config_json(addon_config_path(name))
        for key, value in (values or {}).items():
            put_config_value(stored, str(key), value)
        if not manager.save_config(name, stored, "addon manager"):
            return {}
        return jsonable(manager.config_values(name))

    def app_setting(self, key: str) -> Any:
        """One application setting by name, as the profile holds it.

        Reading is unrestricted, and this is how an addon asks after a switch it
        cannot see in :meth:`settings` under a name it does not know; a key the
        profile never set answers ``None``. The per-addon stores are not reached
        here - that is ``api.settings``.
        """
        return jsonable(self._win.app_config.get(str(key)))

    def set_app_setting(self, key: str, value: Any) -> bool:
        """Write one application setting, for the handful the app lets an addon move.

        Only :attr:`_WRITABLE_SETTINGS` may be written - today
        ``addon_dev_enabled``, which the Addon manager's **Show the Addon Dev tab**
        check reads - and writing it takes effect at once rather than at the next
        start. Anything else is somebody's connection, password or layout, and is
        refused with ``False`` and a line in the log.
        """
        name = str(key)
        if name not in self._WRITABLE_SETTINGS:
            self._api.log(f"app setting {name!r} is not one an addon may write")
            return False
        setter = getattr(self._win, f"set_{name}", None)
        if callable(setter):
            setter(value)
            return True
        self._win.app_config[name] = value
        self._win.save_app_config()
        return True

    # --- the git token the profile holds ---

    def git_token(self) -> str:
        """The profile's global git token, for this addon, or ``""`` for anonymous.

        One token is kept in Settings > Connection and an addon only ever sees it
        when its own **use the global git token** switch is up: the default answer
        is empty, so an addon that asks without asking gets the public rate limit
        and nothing else. The token buys two things and no third - a private
        repository can be read, and the hourly limit is the authenticated one.
        """
        addon = getattr(self._api, "_addon", None)
        if addon is None or not bool(getattr(addon, "git_token", False)):
            return ""
        return str(self._win.app_config.get("git_token") or "").strip()

    def github_headers(self) -> dict:
        """The two headers a GitHub call wants: empty for nobody, or the token in the
        shape GitHub reads with the API version pinned. An addon's own
        ``http.client`` code has to send these itself, and this call is the one
        thing it needs; an addon with no token allowed gets ``{}``.
        """
        token = self.git_token()
        if not token:
            return {}
        return {"Authorization": f"token {token}",
                "X-GitHub-Api-Version": "2022-11-28"}


class Connection:
    """A live SSH/SFTP pair, handed out by ``api.device.open()``."""

    def __init__(self, client: Any, sftp: Any, remote_root: str,
                 gate: Callable[[str], None] | None = None) -> None:
        self.client = client
        self.sftp = sftp
        self.remote_root = remote_root
        self._gate = gate

    def run(self, command: str, timeout: int = 30) -> dict:
        """One command over this connection, in the same ``{"code", "out", "err"}``
        shape as :meth:`DeviceAPI.run`."""
        if self._gate is not None:
            self._gate("ssh")
        return exec_remote(self.client, command, timeout)

    def remote(self, path: str = "", outside_root: bool = False) -> str:
        """A path as the device sees it: joined to this connection's remote root
        unless ``outside_root``."""
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

    def _gui(self, job: Callable[[], Any]) -> Any:
        """Read what only the interface can read, from whichever thread this was called on.

        Most of what this class asks the window for is a Python value the window
        keeps - a switch in ``app_config``, a connection object, a flag saying
        whether a session is up - and reading those from an addon's thread is
        safe. What is not safe is a value that lives inside a widget: the typed
        host, the ticked box, the port on the spinner. Those are asked for here,
        and the asking is the only hop a device call needs.
        """
        if on_gui_thread():
            return job()
        bus = getattr(self._api._manager, "bus", None)
        if bus is None:
            return job()        # a harness with no bus has no other thread either
        return bus.ask(job, timeout=GUI_ASK_TIMEOUT)

    def _settings(self, override: dict | None = None) -> dict:
        """Connection details: profile, bound device and override, in order."""
        base = self._gui(lambda: self._win.current_settings(silent=True))
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

    def _guard(self, transport: str, uses_main: bool | None = None) -> None:
        """Refuse a transport unless every gate allows it: the master switch,
        that transport's app-wide switch, this addon's own switch (the addon as
        a whole, and that transport on its row) and the addon's
        ``requires`` - and, when the call would ride the app's session, unless
        that session is actually open.

        ``uses_main`` says whose connection the call reaches; left out, an
        addon on a connection of its own is judged on its own and only one
        riding the app's session has to wait for the user to press Connect.
        """
        win = self._win
        check = getattr(win, "connections_enabled", None)
        if callable(check) and not check():
            raise AddonError(
                "connections are switched off (Settings > Connection)")
        config = getattr(win, "app_config", {}) or {}
        if not bool(config.get(f"{transport}_enabled", True)):
            raise AddonError(
                f"{transport.upper()} is switched off (Settings > Connection)")
        addon = self._addon()
        if addon is not None and not bool(getattr(addon, "enabled", True)):
            raise AddonError(
                "this addon is switched off: turn it on on its row in the "
                "Addon manager before it can use a connection")
        if addon is not None \
                and not bool(getattr(addon, f"{transport}_enabled", True)):
            raise AddonError(
                f"{transport.upper()} is switched off for this addon: its own "
                f"{transport.upper()} switch, on its row in the Addon "
                "manager, has to be on as well as the app-wide one")
        declared = tuple(getattr(addon, "requires", ()) or ()) if addon is not None else ()
        if transport not in declared:
            wanted = sorted(set(declared) | {transport})
            raise AddonError(
                f"this addon may not use {transport.upper()}: it has to declare "
                f"its connection services, e.g. \"requires\": {wanted} "
                "in its ADDON manifest")
        if uses_main is None:
            uses_main = not self._uses_own()
        if uses_main and not self._main_open():
            raise AddonError(
                "the main connection is not open: press Connect in the "
                "Connection panel, or call device.ensure_connected() to ask "
                "for it" + (" - or switch this addon onto a connection of its "
                            "own on its tab in the Addon manager" if self._may_have_own()
                            else ""))

    def _may_have_own(self) -> bool:
        """Whether the tab's switch could put this addon on its own connection."""
        addon = self._addon()
        if addon is None or getattr(addon, "is_bundled", False):
            return False
        return str(getattr(addon, "connection", "app")) == "both"

    def _uses_own(self) -> bool:
        """Whether this addon runs on a connection of its own right now."""
        addon = self._addon()
        return bool(getattr(addon, "uses_own", False)) if addon is not None else False

    def _allow_own(self) -> None:
        """Refuse any connection that is not the app's session unless the
        manifest declared one; shipped addons carry their own permission."""
        addon = self._addon()
        if addon is None or getattr(addon, "is_bundled", False):
            return
        if str(getattr(addon, "connection", "app")) not in ("own", "both"):
            raise AddonError(
                'this addon may not open a connection of its own: it has to '
                'declare its connection source, e.g. "connection": "own" '
                '(or "both") in its ADDON manifest')

    def _need_app(self, reason: str) -> None:
        """Refuse work that reaches the app's own session for an addon that
        declared only a connection of its own."""
        if self._uses_own():
            raise AddonError(
                f"{reason} reaches the app's main session, but this addon "
                'declared "connection": "own" in its ADDON manifest')

    def _main(self) -> Any:
        """The one main connection, whoever is allowed to use it."""
        return getattr(self._win, "shared_conn", None)

    def _main_open(self) -> bool:
        """Whether the app's main connection - SSH and SFTP together - is up."""
        shared = self._main()
        if shared is not None:
            try:
                return bool(shared.active())
            except Exception:
                pass
        return bool(getattr(self._win, "main_connected", False))

    def _live_main(self) -> Any:
        """The open main session for an addon that shares it, ``None`` otherwise.

        An addon never opens the main connection from here: that connection is
        the user's to open, by button or by the startup switch. Asking for it is
        what ``ensure_connected`` does.
        """
        if self._uses_own() or not self._main_open():
            return None
        return self._main()

    def _mark(self, transport: str) -> None:
        """Note the transport for the dot on the addon's tab; never fails the call."""
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
        self._guard("ssh", uses_main=not bool(device))
        if device:
            # A one-off device is a connection of its own: declared only.
            self._allow_own()
            shared = None
        else:
            shared = self._live_main()
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
        self._guard("ssh", uses_main=not bool(device))
        if device:
            self._allow_own()
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout, device)
        if not block:
            return future
        return _await(future, _timeout(timeout))

    def run_async(self, command: str, on_done: Callable[[dict], None] | None = None,
                  timeout: Any = 30, device: dict | None = None) -> Any:
        """``run`` without waiting: ``on_done(result)`` arrives on the addon's own thread."""
        self._check_device(device)
        self._guard("ssh", uses_main=not bool(device))
        if device:
            self._allow_own()
        self._mark("ssh")
        future = POOL.submit(self._exec, command, timeout, device)
        if on_done is not None:
            answer = _there(self._api, on_done)

            def deliver(done: Any) -> None:
                try:
                    result = done.result()
                except Exception as error:
                    result = {"code": -1, "out": "", "err": str(error)}
                answer(result)
            future.add_done_callback(deliver)
        return future

    def shell_send(self, line: str) -> bool:
        """Type into the terminal tab. False when its channel is not open.

        The tab is a channel of the main connection, so this needs that
        connection up and the addon riding it, never one of its own.
        """
        self._guard("ssh")
        self._need_app("device.shell_send()")
        self._mark("ssh")
        worker = self._win.shell_worker
        if worker is None or not worker.isRunning():
            return False
        worker.send_line(str(line))
        self._trace(f"shell> {line}")
        return True

    def is_connected(self, protocol: str | None = None) -> bool:
        """Whether this addon could use a connection right now.

        Its own switch, that transport's switch on its tab, the master switch
        and the transport's app-wide switch all count, and an addon sharing the
        app's session can only use what is open: the one main connection. An
        addon on a connection of its own brings that connection up itself, so
        the gates passing is all the app can report.
        """
        win = self._win
        check = getattr(win, "connections_enabled", None)
        if callable(check) and not check():
            return False
        addon = self._addon()
        if addon is not None and not bool(getattr(addon, "enabled", True)):
            return False
        config = getattr(win, "app_config", {}) or {}
        declared = (tuple(getattr(addon, "requires", ()) or ())
                    if addon is not None else None)
        live = self._main_open()
        own = self._uses_own()
        states = {}
        for kind in ("sftp", "ssh"):
            allowed = (bool(config.get(f"{kind}_enabled", True))
                       and bool(getattr(addon, f"{kind}_enabled", True))
                       and (declared is None or kind in declared))
            states[kind] = allowed and (True if own else live)
        if protocol is None:
            return any(states.values())
        return states.get(str(protocol).lower(), False)

    def mode(self) -> str:
        """``"main"`` shares the app's session, ``"own"`` connects per call."""
        return "own" if self._uses_own() else "main"

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
        if str(getattr(addon, "connection", "app")) != "both":
            declared = str(getattr(addon, "connection", "app"))
            raise AddonError('only an addon that declared "connection": '
                             '"both" may switch between the two; this one '
                             f'declared "{declared}"')
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
        self._allow_own()
        if not self._uses_own():
            raise AddonError("bind only applies to an addon using its own "
                             "connection: declare \"connection\": \"both\" "
                             "and switch on its own connection in the store's "
                             "Addon manager, or call "
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
        """Ask the app to open its main connection; say whether it was already up.

        There is one connection and it carries both transports, so either
        protocol asks for the same thing. The connection opens through the
        window, on the GUI thread, exactly as the Connect button does - an addon
        only asks and never connects itself. Asking is the one call allowed while
        the main connection is down, so it skips that part of the gates.
        """
        kind = str(protocol).lower()
        if kind not in ("sftp", "ssh"):
            raise AddonError("protocol must be 'sftp' or 'ssh'")
        self._guard(kind, uses_main=False)
        self._need_app("device.ensure_connected()")
        already = self._main_open()
        if not already:
            self._api._manager.bus.post(self._win.connect_remote)
        return already

    # --- files ---

    def _sftp(self, operation: str, action: Callable, timeout: Any = 30) -> Any:
        """Run `action(client, sftp)` in the pool, on the main connection or a fresh one."""
        self._guard("sftp")
        self._mark("sftp")
        settings = self._settings()

        def work() -> Any:
            self._guard("sftp")      # the gates may have moved since submitting
            self._trace(f"sftp {operation}")
            shared = self._live_main()
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
        self._guard("sftp", uses_main=False)
        self._allow_own()
        self._mark("sftp")
        settings = self._settings(device)

        def gate(transport: str) -> None:
            # Whatever this handle reaches is the addon's own connection, never
            # the app's, so its commands are gated on the switches alone.
            self._guard(transport, uses_main=False)

        client = None
        sftp = None
        try:
            client = _await(POOL.submit(create_ssh_client, settings), _timeout(timeout))
            sftp = client.open_sftp()
            yield Connection(client, sftp, settings["remote_root"], gate=gate)
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
        """Whether something on the device answers to this path."""
        def work(client: Any, sftp: Any, root: str) -> Any:
            try:
                sftp.stat(resolve_remote(root, path, outside_root))
            except OSError:
                return False
            return True

        return self._sftp("stat", work)

    def stat(self, path: str, outside_root: bool = False) -> dict | None:
        """``{size, mtime, mode, directory}`` for a device path, ``None`` when it
        is not there."""
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
        """A whole file off the device, as bytes."""
        def work(client: Any, sftp: Any, root: str) -> Any:
            with sftp.open(resolve_remote(root, path, outside_root), "rb") as handle:
                return handle.read()

        return self._sftp("read", work)

    def read_text(self, path: str, encoding: str = "utf-8",
                  outside_root: bool = False) -> str:
        """A device file as text; bytes the encoding cannot read are replaced
        rather than raised on."""
        return self.read_bytes(path, outside_root).decode(encoding, errors="replace")

    def write_bytes(self, path: str, payload: bytes, mkdir: bool = True,
                    outside_root: bool = False) -> int:
        """Replace a file on the device with these bytes, making the folders above
        it when ``mkdir``; returns how many were written."""
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
        """``write_bytes`` with the text encoded first."""
        return self.write_bytes(path, str(text).encode(encoding), mkdir, outside_root)

    def append_text(self, path: str, text: Any, outside_root: bool = False) -> int:
        """Add one line to the end of a device file, making the file when it is
        not there; returns the bytes it now holds."""
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
        """A folder on the device with every parent above it; returns the whole
        remote path."""
        def work(client: Any, sftp: Any, root: str) -> Any:
            target = resolve_remote(root, path, outside_root=True)
            _make_dirs(sftp, target)
            return target

        return self._sftp("mkdir", work)

    def remove(self, path: str, recursive: bool = False,
               outside_root: bool = False) -> bool:
        """Take a file off the device, or a folder whole when ``recursive``; the
        remote root itself is never touched."""
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
        """Move or rename something on the device; returns where it is now."""
        def work(client: Any, sftp: Any, root: str) -> Any:
            source = resolve_remote(root, path, outside_root)
            target = resolve_remote(root, new_path, outside_root)
            sftp.rename(source, target)
            return target

        return self._sftp("rename", work)

    def upload(self, local: Any, remote_path: str, outside_root: bool = False) -> str:
        """Send a local file to the device, into ``remote_path`` or the remote
        root when it is empty; returns the path it was written to."""
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
        """Copy a device file locally, under ``local`` or its own name; returns
        the file that was made."""
        target = Path(str(local or Path(str(remote_path)).name)).expanduser()

        def work(client: Any, sftp: Any, root: str) -> Any:
            source = resolve_remote(root, remote_path, outside_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(source, str(target))
            return str(target)

        return self._sftp("download", work)


def _make_dirs(sftp: Any, path: str) -> None:
    """mkdir -p on the device, ignoring the root itself."""
    from core.net import make_remote_directories

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
    """Dialogs, the menu bar, the status line and the clipboard."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _win(self) -> QWidget:
        return self._api.win

    def information(self, title: str, text: str) -> None:
        """An information box, with this addon's name on its title."""
        QMessageBox.information(self._win, self._titled(title), str(text))

    def warning(self, title: str, text: str) -> None:
        """A warning box, with this addon's name on its title."""
        QMessageBox.warning(self._win, self._titled(title), str(text))

    def error(self, title: str, text: str) -> None:
        """A critical error box; worth an :meth:`AddonAPI.error` in the log too."""
        QMessageBox.critical(self._win, self._titled(title), str(text))

    def question(self, title: str, text: str, default: bool = False) -> bool:
        """Yes or no, with ``default`` saying which button answers to Enter."""
        button = QMessageBox.StandardButton.Yes if default else QMessageBox.StandardButton.No
        answer = QMessageBox.question(
            self._win, self._titled(title), str(text),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, button,
        )
        return answer == QMessageBox.StandardButton.Yes

    def input(self, title: str, label: str, default: str = "", secret: bool = False) -> str | None:
        """One line of text, ``None`` when the box was cancelled; ``secret`` hides
        what is typed."""
        from PyQt6.QtWidgets import QInputDialog

        mode = QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal
        text, accepted = QInputDialog.getText(self._win, self._titled(title), str(label), mode, str(default))
        return text if accepted else None

    def copy(self, text: str) -> bool:
        """Put ``text`` on the system clipboard; ``False`` when there is no clipboard here.

        The one way an addon can hand something to the rest of the machine
        without writing a file: a path, a command, a generated password. What
        lands there replaces whatever was on it, which is why an addon that
        copies a secret is given :meth:`clipboard_clear` to take it back with,
        and why that call may be told what it put there first.
        """
        board = self._clipboard()
        if board is None:
            return False
        try:
            board.setText("" if text is None else str(text))
            return True
        except Exception:                        # a clipboard that refuses is said so
            return False

    def clipboard(self) -> str:
        """What the clipboard holds as text; ``""`` when it holds nothing readable."""
        board = self._clipboard()
        if board is None:
            return ""
        try:
            return str(board.text() or "")
        except Exception:
            return ""

    def clipboard_clear(self, only_if: str = "") -> bool:
        """Take the text off the clipboard again, and say whether it went.

        ``only_if`` is the guard an addon that copies a secret needs: with it the
        clipboard is emptied only while it still holds exactly those characters,
        so a timer set to tidy a password away cannot wipe the paragraph
        somebody copied afterwards. Without it, whatever is there is cleared.
        """
        board = self._clipboard()
        if board is None:
            return False
        try:
            if only_if and str(board.text() or "") != str(only_if):
                return False
            board.clear()
            return True
        except Exception:
            return False

    def _clipboard(self) -> Any:
        """The system clipboard, or ``None`` when this process has no GUI to own one."""
        from PyQt6.QtWidgets import QApplication

        try:
            return QApplication.clipboard()
        except Exception:                        # no QApplication: no clipboard
            return None

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
            from core.ui import ColorWheelDialog

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

    def pick_file(self, title: str = "Choose a file", kind: str = "open",
                  filter: str = "", initial: str = "") -> str:
        """A file box: ``kind`` is ``open``, ``save`` or ``folder``, and the answer
        is the path picked or ``""`` when nothing was.

        ``filter`` is Qt's own shape - ``"Keys (*.pem *.key);;All files (*)"`` -
        and empty lets anything through. ``initial`` is where the box starts: a
        folder, or a file already named, so an addon can hand back whatever the
        user had chosen last. A file chosen here is on this machine; an addon
        that means to put it on the device says so itself - nothing is uploaded
        behind its back.
        """
        from PyQt6.QtWidgets import QFileDialog

        mode = str(kind or "open").strip().lower()
        start = str(initial or "")
        if mode == "folder":
            return QFileDialog.getExistingDirectory(self._win, self._titled(title),
                                                   start) or ""
        if mode in ("save", "write"):
            picked, _ = QFileDialog.getSaveFileName(self._win, self._titled(title),
                                                    start, str(filter or ""))
            return picked or ""
        picked, _ = QFileDialog.getOpenFileName(self._win, self._titled(title),
                                                start, str(filter or ""))
        return picked or ""

    def popup(self, title: str, text: str) -> None:
        """A notice for the user, named for the addon raising it: a toast from the
        app's tray icon when it has one and notifications are allowed, and the
        status line either way.

        Use it for something worth interrupting a person for - an update found,
        a job that finished, a device that came back - not for progress, which
        is what ``notify`` and the tab's own status label are for.
        """
        head = self._titled(title)
        say = getattr(self._win, "notify_popup", None)
        if callable(say):
            try:
                say(head, str(text or ""))
                return
            except Exception:                # an app that cannot toast still hears
                pass
        self._win.statusBar().showMessage(f"{head}: {text}", 6000)

    def notify(self, text: str, ms: int = 5000) -> None:
        """A message in the status line that goes away by itself after ``ms``."""
        self._win.statusBar().showMessage(str(text), max(500, int(ms)))

    def browse(self, url: str) -> bool:
        """Hand an address to the machine's own browser, and say whether it went.

        The way out of an embedded page this app's web view cannot show: a
        router with a self-signed certificate, or a page that wants something a
        browser has already been agreed to. Nothing is fetched here, so the
        answer is only ``False`` when this machine has no browser to open.
        """
        from PyQt6.QtGui import QDesktopServices

        try:
            return bool(QDesktopServices.openUrl(QUrl(str(url or ""))))
        except Exception:                        # a headless box has no browser
            return False

    def window(self) -> QWidget:
        """The main window. Use sparingly - it is the app itself."""
        return self._win

    def menu_item(self, text: str, callback: Callable[[], None],
                  menu: str = "Tools",
                  shortcut: str | None = None) -> QAction:
        """Add an entry to the menu bar for as long as the addon stays enabled.

        The press is heard on the interface and the callback is then run on the
        addon's own thread, like every other thing that addon's code does.
        """
        target = self._find_menu(menu)
        action = target.addAction(str(text))
        answer = _there(self._api, callback)
        action.triggered.connect(lambda _checked=False: answer(None))
        if shortcut:
            from PyQt6.QtGui import QKeySequence

            action.setShortcut(QKeySequence(str(shortcut)))
        self._api._addon.actions.append((target, action))
        return action

    def remove_menu_item(self, action: QAction) -> None:
        """Take away an entry added with :meth:`menu_item`, given the QAction it
        handed back."""
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

    def dialog(self, spec: dict, title: str = "Input", ok: bool = True,
               answers: list | None = None) -> dict | None:
        """A modal dialog built from a widget spec; returns its values by id.

        ``answers`` replaces the Ok button with the named ones, for a question
        that has more than two honest answers - go back and keep what is
        written, go back and start clean, or leave it alone. Whichever button
        was pressed comes back under the ``answer`` key beside the field
        values; closing the box any other way is ``None``, so a person who
        never touched it is told apart from one who picked something.
        """
        from PyQt6.QtWidgets import QDialogButtonBox

        box = QDialog(self._win)
        box.setWindowTitle(self._titled(title))
        layout = QVBoxLayout(box)
        body, registry = build_spec(spec if isinstance(spec, dict) else {"kind": "column", "children": spec})
        layout.addWidget(body, 1)
        chosen: list[str] = []
        buttons = QDialogButtonBox(box)
        for text in (answers or []):
            button = buttons.addButton(str(text),
                                       QDialogButtonBox.ButtonRole.AcceptRole)
            button.clicked.connect(lambda _=False, name=str(text): chosen.append(name))
        if not answers:
            buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(box.accept)
        buttons.rejected.connect(box.reject)
        layout.addWidget(buttons)
        if not box.exec():
            return None
        values = {}
        for identifier, widget in registry.items():
            values[identifier] = read_widget_value(widget)
        if chosen:
            values["answer"] = chosen[0]
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
    """A tab's mark out of an SVG document (as text), a picture file (absolute, or beside the addon's folder), or a base64 data URI; anything unreadable returns None."""
    text = str("" if source is None else source).strip()
    if not text:
        return None
    if not text.startswith(("<svg", "<?xml", "data:")):
        # A file, named absolutely or beside the addon's folder. An SVG document
        # and a base64 data URI are already the picture, so they go straight on.
        path = Path(text)
        if not path.is_absolute() and home is not None:
            path = Path(home) / text
        text = str(path)
    image = load_picture(text)
    if image is None:
        return None
    image = image.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return None if image.isNull() else QIcon(image)


def _icon_data_uri(icon: Any) -> str:
    """A QIcon as a PNG data URI, or nothing when it holds no picture.

    A favicon is drawn small and sent as text, so the same event reaches an addon
    imported into the app and one running in a process of its own; ``build_icon``
    reads the URI straight back when the addon hands it to a button.
    """
    import base64
    from PyQt6.QtCore import QBuffer, QIODevice

    try:
        pixmap = icon.pixmap(32, 32)
    except (AttributeError, RuntimeError, TypeError):
        return ""
    if pixmap is None or pixmap.isNull():
        return ""
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return ""
    pixmap.save(buffer, "PNG")
    buffer.close()
    return "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode("ascii")


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
        """Where to put a new tab: beside the tabs this addon already owns,
        unless the addon says otherwise."""
        if position is None:
            return self._child_position()
        return max(0, min(int(position), self._win.tabs.count()))

    def _child_position(self) -> int:
        """The spot for a tab this addon is opening now.

        A tab an addon opens on top of its own work - a report, a page, a helper -
        belongs next to the tab that spawned it, not at the far end of the bar
        where the user has to go looking for it. So: the place immediately left
        of this addon's own leftmost tab. An addon whose tab is pinned beside
        Settings has no movable tab to sit beside, and an addon with no tab yet
        has none either; both fall back to the end of the reorderable row.
        """
        bar = self._win.tabs.tabBar()
        pinned = getattr(bar, "is_pinned", None)
        indices = sorted(self._win.tabs.indexOf(entry.get("page"))
                         for entry in self._store.values())
        for index in indices:
            if index < 0:
                continue
            if callable(pinned) and pinned(index):
                continue
            return index
        return self._before_settings()

    def names(self) -> list[str]:
        """The tabs this addon has open, under the names it gave them."""
        return list(self._store)

    def _settle(self, name: str) -> bool:
        """Hand a tab just built to the addon's own window, when it keeps them there.

        The move is the app's decision rather than the addon's: a record with the
        force-popout switch up, or a manifest that says ``"always"``, puts every
        page this addon builds in a window instead of in the bar, and the addon is
        none the wiser - its widget, its registry and its name are untouched.
        """
        settle = getattr(self._api._manager, "settle_tab", None)
        if not callable(settle):
            return False
        try:
            return bool(settle(self._api.id, str(name)))
        except Exception as error:            # a tab in the bar beats no tab at all
            self._api.log(f"could not open '{name}' in its own window: {error}")
            return False

    def _recall(self, name: str) -> None:
        """Take a page out of the addon's window before it is deleted, so no window
        is left holding a widget that has been destroyed under it."""
        recall = getattr(self._api._manager, "recall_tab", None)
        if not callable(recall):
            return
        try:
            recall(self._api.id, str(name))
        except Exception:
            pass

    def exists(self, name: str) -> bool:
        """Whether this addon holds a tab under that name."""
        return str(name) in self._store

    def add_web_tab(self, name: str, url: str = "", ip: str = "", port: int = 0,
                    path: str = "", scheme: str = "", position: int | None = None,
                    pinned: bool = False, icon_only: bool = False,
                    menu: list | None = None) -> str:
        """An embedded page belonging to no profile: not stored, not rebuilt; give either a full url or ip/port/path/scheme. The tab wears an X, and closing it by hand simply takes it away.

        ``menu`` is a list of ``{"id": ..., "label": ...}`` entries: the page then
        wears a small button over the corner of the view that pops them up, and
        picking one reports ``on_widget(name, id, "clicked", {})`` back to the
        addon, exactly as a button on a spec-built tab does.
        """
        name = str(name).strip()
        if not name:
            raise AddonError("a tab needs a name")
        if name in self._store:
            self.close(name)
        tab = dict(_tab_definition(url, ip, port, path, scheme))
        tab["name"] = name
        entries = [{"id": str(item.get("id") or ""),
                    "label": str(item.get("label") or item.get("id") or "")}
                   for item in (menu or [])
                   if isinstance(item, dict) and str(item.get("id") or "")]
        pick = None
        if entries:
            tab["menu"] = entries

            def pick(menu_id: str, tab_name: str = name) -> None:
                self._api._manager.push_widget_event(self._api.id, tab_name,
                                                     str(menu_id), "clicked", {})
        entry = self._win.add_transient_web_tab(
            tab, self._index(position),
            on_closed=lambda: self._forget_closed(entry["page"]),
            on_menu=pick)
        self._store[name] = {
            "page": entry["page"], "url_input": entry["url_input"],
            "view": entry["view"], "tab": tab, "web": True, "registry": {},
        }
        if self._settle(name):
            self._api.log(f"opened web tab '{name}' in its own window")
            return name
        if pinned or icon_only:
            self.set_pinned(name, pinned, icon_only=icon_only)
        self._api.log(f"opened web tab '{name}' -> {entry['url_input'].text()}")
        return name

    def _forget_closed(self, page: QWidget) -> None:
        """The user closed one of this addon's pages with the tab's X: drop it
        from the registry, so the addon's own list matches the window."""
        for name in list(self._store):
            if self._store[name].get("page") is page:
                self._store.pop(name)

    def add_tab(self, name: str, widget: QWidget | None = None, spec: dict | None = None,
                style: str = "", position: int | None = None,
                pinned: bool = False, icon_only: bool = False) -> str:
        """A custom tab: any QWidget, or one built from a spec dict; the new tab does not steal the view, use activate to show it.

        ``pinned`` holds the tab in the block at the right edge of the bar, beside
        Settings, out of the row of tabs the user drags about; ``icon_only``
        shows it as a mark with no caption (it still answers to its name, and the
        name is its tooltip). Either one asked for on a web tab works there too.
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
            style_sheet(widget, style)
        self._win.tabs.insertTab(self._index(position), widget, name)
        self._store[name] = {
            "page": widget, "url_input": None, "view": widget if _is_web_view(widget) else None,
            "tab": {"name": name}, "web": False, "registry": registry,
        }
        if self._settle(name):
            self._refresh_tab_dots()
            self._api.log(f"opened tab '{name}' in its own window")
            return name
        if pinned or icon_only:
            self.set_pinned(name, pinned, icon_only=icon_only)
        self._refresh_tab_dots()
        self._api.log(f"opened tab '{name}'")
        return name

    def set_pinned(self, name: str, pinned: bool = True,
                   icon_only: bool | None = None) -> bool:
        """Hold one of this addon's tabs beside Settings, or let it back into the row.

        A pinned tab is window furniture: the user cannot drag it out of the run
        at the right edge, it keeps its place there while other tabs come and go,
        and its own order along the edge is remembered apart from the movable tabs
        - which may be slid about within the run, but never leave it.
        ``icon_only`` draws it as a mark with
        no caption - the name still identifies it, and the tooltip shows it - and
        only a pinned tab may keep a blank caption, because the bar puts a
        movable tab's name back the moment it re-measures.
        """
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        page = entry["page"]
        show = bool(icon_only) if icon_only is not None else bool(entry.get("icon_only"))
        if not pinned:
            unpin = getattr(self._win, "unpin_tab", None)
            if callable(unpin):
                unpin(page)
            entry.pop("pinned", None)
            entry.pop("icon_only", None)
            self._refit()
            self._refresh_tab_dots()
            return True
        if not callable(getattr(self._win, "pin_tab", None)):
            return False
        mark = self._icon_mark(str(name))
        placed = self._win.pin_tab(page, str(name), icon_only=show)
        if placed:
            entry["pinned"] = True
            entry["icon_only"] = show
            if mark is not None:
                # Pinning blanks the caption and re-measures the row; the mark the
                # addon chose has to be put back on the tab in its new place.
                index = self._win.tabs.indexOf(page)
                if index >= 0:
                    self._win.tabs.setTabIcon(index, mark)
        self._refit()
        self._refresh_tab_dots()
        return bool(placed)

    def _icon_mark(self, name: str) -> Any:
        """The picture this tab was given, if it still has one."""
        entry = self._store.get(str(name)) or {}
        wanted = entry.get("icon_mark")
        return build_icon(wanted, self._api._addon.home) if wanted else None

    def _refit(self) -> None:
        """Let the bar re-measure the row after a tab joined or left the pinned block."""
        bar = self._win.tabs.tabBar() if hasattr(self._win.tabs, "tabBar") else None
        if bar is not None and hasattr(bar, "refit"):
            bar.refit()

    def close(self, name: str) -> bool:
        """Close one of this addon's tabs; ``True`` when it had it."""
        entry = self._store.pop(str(name), None)
        if entry is None:
            return False
        self._recall(str(name))
        for control in (entry.get("registry") or {}).values():
            # A shell left running when its page goes out is a thread still
            # writing into a widget that no longer is.
            if _is_terminal(control):
                _terminal_stop(control)
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
        """Change what the tab's caption says; afterwards the tab is addressed by
        the new name."""
        entry = self._store.get(str(name))
        if entry is None:
            return False
        index = self._win.tabs.indexOf(entry["page"])
        if index < 0:
            return False
        self._win.tabs.setTabText(index, str(new_name))
        if entry.get("pinned"):
            # A pinned tab shows no caption, so the name lives in its data and
            # tooltip; setTabText alone would leave the old one on the tip.
            self._win.pin_tab(entry["page"], str(new_name),
                              icon_only=bool(entry.get("icon_only", True)))
        self._store[str(new_name)] = self._store.pop(str(name))
        return True

    def activate(self, name: str) -> bool:
        """Put this addon's tab on screen; ``True`` when it had it.

        A tab that has been popped out stands in a window rather than in the
        bar: activating it shows the page in that window and brings the window
        forward, so a jump to an addon's own page works however it is worn.
        """
        entry = self._store.get(str(name))
        if entry is None:
            return False
        page = entry["page"]
        bar = getattr(self._win, "tabs", None)
        if bar is not None and bar.indexOf(page) >= 0:
            bar.setCurrentWidget(page)
            return True
        window = self._api._manager.windows.get(str(self._api._addon.id))
        if window is not None and window.has_page(str(name)):
            try:
                window.tabs.setCurrentWidget(page)
            except (AttributeError, RuntimeError):
                pass
            window.present()
            return True
        return False

    def lock(self, locked: bool = True) -> bool:
        """Hold the window's tab bar on the tab it is showing, or let it go.

        A long job that fills its own tab with a progress pane is worth nothing
        if the person can click to another tab while it runs and leave the job
        with nowhere to be read: this holds the bar so no click, wheel or
        keyboard nudge moves a page until ``locked`` is off again. It is the
        window's whole bar, not one tab's, because the point is that the job's
        tab is the only thing on screen that is talking. A ``tabs`` container
        inside an addon's own page is held by the same idea through its
        ``locked`` property, so a store can shut its sub-bar too.

        The hold is the app's, not the addon's: stopping the addon lets go of it,
        so a job that dies halfway cannot leave the bar frozen. ``False`` comes
        back when the window has no bar to hold.
        """
        hold = getattr(self._win, "set_tabs_locked", None)
        if not callable(hold):
            return False
        try:
            return bool(hold(bool(locked)))
        except Exception:                    # a bar that will not hold is a bar
            return False

    def bar_visible(self, visible: bool) -> bool:
        """Stow the window's tab bar away, or bring it back.

        A job that fills its tab with a progress pane wants the whole window,
        and a bar across the top is a strip of it spent showing tabs there is
        no switching to while the bar is held anyway: this hides the bar, not
        the pages, and the same call with ``visible`` on puts it back. It is
        the look of the bar, not the order of the tabs - every tab keeps its
        place, and an app-side jump to another page still works while it is
        gone. A ``tabs`` container inside an addon's own page stows its bar
        through the same ``bar_visible`` property. ``False`` comes back when
        the window has no bar to stow.
        """
        show = getattr(self._win, "set_tabs_bar_visible", None)
        if not callable(show):
            return False
        try:
            return bool(show(bool(visible)))
        except Exception:                    # a bar that will not stow is a bar
            return False

    def url(self, name: str) -> str:
        """The address an embedded page is showing; empty for a custom tab."""
        entry = self._store.get(str(name))
        if entry is None or entry["url_input"] is None:
            return ""
        return entry["url_input"].text()

    def navigate(self, name: str, url: str) -> None:
        """Point an embedded page at another address; raises when the addon has no
        such tab."""
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        if entry["url_input"] is not None:
            entry["url_input"].setText(str(url))
        if entry["view"] is not None:
            entry["view"].setUrl(QUrl(str(url)))

    def reload(self, name: str) -> None:
        """Load an embedded page again, doing nothing when the tab is gone."""
        entry = self._store.get(str(name))
        if entry is None:
            return
        waiting = getattr(entry.get("page"), "_first_show_load", None)
        if waiting is not None and waiting.fire():
            # The tab has never been looked at, so "again" can only mean "for
            # the first time": what it wears in its address bar is loaded.
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
        """The tab itself, or with an id one control inside a tab built from a
        spec; ``None`` when the addon has no such tab."""
        entry = self._store.get(str(name))
        if entry is None:
            return None
        if identifier is None:
            return entry["page"]
        return entry["registry"].get(str(identifier))

    def set_widget(self, name: str, identifier: str, prop: str = "text",
                   value: Any = None) -> bool:
        """Write one property of one control by its spec id - ``text``, ``value``,
        ``rows``, ``items``, ``enabled``, ...; ``False`` when the id is not there."""
        widget = self.widget(name, identifier)
        if widget is None:
            return False
        prop = str(prop)
        if prop in TERMINAL_PRIVILEGED and _is_terminal(widget):
            # Opening a connection and typing down one are the app's business
            # as much as ``device.run`` is, so they pass the same gates.
            _authorize_terminal(self._api, prop, widget)
        apply_widget_property(widget, prop, value)
        return True

    def widget_value(self, name: str, identifier: str, what: str = "value") -> Any:
        """What one control holds now; raises when the tab has no such id."""
        widget = self.widget(name, identifier)
        if widget is None:
            raise AddonError(f"{name!r} has no widget with id {identifier!r}")
        return jsonable(read_widget_value(widget, what))

    def _resolve_view(self, name: str, identifier: str | None = None) -> Any:
        """The embedded page behind a tab, or one nested in its spec by id.

        A web tab's own view is stored whole, so a name finds it; an embedded
        ``web``/``webview`` widget inside a custom tab lives in that tab's
        registry and is reached by its spec id. Anything else is refused the
        way the old per-method checks did.
        """
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        if identifier is None:
            view = entry.get("view")
            if not _is_web_view(view):
                raise AddonError(f"{name!r} is not an embedded page")
            return view
        view = entry.get("registry", {}).get(str(identifier))
        if not _is_web_view(view):
            raise AddonError(f"{name!r} id {identifier!r} is not an embedded page")
        return view

    def set_html(self, name: str, html: str,
                 identifier: str | None = None) -> None:
        """Render markup in an embedded page without a server - a fully custom page.

        ``identifier`` names a ``web`` widget inside a custom tab; left out, the
        tab's own page is used.
        """
        view = self._resolve_view(name, identifier)
        page = str(html)
        if "<html" not in page.lower():
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{background:#121212;color:#e0e0e0;"
                "font-family:'Segoe UI',sans-serif;margin:18px}</style></head><body>"
                + page + "</body></html>"
            )
        self._api._manager.bus.post(lambda: view.setHtml(page))

    def inject_css(self, name: str, css: str,
                   identifier: str | None = None) -> None:
        """Style a page the device serves - the addon's own look on top.

        Works on a web tab and on a ``web`` widget embedded in a custom tab
        (named by ``identifier`).
        """
        view = self._resolve_view(name, identifier)
        script = (
            "var s=document.getElementById('addon-style');"
            "if(!s){s=document.createElement('style');s.id='addon-style';"
            "document.head.appendChild(s);}"
            "s.innerHTML=" + _js_string(str(css)) + ";"
        )
        self._api._manager.bus.post(lambda: view.page().runJavaScript(script))

    def inject_js(self, name: str, code: Any,
                  callback: Callable[[Any], None] | None = None,
                  identifier: str | None = None) -> None:
        """Run JavaScript in an embedded page; ``callback`` takes whatever the
        script returns, on the addon's own thread. A ``web`` widget inside a
        custom tab is reached by its ``identifier``.
        """
        view = self._resolve_view(name, identifier)
        answer = _there(self._api, callback) if callback else None
        self._api._manager.bus.post(
            lambda: view.page().runJavaScript(str(code), answer) if answer
            else view.page().runJavaScript(str(code)))

    def set_cookie(self, name: str, cookie: Any, url: str = "",
                   identifier: str | None = None) -> None:
        """Give an embedded page a cookie - a session token, a saved login.

        ``cookie`` is a ``name=value`` line (its ``; Domain=..; Path=..;``
        attributes are read too) or a dict of ``name, value, domain, path,
        secure, expires`` (``expires`` as seconds since the epoch). ``url`` is
        the page the cookie is meant for, so it is scoped to the right host when
        no domain is given. The embedded pages share one browser profile, so a
        cookie set here is seen by every web tab; set it before you navigate.
        """
        view = self._resolve_view(name, identifier)
        from PyQt6.QtCore import QDateTime, QUrl
        from PyQt6.QtNetwork import QNetworkCookie

        if isinstance(cookie, dict):
            qc = QNetworkCookie(str(cookie.get("name", "")).encode("utf-8"),
                                str(cookie.get("value", "")).encode("utf-8"))
            if cookie.get("domain"):
                qc.setDomain(str(cookie["domain"]))
            if cookie.get("path"):
                qc.setPath(str(cookie["path"]))
            qc.setSecure(bool(cookie.get("secure")))
            expires = cookie.get("expires")
            if expires:
                try:
                    qc.setExpiryDate(QDateTime.fromSecsSinceEpoch(int(expires)))
                except (TypeError, ValueError):
                    pass
        else:
            qc = QNetworkCookie.fromRawHeader(str(cookie).strip().encode("utf-8"))
        if qc is None or not qc.name():
            raise AddonError("that cookie could not be read - use 'name=value' "
                             "or a dict with name and value")
        store = view.page().profile().cookieStore()
        target = QUrl(str(url)) if url else QUrl()
        self._api._manager.bus.post(lambda: store.setCookie(qc, target))

    def clear_cookies(self, name: str, identifier: str | None = None) -> None:
        """Drop the cookies the embedded pages carry.

        The browser profile is shared, so this empties the store for every web
        tab, not just the one named; a tab is named only to reach that store.
        """
        view = self._resolve_view(name, identifier)
        store = view.page().profile().cookieStore()
        self._api._manager.bus.post(lambda: store.clearCookies())

    def read_cookies(self, name: str, identifier: str | None = None,
                     callback: Callable[[Any], None] | None = None) -> None:
        """Ask an embedded page what cookies its document can see (``document.cookie``).

        ``callback`` gets the ``k=v; k=v`` text on the addon's own thread.
        Cookies set through :meth:`set_cookie` that are marked HttpOnly are not
        visible to the page, so they are not reported here.
        """
        view = self._resolve_view(name, identifier)
        answer = _there(self._api, callback)

        def wrapped(value: Any) -> None:
            if callable(callback):
                answer(str(value or ""))

        self._api._manager.bus.post(
            lambda: view.page().runJavaScript("document.cookie", wrapped))

    def set_style(self, name: str, css: str) -> None:
        """A Qt stylesheet on a custom tab (for web tabs this themes the frame)."""
        entry = self._store.get(str(name))
        if entry is None:
            raise AddonError(f"no addon tab called {name!r}")
        style_sheet(entry["page"], css)

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


class WindowAPI:
    """The window this addon's tabs live in, when that is a window of its own.

    A tab normally stands in the main window's bar, inside a frame the addon has no
    say about. An addon whose tabs are **popped out** owns a top-level window
    instead: the page leaves the bar and is put in a window wearing this addon's
    name and mark, and it comes back to the place it left when the popout is
    switched off or the window's **Dock back** button is pressed. Nothing is
    rebuilt and the addon is not restarted, so a job running in the page keeps
    running through the trip.

    Every call names one of this addon's own tabs. A tab it does not own is refused
    by name. A tab still standing in the bar has no window to steer: :meth:`open`
    and :meth:`popout` make one, and the calls that ask after the frame say they
    found none. What the window does - opened, closed, moved, resized, docked,
    undocked - is told through ``api.events.on("window", callback)`` as
    ``{"addon", "tab", "event", ...}``.
    """

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _win(self) -> QWidget:
        return self._api.win

    @property
    def _addon(self) -> Any:
        return self._api._addon

    def _popout(self) -> Any:
        """The module that owns the windows; missing only in a bare addon process."""
        try:
            from core import popout
        except ImportError as error:            # an addon running with no app
            raise AddonError(f"there is no window support in this program: {error}")
        return popout

    def _entry(self, tab: str) -> dict:
        """This addon's record for one of its tabs, or a refusal naming it."""
        entry = (self._addon.tabs or {}).get(str(tab))
        if entry is None:
            raise AddonError(f"this addon has no tab called {tab!r}")
        return entry

    def _window(self, tab: str) -> Any:
        """The window holding this tab, or ``None`` while it stands in a bar."""
        name = str(tab)
        self._entry(name)
        window = self._api._manager.windows.get(str(self._addon.id))
        if window is not None and window.has_page(name):
            return window
        # A program launched as one addon's window has no bar to go back to: the
        # window it is standing in owns every page it holds.
        host = self._win
        if getattr(host, "is_main_window", True) is False:
            return host
        return None

    def _target(self, tab: str) -> Any:
        """The window this tab lives in; an addon that has no window is told so."""
        window = self._window(tab)
        if window is None:
            raise AddonError(
                f"tab {tab!r} stands in the main window's bar and has no window of "
                "its own; call api.window.popout(tab, True) to give it one")
        return window

    # --- the decision to have a window ---

    def popped(self, tab: str) -> bool:
        """Whether this tab is in a window of its own rather than in the bar."""
        try:
            return self._window(tab) is not None
        except AddonError:
            return False

    def popout(self, tab: str, on: bool = True) -> bool:
        """Undock one of this addon's tabs into a window of its own, or dock it back.

        ``on=True`` takes the page out of the main window's tab bar and puts it in a
        top-level window, making that window the first time; ``on=False`` puts it
        back in the slot in the bar it came from, at the same place in the order and
        pinned or not as it was. The addon's own switch, ``enabled`` in Settings and
        the store's **force popout** all move the same pages by the same code.
        Returns whether anything moved.
        """
        name = str(tab)
        self._entry(name)
        popout = self._popout()
        manager, addon = self._api._manager, self._addon
        if bool(on):
            return bool(popout.pop_out(manager, addon, name))
        return bool(popout.dock_back(manager, addon, name))

    # --- what the window is ---

    def state(self, tab: str) -> dict:
        """Everything worth knowing about this tab's window: whether it has one, and
        its title, size, place, visibility, the three flags, and whether a desktop
        shortcut for this addon exists. A tab still in the bar answers with
        ``popped`` False and the same keys empty, so a caller never has to guess.
        """
        name = str(tab)
        entry = self._entry(name)
        popout = self._popout()
        addon = self._addon
        linked = bool(popout.has_shortcut(addon))
        data: dict = {
            "addon": str(addon.id), "tab": name, "popped": False,
            "declared": str(getattr(addon, "popout", "") or ""),
            "switch": bool(getattr(addon, "popout_record", False)),
            "shortcut": linked,
            "shortcut_path": str(popout.shortcut_file(addon)) if linked else "",
        }
        window = self._window(name)
        if window is not None:
            data.update(window.state())
            data["popped"] = True
            return jsonable(data)
        bar = getattr(self._win, "tabs", None)
        index = bar.indexOf(entry["page"]) if bar is not None else -1
        data.update({
            "title": str(self._win.windowTitle()) if hasattr(self._win, "windowTitle") else "",
            "size": [], "position": [], "visible": bool(index >= 0),
            "shown": bool(index >= 0 and bar.currentIndex() == index),
            "resizable": True, "closable": True, "on_top": False,
            "pages": [name] if index >= 0 else [],
        })
        return jsonable(data)

    def names(self, tab: str) -> list[str]:
        """Every page this tab's window holds; empty for a tab that has no window."""
        window = self._window(tab)
        return list(window.names()) if window is not None else []

    # --- showing and going away ---

    def open(self, tab: str) -> bool:
        """Put this tab's window on screen and give it the keyboard, making the
        window by popping the tab out when it is still standing in the bar."""
        window = self._window(tab)
        if window is None:
            if not self.popout(tab, True):
                return False
            window = self._window(tab)
        if window is None:
            return False
        window.present()
        return True

    def close(self, tab: str) -> bool:
        """Take this tab's window out of sight, the way closing it with its own
        button does: the addon keeps running and the page keeps everything it held.
        To give the tab back to the main window use :meth:`popout` with ``False``.
        """
        window = self._window(tab)
        if window is None:
            return False
        window.close()
        return True

    def raise_window(self, tab: str) -> bool:
        """Bring the window above the others without moving it or waking the addon."""
        window = self._window(tab)
        if window is None:
            return False
        window.present()
        return True

    def show(self, tab: str, on: bool = True) -> bool:
        """Show or hide the window; hidden means out of sight, not shut down."""
        window = self._window(tab)
        if window is None:
            return False
        if bool(on):
            window.present()
        else:
            window.hide()
        return True

    def center(self, tab: str) -> bool:
        """Stand the window in the middle of the screen it is on."""
        window = self._window(tab)
        if window is None:
            return False
        window.center()
        return True

    # --- the frame the addon asks for ---

    def set_title(self, tab: str, text: str) -> bool:
        """What the window's title bar and its own header say."""
        self._target(tab).set_title(str(text))
        return True

    def set_size(self, tab: str, width: int, height: int) -> bool:
        """How big the window is, in pixels; the smaller of it the user may drag."""
        window = self._target(tab)
        window.set_sizes(int(width), int(height))
        return True

    def set_minimum(self, tab: str, width: int, height: int) -> bool:
        """The smallest the window may be shrunk to; kept when it is locked shut."""
        self._target(tab).set_minimum(int(width), int(height))
        return True

    def set_position(self, tab: str, x: int, y: int) -> bool:
        """Where the window stands, on the screen's own coordinates."""
        self._target(tab).set_position(int(x), int(y))
        return True

    def set_resizable(self, tab: str, on: bool = True) -> bool:
        """Whether the user may drag the window bigger or smaller; off freezes it at
        the size it is, and the minimum the addon set is kept for when it comes back.
        """
        return bool(self._target(tab).set_flag("resizable", bool(on)))

    def set_closable(self, tab: str, on: bool = True) -> bool:
        """Whether the window wears a close button. Off leaves the addon running with
        no way for the user to put the window down but **Dock back**.
        """
        return bool(self._target(tab).set_flag("closable", bool(on)))

    def set_always_on_top(self, tab: str, on: bool = True) -> bool:
        """Keep the window above the rest of the desktop, for a thing to watch."""
        return bool(self._target(tab).set_flag("on_top", bool(on)))

    def set_style(self, tab: str, css: str) -> bool:
        """This addon's own stylesheet laid over the theme the window is drawn from."""
        self._target(tab).set_style(str(css or ""))
        return True

    def set_icon(self, tab: str, icon: Any = None) -> bool:
        """The window's mark, and the picture its tab wears; the same two shapes
        ``api.tabs.set_icon`` takes - SVG text or a picture file - and ``None``
        clears it. An addon that owns one window with two pages marks the page.
        """
        name = str(tab)
        self._entry(name)
        wanted = str("" if icon is None else icon).strip()
        mark = None
        if wanted:
            mark = build_icon(wanted, self._addon.home)
            if mark is None:
                self._api.log("that window icon could not be read, so nothing changed")
                return False
        window = self._window(name)
        if window is None:
            return self._api.tabs.set_icon(name, wanted or None)
        if mark is not None:
            window.set_icon(mark)
        window.set_tab_icon(name, mark)
        return True

    # --- the desktop link ---

    def set_shortcut(self, on: bool = True) -> bool:
        """Ask for, or take back, a link on the Desktop that starts this one addon in
        a window of its own; a copy launched that way loads nothing else but this
        addon, the API it runs on and, when the addon rides it, the profile's main
        connection. The user may move or delete the link, which switches it off here.
        """
        addon = self._addon
        popout = self._popout()
        if bool(on):
            made, where = popout.write_shortcut(addon)
        else:
            made, where = popout.remove_shortcut(addon)
        self._api.log(f"desktop shortcut: {where}" if made or not on
                      else f"no desktop shortcut: {where}")
        return bool(made)

    def shortcut_path(self) -> str:
        """Where this addon's Desktop link is, or why it is not."""
        popout = self._popout()
        addon = self._addon
        if popout.has_shortcut(addon):
            return str(popout.shortcut_file(addon))
        return ""


class EventsAPI:
    """Subscribe to what the app does."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def on(self, name: str, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Register a listener; returns the callable that unregisters it."""
        return self._api._manager.add_listener(str(name), self._api.id, callback)

    def off(self, name: str, callback: Callable[[dict], None] | None = None) -> int:
        """Drop every listener for one event, or just the callback given; returns
        how many went away."""
        return self._api._manager.remove_listener(str(name), self._api.id, callback)

    def names(self) -> list[str]:
        """Every event the app broadcasts, whether this addon listens or not."""
        return list(self._api._manager.EVENT_NAMES)


class SettingsAPI:
    """The addon's own keys in config.json, saved with the profile."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def _record(self) -> dict:
        return self._api._manager.record_for(self._api.id)

    def all(self) -> dict:
        """Every key this addon has stored with the profile."""
        return jsonable(dict(self._record().get("settings") or {}))

    def get(self, key: str, default: Any = None) -> Any:
        """One stored key, or ``default`` when it was never set."""
        return jsonable((self._record().get("settings") or {}).get(str(key), default))

    def set(self, key: str, value: Any) -> None:
        """Store one key; it travels with the profile, beside the addon's switch."""
        self.update({key: value})

    def update(self, values: dict) -> None:
        """Store several keys at once, leaving the rest of the record alone."""
        record = self._record()
        store = dict(record.get("settings") or {})
        store.update({str(k): v for k, v in (values or {}).items()})
        record["settings"] = store
        self._api._manager.save_records()

    def pop(self, key: str, default: Any = None) -> Any:
        """Take one key away and say what it held."""
        record = self._record()
        store = dict(record.get("settings") or {})
        gone = store.pop(str(key), default)
        record["settings"] = store
        self._api._manager.save_records()
        return jsonable(gone)

    def remove(self) -> None:
        """Forget everything this addon has stored with the profile."""
        record = self._record()
        record.pop("settings", None)
        self._api._manager.save_records()


class SettingsPagesAPI:
    """A page of the app's Settings window that this addon owns.

    ``api.settings`` is a private key store the user never sees. This is the
    other thing: real controls, on a page of the Settings bar that stands beside
    the program's own categories - *outside* the shared **Installed addons** page,
    so an addon with a lot of options gets the room an option deserves. The page
    is the app's furniture: it goes away by itself when the addon is switched off
    or removed, and it is built the same way a tab is - from a widget spec, whose
    controls report back through ``on_widget`` with this page's name.
    """

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    @property
    def _store(self) -> dict:
        return self._api._addon.settings_pages

    @property
    def _win(self) -> QWidget:
        return self._api.win

    def names(self) -> list[str]:
        """Every page this addon has added, in the order it added them."""
        return list(self._store)

    def exists(self, name: str) -> bool:
        """Whether this addon owns a page under that name."""
        return str(name) in self._store

    def add(self, name: str, title: str = "", widget: QWidget | None = None,
            spec: dict | None = None, style: str = "") -> str:
        """Put a new page into the Settings bar; ``name`` is how the addon addresses it, ``title`` is what the tab says.

        Give a spec dict (the same language as :meth:`TabsAPI.add_tab`) or any
        QWidget. Re-adding a name replaces that page rather than stacking a second
        one beside it. Returns the name, or raises when nothing could be built or
        the window has no Settings bar to add to.
        """
        name = str(name).strip()
        if not name:
            raise AddonError("a settings page needs a name")
        if widget is None and spec is None:
            raise AddonError("settings_pages.add needs a widget or a spec")
        self.remove(name)
        registry: dict[str, QWidget] = {}
        if widget is None:
            events = lambda identifier, event, payload, page=name: (
                self._api._manager.push_widget_event(self._api.id, page, identifier,
                                                     event, payload))
            widget, registry = build_spec(spec, events=events)
        if style:
            style_sheet(widget, style)
        caption = str(title or name)
        host = self._win.add_settings_page(caption, widget)
        if host is None:
            raise AddonError("no Settings bar to add a page to")
        self._store[name] = {"body": widget, "host": host, "title": caption,
                             "registry": registry}
        self._api.log(f"added settings page '{caption}'")
        return name

    def remove(self, name: str) -> bool:
        """Take one page away; True when this addon had it."""
        entry = self._store.pop(str(name), None)
        if entry is None:
            return False
        self._win.remove_settings_page(entry["body"])
        entry["body"].deleteLater()
        return True

    def _bar(self) -> Any:
        return getattr(self._win, "settings_tabs", None)

    def _index(self, name: str) -> int:
        """Where this page sits in the Settings bar, or -1 when it is not there."""
        entry = self._store.get(str(name))
        bar = self._bar()
        if entry is None or bar is None:
            return -1
        return bar.indexOf(entry["host"])

    def title(self, name: str) -> str:
        """The caption the Settings bar wears for this page."""
        index = self._index(name)
        if index < 0:
            return ""
        return str(self._bar().tabText(index))

    def set_title(self, name: str, title: str) -> bool:
        """Rename the tab without touching the page's contents."""
        index = self._index(name)
        entry = self._store.get(str(name))
        if index < 0 or entry is None:
            return False
        caption = str(title or entry.get("title") or str(name))
        entry["title"] = caption
        self._bar().setTabText(index, caption)
        return True

    def activate(self, name: str) -> bool:
        """Show Settings with this page selected."""
        index = self._index(name)
        if index < 0:
            return False
        settings = getattr(self._win, "settings_page", None)
        if settings is not None:
            self._win.tabs.setCurrentWidget(settings)
        self._bar().setCurrentIndex(index)
        return True

    def widget(self, name: str, identifier: str | None = None) -> QWidget | None:
        """The page itself, or with an id one control inside it."""
        entry = self._store.get(str(name))
        if entry is None:
            return None
        if identifier is None:
            return entry["body"]
        return entry["registry"].get(str(identifier))

    def set_widget(self, name: str, identifier: str, prop: str = "text",
                   value: Any = None) -> bool:
        """Write one property of one control, exactly as ``tabs.set_widget`` does."""
        widget = self.widget(name, identifier)
        if widget is None:
            return False
        prop = str(prop)
        if prop in TERMINAL_PRIVILEGED and _is_terminal(widget):
            _authorize_terminal(self._api, prop, widget)
        apply_widget_property(widget, prop, value)
        return True

    def widget_value(self, name: str, identifier: str,
                     what: str = "value") -> Any:
        """What one control on the page holds right now."""
        widget = self.widget(name, identifier)
        if widget is None:
            raise AddonError(f"{name!r} has no widget with id {identifier!r}")
        return jsonable(read_widget_value(widget, what))

    def set_icon(self, name: str, icon: Any = None) -> bool:
        """No use here: a Settings page has no tab mark of its own to change.

        Kept so an addon that copies its tab code across gets a plain answer
        instead of an AttributeError; the real tab API is
        :meth:`TabsAPI.set_icon`.
        """
        self._api.log("a settings page wears no icon; only a main-window tab can")
        return False


class ConfigAPI:
    """The addon's own file in ``addons/_data/<id>``, shared by every profile.

    Declare the keys once from ``init`` and the app shows them in
    the store's Addon manager."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api
        self._addon = api._addon

    # --- the file ---

    @property
    def file(self) -> Path:
        """Where this addon's config file sits, as a path."""
        return addon_config_path(self._api.id)

    def path(self) -> str:
        """The same location as text, which is what crosses a process boundary."""
        return str(self.file)

    def exists(self) -> bool:
        """Whether the addon has written its file yet."""
        return self.file.is_file()

    def open_folder(self) -> bool:
        """Show the addon's own ``addons/_data/<id>`` folder; ``True`` when the file viewer really opened."""
        from core.ui import open_in_file_manager

        return open_in_file_manager(addon_data_dir(self._api.id))

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
        """The defaults handed to ``declare``, as the addon named them."""
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
        """One value out of the merged file, dotted keys and all."""
        return jsonable(config_get(self.all(), str(key), default))

    def set(self, key: str, value: Any) -> None:
        """Write one key into the file."""
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


class ScheduledTimer:
    """A timer an addon may command from its own thread.

    The Qt object stays where it was made and where it fires - the interface's
    thread - and this is the safe way to reach it from wherever the addon's code is
    running. The answer used to be the ``QTimer`` itself, which an addon could only
    stop correctly from the thread that built the window; a ``ticker.stop()`` from
    its own thread was a Qt call across the line, right or wrong by chance.

    Nothing here is asked of the addon's queue, so stopping a timer cannot be held
    up by the work the timer was there to do.
    """

    def __init__(self, timer: "QTimer", api: "AddonAPI", ms: float,
                 repeat: bool) -> None:
        self._timer = timer
        self._api = api
        self._ms = float(ms)
        self._repeat = bool(repeat)

    def _gui(self, job: Callable[[], Any]) -> Any:
        """Do this on the thread the timer lives on, from wherever it is asked."""
        if on_gui_thread():
            return job()
        bus = getattr(self._api._manager, "bus", None)
        if bus is None:
            return job()        # a harness with no bus has no other thread either
        return bus.ask(job, timeout=GUI_ASK_TIMEOUT)

    def stop(self) -> None:
        """End it: no further calls, and a repeating timer stops repeating."""
        self._gui(self._timer.stop)

    def start(self) -> None:
        """Set it going again, from whatever it was last told to wait for."""
        self._gui(self._timer.start)

    def active(self) -> bool:
        """Whether it is standing by to call."""
        return bool(self._gui(lambda: self._timer.isActive()))

    def set_seconds(self, seconds: Any) -> None:
        """Change how long it waits between calls, keeping whether it is running."""
        ms = max(1.0, float(seconds) * 1000.0)
        self._ms = ms
        running = bool(self._gui(lambda: self._timer.isActive()))

        def move() -> None:
            self._timer.stop()
            self._timer.setInterval(int(ms))
            if running:
                self._timer.start()

        self._gui(move)

    @property
    def seconds(self) -> float:
        """What it was started with, in seconds."""
        return self._ms / 1000.0

    @property
    def repeating(self) -> bool:
        """Whether it calls again or once only."""
        return self._repeat

    def __repr__(self) -> str:
        # Says nothing about the Qt timer, because a name printed in a log must not
        # be a call that waits on another thread.
        return (f"<scheduled {'every' if self._repeat else 'after'} "
                f"{self._ms / 1000.0:g}s>")


class SchedulerAPI:
    """Timers that stop themselves when the addon is switched off.

    The timer belongs to the interface and the callback belongs to the addon, so
    the two are joined by a hop: the moment fires on the GUI thread, and what it
    calls is queued onto the thread the addon's own code runs on - which is what
    lets an updater walk a dozen ``schedule.after(0.0, next_step)`` steps of a
    download without the window being the thing that waits for the network.
    """

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def after(self, seconds: Any, callback: Callable[[], None]) -> ScheduledTimer:
        """Call this once, ``seconds`` later, on the addon's own thread."""
        return self._start(float(seconds) * 1000.0, callback, False)

    def every(self, seconds: Any, callback: Callable[[], None]) -> ScheduledTimer:
        """Call this again every ``seconds`` until it is stopped or the addon is
        switched off."""
        return self._start(max(100.0, float(seconds) * 1000.0), callback, True)

    def _start(self, ms: float, callback: Callable[[], None],
               repeat: bool) -> ScheduledTimer:
        timer = QTimer()
        timer.setSingleShot(not repeat)
        timer.timeout.connect(lambda: self._fire(callback))
        timer.start(int(ms))
        # Kept for the teardown, which stops them on the thread that made them.
        self._api._addon.timers.append(timer)
        return ScheduledTimer(timer, self._api, ms, repeat)

    def _fire(self, callback: Callable[[], None]) -> None:
        """Hand a due timer's callback to the thread the addon's code runs on."""
        worker = getattr(self._api._addon, "worker", None)
        if worker is None:
            # No thread of its own: this fires straight from the Qt timer's
            # timeout slot. Run it under the same guard the threaded hop uses,
            # so an addon's own mistake is reported rather than left to reach the
            # C++ event loop - an unhandled exception in a Qt slot takes the whole
            # program down, not just the addon that made it.
            guard(callback, reporter=self._api.error)
            return
        # Not ``Addon.post``: an addon being stopped has its queue refused, and a
        # callback that comes due in that moment is simply gone rather than run
        # on the wrong thread out of spite.
        worker.post(lambda: guard(callback, reporter=self._api.error))


class PathsAPI:
    """Folders an addon may read and write."""

    def __init__(self, api: "AddonAPI") -> None:
        self._addon = api._addon
        self._api = api

    @property
    def addon(self) -> Path:
        """The folder the addon's own file sits in."""
        return self._addon.home

    @property
    def data(self) -> Path:
        """The addon's data folder, made when asked for: anything the addon keeps
        for itself goes here."""
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
        """The folder Berry Manager runs from."""
        from core.const import BASE_DIR

        return BASE_DIR

    @property
    def addons(self) -> Path:
        """The addons folder, where a drop-in file is picked up on the next scan."""
        from core.const import ADDONS_DIR

        return ADDONS_DIR

    @property
    def assets(self) -> Path:
        """The pictures shipped inside the program; read-only in spirit."""
        from core.const import ASSETS_DIR

        return ASSETS_DIR

    @property
    def themes(self) -> Path:
        """The themes folder beside the program, where every ``.theme`` file lives."""
        return core.ui.folder()

    @property
    def log(self) -> Path:
        """The app log file - the one :meth:`AddonAPI.log` writes into."""
        return LOG_FILE


class ThemeAPI:
    """The look of the window: what the theme in force says, and how to change one.

    A theme is one JSON object - a handful of named colours, the shape of the
    tabs, and a few lines of stylesheet of its own - kept in
    ``themes/<name>.theme``. Reading is safe from any thread; ``use`` and
    ``save`` redraw the window, so they belong on the GUI thread, which the
    process bridge already routes here.
    """

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def _window(self) -> Any:
        """The app window, when there is one to ask; a test or a build has none."""
        return getattr(self._api, "win", None)

    def _repaint(self, name: str) -> None:
        """Tell the window the look moved, if a window is here to hear it."""
        win = self._window()
        apply = getattr(win, "apply_theme", None)
        if callable(apply):
            apply(name)

    def name(self) -> str:
        """The theme the window is drawn from, by its file's name."""
        return core.ui.current_name()

    def names(self) -> list[str]:
        """Every theme on disk, ``default`` first and the rest in order."""
        return core.ui.names()

    def exists(self, name: str) -> bool:
        """Whether a theme of that name has a file behind it."""
        return core.ui.exists(str(name))

    def folder(self) -> str:
        """The themes folder, made on the way so it is always there to write in."""
        return str(core.ui.folder())

    def read(self, name: str = "") -> dict:
        """One theme complete - every colour and every knob, the ones in force
        when no name is given - as the dict its file stands for."""
        return jsonable(core.ui.theme(name or None))

    def raw(self, name: str) -> dict:
        """One theme file exactly as written, with nothing filled in; ``{}`` when
        it is missing or cannot be read."""
        return jsonable(core.ui.raw(name))

    def colors(self, name: str = "") -> dict:
        """Every colour token and the hex it currently means."""
        return core.ui.colors(name or None)

    def color(self, token: str, name: str = "", default: str = "") -> str:
        """One colour token as a hex; ``default`` when the interface has no such token."""
        return core.ui.color(str(token), name or None, default)

    def tabs(self, name: str = "") -> dict:
        """Every tab knob and the size it currently means, in pixels at 100% zoom."""
        return core.ui.tabs(name or None)

    def metric(self, knob: str, name: str = "") -> Any:
        """One tab knob by itself; ``None`` when no such knob is drawn."""
        return core.ui.tab_metric(str(knob), name or None)

    def icon_px(self) -> int:
        """The smallest a tab's picture is drawn, at 100% zoom."""
        return core.ui.icon_px()

    def chrome_px(self) -> int:
        """What a tab spends on itself besides its text, at 100% zoom."""
        return core.ui.chrome_px()

    def shows_captions(self) -> bool:
        """Whether a tab may show its name at all; off leaves the bar icon-only."""
        return core.ui.shows_captions()

    def shows_dots(self) -> bool:
        """Whether the SSH/SFTP dots are worn beside a tab's name."""
        return core.ui.shows_dots()

    def stylesheet(self, name: str = "") -> str:
        """The whole application sheet for one theme, at 100% zoom."""
        return core.ui.stylesheet(name or None)

    def sheet_of(self, theme: dict) -> str:
        """The sheet a theme that is still being edited would produce."""
        return core.ui.sheet_of(theme)

    def validate(self, theme: dict) -> list[str]:
        """What is wrong with a candidate theme, one line each; empty when it is
        usable. Unknown tokens are reported because they will be ignored."""
        return core.ui.validate(theme)

    def groups(self) -> list[dict]:
        """The colour tokens in the blocks an editor shows them in: ``{title,
        tokens: [{name, color, hint}]}`` for the theme in force."""
        return jsonable([
            {"title": title,
             "tokens": [{"name": token, "color": core.ui.color(token),
                        "hint": core.ui.token_hint(token)}
                        for token in tokens]}
            for title, tokens in core.ui.groups()])

    def token_knobs(self) -> list[dict]:
        """Every tab knob the bar answers to, with what it means and the size it
        is drawn at now: ``{name, value, kind, hint}``.

        ``kind`` says whether the knob is a number of pixels or a switch, so an
        editor can put the right control beside it without being told.
        """
        from core.const import THEME_TABS
        from core.ui import TAB_HINTS

        live = core.ui.tabs()
        return jsonable([
            {"name": knob, "value": live.get(knob), "kind": "switch"
             if isinstance(THEME_TABS[knob], bool) else "pixels",
             "hint": TAB_HINTS.get(knob, "")}
            for knob in THEME_TABS])

    def templates(self) -> list[dict]:
        """The looks a new theme may be begun from: ``{name, blurb, theme}``."""
        return jsonable(core.ui.templates())

    def template_text(self, name: str) -> str:
        """One template as the text of a ``.theme`` file, ready for an editor."""
        return core.ui.template_text(str(name))

    def use(self, name: str) -> str:
        """Draw the window from one theme and keep the choice for this profile;
        the name actually used, which falls back to ``default`` for a theme with
        no file."""
        win = self._window()
        apply = getattr(win, "apply_theme", None)
        if callable(apply):
            return str(apply(str(name)))
        return core.ui.use(str(name))

    def open_appearance(self) -> bool:
        """Show the app's Settings > Appearance page, where the theme in force
        is picked; whether the window could be talked into opening it.

        Themes are chosen in the app rather than in an addon, so an addon that
        edits them hands the person to that page instead of duplicating the
        picker; a window with no Settings to show answers False.
        """
        win = self._window()
        show = getattr(win, "show_appearance_settings", None)
        if callable(show):
            return bool(show())
        return False

    def save(self, name: str, theme: dict, force: bool = False) -> str:
        """Write one theme to its file; raises with what is wrong unless ``force``.

        Saving the look already in force redraws the window from it, which is
        what an editor wants: the change shows as soon as the file lands.
        """
        wanted = core.ui.safe_name(name) or core.ui.current_name()
        core.ui.write(wanted, theme, force=bool(force))
        if core.ui.current_name() == wanted:
            self._repaint(wanted)
        return wanted

    def copy(self, name: str, target: str) -> str:
        """Duplicate a theme under a new name; the name, or a refusal when that
        name is already taken."""
        return core.ui.copy(str(name), str(target))

    def remove(self, name: str) -> bool:
        """Delete one theme file; ``False`` for the shipped default, which stays."""
        wanted = str(name)
        was = core.ui.current_name()
        gone = core.ui.remove(wanted)
        if gone and was == core.ui.current_name() and wanted != was:
            self._repaint(was)
        return bool(gone)

    def reference(self) -> list[dict]:
        """Every call on ``api.theme`` itself: ``{name, args, doc}``.

        Read off the live object rather than written down twice, so the
        documentation a theme editor shows cannot fall behind the API it
        describes. Positional and keyword arguments are spelled out as the
        signature has them, defaults included.
        """
        out: list[dict] = []
        for name in dir(ThemeAPI):
            if name.startswith("_"):
                continue
            function = getattr(ThemeAPI, name, None)
            if not callable(function):
                continue
            try:
                signature = inspect.signature(function)
            except (TypeError, ValueError):
                continue
            parts = []
            for parameter in signature.parameters.values():
                if parameter.name in ("self", "api"):
                    continue
                shown = parameter.name
                if parameter.default is not inspect.Parameter.empty:
                    shown += "=" + repr(parameter.default)
                parts.append(shown)
            out.append(jsonable({
                "name": name,
                "args": f"api.theme.{name}({', '.join(parts)})",
                "doc": inspect.getdoc(function) or ""}))
        return out

    def rename(self, name: str, target: str) -> str:
        """A theme under a new name, the old file gone; the new name.

        Copy then remove, so a half-finished rename cannot leave the theme with
        no file at all: nothing is deleted until the new one has landed.
        """
        fresh = self.copy(name, target)
        self.remove(name)
        if core.ui.current_name() == str(name):
            self.use(fresh)
        return fresh


class PeersAPI:
    """Look at, and talk to, the other addons."""

    def __init__(self, api: "AddonAPI") -> None:
        self._api = api

    def list(self) -> list[str]:
        """The addons running right now, by id."""
        return [addon.id for addon in self._api._manager.loaded()]

    def info(self, addon_id: str) -> dict | None:
        """``{id, name, version, enabled, kind}`` for one addon, ``None`` when it
        is not running."""
        addon = self._api._manager.find(str(addon_id))
        if addon is None:
            return None
        return jsonable({"id": addon.id, "name": addon.name, "version": addon.version,
                         "enabled": addon.enabled, "kind": addon.kind})

    def send(self, addon_id: str, data: Any = None) -> bool:
        """Hand data to another addon's ``on_message``; ``False`` when nobody took
        it."""
        return self._api._manager.deliver(self._api.id, str(addon_id), data)


class AddonAPI:
    """The object every addon receives: ``def init(api)``."""

    def __init__(self, manager: Any, addon: Any) -> None:
        from core.const import BASE_DIR

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
        self.window = WindowAPI(self)
        self.events = EventsAPI(self)
        self.settings = SettingsAPI(self)
        self.settings_pages = SettingsPagesAPI(self)
        self.config = ConfigAPI(self)
        self.schedule = SchedulerAPI(self)
        self.paths = PathsAPI(self)
        self.theme = ThemeAPI(self)
        self.peers = PeersAPI(self)

    def log(self, text: Any) -> None:
        """Write to the app log under this addon's name."""
        self._manager.log(self.id, str(text))

    def error(self, text: Any) -> None:
        """Write an error line to the app log under this addon's name."""
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
