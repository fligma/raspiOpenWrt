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
import sys
from typing import TYPE_CHECKING

from PyQt6.QtCore import QProcess, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manager_config import web_tab_url
from manager_widgets import TabEditDialog

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None


CERT_BYPASS_FLAG = "--ignore-certificate-errors"
CHROMIUM_FLAGS_ENV = "QTWEBENGINE_CHROMIUM_FLAGS"


def cert_bypass_active() -> bool:
    """Whether this process started the embedded browser with the bypass on."""
    return CERT_BYPASS_FLAG in os.environ.get(CHROMIUM_FLAGS_ENV, "").split()


def set_cert_bypass(enabled: bool) -> None:
    """Put or remove the bypass switch in the Chromium flags, keeping any other
    flags the user already set. Only affects a browser process that has not
    started yet, so call it before the first web view is created."""
    flags = [flag for flag in os.environ.get(CHROMIUM_FLAGS_ENV, "").split()
             if flag != CERT_BYPASS_FLAG]
    if enabled:
        flags.append(CERT_BYPASS_FLAG)
    if flags:
        os.environ[CHROMIUM_FLAGS_ENV] = " ".join(flags)
    else:
        os.environ.pop(CHROMIUM_FLAGS_ENV, None)


def restart_process() -> bool:
    """Launch a fresh copy of the program without waiting for it.

    Works for both a source run (interpreter plus script arguments) and a
    frozen PyInstaller build (the executable itself). Returns whether the new
    process could be started; the caller is expected to close this one.
    """
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, sys.argv[1:]
    else:
        program, arguments = sys.executable, sys.argv
    return QProcess.startDetached(program, arguments)


if TYPE_CHECKING:
    from manager_gui import RasconfManager


class BrowserMixin:
    """Embedded browser tabs, mixed into the main window.

    Every method here runs on the main window; the attributes it uses are set up
    by :class:`manager_gui.RasconfManager`, the class that mixes this in.
    """

    def _build_web_like_tab(self, initial_url: str, title: str, position: int | None = None):
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

        # Shown when a load fails, with the two ways out of a certificate block:
        # the system browser, or turning on the certificate bypass.
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
        add_tab()

        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(view.url()))

        def load_url():
            view.setUrl(QUrl(url_input.text()))

        def go_home():
            url_input.setText(web_tab_url(self._tab_def_for(url_input), self.app_config.get("host", "192.168.1.1")))
            load_url()

        go_button.clicked.connect(load_url)
        home_button.clicked.connect(go_home)
        url_input.returnPressed.connect(load_url)

        def apply_tab_icon(icon):
            if not icon.isNull():
                idx = self.tabs.indexOf(page)
                if idx >= 0:
                    self.tabs.setTabIcon(idx, icon)

        view.page().iconChanged.connect(apply_tab_icon)

        def hide_error_bar(_=None):
            error_bar.setVisible(False)

        def report_failure(ok: bool) -> None:
            if ok:
                hide_error_bar()
                return
            target = view.url().toString()
            # With the certificate bypass already running, the user has chosen to
            # accept whatever the device serves, so a remaining https failure is
            # not a trust problem worth a banner - most often nothing simply
            # listens for https on that address. Log it and leave the page quiet.
            if target.startswith("https:") and self._cert_bypass_session:
                self.log(f"Web tab could not load {target} (certificate errors are ignored)")
                return
            error_label.setText(self._load_failure_message(target) + "\n" + target)
            # Offering the bypass only makes sense while it is still switched off.
            accept_btn.setVisible(target.startswith("https:")
                                  and not bool(self.app_config.get("ignore_cert_errors")))
            error_bar.setVisible(True)
            self.log(f"Web tab could not load {target}")

        view.loadStarted.connect(hide_error_bar)
        view.loadFinished.connect(report_failure)

        layout.addWidget(view, 1)
        return url_input, view, page

    def _build_load_error_bar(self) -> tuple[QWidget, QLabel, QPushButton, QPushButton]:
        """The hidden notice row under a web tab's address bar, with the label
        and the two buttons: open externally, or accept the certificate."""
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
        """Explain a failed page load, pointing at the certificate switch when
        an https page was blocked while the bypass is off."""
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
        """Look up the stored tab definition that owns the given url input -
        a configurable one or a transient tab an addon opened."""
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
        """Recreate every configurable tab from the current config, keeping them
        in front of the Settings tab. Used after adding, editing or removing one.
        Addon tabs are not touched, so they end up behind the configurable ones
        again rather than being thrown away."""
        for entry in getattr(self, "_web_tab_entries", []):
            idx = self.tabs.indexOf(entry["page"])
            if idx >= 0:
                self.tabs.removeTab(idx)
                entry["page"].deleteLater()
        self._web_tab_entries = []
        default_host = self.app_config.get("host", "192.168.1.1")
        # Rebuild just to the left of the pinned gap + Settings pair so the
        # configurable tabs stay among the reorderable tabs (never detached).
        position = self._content_insert_index()
        for tab in self.app_config.get("web_tabs", []):
            self._add_web_tab(tab, default_host, position)
            position += 1

    def refresh_web_tab_urls(self) -> None:
        """Re-point host-following tabs at the active profile host without a reload.
        Addon tabs that follow the host are re-pointed too; one with a fixed
        address is left exactly where its addon put it."""
        default_host = self.app_config.get("host", "192.168.1.1")
        for entry in list(getattr(self, "_web_tab_entries", [])) + list(
                getattr(self, "_transient_tab_entries", [])):
            if not entry["tab"].get("ip"):
                entry["url_input"].setText(web_tab_url(entry["tab"], default_host))

    def add_transient_web_tab(self, tab: dict, position: int | None = None) -> dict:
        """Open a browser tab that belongs to no profile.

        Addons use this: the tab is not written to config.json, never listed in
        Settings > Custom tabs and is not rebuilt when the profile changes - it
        stays up until whoever created it closes it.
        """
        default_host = self.app_config.get("host", "192.168.1.1")
        url = web_tab_url(tab, tab.get("ip") or default_host)
        url_input, view, page = self._build_web_like_tab(url, tab.get("name", "Tab"), position)
        entry = {"page": page, "url_input": url_input, "view": view, "tab": tab}
        self._transient_tab_entries.append(entry)
        if view is not None:
            QTimer.singleShot(200, lambda v=view, u=url_input: v.setUrl(QUrl(u.text())))
        return entry

    def remove_transient_tab(self, page: QWidget) -> None:
        """Forget a transient tab; whoever made it removes the tab itself."""
        entries = getattr(self, "_transient_tab_entries", [])
        entries[:] = [entry for entry in entries if entry["page"] is not page]

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
        """Push the saved certificate switch into the Chromium flags and, when the
        running browser started with the opposite value, offer a restart."""
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
            self, "Restart rasconf Manager?",
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
        """Relaunch the program so QtWebEngine starts with the new flags."""
        if not restart_process():
            QMessageBox.warning(
                self, "Restart failed",
                "A new instance could not be started. Close the app and open it again.",
            )
            return
        self.log("Restarting to apply the certificate setting")
        self.close()
        QApplication.instance().quit()

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
