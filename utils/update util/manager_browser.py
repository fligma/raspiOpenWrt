#!/usr/bin/env python3
"""Everything the embedded browser tabs need.

Two halves live here: the plain helpers below - the certificate bypass switch
and the app relaunch - and :class:`BrowserMixin`, the web tabs themselves.

QtWebEngine offers no callback for a certificate it does not trust - the page
simply fails with the Chromium error. The one supported way to accept such a
page is the Chromium command-line switch ``--ignore-certificate-errors``, which
is read from ``QTWEBENGINE_CHROMIUM_FLAGS`` when QtWebEngine starts its browser
process. That happens with the first embedded view, so the switch has to be in
the environment before any tab is built; changing it later only takes effect on
a restart (see :func:`restart_process`).
"""


from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from addon_common import clean_child_env
from manager_config import web_tab_url
from manager_const import zoom_size
from manager_widgets import TabEditDialog

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage
except ImportError:
    QWebEnginePage = None


CERT_BYPASS_FLAG = "--ignore-certificate-errors"
CHROMIUM_FLAGS_ENV = "QTWEBENGINE_CHROMIUM_FLAGS"


def cert_bypass_active() -> bool:
    """Whether this process started the embedded browser with the bypass on."""
    return CERT_BYPASS_FLAG in os.environ.get(CHROMIUM_FLAGS_ENV, "").split()


def set_cert_bypass(enabled: bool) -> None:
    """Put or remove the bypass in the Chromium flags, keeping others; only affects a not-yet-started browser, so call before the first view."""
    flags = [flag for flag in os.environ.get(CHROMIUM_FLAGS_ENV, "").split()
             if flag != CERT_BYPASS_FLAG]
    if enabled:
        flags.append(CERT_BYPASS_FLAG)
    if flags:
        os.environ[CHROMIUM_FLAGS_ENV] = " ".join(flags)
    else:
        os.environ.pop(CHROMIUM_FLAGS_ENV, None)


#: Whether this process owes the user a fresh copy of the app; set by a
#: restart request and taken by ``berry_manager`` once everything is closed.
_RESTART = {"wanted": False}


def restart_process() -> bool:
    """Ask for the app to relaunch; the fresh copy only starts once this one
    has closed, so the two never share the config, the addons or the
    unpacked build folder at the same time (see :func:`relaunch_process`).
    """
    _RESTART["wanted"] = True
    return True


def cancel_restart_request() -> None:
    """Withdraw a restart request: the user answered an abort question with
    cancel, so the app stays open and must not relaunch when it closes later
    for an unrelated reason."""
    _RESTART["wanted"] = False


def take_restart_request() -> bool:
    """True once, when a restart was asked for; the entry point calls this
    after the event loop ends and relaunches instead of exiting."""
    wanted = _RESTART["wanted"]
    _RESTART["wanted"] = False
    return wanted


def relaunch_process() -> bool:
    """Start a detached fresh copy right now, with the dying process's own
    folders out of its search path; call it only when teardown is finished."""
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, sys.argv[1:]
    else:
        program = sys.executable
        first = os.path.abspath(sys.argv[0])       # cwd may not be the app's
        arguments = [first, *sys.argv[1:]]
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True,
                    "env": clean_child_env()}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([program, *arguments], **kwargs)
    except OSError:
        return False
    return True


def replaced_program_path() -> Path:
    """Where the update addon parks the old copy of this program when it
    swaps in a new one: beside the running file, under the same name plus
    ``.old``. Only a frozen build has such a file; a checkout is updated by
    replacing its source, not by moving the interpreter aside."""
    if not getattr(sys, "frozen", False):
        raise ValueError("only a compiled build replaces its own program")
    return Path(sys.executable).with_name(Path(sys.executable).name + ".old")


def clear_replaced_program() -> None:
    """Take away the old copy an update left behind, on the way up.

    Windows will not delete a program while it runs, so the swap moves it
    aside under a name nobody is holding and this fresh copy - the first one
    started from the new file - finishes the deletion here. A leftover that
    still refuses is worth a shrug, not a dialog: it is one dead file beside
    a program that has already started.
    """
    try:
        leftover = replaced_program_path()
    except ValueError:
        return
    try:
        if leftover.is_file():
            leftover.unlink()
    except OSError:
        pass


if TYPE_CHECKING:
    from manager_gui import BerryManager


if QWebEnginePage is not None:
    class WebTabPage(QWebEnginePage):
        """A page that lets a click wanting its own tab or window - a
        ``target=_blank`` link, a script's ``window.open`` - get a real,
        closable tab; the plain view would swallow the request in silence."""

        def __init__(self, browser: "BrowserMixin") -> None:
            super().__init__()
            self._browser = browser

        def createWindow(self, feature):        # noqa: ARG002 - one home for every kind
            try:
                return self._browser.open_requested_tab()
            except Exception:
                return None
else:
    WebTabPage = None


class BrowserMixin:
    """Embedded browser tabs, mixed into the main window; its attributes come from ``manager_gui.BerryManager``."""

    def _build_web_like_tab(self, initial_url: str, title: str,
                            position: int | None = None) -> tuple[QLineEdit, QWebEngineView | None, QWidget]:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        url_input = QLineEdit(initial_url)
        go_button = QPushButton("Go / Refresh")
        go_button.setObjectName("primaryAction")
        home_button = QPushButton("Home")
        controls.addWidget(url_input, 1)
        controls.addWidget(go_button)
        controls.addWidget(home_button)
        layout.addLayout(controls)

        # On a failed load, two exits: the system browser, or the
        # certificate bypass.
        error_bar, error_label, open_btn, accept_btn = self._build_load_error_bar()
        layout.addWidget(error_bar)

        def add_tab() -> int:
            if position is None:
                return self.tabs.addTab(page, title)
            return self.tabs.insertTab(position, page, title)

        if QWebEngineView is None:
            notice = QLabel(
                "Embedded browser is unavailable.\n"
                "Install PyQt6-WebEngine to view this page inside the app, "
                "or open the URL in your system browser:\n\n" + initial_url
            )
            notice.setWordWrap(True)
            notice.setStyleSheet("color: #ffb020; padding: 12px;")
            layout.addWidget(notice, 1)
            open_btn = QPushButton("Open in browser")
            open_btn.clicked.connect(lambda: os.startfile(url_input.text()) if hasattr(os, "startfile") else None)
            layout.addWidget(open_btn)
            go_button.clicked.connect(lambda: None)
            add_tab()
            return url_input, None, page

        view = QWebEngineView()
        if WebTabPage is not None:
            view.setPage(WebTabPage(self))
        add_tab()

        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(view.url()))

        def load_url() -> None:
            view.setUrl(QUrl(url_input.text()))

        def go_home() -> None:
            url_input.setText(web_tab_url(self._tab_def_for(url_input), self.app_config.get("host", "192.168.1.1")))
            load_url()

        go_button.clicked.connect(load_url)
        home_button.clicked.connect(go_home)
        url_input.returnPressed.connect(load_url)

        def apply_tab_icon(icon: QIcon) -> None:
            if not icon.isNull():
                idx = self.tabs.indexOf(page)
                if idx >= 0:
                    self.tabs.setTabIcon(idx, icon)

        view.page().iconChanged.connect(apply_tab_icon)

        def hide_error_bar(_: object = None) -> None:
            error_bar.setVisible(False)

        def report_failure(ok: bool) -> None:
            if ok:
                hide_error_bar()
                return
            target = view.url().toString()
            # With the bypass on the user already accepts what the device
            # serves, so a leftover https failure is not worth a banner;
            # most often nothing listens for https there; log it, quiet.
            if target.startswith("https:") and self._cert_bypass_session:
                self.log(f"Web tab could not load {target} (certificate errors are ignored)")
                return
            error_label.setText(self._load_failure_message(target) + "\n" + target)
            # Offer the bypass only while it is still switched off.
            accept_btn.setVisible(target.startswith("https:")
                                  and not bool(self.app_config.get("ignore_cert_errors")))
            error_bar.setVisible(True)
            self.log(f"Web tab could not load {target}")

        view.loadStarted.connect(hide_error_bar)
        view.loadFinished.connect(report_failure)

        layout.addWidget(view, 1)
        return url_input, view, page

    def _build_load_error_bar(self) -> tuple[QWidget, QLabel, QPushButton, QPushButton]:
        """The hidden notice row under a web tab's address bar: label plus open/accept buttons."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)
        label = QLabel()
        label.setWordWrap(True)
        label.setStyleSheet("color: #ffb020;")
        open_btn = QPushButton("Open in system browser")
        accept_btn = QPushButton("Accept and restart")
        accept_btn.setToolTip(
            "Turn on 'Accept untrusted HTTPS certificates' from Settings > Custom tabs "
            "and restart, so the embedded browser loads pages like this one anyway."
        )
        accept_btn.clicked.connect(self.request_cert_bypass)
        row.addWidget(label, 1)
        row.addWidget(open_btn)
        row.addWidget(accept_btn)
        bar.setVisible(False)
        return bar, label, open_btn, accept_btn

    def _load_failure_message(self, url: str) -> str:
        """Explain a failed load, pointing at the certificate switch when an https page was blocked with the bypass off."""
        if not url.startswith("https:"):
            return ("The page could not be loaded. Check the address and port, "
                    "and that the device is reachable.")
        if self._cert_bypass_session:
            return ("This https page failed even though certificate errors are ignored, "
                    "so the server is most likely down or unreachable.")
        if bool(self.app_config.get("ignore_cert_errors")):
            return ("Certificate errors are saved as ignored, but this session started "
                    "before that; restart the app to apply it.")
        return (
            "The embedded browser does not trust this certificate. It only accepts a chain "
            "whose root is installed in the Windows certificate store under "
            "'Trusted Root Certification Authorities' - trusting a certificate inside "
            "another browser does not carry over. Accept the certificate here to bypass "
            "the check in every web tab, or open the page in your own browser."
        )

    def _tab_def_for(self, url_input: QLineEdit) -> dict:
        """The stored tab definition owning this url input - configurable or transient."""
        for entry in list(getattr(self, "_web_tab_entries", [])) + list(
                getattr(self, "_transient_tab_entries", [])):
            if entry["url_input"] is url_input:
                return entry["tab"]
        return {}

    def build_web_tabs(self) -> None:
        """Create one browser tab per entry in config['web_tabs']."""
        default_host = self.app_config.get("host", "192.168.1.1")
        self._web_tab_entries = []
        for tab in self.app_config.get("web_tabs", []):
            self._add_web_tab(tab, default_host)

    def _add_web_tab(self, tab: dict, default_host: str, position: int | None = None) -> None:
        url = web_tab_url(tab, default_host)
        url_input, view, page = self._build_web_like_tab(url, tab.get("name", "Tab"), position)
        self._web_tab_entries.append({"page": page, "url_input": url_input, "view": view, "tab": tab})
        if view is not None:
            QTimer.singleShot(200, lambda v=view, u=url_input: v.setUrl(QUrl(u.text())))

    def rebuild_web_tabs(self) -> None:
        """Recreate every configurable tab from the config, kept in front of Settings; addon tabs are untouched."""
        for entry in getattr(self, "_web_tab_entries", []):
            idx = self.tabs.indexOf(entry["page"])
            if idx >= 0:
                self.tabs.removeTab(idx)
                entry["page"].deleteLater()
        self._web_tab_entries = []
        default_host = self.app_config.get("host", "192.168.1.1")
        # Rebuild left of the pinned gap + Settings pair so the
        # configurable tabs stay among the reorderable ones (never
        # detached).
        position = self._content_insert_index()
        for tab in self.app_config.get("web_tabs", []):
            self._add_web_tab(tab, default_host, position)
            position += 1

    def refresh_web_tab_urls(self) -> None:
        """Re-point host-following tabs (configurable and addon) at the active host, no reload; fixed-address tabs are left alone."""
        default_host = self.app_config.get("host", "192.168.1.1")
        for entry in list(getattr(self, "_web_tab_entries", [])) + list(
                getattr(self, "_transient_tab_entries", [])):
            if entry.get("popup"):
                continue          # a page's own tab follows its page, not the host
            if not entry["tab"].get("ip"):
                entry["url_input"].setText(web_tab_url(entry["tab"], default_host))

    def add_transient_web_tab(self, tab: dict, position: int | None = None,
                              on_closed: Callable[[], None] | None = None) -> dict:
        """Open a browser tab that belongs to no profile (addons use it): not saved, not listed, not rebuilt. It wears an X for the user to close, and whoever opened it is told when that happens."""
        default_host = self.app_config.get("host", "192.168.1.1")
        url = web_tab_url(tab, tab.get("ip") or default_host)
        url_input, view, page = self._build_web_like_tab(url, tab.get("name", "Tab"), position)
        entry = {"page": page, "url_input": url_input, "view": view, "tab": tab}
        if callable(on_closed):
            entry["on_closed"] = on_closed
        self._transient_tab_entries.append(entry)
        self._mark_tab_closable(page)
        if view is not None:
            QTimer.singleShot(200, lambda v=view, u=url_input: v.setUrl(QUrl(u.text())))
        return entry

    def remove_transient_tab(self, page: QWidget) -> None:
        """Forget a transient tab; whoever made it removes the tab itself."""
        entries = getattr(self, "_transient_tab_entries", [])
        entries[:] = [entry for entry in entries if entry["page"] is not page]

    def _mark_tab_closable(self, page: QWidget) -> None:
        """Put an X on one tab. Only pages an addon opened, or a page opened
        itself, ever get one; the profile's own tabs and the app's tabs stay
        buttonless, because closing them by hand would only confuse."""
        bar = self.tabs.tabBar()
        index = self.tabs.indexOf(page)
        if index < 0:
            return
        button = QToolButton(bar)
        button.setObjectName("tabCloseButton")     # the dot sync spares this name
        button.setText("\u2715")
        button.setAutoRaise(True)
        button.setFixedSize(zoom_size(16), zoom_size(16))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Close this tab")
        button.setStyleSheet(
            "QToolButton { border: none; background: transparent; color: #9a9a9a; }"
            "QToolButton:hover { color: #ff6b6b; }")
        button.clicked.connect(
            lambda _=False, p=page: self.close_transient_web_tab(p))
        bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
        if hasattr(bar, "refit"):
            bar.refit()

    def close_transient_web_tab(self, page: QWidget) -> bool:
        """Shut one closable tab for good: stop its loading, take the tab out,
        and tell whoever opened it so their own list matches the window."""
        entries = getattr(self, "_transient_tab_entries", [])
        entry = next((row for row in entries if row["page"] is page), None)
        if entry is None:
            return False
        index = self.tabs.indexOf(page)
        view = entry.get("view")
        if view is not None:
            try:
                view.stop()
            except RuntimeError:
                pass
        entries.remove(entry)
        name = str(entry["tab"].get("name", "Tab"))
        if index >= 0:
            self.tabs.removeTab(index)
        page.deleteLater()
        callback = entry.get("on_closed")
        if callable(callback):
            try:
                callback()
            except Exception:
                pass
        sync = getattr(self, "sync_tab_transport_dots", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                pass
        self.log(f"Closed tab '{name}'")
        return True

    def open_requested_tab(self):
        """A page inside a web tab asked to open a new tab or window: give it
        its own closable tab, let the site name it once the title arrives, and
        hand back the new page so the request actually lands there."""
        url_input, view, page = self._build_web_like_tab(
            "", "New page", self._content_insert_index())
        entry = {"page": page, "url_input": url_input, "view": view,
                 "tab": {"name": "New page"}, "popup": True}
        self._transient_tab_entries.append(entry)
        self._mark_tab_closable(page)
        if view is not None:
            view.titleChanged.connect(
                lambda title, p=page: self._name_tab_after_site(p, title))
            view.urlChanged.connect(
                lambda _url, v=view, u=url_input: u.setText(v.url().toString()))
        return None if view is None else view.page()

    def _name_tab_after_site(self, page: QWidget, title: str) -> None:
        """A page-opened tab wears the site's own name once the site offers one."""
        text = str(title or "").strip()
        if not text:
            return
        for entry in getattr(self, "_transient_tab_entries", []):
            if entry["page"] is page:
                entry["tab"]["name"] = text
                break
        index = self.tabs.indexOf(page)
        if index >= 0:
            self.tabs.setTabText(index, text)

    def _update_cert_bypass_state(self) -> None:
        """Say whether the saved switch is the one the browser actually runs with."""
        desired = bool(self.app_config.get("ignore_cert_errors"))
        if desired == self._cert_bypass_session:
            text = ("Certificate errors are ignored in this session."
                    if desired else "Every web tab verifies its certificate in this session.")
        else:
            text = "Restart needed: the embedded browser still uses the previous setting."
        self.cert_bypass_state_label.setText(text)

    def _sync_cert_bypass(self, ask_restart: bool = True) -> None:
        """Push the saved cert switch into the Chromium flags; offer a restart if the running browser differs."""
        desired = bool(self.app_config.get("ignore_cert_errors"))
        set_cert_bypass(desired)
        if desired == self._cert_bypass_session:
            self._update_cert_bypass_state()
            return
        if not ask_restart:
            self._update_cert_bypass_state()
            self.log("Certificate setting stored; restart the app to apply it")
            return
        answer = QMessageBox.question(
            self, "Restart Berry Manager?",
            "The embedded browser reads the certificate setting when it starts.\n\n"
            "Restart now so the change takes effect?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restart_for_cert_bypass()
        else:
            self._update_cert_bypass_state()
            self.log("Certificate setting stored; it applies after the next start")

    def restart_for_cert_bypass(self) -> None:
        """Close this copy and have a fresh one start, so QtWebEngine reads
        the new flags; the relaunch itself waits until the process ends."""
        self.log("Restarting to apply the certificate setting")
        restart_process()
        if self.close():
            QApplication.instance().quit()
        else:
            # An abort question was answered "cancel": the window stays up,
            # so nothing may be quit and no relaunch may be owed.
            cancel_restart_request()

    def request_cert_bypass(self) -> None:
        """Turn the bypass on from a blocked web tab and offer to restart."""
        self.app_config["ignore_cert_errors"] = True
        self.cert_bypass_check.setChecked(True)
        self.save_app_config()
        self.log("Enabled 'Accept untrusted HTTPS certificates'")
        self._sync_cert_bypass()

    def _reload_tabs_combo(self, select: str | None = None) -> None:
        """Fill the Settings 'Tabs' dropdown from the configurable tab list."""
        self.tabs_combo.blockSignals(True)
        self.tabs_combo.clear()
        for tab in self.app_config.get("web_tabs", []):
            self.tabs_combo.addItem(tab.get("name", ""))
        if select:
            idx = self.tabs_combo.findText(select)
            if idx >= 0:
                self.tabs_combo.setCurrentIndex(idx)
        self.tabs_combo.blockSignals(False)

    def add_web_tab(self) -> None:
        dialog = TabEditDialog(
            self, {"name": "", "ip": "", "port": 80, "path": "/", "scheme": "http"}, "Add tab"
        )
        if not dialog.exec():
            return
        tab = dialog.values()
        if not tab["name"]:
            QMessageBox.warning(self, "Missing name", "A display name is required.")
            return
        self.app_config.setdefault("web_tabs", []).append(tab)
        self.save_app_config()
        self._reload_tabs_combo(select=tab["name"])
        self.rebuild_web_tabs()
        self._reload_default_tab_combo()
        self.log(f"Added tab '{tab['name']}'")

    def edit_web_tab(self) -> None:
        index = self.tabs_combo.currentIndex()
        if index < 0:
            return
        dialog = TabEditDialog(self, self.app_config["web_tabs"][index], "Edit tab")
        if not dialog.exec():
            return
        tab = dialog.values()
        if not tab["name"]:
            QMessageBox.warning(self, "Missing name", "A display name is required.")
            return
        self.app_config["web_tabs"][index] = tab
        self.save_app_config()
        self._reload_tabs_combo(select=tab["name"])
        self.rebuild_web_tabs()
        self._reload_default_tab_combo()
        self.log(f"Updated tab '{tab['name']}'")

    def remove_web_tab(self) -> None:
        index = self.tabs_combo.currentIndex()
        if index < 0:
            return
        name = self.tabs_combo.currentText()
        answer = QMessageBox.question(
            self, "Remove tab",
            f"Remove the '{name}' tab? (This only hides it; the device is untouched.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.app_config["web_tabs"].pop(index)
        self.save_app_config()
        self._reload_tabs_combo()
        self.rebuild_web_tabs()
        self._reload_default_tab_combo()
        self.log(f"Removed tab '{name}'")
