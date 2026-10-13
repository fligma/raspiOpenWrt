#!/usr/bin/env python3

"""The main window: menu, tab shell, connection panel, Settings tab, profiles and shutdown."""


from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import posixpath
import threading
from typing import Any

from PyQt6.QtCore import QEvent, QObject, QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QPixmap, QResizeEvent, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
import keyring

from manager_addons import AddonManager
from manager_addondev import AddonDevWidget
from manager_browser import BrowserMixin, cert_bypass_active, set_cert_bypass
from manager_config import (
    active_profile_name,
    create_profile,
    delete_profile,
    export_document,
    import_app_config,
    load_app_config,
    load_command_history,
    profile_names,
    rename_profile,
    save_app_config_static,
    set_active_profile,
)
from manager_const import (
    APP_STYLESHEET,
    BASE_DIR,
    DEFAULT_PROFILE,
    ICON_FILES,
    KEYRING_SERVICE,
    LOG_FILE,
    PROJECT_GITHUB_URL,
    PROJECT_LICENSE_URL,
    UI_ZOOM_DEFAULT,
    find_asset,
    scaled_sheet,
    set_zoom,
    step_zoom,
    zoom_size,
)
from manager_files import FileViewsMixin
from manager_net import (
    RebootWaitWorker,
    RemoteExecWorker,
    SSHShellWorker,
    SharedConnection,
    SharedSessionWorker,
    credential_id,
)
from manager_terminal import TerminalMixin
from manager_widgets import (
    CollapseGroup,
    RightPinnedTabBar,
    RightPinnedTabWidget,
    TabTransportDots,
    ToggleGroup,
    ToggleSwitch,
    settings_tab_icon,
    tab_caption,
    tip_on_title,
)


class BerryManager(
    BrowserMixin,
    FileViewsMixin,
    TerminalMixin,
    QMainWindow
):
    """The application window: menu, tab shell, connection panel, log and shutdown.

    The device tabs live in their own modules - manager_browser and
    manager_terminal - and are mixed in here, along with the lightweight SFTP
    connection in manager_files (Berry, a bundled addon, provides the file tab).
    Everything the window itself owns stays in this file: the menu, the Settings
    tab, the profiles and the actions that open a dialog. The reusable widgets
    and popups are in manager_widgets, and the addons a profile runs are handled
    by the AddonManager in manager_addons.
    """

    def __init__(self) -> None:
        super().__init__()
        self.worker: SharedSessionWorker | None = None
        self.shell_worker: SSHShellWorker | None = None
        self.exec_worker: RemoteExecWorker | None = None
        self.reboot_worker: RebootWaitWorker | None = None
        self.shared_conn = SharedConnection()
        self.app_config = load_app_config()
        self.addons = AddonManager(self)
        self._cert_bypass_session = cert_bypass_active()
        self.current_term_color = "#e0e0e0"
        self.term_partial = ""
        self.term_cells: list[tuple[str, str]] = []
        self.term_pen = 0
        self.term_saved_pen = 0
        self.term_line_doc_pos = 0
        self.terminal_history: list[str] = load_command_history()
        self.terminal_history_pos = len(self.terminal_history)
        self.connected_once = False
        self.sftp_connected = False
        self.setWindowTitle("Berry Manager")
        self.resize(1180, 780)

        self.build_menu()
        self.build_ui()
        self.apply_runtime_settings()
        self.apply_window_icon()

        self.addons.reload()
        self.addons.start_done()
        self.sync_tab_transport_dots()
        self._natural_tab_order = self.capture_tab_order()
        self._apply_remembered_order()
        self._select_default_tab()

        want_sftp_auto = bool(self.app_config.get("auto_connect_sftp")) and self.app_config.get("sftp_enabled", True)
        want_ssh_auto = bool(self.app_config.get("auto_connect_ssh")) and self.app_config.get("ssh_enabled", True)
        if self.connections_enabled() and (want_sftp_auto or want_ssh_auto):
            if want_sftp_auto:
                QTimer.singleShot(400, self.connect_remote)
            if want_ssh_auto:
                QTimer.singleShot(400, self.connect_shell)

    def build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self.action_new_profile = QAction("New profile...", self)
        self.action_new_profile.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.action_new_profile.triggered.connect(self.new_profile)
        file_menu.addAction(self.action_new_profile)

        self.profile_menu = QMenu("Load profile", self)
        self.profile_menu.aboutToShow.connect(self.refresh_profile_menu)
        file_menu.addMenu(self.profile_menu)

        self.action_rename_profile = QAction("Rename current profile...", self)
        self.action_rename_profile.triggered.connect(self.rename_current_profile)
        file_menu.addAction(self.action_rename_profile)

        self.action_delete_profile = QAction("Delete current profile...", self)
        self.action_delete_profile.triggered.connect(self.delete_current_profile)
        file_menu.addAction(self.action_delete_profile)

        file_menu.addSeparator()
        self.action_export_config = QAction("Export configuration...", self)
        self.action_export_config.triggered.connect(self.export_config)
        file_menu.addAction(self.action_export_config)

        self.action_import_config = QAction("Import configuration...", self)
        self.action_import_config.triggered.connect(self.import_config)
        file_menu.addAction(self.action_import_config)

        file_menu.addSeparator()
        self.action_save_log = QAction("Save log to file...", self)
        self.action_save_log.triggered.connect(self.save_log_to_file)
        file_menu.addAction(self.action_save_log)

        file_menu.addSeparator()
        self.action_quit = QAction("Quit", self)
        self.action_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.action_quit.triggered.connect(self.close)
        file_menu.addAction(self.action_quit)

        edit_menu = menubar.addMenu("&Edit")
        self.action_clear_log = QAction("Clear log", self)
        self.action_clear_log.triggered.connect(lambda: self.log_output.clear())
        edit_menu.addAction(self.action_clear_log)
        self.action_clear_terminal = QAction("Clear terminal", self)
        self.action_clear_terminal.setShortcut(QKeySequence("Ctrl+L"))
        self.action_clear_terminal.triggered.connect(self.clear_terminal)
        edit_menu.addAction(self.action_clear_terminal)
        self.action_clear_history = QAction("Clear command history", self)
        self.action_clear_history.triggered.connect(self.clear_command_history)
        edit_menu.addAction(self.action_clear_history)

        view_menu = menubar.addMenu("&View")
        self.action_zoom_in = QAction("Zoom in", self)
        self.action_zoom_in.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.action_zoom_in.setToolTip(
            "Draw the whole interface one step larger")
        self.action_zoom_in.triggered.connect(self.zoom_in)
        view_menu.addAction(self.action_zoom_in)
        self.action_zoom_out = QAction("Zoom out", self)
        self.action_zoom_out.setShortcuts(
            [QKeySequence("Ctrl+-"), QKeySequence("Ctrl+_")])
        self.action_zoom_out.setToolTip(
            "Draw the whole interface one step smaller")
        self.action_zoom_out.triggered.connect(self.zoom_out)
        view_menu.addAction(self.action_zoom_out)
        self.action_zoom_reset = QAction("Reset zoom", self)
        self.action_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.action_zoom_reset.setToolTip(
            f"Back to {UI_ZOOM_DEFAULT}%")
        self.action_zoom_reset.triggered.connect(self.reset_zoom)
        view_menu.addAction(self.action_zoom_reset)
        view_menu.addSeparator()
        self.action_log_console = QAction("Show log console", self)
        self.action_log_console.setCheckable(True)
        self.action_log_console.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.action_log_console.setToolTip(
            "Hide or show the log pane at the bottom of the window. Logging goes "
            "on while it is out of sight, so nothing is missed.")
        self.action_log_console.setChecked(
            bool(self.app_config.get("log_visible", True)))
        self.action_log_console.toggled.connect(self.toggle_log_console)
        view_menu.addAction(self.action_log_console)
        view_menu.addSeparator()
        self.action_fullscreen = QAction("Fullscreen", self)
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.setShortcut(QKeySequence("F11"))
        self.action_fullscreen.toggled.connect(self.set_fullscreen)
        view_menu.addAction(self.action_fullscreen)
        view_menu.addSeparator()
        self.action_alt_icon = QAction("Alternate icon", self)
        self.action_alt_icon.setCheckable(True)
        self.action_alt_icon.setChecked(bool(self.app_config.get("use_alt_icon", False)))
        self.action_alt_icon.setToolTip("Switch between the default and alternate toolbar icon")
        self.action_alt_icon.toggled.connect(self.toggle_window_icon)
        view_menu.addAction(self.action_alt_icon)

        tools_menu = menubar.addMenu("&Tools")
        self.action_edit_quick = QAction("Edit quick commands...", self)
        self.action_edit_quick.triggered.connect(self.edit_quick_commands)
        tools_menu.addAction(self.action_edit_quick)
        tools_menu.addSeparator()
        self.action_addon_dev = QAction("Addon Dev", self)
        self.action_addon_dev.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.action_addon_dev.triggered.connect(self.show_addon_dev)
        tools_menu.addAction(self.action_addon_dev)
        self.action_addon_dev.setVisible(self.addon_dev_wanted())

        help_menu = menubar.addMenu("&Help")
        self.action_about = QAction("About", self)
        self.action_about.triggered.connect(self.show_about)
        help_menu.addAction(self.action_about)

    def build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        connection_group = ToggleGroup(
            "Connection", bool(self.app_config.get("master_connection", True)),
            body="box",
            tip="The master switch. Off closes every connection and stops any "
                "addon from connecting, with the app's session or its own; on "
                "lets the Connect buttons and the addons work again.",
        )
        credentials = QWidget()
        connection_layout = QGridLayout(credentials)
        connection_layout.setHorizontalSpacing(10)
        connection_layout.setVerticalSpacing(8)
        self.host_input = QLineEdit(str(self.app_config["host"]))
        self.host_input.setPlaceholderText("IP or hostname, e.g. 192.168.1.1")
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.app_config["port"])
        self.username_input = QLineEdit(str(self.app_config["username"]))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Optional when using an SSH key")
        self.show_password_toggle = QToolButton()
        self.show_password_toggle.setText("Show")
        self.show_password_toggle.setCheckable(True)
        self.show_password_toggle.toggled.connect(
            lambda on: self.password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        self.remember_password_checkbox = QCheckBox("Remember password securely")
        self.remember_password_checkbox.setChecked(bool(self.app_config.get("remember_password", False)))
        self.remember_password_checkbox.setToolTip(
            "Stores the password in the operating system credential store, not in config.json."
        )
        self.key_input = QLineEdit(str(self.app_config["key_file"]))
        self.key_input.setPlaceholderText("Optional private key file")
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(self.choose_key_file)
        self.remote_root_input = QLineEdit(str(self.app_config["remote_root"]))
        self.remote_root_input.setPlaceholderText("/www_rasconf")
        self.trust_host_checkbox = QCheckBox("Trust and save unknown host key on first connection")
        self.trust_host_checkbox.setChecked(bool(self.app_config["trust_unknown_host"]))
        self.trust_host_checkbox.setToolTip(
            "Leave unchecked for strict known-host verification. Enable only when you have verified the device's identity."
        )
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(3, 300)
        self.timeout_input.setValue(int(self.app_config.get("ssh_timeout", 12)))
        self.timeout_input.setSuffix(" s")
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primaryAction")
        self.connect_button.clicked.connect(self.connect_remote)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("dangerAction")
        self.disconnect_button.setToolTip("Close the session and disable remote actions until you reconnect")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_remote)

        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password_input, 1)
        pw_row.addWidget(self.show_password_toggle)

        connection_layout.addWidget(QLabel("Host"), 0, 0)
        connection_layout.addWidget(self.host_input, 0, 1, 1, 2)
        connection_layout.addWidget(QLabel("Port"), 0, 2)
        connection_layout.addWidget(self.port_input, 0, 3)
        connection_layout.addWidget(QLabel("Username"), 1, 0)
        connection_layout.addWidget(self.username_input, 1, 1, 1, 2)
        connection_layout.addWidget(QLabel("Password"), 1, 2)
        connection_layout.addLayout(pw_row, 1, 3)
        connection_layout.addWidget(self.remember_password_checkbox, 2, 0, 1, 4)
        connection_layout.addWidget(QLabel("Private key"), 3, 0)
        connection_layout.addWidget(self.key_input, 3, 1, 1, 2)
        connection_layout.addWidget(key_browse, 3, 3)
        self.remote_dir_label = QLabel("Remote dir")
        self.remote_dir_label.setToolTip("Folder Berry opens on and browses. SSH does not use it.")
        connection_layout.addWidget(self.remote_dir_label, 4, 0)
        connection_layout.addWidget(self.remote_root_input, 4, 1, 1, 2)
        connection_layout.addWidget(QLabel("Timeout"), 4, 2)
        connection_layout.addWidget(self.timeout_input, 4, 3)
        conn_actions = QHBoxLayout()
        conn_actions.addWidget(self.connect_button)
        conn_actions.addWidget(self.disconnect_button)
        connection_layout.addWidget(self.trust_host_checkbox, 5, 0, 1, 2)
        connection_layout.addLayout(conn_actions, 5, 2, 1, 2)
        connection_group.body_layout.addWidget(credentials)

        connection_group.switch.toggled.connect(self.on_master_connection_toggled)

        self.connection_group = connection_group

        self.tabs = RightPinnedTabWidget()

        self.tabs.setTabBar(RightPinnedTabBar())
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._order_tab_bar = self.tabs.tabBar()
        self._order_tab_bar.installEventFilter(self)

        self.tabs.tabCountChanged.connect(self._on_tab_count_changed)
        root_layout.addWidget(self.tabs, 1)

        self.build_ssh_tab()
        self._web_tab_entries = []
        self._transient_tab_entries = []
        self.build_web_tabs()
        self.build_settings_tab()

        self.addon_dev = None
        self.ensure_addon_dev_tab()

        self._gap_page = QWidget()
        self.tabs.addTab(self._gap_page, "")
        self.tabs.tabBar().setTabEnabled(self.tabs.indexOf(self._gap_page), False)
        self._sync_pinned_settings()
        self._stretch_tab_bar()

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(130)
        root_layout.addWidget(self.log_output)
        self.apply_log_console_visibility()

        for field in (
            self.host_input,
            self.username_input,
            self.key_input,
            self.remote_root_input,
        ):
            field.editingFinished.connect(self.save_current_profile_edits)
        self.port_input.valueChanged.connect(self.save_current_profile_edits)
        self.timeout_input.valueChanged.connect(self.save_runtime_settings)
        self.trust_host_checkbox.stateChanged.connect(self.save_current_profile_edits)
        self.remember_password_checkbox.toggled.connect(self.remember_password_changed)

        self.setCentralWidget(central)
        root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(640, 420)
        self.apply_zoom()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Pick a profile from File > Load profile, then connect to the device")
        self.load_saved_password()
        self.refresh_profile_menu()

    def ensure_main_tabs(self) -> None:
        """Add or remove the SSH core tab to match its enabled flag; Berry/browser/Settings handled elsewhere."""
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        self._set_main_tab(self.ssh_page, "SSH", ssh_on, 0)
        self.update_connection_ui()

    def _content_insert_index(self) -> int:
        """Where a new content tab belongs: just left of the pinned gap + Settings pair."""
        gap = getattr(self, "_gap_page", None)
        if gap is not None:
            index = self.tabs.indexOf(gap)
            if index >= 0:
                return index
        settings = getattr(self, "settings_page", None)
        if settings is not None:
            index = self.tabs.indexOf(settings)
            if index >= 0:
                return index
        return self.tabs.count()

    def _sync_pinned_settings(self) -> None:
        """Snap the gap and Settings tabs back to the far right, in that order, then recompute the gap width."""
        bar = self.tabs.tabBar()
        gap = getattr(self, "_gap_page", None)
        settings = getattr(self, "settings_page", None)
        if gap is None or settings is None:
            return
        last = self.tabs.count() - 1
        gi = self.tabs.indexOf(gap)
        if gi >= 0 and gi != last - 1:
            bar.moveTab(gi, last)
        si = self.tabs.indexOf(settings)
        if si >= 0 and si != last:
            bar.moveTab(si, last)
        gi = self.tabs.indexOf(gap)
        if gi >= 0:
            bar.setTabEnabled(gi, False)
            if hasattr(bar, "set_gap_index"):
                bar.set_gap_index(gi)

    def _on_tab_count_changed(self, _count: int) -> None:
        """A tab was added or removed: defer a tick, then re-pin the gap and Settings pair."""
        if getattr(self, "_gap_page", None) is None:
            return
        QTimer.singleShot(0, self._repin_settings)

    def _repin_settings(self) -> None:
        """Re-sync the pinned pair after a deferred count change (see
        :meth:`_on_tab_count_changed`). No-op once the window is closing."""
        if getattr(self, "_gap_page", None) is None:
            return
        if self.tabs.indexOf(self._gap_page) < 0:
            return
        self._sync_pinned_settings()
        self._stretch_tab_bar()

    def restore_tab_layout(self) -> None:
        """Put every tab back in its saved position and re-pin Settings (called after the addon manager reloads)."""
        self._apply_remembered_order()
        self._repin_settings()

    def update_connection_ui(self) -> None:
        """Match the Connection panel's lines and buttons to what is on, following the profile settings."""
        master_on = self.connections_enabled()
        sftp_on = bool(self.app_config.get("sftp_enabled", True))
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        self.connect_button.setVisible(sftp_on)
        self.disconnect_button.setVisible(sftp_on)

        self.remote_dir_label.setVisible(sftp_on)
        self.remote_root_input.setVisible(sftp_on)
        self.sftp_caption.setVisible(sftp_on)
        self.sftp_dot.setVisible(sftp_on)
        self.sftp_state_label.setVisible(sftp_on)
        self.ssh_caption.setVisible(ssh_on)
        self.ssh_dot.setVisible(ssh_on)
        self.ssh_state_label.setVisible(ssh_on)
 
        self.shell_connect_button.setEnabled(master_on)

    def connections_enabled(self) -> bool:
        """The master switch: is the app allowed to talk to the device at all?"""
        return bool(self.app_config.get("master_connection", True))

    def on_master_connection_toggled(self, checked: bool) -> None:
        """The master switch: off tears down both sessions and blocks all connecting; on connects nothing by itself."""
        self.app_config["master_connection"] = bool(checked)
        self.save_app_config()
        if not checked:
            self.close_all_connections()
        self.update_connection_ui()
        self.log("Connections enabled" if checked else "Connections switched off")
        self.addon_event("connections_enabled" if checked else "connections_disabled")

    def close_all_connections(self) -> None:
        """Take both main sessions down (the master switch went off)."""
        if self.shell_worker is not None and self.shell_worker.isRunning():
            self.shell_worker.stop()
        if self.worker is not None and self.worker.isRunning():
            return
        self.shared_conn.close()
        self.mark_shared_closed("connections are switched off")

    def _set_main_tab(self, page: QWidget, title: str, want: bool, desired_index: int) -> None:
        current = self.tabs.indexOf(page)
        if want:
            if current == -1:
                self.tabs.insertTab(min(desired_index, self.tabs.count()), page, title)
                self.sync_tab_transport_dots()
        elif current != -1:
            self.tabs.removeTab(current)
            self.sync_tab_transport_dots()

    def show_addon_dev(self) -> None:
        """Jump to the Addon Dev tab, if the Settings switch shows it."""
        if self.addon_dev is not None:
            self.tabs.setCurrentWidget(self.addon_dev)

    def addon_dev_wanted(self) -> bool:
        """The Settings > Startup switch: is the Addon Dev tab wanted?"""
        return bool(self.app_config.get("addon_dev_enabled", True))

    def ensure_addon_dev_tab(self) -> None:
        """Add or drop the Addon Dev tab to match the Settings switch; off keeps the widget alive but hidden."""
        want = self.addon_dev_wanted()
        if want and self.addon_dev is None:
            self.addon_dev = AddonDevWidget(self)
        if self.addon_dev is not None:
            index = self.tabs.indexOf(self.addon_dev)
            if want and index == -1:
                self.tabs.insertTab(self._content_insert_index(), self.addon_dev, "Addon Dev")
            elif not want and index != -1:
                self.tabs.removeTab(index)
        self.action_addon_dev.setVisible(want)
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))

    def apply_runtime_settings(self) -> None:
        self.apply_zoom()
        self.ensure_main_tabs()
        self.ensure_addon_dev_tab()
        self.apply_log_console_visibility()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))
        self._select_default_tab()
        self.rebuild_quick_bar()

    def _icon_path(self, filename: str) -> Path | None:
        """Resolve a shipped icon from the assets folder."""
        return find_asset(filename)

    def current_icon_path(self) -> Path | None:
        """The logo View > Alternate icon points at now; every place that shows it asks this one method."""
        primary, alternate = ICON_FILES
        wanted = alternate if bool(self.app_config.get("use_alt_icon")) else primary
        return self._icon_path(wanted) or self._icon_path(primary)

    def paint_header_logo(self) -> None:
        """The toolbar picture, drawn at the zoom the interface is using."""
        icon = getattr(self, "header_icon", None)
        if icon is None:
            return
        path = self.current_icon_path()
        if path is None:
            return
        side = zoom_size(22)
        icon.setPixmap(QPixmap(str(path)).scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def apply_window_icon(self) -> None:
        """Show the selected icon in the top toolbar and as the window icon."""
        manager = getattr(self, "addons", None)
        if manager is not None:

            try:
                manager.refresh_app_icons()
            except Exception as error:
                self.log(f"addon tab icons: {error}")
        path = self.current_icon_path()
        if path is None:
            return
        icon = QIcon(str(path))
        self.setWindowIcon(icon)
        QApplication.setWindowIcon(icon)
        self.paint_header_logo()

    # Interface zoom

    def ui_zoom(self) -> int:
        """The saved density of the whole interface, in percent."""
        return int(self.app_config.get("ui_zoom", UI_ZOOM_DEFAULT))

    def terminal_points(self) -> int:
        """The terminal's font: the Settings size, drawn at the current zoom."""
        base = int(self.app_config.get("terminal_font_size", 10))
        return max(6, min(48, zoom_size(base, self.ui_zoom())))

    def zoom_in(self) -> None:
        self.zoom_to(step_zoom(self.ui_zoom(), +1))

    def zoom_out(self) -> None:
        self.zoom_to(step_zoom(self.ui_zoom(), -1))

    def reset_zoom(self) -> None:
        self.zoom_to(UI_ZOOM_DEFAULT)

    def zoom_to(self, zoom: int) -> None:
        """Move the whole view to the rung asked for, and remember it."""
        percent = step_zoom(zoom, 0)
        self.app_config["ui_zoom"] = percent
        self.apply_zoom()
        self.save_app_config()
        self.statusBar().showMessage(f"Interface zoom: {percent}%", 3000)

    def apply_zoom(self) -> None:
        """Draw every part of the window at the saved zoom, multiplying the sheet's px/pt and the terminal font."""
        percent = set_zoom(self.ui_zoom())
        self.setStyleSheet(scaled_sheet(APP_STYLESHEET, percent))
        pane = getattr(self, "log_output", None)
        if pane is not None:
            pane.setMaximumHeight(zoom_size(130, percent))
        font = getattr(self, "terminal_font", None)
        if font is not None and hasattr(self, "terminal"):
            font.setPointSize(self.terminal_points())
            self.terminal.set_font(font)
        self.paint_header_logo()

        if hasattr(self, "tabs"):
            self.zoom_tab_icons(percent)
            self.sync_tab_transport_dots()

    def zoom_tab_icons(self, percent: int) -> None:
        """Scale the tab icons (addon ones included) from the style's baseline, so 100% matches as shipped."""
        bar = self.tabs.tabBar()
        base = getattr(self, "_tab_icon_base", None)
        if base is None:
            base = max(1, bar.iconSize().width())
            self._tab_icon_base = base
        side = zoom_size(base, percent)
        bar.setIconSize(QSize(side, side))

        if hasattr(bar, "refit"):
            bar.refit()

    def toggle_log_console(self, shown: bool) -> None:
        """View > Show log console: fold the bottom log pane away, or bring it back."""
        self.app_config["log_visible"] = bool(shown)
        self.apply_log_console_visibility()
        self.save_app_config()

    def apply_log_console_visibility(self) -> None:
        """Match the bottom log pane to the View switch; only the widget hides, logging continues."""
        pane = getattr(self, "log_output", None)
        if pane is not None:
            pane.setVisible(bool(self.app_config.get("log_visible", True)))

    def toggle_window_icon(self, checked: bool) -> None:
        self.app_config["use_alt_icon"] = bool(checked)
        self.save_app_config()
        self.apply_window_icon()

    def set_fullscreen(self, enabled: bool) -> None:
        self.showFullScreen() if enabled else self.showNormal()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "action_fullscreen"):
            self.action_fullscreen.blockSignals(True)
            self.action_fullscreen.setChecked(self.isFullScreen())
            self.action_fullscreen.blockSignals(False)
        super().changeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._stretch_tab_bar()

    def _stretch_tab_bar(self) -> None:
        """Hold the tab bar as wide as the page so the pinned Settings tab, with
        its elastic gap tab, sits flush against the right edge."""
        if not hasattr(self, "tabs"):
            return
        bar = self.tabs.tabBar()
        if isinstance(bar, RightPinnedTabBar):
            bar.setMinimumWidth(max(0, self.tabs.width() - 4))

    def fit_window_to_screen(self) -> None:
        """One-time startup guard: shrink the window only if the default size exceeds the screen; never repositions it."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        max_w = max(320, int(area.width() * 0.95))
        max_h = max(240, int(area.height() * 0.95))
        if self.width() > max_w or self.height() > max_h:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))

    def current_settings(self, silent: bool = False) -> dict | None:
        try:
            remote_root = self.remote_root_input.text().strip()
            if not remote_root.startswith("/"):
                raise ValueError("Remote directory must be an absolute path, such as /www_rasconf")
            remote_root = posixpath.normpath(remote_root)
            if remote_root == "/":
                raise ValueError("Remote directory cannot be the filesystem root")
            if not self.host_input.text().strip():
                raise ValueError("Enter the device hostname or IP address")
            if not self.username_input.text().strip():
                raise ValueError("Enter the SSH username")
        except ValueError as error:
            if not silent:
                QMessageBox.warning(self, "Check settings", str(error))
            return None
        return {
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "key_file": self.key_input.text().strip(),
            "remote_root": remote_root,
            "source": str(self.app_config.get("source", DEFAULT_PROFILE["source"])),
            "trust_unknown_host": self.trust_host_checkbox.isChecked(),
            "remember_password": self.remember_password_checkbox.isChecked(),
            "ssh_timeout": self.timeout_input.value(),
            "show_hidden_files": bool(self.app_config.get("show_hidden_files", False)),
        }

    def _live_transport_states(self) -> dict[str, bool]:
        """The app's live SSH/SFTP state; one open main session lights both dots since it carries both transports."""
        shell = self.shell_worker
        shared = self.shared_conn.active()
        return {
            "sftp": bool(self.sftp_connected or shared),
            "ssh": bool((shell is not None and shell.isRunning()) or shared),
        }

    def _transport_tab_pages(self) -> set[QWidget]:
        """The tabs that carry transport dots: the SSH terminal and each addon's non-web tab."""
        pages: set[QWidget] = set()
        ssh = getattr(self, "ssh_page", None)
        if ssh is not None and self.tabs.indexOf(ssh) >= 0:
            pages.add(ssh)
        manager = getattr(self, "addons", None)
        if manager is not None:
            for addon in manager.addons:
                for entry in (addon.tabs or {}).values():
                    if entry.get("web"):
                        continue
                    page = entry.get("page")
                    if page is not None and self.tabs.indexOf(page) >= 0:
                        pages.add(page)
        return pages

    def sync_tab_transport_dots(self) -> None:
        """Put an s/f dot beside each tab that owns a connection; rebuilt every pass since setTabButton takes ownership."""
        wanted = self._transport_tab_pages()
        live = self._live_transport_states()
        bar = self.tabs.tabBar()
        keep: list[TabTransportDots] = []
        for index in range(self.tabs.count()):
            if bar.tabButton(index, QTabBar.ButtonPosition.RightSide) is not None:
                bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
            if self.tabs.widget(index) in wanted:
                dots = TabTransportDots()
                dots.set_state(live)
                keep.append(dots)
                bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, dots)
        self._tab_dots = keep

        if hasattr(bar, "refit"):
            bar.refit()

    # Tab order

    def capture_tab_order(self) -> list[str]:
        """The titles of the reorderable tabs, left to right. The pinned gap and
        Settings tabs are always last and never moved, so they are left out."""
        gap = getattr(self, "_gap_page", None)
        settings = getattr(self, "settings_page", None)
        bar = self.tabs.tabBar()
        titles = []
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if widget is gap or widget is settings:
                continue
            titles.append(tab_caption(bar, i))
        return titles

    def apply_tab_order(self, order: list[str] | None) -> None:
        """Drag the tabs into order (titles); missing titles are skipped and an omitted tab stays last."""
        if not order:
            return
        bar = self.tabs.tabBar()
        target = 0
        for title in order:
            current = next(
                (i for i in range(target, self.tabs.count()) if tab_caption(bar, i) == title),
                None,
            )
            if current is None:
                continue
            if current != target:
                bar.moveTab(current, target)
            target += 1

    def _apply_remembered_order(self) -> None:
        """Reorder to the stored arrangement when remembering is on; no-op until the built-in order is captured."""
        if getattr(self, "_natural_tab_order", None) is None:
            return
        if bool(self.app_config.get("remember_tab_order", True)):
            self.apply_tab_order(self.app_config.get("tab_order"))
            self._sync_pinned_settings()

    def _select_default_tab(self, title: str | None = None) -> None:
        """Show the configured start tab, falling back to the first one."""
        wanted = title or self.app_config.get("default_tab", "Berry")
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            if tab_caption(bar, i) == wanted:
                self.tabs.setCurrentIndex(i)
                return
        if self.tabs.currentIndex() < 0 and self.tabs.count():
            self.tabs.setCurrentIndex(0)

    def save_tab_order_now(self) -> None:
        self._sync_pinned_settings()
        order = self.capture_tab_order()
        self.app_config["tab_order"] = order
        self.save_app_config()
        self.log("Tab order saved: " + " \u2192 ".join(order))

    def reset_tab_order(self) -> None:
        """Put the tabs back the way the program builds them and forget the
        saved arrangement (the remembered order is restored next launch too)."""
        natural = getattr(self, "_natural_tab_order", None) or []
        self.apply_tab_order(natural)
        self._sync_pinned_settings()
        self.app_config["tab_order"] = list(natural)
        self.save_app_config()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))
        self._select_default_tab()
        self.log("Tab order reset to default")

    def _maybe_persist_order(self) -> None:
        """After a drag, store the new order only when remembering is on and a
        tab really moved (selecting a tab must not write the config)."""
        if getattr(self, "_natural_tab_order", None) is None:
            return
        if not bool(self.app_config.get("remember_tab_order", True)):
            return

        self._sync_pinned_settings()
        order = self.capture_tab_order()
        if order != (self.app_config.get("tab_order") or []):
            self.app_config["tab_order"] = order
            self.save_app_config()

    def _on_tab_order_toggled(self, checked: bool) -> None:
        """Settings > Startup: start (or stop) remembering the tab order. Switching
        it on keeps the arrangement that is on screen right now."""
        self.app_config["remember_tab_order"] = bool(checked)
        if checked:
            self.app_config["tab_order"] = self.capture_tab_order()
        self.save_app_config()
        self.log("Tab order will be remembered" if checked else "Tab order will not be remembered")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is getattr(self, "_order_tab_bar", None) \
                and event.type() == QEvent.Type.MouseButtonRelease:

            QTimer.singleShot(0, self._maybe_persist_order)
        return super().eventFilter(obj, event)

    def set_status_dot(self, which: str, mode: str) -> None:
        """Light or put out the app's own transport dot; they live under the connection panel now."""
        dot = self.ssh_dot if which == "ssh" else self.sftp_dot
        word = self.ssh_state_label if which == "ssh" else self.sftp_state_label
        online = mode == "online"
        dot.setObjectName("statusDotOnline" if online else "statusDotOffline")
        dot.setToolTip(f"{which.upper()}: "
                       + ("connected" if online else "not connected"))
        word.setText("connected" if online else "closed")
        word.setStyleSheet("color: #23d18b;" if online else "color: #c51a4a;")
        if online:
            self.connected_once = True
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        self.addons.refresh_connection_dots()
        self.sync_tab_transport_dots()

    def _transport_state_part(self, transport: str, states: dict[str, bool]) -> QWidget:
        """One ``SSH ● connected`` trio, for an addon's line in the overview."""
        online = bool(states.get(transport))
        row = QHBoxLayout()
        row.setSpacing(4)
        caption = QLabel(transport.upper())
        caption.setStyleSheet("color: #aaaaaa;")
        dot = QLabel("\u25CF")
        dot.setObjectName("statusDotOnline" if online else "statusDotOffline")
        dot.setToolTip(f"{transport.upper()}: "
                       + ("connected" if online else "not connected"))
        word = QLabel("connected" if online else "closed")
        word.setStyleSheet("color: #23d18b;" if online else "color: #c51a4a;")
        for part in (caption, dot, word):
            row.addWidget(part)
        wrap = QWidget()
        wrap.setLayout(row)
        return wrap

    def refresh_connection_overview(self) -> None:
        """List running addons in Settings > Advanced with each declared transport's state and which connection it uses."""
        layout = getattr(self, "connection_overview_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            stale = item.widget()
            if stale is not None:
                stale.setParent(None)
                stale.deleteLater()
        manager = getattr(self, "addons", None)
        running = manager.loaded() if manager is not None else []
        if not running:
            empty = QLabel("No addons are running.")
            empty.setStyleSheet("color: #8f8f8f;")
            layout.addWidget(empty)
            return
        states = self._live_transport_states()
        for addon in running:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(QLabel(addon.name or addon.id))
            declared = tuple(getattr(addon, "requires", ()) or ())
            used = [transport for transport in ("ssh", "sftp")
                    if transport in declared]
            if used:
                for transport in used:
                    row.addWidget(self._transport_state_part(transport, states))
                own = bool(getattr(addon, "own_connection", False))
                mode = QLabel("own connection" if own else "main connection")
                mode.setStyleSheet("color: #8f8f8f;")
                mode.setToolTip(
                    "This addon opens a fresh connection for every call." if own
                    else "This addon shares the app's main SSH/SFTP session.")
                row.addWidget(mode)
            else:
                quiet = QLabel("no connection declared")
                quiet.setStyleSheet("color: #6a6a6a;")
                quiet.setToolTip("This addon has not declared any transport under "
                                 '\"requires\" in its manifest, so it cannot connect.')
                row.addWidget(quiet)
            row.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(row)
            layout.addWidget(wrap)

    def log(self, message: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.log_output.append(timestamped)
        if self.app_config.get("logging_enabled", True):
            try:
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with LOG_FILE.open("a", encoding="utf-8") as handle:
                    handle.write(timestamped + "\n")
            except OSError:
                pass
        max_lines = int(self.app_config.get("max_log_lines", 500))
        document = self.log_output.document()
        while document.blockCount() > max_lines:
            cursor = QTextCursor(document.firstBlock())
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def addon_event(self, name: str, payload: dict | None = None, **extra: Any) -> None:
        """Tell the addons what happened; never lets a broken addon or manager-less app reach the caller."""
        manager = getattr(self, "addons", None)
        if manager is None:
            return
        data = dict(payload or {})
        data.update(extra)
        try:
            manager.notify(name, data)
        except Exception as error:
            self.log(f"addon event '{name}' failed: {error}")

    def _on_tab_changed(self, index: int) -> None:
        self.addon_event("tab_changed",
                         tab=tab_caption(self.tabs.tabBar(), index), index=index)

    def closeEvent(self, event: QCloseEvent) -> None:
        if getattr(self, "_closing", False):
            event.ignore()                    # already on the way down
            return
        # Ask first: cancelling here must not have taken the addons or the
        # connection down with it.
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Connection in progress",
                "The main connection is still being made. Abort and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._maybe_persist_order()
        self.save_current_profile_edits()
        self.addon_event("shutdown")
        # No thread may outlive the window: Qt aborts the process when a
        # QThread is destroyed mid-run, and a restart is exactly the close
        # that happens over live work - a connect in flight, a quick
        # command, a shell or a reboot watch. Each gets a polite ask, a
        # bounded wait and only then the hard stop; the closing flag keeps
        # their signals from popping dialogs or reconnecting meanwhile.
        self._stop_thread(self.worker)
        self._stop_thread(self.exec_worker)
        if self.reboot_worker is not None:
            self._stop_thread(self.reboot_worker, self.reboot_worker.cancel)
        if self.shell_worker is not None:
            self._stop_thread(self.shell_worker, self.shell_worker.stop)
        if self.addon_dev is not None:
            self.addon_dev.shutdown()         # no build child outlives the app
        # Addons last before the window goes: stopping a process bridge pumps
        # the event queue while it waits, so an addon mid-request can finish
        # cleanly instead of stalling this thread; anything still alive after
        # that is killed with its whole process tree.
        self.addons.shutdown_all()
        self.addons.bus.dead = True           # late jobs only reach deleted widgets
        self._quiesce_web_views()
        self._close_shared_conn()
        super().closeEvent(event)

    def _close_shared_conn(self) -> None:
        """Close the shared session without ever letting its internals
        outstall the shutdown: paramiko can block in close (joining threads
        that a killed worker left behind), and a dying app must not wait."""
        closer = threading.Thread(target=self.shared_conn.close,
                                  daemon=True, name="conn-closer")
        closer.start()
        closer.join(3)

    @staticmethod
    def _stop_thread(thread: QObject | None, ask: Any = None) -> None:
        """Bring one worker thread down for good: polite ask, interruption,
        a bounded wait, and terminate only as the last resort."""
        if thread is None or not thread.isRunning():
            return
        if callable(ask):
            try:
                ask()
            except Exception:
                pass
        thread.requestInterruption()
        if not thread.wait(6000):
            thread.terminate()
            thread.wait(1500)

    def _quiesce_web_views(self) -> None:
        """Stop every embedded page loading before the window is torn down."""
        for entry in list(getattr(self, "_web_tab_entries", [])) + \
                list(getattr(self, "_transient_tab_entries", [])):
            view = entry.get("view")
            if view is None:
                continue
            try:
                view.stop()
            except RuntimeError:
                pass

    # Settings tab

    def _make_toggle_group(self, title: str, checked: bool) -> tuple[ToggleGroup, QFormLayout]:
        """A toggle-headed group whose indented form body hides when off."""
        group = ToggleGroup(title, checked, body="form")
        return group, group.body_layout

    def _make_category(self, title: str, checked: bool, tip: str = "") -> tuple[ToggleGroup, QVBoxLayout]:
        """A top-level Settings category with a switch in its header; the
        caller wires the state to the matching tab."""
        group = ToggleGroup(title, checked, body="box", tip=tip)
        return group, group.body_layout

    def build_settings_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(16)

        connection_group = self.connection_group
        panel_body = connection_group.body_layout

        status_group = CollapseGroup(
            "Connection status",
            tip="Read-only: the program's own SSH and SFTP state, and what every "
                "running addon has connected with. Nothing here changes a setting.",
        )
        status_body = status_group.body_layout

        self.ssh_caption = QLabel("SSH")
        self.ssh_caption.setStyleSheet("color: #aaaaaa;")
        self.ssh_dot = QLabel("\u25CF")
        self.ssh_dot.setObjectName("statusDotOffline")
        self.ssh_dot.setToolTip("SSH: not connected")
        self.ssh_state_label = QLabel("closed")
        self.ssh_state_label.setStyleSheet("color: #c51a4a;")
        self.sftp_caption = QLabel("SFTP")
        self.sftp_caption.setStyleSheet("color: #aaaaaa;")
        self.sftp_dot = QLabel("\u25CF")
        self.sftp_dot.setObjectName("statusDotOffline")
        self.sftp_dot.setToolTip("SFTP: not connected")
        self.sftp_state_label = QLabel("closed")
        self.sftp_state_label.setStyleSheet("color: #c51a4a;")
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        status_row.addWidget(self.ssh_caption)
        status_row.addWidget(self.ssh_dot)
        status_row.addWidget(self.ssh_state_label)
        status_row.addSpacing(12)
        status_row.addWidget(self.sftp_caption)
        status_row.addWidget(self.sftp_dot)
        status_row.addWidget(self.sftp_state_label)
        status_row.addStretch(1)
        status_wrap = QWidget()
        status_wrap.setLayout(status_row)
        status_body.addWidget(status_wrap)

        heading = QLabel("Running addons")
        heading.setStyleSheet("color: #8f8f8f;")
        status_body.addWidget(heading)
        self.connection_overview = QWidget()
        self.connection_overview_layout = QVBoxLayout(self.connection_overview)
        self.connection_overview_layout.setContentsMargins(0, 0, 0, 0)
        self.connection_overview_layout.setSpacing(4)
        status_body.addWidget(self.connection_overview)

        ssh_box = QGroupBox("SSH terminal")
        tip_on_title(ssh_box,
            "Options for the SSH terminal. Switching SSH off altogether is the "
            "job of the switch in this panel's header.")
        ssh_body = QVBoxLayout(ssh_box)
        ssh_body.setContentsMargins(16, 12, 12, 10)
        ssh_body.setSpacing(8)

        self.ssh_tab_check = ToggleSwitch("Show the SSH terminal tab")
        self.ssh_tab_check.setToolTip(
            "Add or remove the SSH terminal tab. The protocol stays available "
            "to the API and the addons either way."
        )
        self.ssh_tab_check.setChecked(bool(self.app_config.get("ssh_enabled", True)))
        ssh_body.addWidget(self.ssh_tab_check)

        self.ssh_auto_connect_check = ToggleSwitch("Auto-connect at startup")
        self.ssh_auto_connect_check.setToolTip("Open the SSH terminal automatically when the app starts.")
        self.ssh_auto_connect_check.setChecked(bool(self.app_config.get("auto_connect_ssh", False)))
        ssh_body.addWidget(self.ssh_auto_connect_check)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Terminal font size"))
        self.terminal_font_spin = QSpinBox()
        self.terminal_font_spin.setRange(6, 24)
        self.terminal_font_spin.setValue(int(self.app_config.get("terminal_font_size", 10)))
        font_row.addWidget(self.terminal_font_spin)
        font_row.addStretch(1)
        ssh_body.addLayout(font_row)

        reboot_form = QFormLayout()
        reboot_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        reboot_form.setHorizontalSpacing(12)
        reboot_form.setVerticalSpacing(8)
        self.reboot_wait_spin = QSpinBox()
        self.reboot_wait_spin.setRange(0, 600)
        self.reboot_wait_spin.setSuffix(" s")
        self.reboot_wait_spin.setValue(int(self.app_config.get("reboot_wait_seconds", 30)))
        self.reboot_wait_spin.setToolTip(
            "Time allowed for the device to go down before the first reconnect attempt"
        )
        reboot_form.addRow("Wait before first retry", self.reboot_wait_spin)
        self.reboot_retry_spin = QSpinBox()
        self.reboot_retry_spin.setRange(1, 300)
        self.reboot_retry_spin.setSuffix(" s")
        self.reboot_retry_spin.setValue(int(self.app_config.get("reboot_retry_interval", 10)))
        self.reboot_retry_spin.setToolTip("Seconds between reconnect attempts until the device answers")
        reboot_form.addRow("Retry every", self.reboot_retry_spin)
        self.reboot_auto_watch_check = ToggleSwitch("Offer to watch for the device after a reboot command")
        self.reboot_auto_watch_check.setToolTip(
            "Ask whether to start the wait-and-retry cycle when you send reboot "
            "(or shutdown -r) from the terminal"
        )
        self.reboot_auto_watch_check.setChecked(bool(self.app_config.get("reboot_auto_watch", True)))
        reboot_form.addRow(self.reboot_auto_watch_check)
        ssh_body.addLayout(reboot_form)

        edit_quick_btn = QPushButton("Edit quick commands...")
        edit_quick_btn.clicked.connect(self.edit_quick_commands)
        ssh_body.addWidget(edit_quick_btn)
        panel_body.addWidget(ssh_box)

        sftp_box = QGroupBox("File transfer (SFTP)")
        tip_on_title(sftp_box,
            "The connection Berry uses to browse files on the device. Turning the "
            "Berry tab on or off is done under Bundled with the program below.")
        sftp_body = QVBoxLayout(sftp_box)
        sftp_body.setContentsMargins(16, 12, 12, 10)
        sftp_body.setSpacing(8)

        self.sftp_auto_connect_check = ToggleSwitch("Auto-connect at startup")
        self.sftp_auto_connect_check.setToolTip("Open the SFTP connection automatically when the app starts.")
        self.sftp_auto_connect_check.setChecked(bool(self.app_config.get("auto_connect_sftp", False)))
        sftp_body.addWidget(self.sftp_auto_connect_check)
        panel_body.addWidget(sftp_box)

        panel_body.addWidget(status_group)
        vbox.addWidget(connection_group)
        self.refresh_connection_overview()

        startup_group = QGroupBox("Startup & defaults")
        startup_form = QFormLayout(startup_group)
        startup_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        startup_form.setHorizontalSpacing(12)
        startup_form.setVerticalSpacing(8)

        self.default_tab_combo = QComboBox()
        self.default_tab_combo.setToolTip("Tab selected when the app starts (only visible tabs are listed).")
        startup_form.addRow("Default tab", self.default_tab_combo)

        self.tab_order_check = ToggleSwitch("Remember tab order")
        self.tab_order_check.setToolTip(
            "Keep the tabs in the order you drag them into, next time the app "
            "starts. Turn off to use the built-in order on every launch."
        )
        self.tab_order_check.setChecked(bool(self.app_config.get("remember_tab_order", True)))
        self.tab_order_check.toggled.connect(self._on_tab_order_toggled)
        startup_form.addRow(self.tab_order_check)

        order_row = QHBoxLayout()
        save_order_btn = QPushButton("Save current order")
        save_order_btn.setToolTip("Store the tabs exactly as they are arranged now.")
        save_order_btn.clicked.connect(self.save_tab_order_now)
        reset_order_btn = QPushButton("Reset to default order")
        reset_order_btn.clicked.connect(self.reset_tab_order)
        order_row.addWidget(save_order_btn)
        order_row.addWidget(reset_order_btn)
        order_row.addStretch(1)
        order_wrap = QWidget()
        order_wrap.setLayout(order_row)
        startup_form.addRow("Tab order", order_wrap)

        self.addon_dev_check = ToggleSwitch("Show the Addon Dev tab")
        self.addon_dev_check.setToolTip(
            "The page that lists every addon with its logs and processes, reads and "
            "edits its files, and turns a .py addon into a program of its own. The "
            "same entry appears under Tools. Switching it off takes the tab away; "
            "switching addons on and off is done in Settings > Addons below."
        )
        self.addon_dev_check.setChecked(self.addon_dev_wanted())
        self.addon_dev_check.toggled.connect(self._on_addon_dev_toggled)
        startup_form.addRow(self.addon_dev_check)

        self.logging_group, log_form = self._make_toggle_group(
            "Enable logging to file", bool(self.app_config.get("logging_enabled", True))
        )
        self.max_log_spin = QSpinBox()
        self.max_log_spin.setRange(50, 10000)
        self.max_log_spin.setValue(int(self.app_config.get("max_log_lines", 500)))
        log_form.addRow("Max lines kept on screen", self.max_log_spin)
        log_path_label = QLabel(str(LOG_FILE))
        log_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        log_path_label.setStyleSheet("color: #aaaaaa;")
        log_form.addRow("Log file", log_path_label)
        startup_form.addRow(self.logging_group)
        vbox.addWidget(startup_group)

        tabs_category = QGroupBox("Custom tabs")
        tip_on_title(tabs_category, "Embedded browser tabs. These are always shown.")
        tabs_body = QVBoxLayout(tabs_category)
        tabs_body.setContentsMargins(16, 12, 12, 10)
        self.tabs_combo = QComboBox()
        self.tabs_combo.setToolTip("Embeddable browser tabs shown after the main tabs.")
        add_tab_btn = QPushButton("Add...")
        add_tab_btn.clicked.connect(self.add_web_tab)
        edit_tab_btn = QPushButton("Edit")
        edit_tab_btn.clicked.connect(self.edit_web_tab)
        remove_tab_btn = QPushButton("Remove")
        remove_tab_btn.setObjectName("dangerAction")
        remove_tab_btn.clicked.connect(self.remove_web_tab)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.tabs_combo, 1)
        tabs_row.addWidget(add_tab_btn)
        tabs_row.addWidget(edit_tab_btn)
        tabs_row.addWidget(remove_tab_btn)
        tabs_body.addLayout(tabs_row)

        self.cert_bypass_check = ToggleSwitch("Accept untrusted HTTPS certificates")
        self.cert_bypass_check.setToolTip(
            "For a self-signed certificate the embedded browser refuses to load the page. When on, "
            "certificate errors are ignored in every web tab. QtWebEngine reads this switch when it "
            "starts, so the app restarts to apply a change. Installing the certificate under "
            "'Trusted Root Certification Authorities' in the Windows certificate store is the safer fix."
        )
        self.cert_bypass_check.setChecked(bool(self.app_config.get("ignore_cert_errors", False)))
        tabs_body.addWidget(self.cert_bypass_check)
        self.cert_bypass_state_label = QLabel()
        self.cert_bypass_state_label.setStyleSheet("color: #aaaaaa;")
        tabs_body.addWidget(self.cert_bypass_state_label)
        self._update_cert_bypass_state()
        vbox.addWidget(tabs_category)

        installed_widget = self.addons.build_settings_widget()
        bundled_widget = self.addons.build_bundled_widget()
        vbox.addWidget(bundled_widget)
        vbox.addWidget(installed_widget)

        vbox.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        save_btn = QPushButton("Apply settings")
        save_btn.setObjectName("primaryAction")
        save_btn.clicked.connect(self.apply_settings_from_form)
        outer.addWidget(save_btn)

        self.tabs.addTab(page, "Settings")
        self.settings_page = page

        self.tabs.tabBar().setTabIcon(self.tabs.indexOf(page), settings_tab_icon())
        self._reload_tabs_combo()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))

        self.ssh_tab_check.toggled.connect(self._on_tab_category_toggled)

    def _reload_default_tab_combo(self, select: str | None = None) -> None:
        """List every real tab as the possible startup tab, keeping the choice."""
        chosen = select or self.default_tab_combo.currentText() or self.app_config.get("default_tab", "Berry")
        self.default_tab_combo.blockSignals(True)
        self.default_tab_combo.clear()

        bar = self.tabs.tabBar()
        self.default_tab_combo.addItems(
            [tab_caption(bar, i) for i in range(self.tabs.count())
             if tab_caption(bar, i).strip()]
        )
        idx = self.default_tab_combo.findText(chosen)
        self.default_tab_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.default_tab_combo.blockSignals(False)

    def apply_settings_from_form(self) -> None:
        self.app_config["default_tab"] = self.default_tab_combo.currentText()
        self.app_config["auto_connect_sftp"] = self.sftp_auto_connect_check.isChecked()
        self.app_config["auto_connect_ssh"] = self.ssh_auto_connect_check.isChecked()
        self.app_config["terminal_font_size"] = self.terminal_font_spin.value()
        self.app_config["max_log_lines"] = self.max_log_spin.value()
        self.app_config["logging_enabled"] = self.logging_group.isChecked()
        self.app_config["ssh_enabled"] = self.ssh_tab_check.isChecked()
        self.app_config["reboot_wait_seconds"] = self.reboot_wait_spin.value()
        self.app_config["reboot_retry_interval"] = self.reboot_retry_spin.value()
        self.app_config["reboot_auto_watch"] = self.reboot_auto_watch_check.isChecked()
        self.app_config["ignore_cert_errors"] = self.cert_bypass_check.isChecked()
        self.app_config["addon_dev_enabled"] = self.addon_dev_check.isChecked()
        self.app_config["remember_tab_order"] = self.tab_order_check.isChecked()
        self.save_app_config()
        self.apply_runtime_settings()
        self._apply_remembered_order()
        self._select_default_tab()
        self.refresh_web_tab_urls()
        self.log("Settings applied")
        self.addon_event("settings_applied")

        self._sync_cert_bypass()

    def _on_tab_category_toggled(self, _checked: bool = False) -> None:
        """Persist the SSH tab switch state and show/hide the terminal tab. (The
        Berry tab is switched from its own bundled addon category.)"""
        self.app_config["ssh_enabled"] = self.ssh_tab_check.isChecked()
        self.save_app_config()
        self.ensure_main_tabs()
        self._apply_remembered_order()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))

    def _on_addon_dev_toggled(self, _checked: bool = False) -> None:
        """Show or hide the Addon Dev tab and its Tools entry as it is switched."""
        self.app_config["addon_dev_enabled"] = self.addon_dev_check.isChecked()
        self.save_app_config()
        self.ensure_addon_dev_tab()
        self._apply_remembered_order()

    # Profiles and credentials

    def refresh_profile_menu(self) -> None:
        """Rebuild File > Load profile: one entry per profile, the active one ticked."""
        menu = getattr(self, "profile_menu", None)
        if menu is None:
            return
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = active_profile_name()
        names = profile_names()
        for name in names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.setToolTip("Load this profile's connection details and settings")
            action.triggered.connect(lambda _=False, text=name: self.load_profile(text))
            group.addAction(action)
        if not names:
            empty = menu.addAction("(no profiles)")
            empty.setEnabled(False)

    def load_profile(self, name: str) -> None:
        if not name or name == active_profile_name():
            return

        self.save_current_profile_edits()
        self.app_config = set_active_profile(name)
        self.reload_ui_from_config()
        self.log(f"Switched to profile '{name}'")

    def reload_ui_from_config(self) -> None:
        """Refresh every widget from self.app_config after a profile switch,
        a profile delete or a config import."""
        cfg = self.app_config
        self.host_input.setText(str(cfg.get("host", DEFAULT_PROFILE["host"])))
        self.port_input.setValue(int(cfg.get("port", DEFAULT_PROFILE["port"])))
        self.username_input.setText(str(cfg.get("username", DEFAULT_PROFILE["username"])))
        self.key_input.setText(str(cfg.get("key_file", "")))
        self.remote_root_input.setText(str(cfg.get("remote_root", DEFAULT_PROFILE["remote_root"])))
        self.timeout_input.setValue(int(cfg.get("ssh_timeout", 12)))
        self.trust_host_checkbox.setChecked(bool(cfg.get("trust_unknown_host", False)))

        self.remember_password_checkbox.blockSignals(True)
        self.remember_password_checkbox.setChecked(bool(cfg.get("remember_password", False)))
        self.remember_password_checkbox.blockSignals(False)
        self.password_input.clear()
        self.load_saved_password()

        self.sftp_auto_connect_check.setChecked(bool(cfg.get("auto_connect_sftp", False)))
        self.ssh_auto_connect_check.setChecked(bool(cfg.get("auto_connect_ssh", False)))
        self.logging_group.setChecked(bool(cfg.get("logging_enabled", True)))
        self.max_log_spin.setValue(int(cfg.get("max_log_lines", 500)))
        self.terminal_font_spin.setValue(int(cfg.get("terminal_font_size", 10)))
        self.reboot_wait_spin.setValue(int(cfg.get("reboot_wait_seconds", 30)))
        self.reboot_retry_spin.setValue(int(cfg.get("reboot_retry_interval", 10)))
        self.reboot_auto_watch_check.setChecked(bool(cfg.get("reboot_auto_watch", True)))
        self.cert_bypass_check.setChecked(bool(cfg.get("ignore_cert_errors", False)))

        self.addon_dev_check.blockSignals(True)
        self.addon_dev_check.setChecked(bool(cfg.get("addon_dev_enabled", True)))
        self.addon_dev_check.blockSignals(False)

        self.tab_order_check.blockSignals(True)
        self.tab_order_check.setChecked(bool(cfg.get("remember_tab_order", True)))
        self.tab_order_check.blockSignals(False)

        set_cert_bypass(bool(cfg.get("ignore_cert_errors")))
        self._update_cert_bypass_state()
        self.action_alt_icon.setChecked(bool(cfg.get("use_alt_icon", False)))

        self.action_log_console.setChecked(bool(cfg.get("log_visible", True)))

        self.ssh_tab_check.setChecked(bool(cfg.get("ssh_enabled", True)))

        master_switch = self.connection_group.switch
        master_wanted = bool(cfg.get("master_connection", True))
        master_switch.blockSignals(True)
        master_switch.setChecked(master_wanted)
        master_switch.blockSignals(False)
        self.connection_group.body_widget.setVisible(master_wanted)
        if not self.connections_enabled():
            self.close_all_connections()

        self.rebuild_web_tabs()
        self._reload_tabs_combo()
        self.apply_runtime_settings()
        self.refresh_web_tab_urls()
        self.save_app_config()

        self.addons.reconcile()
        self._apply_remembered_order()
        self._select_default_tab()
        self.addon_event("profile_changed", profile=cfg.get("name", active_profile_name()))

    def new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in profile_names():
            QMessageBox.warning(self, "Duplicate", "A profile with that name already exists.")
            return
        self.save_current_profile_edits()

        self.app_config = create_profile(name, self.app_config)
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        self.log(f"Created profile '{active_profile_name()}' from the current settings")

    def rename_current_profile(self) -> None:
        current = active_profile_name()
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=current or "")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name != current and name in profile_names():
            QMessageBox.warning(self, "Duplicate", "Another profile already has that name.")
            return
        if rename_profile(current, name):
            self.refresh_profile_menu()
            self.log(f"Profile renamed to '{name}'")

    def delete_current_profile(self) -> None:
        current = active_profile_name()
        if len(profile_names()) <= 1:
            QMessageBox.information(self, "Cannot delete", "At least one profile must exist.")
            return
        answer = QMessageBox.question(
            self, "Delete profile",
            f"Delete profile '{current}'? (This removes its saved connection details and settings.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        working = delete_profile(current)
        if working is not None:
            self.app_config = working
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        self.log(f"Deleted profile '{current}'")

    def save_current_profile_edits(self, _value: object = None) -> None:
        cfg = self.app_config
        cfg["host"] = self.host_input.text().strip() or DEFAULT_PROFILE["host"]
        cfg["port"] = self.port_input.value()
        cfg["username"] = self.username_input.text().strip() or DEFAULT_PROFILE["username"]
        cfg["key_file"] = self.key_input.text().strip()
        cfg["remote_root"] = self.remote_root_input.text().strip() or DEFAULT_PROFILE["remote_root"]
        cfg["trust_unknown_host"] = self.trust_host_checkbox.isChecked()
        cfg["remember_password"] = self.remember_password_checkbox.isChecked()
        self.save_app_config()

    def save_app_config(self, _value: object = None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        save_app_config_static(self.app_config)

    def save_runtime_settings(self, _value: object = None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        self.save_app_config()

    def current_credential_id(self) -> str:
        return credential_id(
            self.username_input.text().strip(),
            self.host_input.text().strip(),
            self.port_input.value(),
        )

    def load_saved_password(self) -> None:
        if not self.remember_password_checkbox.isChecked():
            return
        try:
            password = keyring.get_password(KEYRING_SERVICE, self.current_credential_id())
        except Exception as error:
            self.log(f"Unable to read saved password from the OS credential store: {error}")
            return
        if password:
            self.password_input.setText(password)

    def remember_password_changed(self, remember: bool) -> None:
        self.save_current_profile_edits()
        if remember:
            self.store_password()
            return
        try:
            keyring.delete_password(KEYRING_SERVICE, self.current_credential_id())
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as error:
            self.log(f"Unable to remove saved password from the OS credential store: {error}")

    def store_password(self) -> None:
        password = self.password_input.text()
        if not self.remember_password_checkbox.isChecked() or not password:
            return
        try:
            keyring.set_password(KEYRING_SERVICE, self.current_credential_id(), password)
        except Exception as error:
            self.log(f"Unable to save password to the OS credential store: {error}")

    def choose_key_file(self, _checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH private key")
        if path:
            self.key_input.setText(path)
            self.save_current_profile_edits()

    # Menu actions that open a dialog

    def export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export configuration", str(BASE_DIR / "config-export.json"), "JSON files (*.json)"
        )
        if not path:
            return
        sanitized = export_document()
        try:
            Path(path).write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
            QMessageBox.information(
                self, "Exported",
                f"Saved to {path}\n\nAll profiles are included. Passwords are never stored in this file.",
            )
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))

    def import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import configuration", str(BASE_DIR), "JSON files (*.json)")
        if not path:
            return
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        if not isinstance(loaded, dict):
            QMessageBox.critical(self, "Import failed", "File does not contain a JSON object.")
            return
        self.app_config = import_app_config(loaded)
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        QMessageBox.information(self, "Imported", "Configuration replaced from file.")

    def save_log_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log",
            str(BASE_DIR / f"berry_log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.log_output.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About Berry")
        box.setText(
            "<b>Berry Manager V1.5.5</b><br>"
            "Manage routers and devices over ssh&sftp<br>"
            'GitHub: <a href="{repo}" style="color: #c51a4a">fligma/raspiOpenWrt</a><br>'
            'License: <a href="{license}" style="color: #c51a4a">MIT License</a><br><br>'
            "Copyright &copy; 2026 fligma. Licensed under the MIT License".format(
                repo=PROJECT_GITHUB_URL, license=PROJECT_LICENSE_URL
            )
        )
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        path = self.current_icon_path()
        if path is not None:
            box.layout().activate()
            side = max(64, box.sizeHint().height())
            logo = QPixmap(str(path)).scaled(
                side, side,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            box.setIconPixmap(logo)
        box.exec()
