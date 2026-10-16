#!/usr/bin/env python3
"""A window of its own for an addon that asked for one.

An addon's tabs normally stand in the main window's bar, among the tabs the user
arranges. A **popout** is the same page in a top-level window: the widget is not
rebuilt and the addon is not restarted, the page is simply taken out of the bar
and put in a window that wears the addon's name and mark. One addon, one window;
two of its tabs are two pages in that one window.

Three things are decided here and nowhere else:

* which pages live where - :func:`move_out` and :func:`move_back` are the only
  code that reparents an addon's page, so a tab cannot be in the bar and in a
  window at once (Qt would put it in neither);
* where the window stood - its size, its place and whether it was maximised are
  written into the profile's addon record, the same place the addon's switch is;
* how a window is launched on its own - :func:`write_shortcut` puts a link on the
  Desktop that starts this program with ``--popout <id>``, and
  :class:`PopoutHost` is what answers an addon that runs that way, since it has
  no main window to ask for a Settings page or a pinned tab.

Nothing here knows what an addon does. It knows where a widget is.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel,
                             QMenu, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from addon_common import find_addon_picture, find_icon
from core.const import ADDONS_DIR, ASSETS_DIR, BASE_DIR, scaled_sheet
import core.ui
from core.net import SharedConnection, SharedSessionWorker, credential_id
from core.views import BrowserMixin, cert_bypass_active, set_cert_bypass

#: The command-line switch that starts the program as one addon's window.
POPOUT_FLAG = "--popout"

#: Where the window stands and how big it is, as the record keeps them.
GEOMETRY_KEYS = ("x", "y", "width", "height", "maximized")

#: A tab the record never wrote down: it goes in after the ones that were, since
#: nothing is known about where it belonged.
LAST_SLOT = 1 << 30


# --- the window ---

class PopoutWindow(QWidget):
    """One addon's tabs in a top-level window of its own.

    The window is the addon's, not the app's: it wears the addon's name and
    picture, and its bar shows only when the addon actually owns more than one
    page. Closing it hides it rather than destroying it, because the addon is
    still running and its connection, its timers and its half-finished job are in
    that page; the row's button brings the window back.
    """

    def __init__(self, title: str = "", save: Callable[[dict], None] | None = None,
                 report: Callable[[str, dict], None] | None = None,
                 dock: Callable[[str], None] | None = None,
                 icon: QIcon | None = None) -> None:
        super().__init__()
        self._save = save
        self._report = report
        self._dock = dock
        self._closing = False
        self._flags = {"resizable": True, "closable": True, "on_top": False}
        self._minimum: tuple[int, int] | None = None
        self._geometry_timer: QTimer | None = None
        self._pending: tuple[str, dict] | None = None
        self.setObjectName("popoutWindow")
        self.setWindowTitle(str(title or "Berry addon"))
        if icon is not None:
            self.setWindowIcon(icon)
        self.setStyleSheet(scaled_sheet(core.ui.stylesheet()))

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.header = QWidget()
        row = QHBoxLayout(self.header)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)
        self.caption = QLabel(self.windowTitle())
        self.caption.setStyleSheet("font-weight: 600;")
        row.addWidget(self.caption)
        row.addStretch(1)
        self.dock_button = QPushButton("Dock back")
        self.dock_button.setToolTip(
            "Put this addon's tab back in the main window's bar, where it was "
            "before it was popped out, and leave the addon running.")
        self.dock_button.clicked.connect(self._ask_dock)
        self.dock_button.setVisible(dock is not None)
        row.addWidget(self.dock_button)
        column.addWidget(self.header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("popoutTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        column.addWidget(self.tabs, 1)
        self._show_bar()

    # --- pages ---

    def has_page(self, name: str) -> bool:
        """Whether this window holds the page the addon calls ``name``."""
        return self._index(name) >= 0

    def _index(self, name: str) -> int:
        for index in range(self.tabs.count()):
            if str(self.tabs.tabText(index)) == str(name):
                return index
        return -1

    def add_page(self, name: str, page: QWidget, mark: QIcon | None = None,
                 tip: str = "") -> int:
        """Take a page in; ``addTab`` reparents it, which is the whole move."""
        at = self._index(name)
        if at >= 0:
            self.tabs.removeTab(at)
        self.tabs.addTab(page, str(name))
        self._show_bar()
        self.tabs.setCurrentWidget(page)
        at = self._index(name)
        if mark is not None and not mark.isNull():
            self.tabs.setTabIcon(at, mark)
        if tip:
            self.tabs.setTabToolTip(at, str(tip))
        return at

    def remove_page(self, name: str) -> QWidget | None:
        """Let one page go back out; the widget is returned, not deleted."""
        at = self._index(name)
        if at < 0:
            return None
        page = self.tabs.widget(at)
        self.tabs.removeTab(at)
        self._show_bar()
        return page

    def page_mark(self, name: str) -> QIcon | None:
        """The picture this page's tab wears, so it survives the trip back."""
        at = self._index(name)
        if at < 0:
            return None
        mark = self.tabs.tabIcon(at)
        return mark if mark is not None and not mark.isNull() else None

    def set_tab_icon(self, name: str, mark: QIcon | None) -> bool:
        at = self._index(name)
        if at < 0:
            return False
        self.tabs.setTabIcon(at, mark or QIcon())
        return True

    def page(self, name: str) -> QWidget | None:
        at = self._index(name)
        return self.tabs.widget(at) if at >= 0 else None

    def names(self) -> list[str]:
        return [str(self.tabs.tabText(index)) for index in range(self.tabs.count())]

    def _show_bar(self) -> None:
        """One page needs no bar; two are a row the user switches between."""
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)

    # --- what the addon asks for ---

    def set_title(self, text: str) -> None:
        name = str(text or "").strip() or "Berry addon"
        self.setWindowTitle(name)
        self.caption.setText(name)

    def set_icon(self, mark: QIcon | None) -> None:
        if mark is not None:
            self.setWindowIcon(mark)

    def set_style(self, css: str) -> None:
        """The theme with the addon's own sheet laid over it, as a tab's is."""
        base = scaled_sheet(core.ui.stylesheet())
        self.setStyleSheet(f"{base}\n{css}" if css else base)

    def set_sizes(self, width: int | None = None, height: int | None = None) -> None:
        if width and height:
            self.resize(int(width), int(height))

    def set_minimum(self, width: int, height: int) -> None:
        self._minimum = (int(width), int(height))
        if self._flags["resizable"]:
            self.setMinimumSize(int(width), int(height))

    def set_position(self, x: int, y: int) -> None:
        self.move(int(x), int(y))

    def center(self) -> None:
        """The middle of the screen this window is on, not of its own frame."""
        shown = self.screen() if self.screen() is not None else None
        if shown is None:
            app = QApplication.instance()
            shown = app.primaryScreen() if app is not None else None
        if shown is None:
            return
        box = shown.availableGeometry()
        self.move(box.center().x() - self.width() // 2,
                  box.center().y() - self.height() // 2)

    def set_flag(self, name: str, on: bool) -> bool:
        """Resizable, closable or always-on-top; the window's own answers.

        ``setWindowFlag`` may recreate the window, which hides it, so a window
        that was on screen is shown again - and the flags are kept in a dict of
        our own as well, because a window being built has no flags to read back
        yet.
        """
        if name not in self._flags:
            return False
        self._flags[name] = bool(on)
        was = self.isVisible()
        flag = {
            "resizable": Qt.WindowType.WindowMaximizeButtonHint,
            "closable": Qt.WindowType.WindowCloseButtonHint,
            "on_top": Qt.WindowType.WindowStaysOnTopHint,
        }[name]
        self.setWindowFlag(flag, bool(on))
        if name == "resizable":
            if on:
                self.setMaximumSize(16777215, 16777215)
                if self._minimum:
                    self.setMinimumSize(*self._minimum)
            else:
                size = self.size()
                self.setMinimumSize(size)
                self.setMaximumSize(size)
        if was:
            self.show()
        return True

    def flag(self, name: str) -> bool:
        return bool(self._flags.get(name))

    def state(self) -> dict:
        """What the window is: title, size, place, and the three flags."""
        size = self.size()
        place = self.pos()
        return {
            "title": str(self.windowTitle()),
            "size": [int(size.width()), int(size.height())],
            "position": [int(place.x()), int(place.y())],
            "visible": bool(self.isVisible()),
            "shown": bool(self.isVisible()),
            "resizable": self.flag("resizable"),
            "closable": self.flag("closable"),
            "on_top": self.flag("on_top"),
            "pages": self.names(),
        }

    # --- showing and going away ---

    def present(self) -> None:
        """Come up, above the rest, and take the keyboard."""
        self.show()
        QWidget.raise_(self)
        self.activateWindow()

    def open(self) -> None:
        self.show()
        self.present()

    def _ask_dock(self) -> None:
        if self._dock is not None:
            self._dock("")

    def showEvent(self, event: Any) -> None:            # noqa: N802 - Qt name
        super().showEvent(event)
        self._say("opened", {})

    def closeEvent(self, event: Any) -> None:           # noqa: N802 - Qt name
        # Hiding rather than destroying is the whole point: the addon keeps
        # running, so its page has to keep existing. Even a window its addon
        # asked to close goes out of sight rather than out of the program - an
        # addon can be raised again from its row, and a deleted page cannot.
        self.write_geometry()
        self._say("closing", {})
        event.ignore()
        if not self._closing:
            self._closing = True
            self.hide()
            self._closing = False
            self._say("closed", {})

    def changeEvent(self, event: Any) -> None:          # noqa: N802 - Qt name
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.write_geometry()

    def moveEvent(self, event: Any) -> None:            # noqa: N802 - Qt name
        super().moveEvent(event)
        place = self.pos()
        self._debounce("moved", {"position": [int(place.x()), int(place.y())]})

    def resizeEvent(self, event: Any) -> None:          # noqa: N802 - Qt name
        super().resizeEvent(event)
        size = self.size()
        self._debounce("resized", {"size": [int(size.width()), int(size.height())]})

    def _debounce(self, event: str, extra: dict) -> None:
        """A window dragged or dragged open says and writes once, when it stops.

        Qt fires these two for every pixel of a drag, and the record behind them
        is a file; the addon is told about the place the window ended up in, which
        is the only answer it can use.
        """
        self._pending = (event, dict(extra or {}))
        if self._geometry_timer is None:
            self._geometry_timer = QTimer(self)
            self._geometry_timer.setSingleShot(True)
            self._geometry_timer.timeout.connect(self._settle)
        self._geometry_timer.start(350)

    def _settle(self) -> None:
        """The debounced move or resize has stopped: write it, then say it."""
        self.write_geometry()
        pending, self._pending = self._pending, None
        if pending:
            self._say(pending[0], pending[1])

    # --- the record, and the events ---

    def write_geometry(self) -> None:
        if self._save is None or not self.isVisible():
            return
        place = self.pos()
        size = self.size()
        self._save({
            "x": int(place.x()), "y": int(place.y()),
            "width": int(size.width()), "height": int(size.height()),
            "maximized": bool(self.isMaximized()),
        })

    def read_geometry(self, stored: Any) -> bool:
        """Stand where the window stood last time; False when nothing was kept."""
        data = stored if isinstance(stored, dict) else {}
        try:
            width = int(data.get("width") or 0)
            height = int(data.get("height") or 0)
        except (TypeError, ValueError):
            return False
        if width < 60 or height < 60:
            return False
        self.resize(width, height)
        try:
            self.move(int(data.get("x") or 0), int(data.get("y") or 0))
        except (TypeError, ValueError):
            pass
        if bool(data.get("maximized")):
            self.showMaximized()
        return True

    def _say(self, event: str, extra: dict) -> None:
        if self._report is None:
            return
        self._report(event, dict(extra or {}))


# --- which window holds which page ---

def window_for(manager: Any, addon: Any, create: bool = False) -> PopoutWindow | None:
    """The window this addon owns, making it when asked and when it is wanted.

    The registry is the manager's: an addon's window belongs to one running
    program, and a second call for the same addon has to reach the same window
    rather than stack another one over it. A program launched as one addon's
    window puts its own there before anything is asked, so nothing here ever
    builds a second one.
    """
    where = getattr(manager, "windows", None)
    if where is None:
        return None
    held = where.get(addon.id)
    if held is not None:
        return held
    if not create:
        return None

    def save(values: dict) -> None:
        record = manager.record_for(addon.id)
        record["popout_geometry"] = dict(values or {})
        manager.save_records()

    def report(event: str, extra: dict) -> None:
        manager.window_events(addon.id, event, extra)

    window = PopoutWindow(title=str(addon.name or addon.id), save=save,
                          report=report, dock=lambda _n, m=manager, a=addon:
                          dock_back(m, a), icon=addon_mark(manager, addon))
    window.read_geometry(manager.record_for(addon.id).get("popout_geometry"))
    where[addon.id] = window
    return window


def addon_mark(manager: Any, addon: Any) -> QIcon | None:
    """The addon's own picture, the one its tab wears, as a window icon."""
    from addon_api import build_icon

    declared = str((addon.manifest or {}).get("icon") or "")
    for folder in (addon.home, addon.data_dir):
        found = find_icon(folder, addon.id, declared) if folder else ""
        if found:
            return build_icon(found)
    return None


def is_popped(manager: Any, addon: Any, name: str) -> bool:
    """Whether this addon's page is in a window rather than in the bar."""
    window = window_for(manager, addon)
    return bool(window is not None and window.has_page(name))


def settle_bar(manager: Any) -> None:
    """Ask the main window to re-pin its bar, when there is a bar to re-pin.

    The bar keeps the room it does not use in an empty gap tab it remembers by
    index, so every tab that leaves or joins the row to that gap's left shifts
    what the remembered index points at. The window puts it right a tick later on
    its own, which is soon enough for a tab a person opens and forgets and far too
    late for a popout moving a whole addon's pages at once: in between, the real
    tab that has slid into the stale index is drawn as wide as the gap is.
    """
    settle = getattr(getattr(manager, "win", None), "settle_tab_bar", None)
    if callable(settle):
        settle()


def pop_out(manager: Any, addon: Any, name: str) -> bool:
    """Take one page out of the main bar and into the addon's window."""
    win = manager.win
    entry = (addon.tabs or {}).get(str(name))
    if entry is None:
        return False
    page = entry.get("page")
    if page is None:
        return False
    window = window_for(manager, addon, create=True)
    if window is None or window is getattr(win, "tabs", None):
        return False
    if window.has_page(name):
        window.show()
        return True
    bar = getattr(win, "tabs", None)
    mark, tip = None, ""
    if bar is not None:
        index = bar.indexOf(page)
        if index >= 0:
            pinned = page in getattr(win, "_pinned_pages", [])
            icon_only = bool(getattr(win, "_pinned_icon_only", {}).get(page))
            mark, tip = bar.tabIcon(index), str(bar.tabToolTip(index) or "")
            # Unpinning first, because ``unpin_tab`` reads the tab's place on the
            # bar to put its caption back; after the removal there is no place.
            if pinned and callable(getattr(win, "unpin_tab", None)):
                win.unpin_tab(page)
            bar.removeTab(index)
            # The bar has closed up behind every tab of this addon that left
            # before this one, so ``index`` is where the page stood a moment ago
            # rather than where it originally did. The ones already away, and
            # standing at or before it, are counted back in: docking the lot home
            # in slot order then puts each page back where it started.
            away = sum(1 for where in manager.slots_for(addon.id).values()
                       if isinstance(where, dict)
                       and int(where.get("slot") or 0) <= index)
            manager.remember_slot(addon.id, str(name), index + away,
                                  pinned, icon_only)
    window.add_page(str(name), page, mark=mark, tip=tip)
    entry["popped"] = True
    window.show()
    settle_bar(manager)
    manager.window_events(addon.id, "undocked", {"tab": str(name)})
    if callable(getattr(win, "sync_tab_transport_dots", None)):
        win.sync_tab_transport_dots()
    return True


def dock_back(manager: Any, addon: Any, name: str = "") -> bool:
    """Put one page back in the main bar; with no name, every page it holds.

    A window whose pages have all gone is hidden: the addon is still running and
    its record still says whether it should be popped out, so the window is
    waiting for the next tab rather than being thrown away.
    """
    window = window_for(manager, addon)
    if window is None:
        return False
    win = manager.win
    bar = getattr(win, "tabs", None)
    if bar is None or not getattr(win, "is_main_window", False):
        # Nothing was launched with a main window: there is no bar to go back to.
        return False
    if name:
        wanted = [str(name)]
    else:
        # Home in the order they left the bar: an addon switched back as a whole
        # has each page's slot written down, and putting them in one by one in
        # that order is what keeps the row the user built.
        def remembered(one: str) -> int:
            try:
                return int(manager.slot_for(addon.id, one).get("slot"))
            except (TypeError, ValueError):
                return LAST_SLOT

        wanted = sorted(window.names(), key=remembered)
    moved = False
    for page_name in wanted:
        mark = window.page_mark(page_name)
        page = window.remove_page(page_name)
        if page is None:
            continue
        entry = (addon.tabs or {}).get(page_name) or {}
        where = manager.slot_for(addon.id, page_name)
        at = max(0, min(int(where.get("slot", bar.count())), bar.count()))
        bar.insertTab(at, page, page_name)
        if mark is not None:
            bar.setTabIcon(at, mark)
        if entry.get("popped"):
            moved = True
        entry["popped"] = False
        if where.get("pinned") and callable(getattr(win, "pin_tab", None)):
            # Pinning moves the page to the edge itself; the slot it was put in
            # is only where the bar had it before it left.
            win.pin_tab(page, page_name, icon_only=bool(where.get("icon_only")))
        bar.setCurrentWidget(page)
    if not window.names():
        window.hide()
    settle_bar(manager)
    if moved:
        manager.window_events(addon.id, "docked", {"tab": str(name) if name else ""})
    if callable(getattr(win, "sync_tab_transport_dots", None)):
        win.sync_tab_transport_dots()
    return moved


def apply_popout(manager: Any, addon: Any, force: bool = False) -> None:
    """Move every tab this addon owns into its window, when it belongs there.

    Only the way out is taken here: coming back is a decision somebody moved, and
    an addon that popped one of its own tabs during its start-up would otherwise
    have it dragged back into the bar by a switch that was never touched.

    ``force`` is the addon's own code asking for its window: a manifest that
    named a state, or a switch the record does not carry, is then no reason to
    leave the pages where the app would have put them.
    """
    if not force and not bool(getattr(addon, "effective_popout", False)):
        return
    for name in list((addon.tabs or {}).keys()):
        entry = addon.tabs.get(name) or {}
        if entry.get("page") is None:
            continue
        if not is_popped(manager, addon, name):
            pop_out(manager, addon, name)


def recall(manager: Any, addon: Any) -> None:
    """Bring everything of this addon's that is floating back to the bar.

    Run before the app takes an addon's tabs down: a page deleted while a window
    still lists it leaves that window painting a dead addon.
    """
    window = getattr(manager, "windows", {}).get(addon.id)
    if window is None or not window.names():
        return
    for name in list(window.names()):
        dock_back(manager, addon, name)


def close_windows(manager: Any, addon: Any) -> None:
    """Take an addon's window down with it: switched off, reloaded, uninstalled.

    The pages go back into the bar first, because a widget whose tab has been
    removed with it still has a window holding it, and Qt would paint a dead
    addon's page on screen.
    """
    window = getattr(manager, "windows", {}).get(addon.id)
    if window is None:
        return
    recall(manager, addon)
    manager.windows.pop(addon.id, None)
    window.deleteLater()


# --- the desktop link ---

def popout_target() -> tuple[str, str]:
    """What a link has to run: the program, and the arguments before the addon.

    A frozen build is its own executable; a checkout is the interpreter - the
    windowless one where it exists - and the script. The script's path is quoted,
    because the folder it sits in may have a space in it.
    """
    if getattr(sys, "frozen", False):
        return str(sys.executable), ""
    folder = Path(sys.executable)
    quiet = folder.parent / ("pythonw.exe" if os.name == "nt" else "pythonw")
    runner = str(quiet) if quiet.is_file() else str(sys.executable)
    script = Path(__file__).resolve().parent.parent / "berry_manager.py"
    return runner, f'"{script}"'


def desktop_folder() -> Path:
    """The folder this machine shows on the Desktop, asked of the shell itself.

    Never guessed: a localized Windows keeps its Desktop somewhere the name
    ``home/Desktop`` does not point at, and a link written there is invisible.
    ``BERRY_DESKTOP_DIR`` says otherwise for a test, which must not touch the
    real Desktop to prove that a link is written and removed.
    """
    override = str(os.environ.get("BERRY_DESKTOP_DIR") or "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        got = _ask_powershell("[Environment]::GetFolderPath('Desktop')")
        if got:
            return Path(got)
    return Path.home() / "Desktop"


def _ask_powershell(statement: str) -> str:
    """One line from PowerShell, stripped; empty when there is no PowerShell."""
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", statement],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return str(done.stdout or "").strip()


def shortcut_file(addon: Any) -> Path:
    """Where this addon's desktop link would live."""
    name = str(addon.id)
    if os.name == "nt":
        return desktop_folder() / f"Berry - {name}.lnk"
    return desktop_folder() / f"berry-{name}.desktop"


def application_file(addon: Any) -> Path:
    """The Linux ``.desktop`` entry, which the Desktop file is a copy of."""
    return Path.home() / ".local" / "share" / "applications" / \
        f"berry-{addon.id}.desktop"


def icon_for_shortcut(addon: Any) -> str:
    """The addon's picture as a file this platform's link can point at.

    A ``.lnk`` wants an ``.ico``; the marks addons publish are PNGs, so one is
    rewrapped beside the addon's data - once, and kept, because a link reads the
    file every time it is drawn.
    """
    from core.addon import ico_from_png, ico_scaled

    picture = find_addon_picture(_Paths(addon), addon.id,
                                 str((addon.manifest or {}).get("icon") or ""))
    if not picture:
        return ""
    source = Path(picture)
    if source.suffix.lower() in (".ico", ".icns"):
        return str(source)
    if os.name != "nt":
        return str(source)
    target = Path(addon.data_dir) / f"{addon.id}.ico"
    if target.is_file():
        return str(target)
    made = ico_from_png(source, target) or ico_scaled(source, target)
    return str(made) if made is not None else str(source)


class _Paths:
    """The three folders an addon's picture may sit in, for ``find_addon_picture``.

    It reads ``.addon``, ``.assets`` and ``.data`` off whatever it is handed; an
    addon's runtime record knows the first and the last, and the program's
    ``assets`` folder is the one that carries a shipped addon's mark.
    """

    def __init__(self, addon: Any) -> None:
        self.addon = getattr(addon, "home", None)
        self.data = getattr(addon, "data_dir", None)
        self.assets = ASSETS_DIR


def write_shortcut(addon: Any) -> tuple[bool, str]:
    """Put a link on the Desktop that starts this one addon in its own window.

    Returns whether it was made and where it is, or why it could not be: a link
    is a courtesy the user asked for, so a failure is said rather than raised.
    """
    target, prefix = popout_target()
    arguments = f"{prefix} {popout_arguments(addon.id)}".strip()
    where = shortcut_file(addon)
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, f"no Desktop to write to: {error}"
    mark = icon_for_shortcut(addon)
    if os.name == "nt":
        made = _write_lnk(where, target, arguments, mark, addon)
    else:
        made = _write_desktop(where, target, arguments, mark, addon)
    if made:
        return False, made
    return True, str(where)


def _write_lnk(where: Path, target: str, arguments: str, mark: str,
               addon: Any) -> str:
    """A real ``.lnk``, through the shell's own writer.

    The values travel as environment variables rather than as quoted text inside
    the script: an addon's name can hold anything a person typed into it, and a
    property bag does not have to survive the parser.
    """
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BERRY_LNK); "
        "$s.TargetPath = $env:BERRY_EXE; "
        "$s.Arguments = $env:BERRY_ARGS; "
        "$s.WorkingDirectory = $env:BERRY_CWD; "
        "$s.IconLocation = $env:BERRY_ICON; "
        "$s.Description = $env:BERRY_DESC; "
        "$s.WindowStyle = 1; "
        "$s.Save()"
    )
    env = dict(os.environ)
    env.update({
        "BERRY_LNK": str(where),
        "BERRY_EXE": target,
        "BERRY_ARGS": arguments,
        "BERRY_CWD": str(BASE_DIR),
        "BERRY_ICON": mark or target,
        "BERRY_DESC": f"{addon.name or addon.id} - Berry Manager addon",
    })
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as error:
        return f"powershell said nothing: {error}"
    if done.returncode != 0 or not where.is_file():
        return str(done.stderr or "").strip() or "the link was not written"
    return ""


def _write_desktop(where: Path, target: str, arguments: str, mark: str,
                   addon: Any) -> str:
    """A ``.desktop`` entry, filed where the desktop environment reads them.

    The copy onto the Desktop is a copy rather than a link: a file in
    ``~/.local/share/applications`` is what makes the entry findable in the
    launcher's own search, and freedesktop says a desktop copy is a copy.
    """
    command = [target] + [part for part in arguments.split() if part]
    line = " ".join([_quote(part) for part in command])
    fields = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={addon.name or addon.id}",
        f"Comment={getattr(addon, 'description', '') or 'Berry Manager addon'}",
        f"Exec={line}",
        f"Path={BASE_DIR}",
        "Terminal=false",
        "Categories=Utility;",
    ]
    if mark:
        fields.insert(-1, f"Icon={mark}")
    entry = "\n".join(fields) + "\n"
    try:
        home = application_file(addon)
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text(entry, encoding="utf-8")
        where.write_text(entry, encoding="utf-8")
        os.chmod(home, 0o755)
        os.chmod(where, 0o755)
    except OSError as error:
        return str(error)
    return ""


def _quote(part: str) -> str:
    text = str(part)
    return f'"{text}"' if " " in text else text


def remove_shortcut(addon: Any) -> tuple[bool, str]:
    """Take the link away again; ``True`` when there is nothing left of it."""
    gone = []
    for path in (shortcut_file(addon), application_file(addon)):
        try:
            if path.is_file():
                path.unlink()
                gone.append(str(path))
        except OSError as error:
            return False, f"{path}: {error}"
    return True, ", ".join(gone) or "nothing was there"


def has_shortcut(addon: Any) -> bool:
    """Whether the link this app made is still on the Desktop."""
    try:
        return shortcut_file(addon).is_file()
    except OSError:
        return False


# --- a program that is only one addon ---

def popout_id(argv: list[str] | None = None) -> str:
    """The addon named on the command line, or ``""`` for the ordinary launch.

    Read by hand rather than with a parser: one flag with one value is the whole
    interface, and the window's own arguments have to keep passing through
    untouched for everything else.
    """
    return launch_request(argv)[1]


def launch_request(argv: list[str] | None = None) -> tuple[bool, str]:
    """Whether this program was asked to be one addon's window, and for which addon.

    The two answers are kept apart on purpose: a bare ``--popout`` with nothing
    after it is a mistake worth saying out loud, not a reason to start the whole
    application as if the flag had never been typed.
    """
    words = [str(item) for item in (argv if argv is not None else sys.argv[1:])]
    for index, word in enumerate(words):
        if word == POPOUT_FLAG:
            value = str(words[index + 1]).strip() if index + 1 < len(words) else ""
            return True, value
        if word.startswith(POPOUT_FLAG + "="):
            return True, str(word.split("=", 1)[1]).strip()
    return False, ""


class Holder:
    """One read-only field, spelled the way a line edit would be.

    An addon that asks the window for the host it should connect to reads
    ``win.host_input.text()``. In a popout there is no Connection panel, and the
    profile still has a host: so these are here, with the same two words, and no
    way to write through them.
    """

    def __init__(self, value: Any = "") -> None:
        self._value = value

    def text(self) -> str:
        return str(self._value or "")

    def value(self) -> int:
        try:
            return int(self._value)
        except (TypeError, ValueError):
            return 0


class PopoutHost(BrowserMixin, PopoutWindow):
    """What an addon launched from a Desktop link has instead of the window.

    It is a window - the addon's own - and it answers the calls the API makes on
    ``manager.window`` as far as they can be answered with one window and one
    addon in the program. An addon that asks for a Settings page, a pinned tab or
    the app's menu is told plainly that there is nowhere to put it rather than
    crashing on a missing attribute; an addon that asks for its host, its tabs and
    its connection gets the real answers, read from the active profile.

    Being a QWidget as well is not for show: ``api.ui.dialog`` builds a
    :class:`QDialog` over the window it was handed, and a dialog parented on
    nothing sits under the addon's own window.
    """

    #: No bar to go back to: this program *is* the addon.
    is_main_window = False
    can_dock_tabs = False

    def __init__(self, addon_id: str = "", name: str = "",
                 config: dict | None = None, manager: Any = None) -> None:
        PopoutWindow.__init__(self, title=str(name or addon_id or "Berry addon"),
                              save=self._save_geometry, report=self._report)
        self.setObjectName("popoutHost")
        self.addon_id = str(addon_id or "")
        self.app_config: dict = dict(config or {})
        self.manager = manager
        # The furniture the main window has and this one does not.
        self.settings_tabs = None
        self.settings_page = None
        self._transient_tab_entries: list[dict] = []
        self._web_tab_entries: list[dict] = []
        self._cert_bypass_session = cert_bypass_active()
        # The connection, which is real: an addon riding the app's session gets
        # one here exactly as it would in the main window.
        self.shared_conn = SharedConnection()
        self.main_connected = False
        self.worker = None
        self.shell_worker = None
        self._status = _StatusLine(self)
        self._menus: dict[str, QMenu] = {}
        profile = self.app_config
        self.host_input = Holder(profile.get("host", ""))
        self.port_input = Holder(profile.get("port", 22))
        self.username_input = Holder(profile.get("username", ""))
        self.remote_root_input = Holder(profile.get("remote_root", ""))
        self.key_input = Holder(profile.get("key_file", ""))
        self.password_input = Holder(self._saved_password())

    # --- the window itself ---

    @property
    def window(self) -> QWidget:
        """Itself: the addon's tabs and the addon's window are one and the same."""
        return self

    def _save_geometry(self, values: dict) -> None:
        record = self.app_config.setdefault("addons", {}).get(self.addon_id)
        if isinstance(record, dict):
            record["popout_geometry"] = dict(values or {})
            self.save_app_config()

    def _report(self, event: str, extra: dict) -> None:
        tab = str((extra or {}).get("tab") or "").strip()
        self.log(f"window {event}{': ' + tab if tab else ''}")
        if self.manager is not None:
            notify = getattr(self.manager, "window_events", None)
            if callable(notify):
                notify(self.addon_id, event, dict(extra or {}))

    # --- what the API reads ---

    def log(self, text: Any) -> None:
        """The popout's log is the console it was started from, and its own title."""
        print(f"[{self.addon_id}] {text}")

    def save_app_config(self, _value: Any = None) -> None:
        from core.ui import save_app_config_static

        save_app_config_static(self.app_config)

    def connections_enabled(self) -> bool:
        """The master switch, read from the profile this window was started with."""
        return bool(self.app_config.get("master_connection", True))

    def _saved_password(self) -> str:
        """The profile's stored password, when it asks to be remembered.

        Read the way the main window reads it - from the OS credential store,
        only when the profile says a password is kept there.
        """
        if not bool(self.app_config.get("remember_password", False)):
            return ""
        try:
            import keyring

            return str(keyring.get_password(
                "rasconf-sftp-manager", self.current_credential_id()) or "")
        except Exception as error:               # no store is a plain no
            self.log(f"could not read the saved password: {error}")
            return ""

    def current_credential_id(self) -> str:
        return credential_id(self.username_input.text().strip(),
                             self.host_input.text().strip(),
                             self.port_input.value())

    def current_settings(self, silent: bool = False) -> dict | None:
        """The connection details a call would use, in the window's own shape."""
        root = self.remote_root_input.text().strip()
        if not self.host_input.text().strip() or not self.username_input.text().strip():
            if not silent:
                self.log("the profile has no host or username to connect with")
            return None
        if root and not root.startswith("/"):
            if not silent:
                self.log(f"remote directory {root!r} is not an absolute path")
            return None
        return {
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "key_file": self.key_input.text().strip(),
            "remote_root": root or "/www_berry",
            "source": str(self.app_config.get("source", "")),
            "trust_unknown_host": bool(self.app_config.get("trust_unknown_host")),
            "remember_password": bool(self.app_config.get("remember_password")),
            "ssh_timeout": int(self.app_config.get("ssh_timeout", 12) or 12),
            "show_hidden_files": bool(self.app_config.get("show_hidden_files", False)),
        }

    def main_open(self) -> bool:
        return self.shared_conn.active()

    def connect_remote(self, _checked: Any = False) -> bool:
        """Open the main connection this addon rides, off the GUI thread."""
        if not self.connections_enabled():
            self.log("connections are switched off in this profile")
            return False
        if self.worker is not None and self.worker.isRunning():
            return False
        if self.shared_conn.active():
            self.mark_main_connected()
            return True
        settings = self.current_settings(silent=True)
        if settings is None:
            self.log("nothing to connect to: the profile has no host")
            return False
        self.worker = SharedSessionWorker(self.shared_conn, settings, parent=self)
        self.worker.succeeded.connect(self._connected)
        self.worker.failed.connect(self._connection_failed)
        self.worker.finished.connect(self._connect_finished)
        self.log(f"connecting to {settings['host']}...")
        self.worker.start()
        return True

    def _connected(self) -> None:
        self.mark_main_connected()

    def _connection_failed(self, message: str) -> None:
        self.main_connected = False
        self.log(f"connection failed: {message}")

    def _connect_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def mark_main_connected(self) -> None:
        """Say the session is up, and tell the addon that is all this window has."""
        first = not self.main_connected
        self.main_connected = True
        if first:
            self._status.showMessage("Connected (SSH + SFTP)", 4000)
            self._event("sftp_connected", host=self.host_input.text().strip())
            self._event("ssh_connected", host=self.host_input.text().strip())

    def mark_main_closed(self, reason: str = "the session closed") -> None:
        was = self.main_connected
        self.main_connected = False
        if was:
            self.log("main connection closed")
            self._event("sftp_disconnected", reason=reason)
            self._event("ssh_disconnected", reason=reason)

    def disconnect_remote(self, _checked: Any = False) -> None:
        if self.shell_worker is not None and self.shell_worker.isRunning():
            self.shell_worker.stop()
        self.shared_conn.close()
        self.mark_main_closed()

    def closeEvent(self, event: Any) -> None:           # noqa: N802 - Qt name
        """Closing this window closes the program: there is nothing else in it."""
        self.write_geometry()
        event.accept()

    def _event(self, name: str, **extra: Any) -> None:
        notify = getattr(self.manager, "notify", None)
        if callable(notify):
            try:
                notify(name, dict(extra))
            except Exception as error:            # an event never stops a window
                self.log(f"{name}: {error}")

    def app_icon_path(self) -> str:
        return ""

    def current_icon_path(self) -> str:
        return ""

    def statusBar(self) -> Any:                         # noqa: N802 - Qt name
        """The line a popout has: its own, which writes into the console."""
        return self._status

    def menuBar(self) -> Any:                           # noqa: N802 - Qt name
        """Menus an addon adds are kept here and answer to nothing but themselves."""
        return _MenuBar(self)

    def notify_popup(self, title: str, text: str, ms: int = 6000) -> None:
        """A popout has no tray of its own: the toast is the status line."""
        self._status.showMessage(f"{title}: {text}", max(500, int(ms)))

    # --- what this window cannot offer ---

    def add_settings_page(self, title: str, body: QWidget) -> QWidget | None:
        """There is no Settings bar in a program that is only one addon."""
        self.log(f"no Settings bar here: the page '{title}' was not added")
        return None

    def remove_settings_page(self, body: QWidget) -> bool:
        return False

    def pin_tab(self, page: QWidget, label: str = "",
                icon_only: bool = True) -> bool:
        """Nothing to pin beside: this window holds one addon's tabs and no bar."""
        self.log("a popout window has no pinned run; the tab keeps its name")
        return False

    def unpin_tab(self, page: QWidget) -> None:
        return None

    def sync_tab_transport_dots(self) -> None:
        """The dots live beside a tab in the main bar; here the bar is the window."""
        return None

    # --- the helpers TabsAPI and BrowserMixin reach for ---

    def _content_insert_index(self) -> int:
        return self.tabs.count()

    def remove_transient_tab(self, page: QWidget) -> bool:
        """Take one of this window's tabs out of the bar, and say that it did."""
        index = self.tabs.indexOf(page)
        if index < 0:
            return False
        self.tabs.removeTab(index)
        BrowserMixin.remove_transient_tab(self, page)
        return True

    def request_cert_bypass(self) -> None:
        """Save the switch the browser reads at startup, and say it needs a start."""
        self.app_config["ignore_cert_errors"] = True
        self.save_app_config()
        set_cert_bypass(True)
        self.log("certificate errors will be ignored; restart this window for it")

    def _sync_cert_bypass(self, ask_restart: bool = True) -> None:
        set_cert_bypass(bool(self.app_config.get("ignore_cert_errors")))

    def _update_cert_bypass_state(self) -> None:
        return None

    def restart_for_cert_bypass(self) -> None:
        """A popout restarts itself, not the whole program's window stack."""
        self.log("Restart this window to apply the certificate setting")


class _StatusLine:
    """A status bar's one call, on an object that is not one.

    The API writes its notices through ``win.statusBar().showMessage(...)``. A
    popout window has no strip of its own to write on, and every addon that uses
    it would have to be told twice that a window of one tab has no such line, so
    the line is here instead: it prints, and it puts the words in the title.
    """

    def __init__(self, host: PopoutHost) -> None:
        self._host = host

    def showMessage(self, text: Any, timeout: int = 0) -> None:   # noqa: N802
        words = str(text or "")
        if words:
            self._host.log(words)

    def clearMessage(self) -> None:                    # noqa: N802
        return None


class _MenuBar:
    """Menu titles an addon asks for, kept as loose :class:`QMenu` objects.

    ``api.ui.menu_item`` needs a menu to add its action to and hands the action
    back, so the entries work as buttons wherever they are held; what a popout
    cannot do is draw them. The menus are cached by title, so the same second
    addon, or the same addon twice, gets the same one.
    """

    def __init__(self, host: PopoutHost) -> None:
        self._host = host

    def actions(self) -> list[QAction]:
        return [menu.menuAction() for menu in self._host._menus.values()]

    def addMenu(self, title: str) -> QMenu:            # noqa: N802 - Qt name
        wanted = str(title)
        held = self._host._menus.get(wanted.lower())
        if held is not None:
            return held
        menu = QMenu(wanted, self._host)
        self._host._menus[wanted.lower()] = menu
        return menu


def popout_arguments(addon_id: str) -> str:
    """How a Desktop link spells its launch, for the README and the shortcut."""
    return f"{POPOUT_FLAG} {addon_id}"


def find_addon(manager: Any, wanted: str) -> Any:
    """The scanned addon whose id the link named, whatever spelling it used."""
    lower = str(wanted or "").strip().lower()
    if not lower:
        return None
    return next((addon for addon in manager.addons
                 if str(addon.id).lower() == lower), None)


def open_connection_for(host: "PopoutHost", addon: Any) -> bool:
    """Open the profile's main connection, and only when this addon rides it.

    A window launched from the Desktop has no Connect button to press, so whether
    a session is opened here is decided once, from what the addon declares: an
    addon on a connection of its own needs nothing and gets nothing, and an addon
    that needs no transport at all is not a reason to reach for the device. The
    profile's startup switch is beside the point - the link itself is the asking -
    and that is said in the window's log either way, because a person looking at a
    window that did not connect is entitled to know which rule stopped it.
    """
    name = str(addon.name or addon.id)
    if addon.uses_own:
        host.log(f"{name} opens its own connection, so nothing is opened here")
        return False
    if not tuple(addon.requires or ()):
        host.log(f"{name} needs no connection, so none is opened")
        return False
    if not host.connections_enabled():
        host.log("connections are switched off in this profile "
                 "(Settings > Connection)")
        return False
    needed = [item for item in ("ssh", "sftp")
              if item in [str(part).lower() for part in addon.requires]] or ["sftp"]
    off = [item for item in needed
           if not bool(host.app_config.get(f"{item}_enabled", True))
           or not addon.transport_allowed(item)]
    if off:
        host.log(f"{' and '.join(off)} "
                 f"{'is' if len(off) == 1 else 'are'} switched off, so {name} "
                 "cannot be given a connection")
        return False
    if not bool(host.app_config.get("auto_connect", False)):
        host.log("the startup switch is off, but this window was asked for by name: "
                 "opening the main connection anyway")
    return bool(host.connect_remote())


def finish(manager: Any, host: "PopoutHost") -> None:
    """The window went down: close the session it opened and stop the addon."""
    try:
        host.disconnect_remote()
    except Exception as error:                # going down twice is not worth a word
        host.log(f"connection: {error}")
    try:
        manager.shutdown_all()
    except Exception as error:
        host.log(f"closing the addon: {error}")


def run(app: Any, wanted: str, config: dict) -> int:
    """Start this program as one addon's window and run it; returns the exit code.

    Nothing else of the application is built: no main window, no Settings, no
    profile picker and no splash. The addons folder is read for the one id the
    link named, that addon is started as though the window had come up with it,
    and the main connection is opened if the addon rides it. An id the folder does
    not hold is said on the console and answered with a nonzero code, since a link
    left pointing at an uninstalled addon should not look like a program that
    opened and then closed itself.
    """
    from core.addon import AddonManager

    ident = str(wanted or "").strip().lower()
    if not ident:
        print(f"{POPOUT_FLAG} needs the id of the addon to open")
        return 2
    host = PopoutHost(addon_id=ident, name=ident, config=config)
    # The load said what it moved up into the one git token before there was any
    # window at all; this window is the only place it can still be said.
    for line in core.ui.take_lift_lines():
        host.log(line)
    manager = AddonManager(host)
    host.manager = manager
    known = {str(addon.id).lower() for addon in manager.scan()}
    if ident not in known:
        print(f"no addon called {ident} is in {ADDONS_DIR}; nothing to open")
        return 2
    manager.reload(only=[ident], force=True)
    addon = find_addon(manager, ident)
    if addon is None:
        print(f"{ident} was in the folder a moment ago and is not now")
        return 2
    host.addon_id = str(addon.id)
    host.set_title(str(addon.name or addon.id))
    mark = addon_mark(manager, addon)
    if mark is not None:
        host.set_icon(mark)
    manager.windows[str(addon.id)] = host
    open_connection_for(host, addon)
    if not host.tabs.count():
        host.log("the addon has built no tab yet; the window stays empty until it does")
    host.present()
    app.aboutToQuit.connect(lambda: finish(manager, host))
    return int(app.exec())
