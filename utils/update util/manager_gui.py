#!/usr/bin/env python3
"""rasconf Manager support module (auto-generated split)."""

from __future__ import annotations

import os
import json
import posixpath
import queue
import re
import shutil
import stat
import sys
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath

import keyring
import paramiko
from PyQt6.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    pyqtProperty,
    QUrl,
    QTimer,
    QEvent,
    QSize,
    QRectF,
    QPropertyAnimation,
    QEasingCurve,
)
from PyQt6.QtGui import (
    QAction,
    QFont,
    QIcon,
    QKeySequence,
    QPixmap,
    QTextCursor,
    QDesktopServices,
    QBrush,
    QColor,
    QPainter,
    QPen,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from pathspec import GitIgnoreSpec

from manager_const import (
    APP_CONFIG_EXAMPLE_FILE,
    APP_CONFIG_FILE,
    APP_STYLESHEET,
    BASE_DIR,
    DEFAULT_PROFILE,
    DEFAULT_SOURCE,
    HISTORY_FILE,
    KEYRING_SERVICE,
    KNOWN_HOSTS_FILE,
    LOG_FILE,
    MAX_COMMAND_HISTORY,
    PROJECT_GITHUB_URL,
    PROJECT_LICENSE_URL,
    REPOSITORY_ROOT,
    RESOURCE_DIR,
)
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
    save_command_history,
    set_active_profile,
    web_tab_url,
)
from manager_net import (
    create_ssh_client,
    credential_id,
    exec_remote,
    human_size,
    local_entries,
    make_remote_directories,
    remote_join,
    safe_relative_path,
)
from manager_workers import RemoteExecWorker, RebootWaitWorker, SftpWorker, SSHShellWorker
from manager_dialogs import DryRunDialog, StringListDialog, TabEditDialog


class ToggleSwitch(QCheckBox):
    """A pill-shaped on/off switch that replaces checkboxes in Settings,
    painted in the raspberry accent colours of the app theme."""

    TRACK_W = 40
    TRACK_H = 20

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._handle = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"handle_pos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._handle)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_handle(self) -> float:
        return self._handle

    def _set_handle(self, value: float) -> None:
        self._handle = float(value)
        self.update()

    handle_pos = pyqtProperty(float, fget=_get_handle, fset=_set_handle)

    def minimumSizeHint(self) -> QSize:
        fm = self.fontMetrics()
        width = self.TRACK_W
        if self.text():
            width += 8 + fm.horizontalAdvance(self.text())
        return QSize(width, max(self.TRACK_H, fm.height()) + 4)

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track_top = (self.height() - self.TRACK_H) / 2
        if on:
            track, border = QColor("#c51a4a"), QColor("#8f1236")
        else:
            track, border = QColor("#2b2b2b"), QColor("#4a4a4a")
        if not enabled:
            track.setAlpha(120)
            border.setAlpha(120)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(track))
        radius = self.TRACK_H / 2
        painter.drawRoundedRect(
            QRectF(0.5, track_top + 0.5, self.TRACK_W - 1, self.TRACK_H - 1), radius, radius
        )
        handle_r = radius - 3.5
        travel = self.TRACK_W - 2 * (handle_r + 3.5)
        center_x = handle_r + 3.5 + self._handle * travel
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5f5f5") if enabled else QColor("#888888"))
        painter.drawEllipse(
            QRectF(center_x - handle_r, track_top + radius - handle_r, handle_r * 2, handle_r * 2)
        )
        if self.text():
            color = self.palette().color(self.palette().ColorRole.WindowText)
            if not enabled:
                color.setAlpha(140)
            painter.setPen(QPen(color))
            text_rect = QRectF(self.TRACK_W + 8, 0, self.width() - self.TRACK_W - 8, self.height())
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )


class ToggleGroup(QGroupBox):
    """A settings category headed by a ToggleSwitch instead of the checkable
    group-box checkbox. Keeps the old isChecked/setChecked/toggled interface
    so the Settings code does not have to care which kind it got."""

    def __init__(self, title: str, checked: bool, body: str = "box", tip: str = ""):
        super().__init__()
        if tip:
            self.setToolTip(tip)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 12, 10)
        outer.setSpacing(6)
        header = QHBoxLayout()
        caption = QLabel(title)
        caption.setStyleSheet("color: #c51a4a; font-weight: 600;")
        header.addWidget(caption)
        header.addStretch(1)
        self.switch = ToggleSwitch(parent=self)
        self.switch.setChecked(bool(checked))
        header.addWidget(self.switch)
        outer.addLayout(header)
        body_widget = QWidget()
        if body == "form":
            self.body_layout = QFormLayout(body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
            outer.setContentsMargins(22, 8, 12, 10)
        else:
            self.body_layout = QVBoxLayout(body_widget)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(8)
        body_widget.setVisible(self.switch.isChecked())
        self.switch.toggled.connect(body_widget.setVisible)
        outer.addWidget(body_widget)
        # Old checkable-groupbox interface used across the Settings tab.
        self.toggled = self.switch.toggled
        self.isChecked = self.switch.isChecked
        self.setChecked = self.switch.setChecked


class RasconfManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: SftpWorker | None = None
        self.shell_worker: SSHShellWorker | None = None
        self.exec_worker: RemoteExecWorker | None = None
        self.reboot_worker: RebootWaitWorker | None = None
        self.remote_tree_entries: list[dict] = []
        self.app_config = load_app_config()
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
        self.setWindowTitle("rasconf Manager")
        self.resize(1180, 780)

        self.build_menu()
        self.build_ui()
        self.apply_runtime_settings()
        self.apply_window_icon()
        self.refresh_local_tree()

        want_sftp_auto = bool(self.app_config.get("auto_connect_sftp")) and self.app_config.get("sftp_enabled", True)
        want_ssh_auto = bool(self.app_config.get("auto_connect_ssh")) and self.app_config.get("ssh_enabled", True)
        if want_sftp_auto or want_ssh_auto:
            self.toggle_connection_panel(False)
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
        self.action_toggle_conn = QAction("Connection panel", self)
        self.action_toggle_conn.setCheckable(True)
        self.action_toggle_conn.setChecked(True)
        self.action_toggle_conn.setShortcut(QKeySequence("Ctrl+P"))
        self.action_toggle_conn.toggled.connect(self.toggle_connection_panel)
        view_menu.addAction(self.action_toggle_conn)
        view_menu.addSeparator()
        font_bigger = QAction("Larger terminal font", self)
        font_bigger.setShortcut(QKeySequence("Ctrl++"))
        font_bigger.triggered.connect(lambda: self.adjust_terminal_font(+1))
        view_menu.addAction(font_bigger)
        font_smaller = QAction("Smaller terminal font", self)
        font_smaller.setShortcut(QKeySequence("Ctrl+-"))
        font_smaller.triggered.connect(lambda: self.adjust_terminal_font(-1))
        view_menu.addAction(font_smaller)
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
        self.action_edit_ignores = QAction("Edit deploy ignore patterns...", self)
        self.action_edit_ignores.triggered.connect(self.edit_ignore_patterns)
        tools_menu.addAction(self.action_edit_ignores)
        self.action_edit_post = QAction("Edit post-deploy commands...", self)
        self.action_edit_post.triggered.connect(self.edit_post_deploy_commands)
        tools_menu.addAction(self.action_edit_post)
        self.action_edit_quick = QAction("Edit quick commands...", self)
        self.action_edit_quick.triggered.connect(self.edit_quick_commands)
        tools_menu.addAction(self.action_edit_quick)
        tools_menu.addSeparator()
        self.action_dry_run = QAction("Preview deploy (dry run)", self)
        self.action_dry_run.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.action_dry_run.triggered.connect(self.preview_deploy)
        tools_menu.addAction(self.action_dry_run)

        help_menu = menubar.addMenu("&Help")
        self.action_about = QAction("About", self)
        self.action_about.triggered.connect(self.show_about)
        help_menu.addAction(self.action_about)

    def build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        profile_bar = QHBoxLayout()
        profile_bar.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
        profile_bar.addWidget(self.profile_combo, 1)
        for label, slot in (
            ("New", self.new_profile),
            ("Save", self.save_current_profile_edits),
            ("Rename", self.rename_current_profile),
            ("Delete", self.delete_current_profile),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            profile_bar.addWidget(btn)
        root_layout.addLayout(profile_bar)

        self.connection_toggle = QToolButton()
        self.connection_toggle.setText("SFTP/SSH settings")
        self.connection_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.connection_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.connection_toggle.setCheckable(True)
        self.connection_toggle.setChecked(True)
        self.connection_toggle.toggled.connect(self.toggle_connection_panel)
        self.header_icon = QLabel()
        self.header_icon.setToolTip("Rasconf Manager")
        header_row = QHBoxLayout()
        header_row.addWidget(self.header_icon)
        header_row.addWidget(self.connection_toggle)
        self.sftp_dot = QLabel("\u25CF")
        self.sftp_dot.setObjectName("statusDotOffline")
        self.sftp_dot.setToolTip("SFTP: not connected")
        self.ssh_dot = QLabel("\u25CF")
        self.ssh_dot.setObjectName("statusDotOffline")
        self.ssh_dot.setToolTip("SSH: not connected")
        self.sftp_caption = QLabel("SFTP")
        self.sftp_caption.setStyleSheet("color: #aaaaaa;")
        self.ssh_caption = QLabel("SSH")
        self.ssh_caption.setStyleSheet("color: #aaaaaa;")
        header_row.addWidget(self.sftp_caption)
        header_row.addWidget(self.sftp_dot)
        header_row.addSpacing(8)
        header_row.addWidget(self.ssh_caption)
        header_row.addWidget(self.ssh_dot)
        header_row.addStretch(1)
        root_layout.addLayout(header_row)

        connection_group = QGroupBox("Connection")
        connection_layout = QGridLayout(connection_group)
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
        self.connect_button = QPushButton("Connect (SFTP)")
        self.connect_button.setObjectName("primaryAction")
        self.connect_button.clicked.connect(self.connect_remote)
        self.disconnect_button = QPushButton("Disconnect (SFTP)")
        self.disconnect_button.setObjectName("dangerAction")
        self.disconnect_button.setToolTip("Close the SFTP session and disable remote actions until you reconnect")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_remote)
        self.shell_connect_button = QPushButton("Connect (SSH)")
        self.shell_connect_button.setObjectName("primaryAction")
        self.shell_connect_button.setToolTip("Uses the same credentials as the SFTP connection")
        self.shell_connect_button.clicked.connect(self.connect_shell)
        self.shell_disconnect_button = QPushButton("Disconnect (SSH)")
        self.shell_disconnect_button.setObjectName("dangerAction")
        self.shell_disconnect_button.setToolTip("Close the SSH terminal session")
        self.shell_disconnect_button.setEnabled(False)
        self.shell_disconnect_button.clicked.connect(self.disconnect_shell)

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
        connection_layout.addWidget(QLabel("Remote dir"), 4, 0)
        connection_layout.addWidget(self.remote_root_input, 4, 1, 1, 2)
        connection_layout.addWidget(QLabel("Timeout"), 4, 2)
        connection_layout.addWidget(self.timeout_input, 4, 3)
        conn_actions = QHBoxLayout()
        conn_actions.addWidget(self.shell_connect_button)
        conn_actions.addWidget(self.shell_disconnect_button)
        conn_actions.addWidget(self.connect_button)
        conn_actions.addWidget(self.disconnect_button)
        connection_layout.addWidget(self.trust_host_checkbox, 5, 0, 1, 2)
        connection_layout.addLayout(conn_actions, 5, 2, 1, 2)
        root_layout.addWidget(connection_group)
        self.connection_panel = connection_group

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, 1)

        self.build_sftp_tab()
        self.build_ssh_tab()
        self._web_tab_entries = []  # each: {page, url_input, view, tab}
        self.build_web_tabs()
        self.build_settings_tab()

        for field in (
            self.host_input,
            self.username_input,
            self.key_input,
            self.remote_root_input,
            self.source_input,
        ):
            field.editingFinished.connect(self.save_current_profile_edits)
        self.port_input.valueChanged.connect(self.save_current_profile_edits)
        self.timeout_input.valueChanged.connect(self.save_runtime_settings)
        self.trust_host_checkbox.stateChanged.connect(self.save_current_profile_edits)
        self.remember_password_checkbox.toggled.connect(self.remember_password_changed)

        self.setCentralWidget(central)
        root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(640, 420)
        self.setStyleSheet(APP_STYLESHEET)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Choose a profile, then connect to the device")
        self.load_saved_password()
        self.refresh_profile_combo()

    def build_sftp_tab(self) -> None:
        sftp_page = QWidget()
        sftp_layout = QVBoxLayout(sftp_page)

        source_row = QHBoxLayout()
        self.source_input = QLineEdit(str(self.app_config["source"]))
        source_browse = QPushButton("Browse source...")
        source_browse.clicked.connect(self.choose_source)
        source_refresh = QPushButton("Refresh local")
        source_refresh.clicked.connect(self.refresh_local_tree)
        remote_refresh = QPushButton("Refresh remote")
        remote_refresh.clicked.connect(self.connect_remote)
        source_row.addWidget(QLabel("Local source"))
        source_row.addWidget(self.source_input, 1)
        source_row.addWidget(source_browse)
        source_row.addWidget(source_refresh)
        source_row.addWidget(remote_refresh)
        sftp_layout.addLayout(source_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Hide files that do not match (substring, or */? glob)")
        self.filter_input.textChanged.connect(self.apply_filters)
        self.show_hidden_checkbox = QCheckBox("Show hidden")
        self.show_hidden_checkbox.setChecked(bool(self.app_config.get("show_hidden_files", False)))
        self.show_hidden_checkbox.toggled.connect(self.on_show_hidden_toggled)
        filter_row.addWidget(self.filter_input, 1)
        filter_row.addWidget(self.show_hidden_checkbox)
        sftp_layout.addLayout(filter_row)

        self.local_tree = self.create_tree("Local source files")
        self.local_tree.itemExpanded.connect(self.on_local_item_expanded)
        self.remote_tree = self.create_tree("Remote")
        self.remote_tree.setEnabled(False)

        self.local_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.local_tree.customContextMenuRequested.connect(self.show_local_context_menu)
        self.remote_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.remote_tree.customContextMenuRequested.connect(self.show_remote_context_menu)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.local_tree)
        splitter.addWidget(self.remote_tree)
        sftp_layout.addWidget(splitter, 1)

        action_row = QHBoxLayout()
        self.upload_button = QPushButton("Upload selected \u2192")
        self.upload_button.clicked.connect(self.upload_selected)
        self.download_button = QPushButton("\u2190 Download selected")
        self.download_button.clicked.connect(self.download_selected)
        self.delete_button = QPushButton("Delete remote selection")
        self.delete_button.setObjectName("dangerAction")
        self.delete_button.clicked.connect(self.delete_selected)
        self.deploy_button = QPushButton("Deploy source")
        self.deploy_button.setObjectName("primaryAction")
        self.deploy_button.setToolTip("Upload only files that changed since the last deploy; ignore patterns are never uploaded (Ctrl+D)")
        self.deploy_button.clicked.connect(self.deploy_source)
        self.mirror_status_label = QLabel()
        self.mirror_status_label.setStyleSheet("color: #aaaaaa; padding-left: 6px;")
        self.mirror_status_label.setToolTip("Mirror cleanup is configured in the Settings tab")
        preview_button = QPushButton("Preview deploy")
        preview_button.setToolTip("Show what deploy would change: files to update, skip, and delete (Ctrl+Shift+D)")
        preview_button.clicked.connect(self.preview_deploy)
        self.preview_button = preview_button
        action_row.addWidget(self.upload_button)
        action_row.addWidget(self.download_button)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.deploy_button)
        action_row.addWidget(preview_button)
        action_row.addWidget(self.mirror_status_label)
        self.backup_status_label = QLabel()
        self.backup_status_label.setStyleSheet("color: #aaaaaa; padding-left: 6px;")
        self.update_backup_status_label()
        action_row.addWidget(self.backup_status_label)
        sftp_layout.addLayout(action_row)
        # Remote actions need a live SFTP session; enable them on connect.
        for button in (
            self.upload_button,
            self.download_button,
            self.delete_button,
            self.deploy_button,
            self.preview_button,
        ):
            button.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        sftp_layout.addWidget(self.progress_bar)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(130)
        sftp_layout.addWidget(self.log_output)
        self.tabs.addTab(sftp_page, "SFTP")
        self.sftp_page = sftp_page

        shortcut_deploy = QAction(self)
        shortcut_deploy.setShortcut(QKeySequence("Ctrl+D"))
        shortcut_deploy.triggered.connect(self.deploy_source)
        self.addAction(shortcut_deploy)
        shortcut_refresh = QAction(self)
        shortcut_refresh.setShortcut(QKeySequence("Ctrl+R"))
        shortcut_refresh.triggered.connect(self.connect_remote)
        self.addAction(shortcut_refresh)

    def build_ssh_tab(self) -> None:
        ssh_page = QWidget()
        ssh_layout = QVBoxLayout(ssh_page)
        terminal_actions = QHBoxLayout()
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_terminal)
        self.reboot_button = QPushButton("Reboot device")
        self.reboot_button.setObjectName("dangerAction")
        self.reboot_button.setToolTip(
            "Send reboot over SSH, wait the configured downtime, then retry the "
            "connection until the device answers (waits are set in the Settings tab)"
        )
        self.reboot_button.clicked.connect(self.reboot_device)
        self.reboot_cancel_button = QPushButton("Cancel reboot wait")
        self.reboot_cancel_button.setObjectName("dangerAction")
        self.reboot_cancel_button.setToolTip("Stop waiting for the device to come back")
        self.reboot_cancel_button.setEnabled(False)
        self.reboot_cancel_button.clicked.connect(self.cancel_reboot_watch)
        terminal_actions.addWidget(clear_button)
        terminal_actions.addWidget(self.reboot_button)
        terminal_actions.addWidget(self.reboot_cancel_button)
        terminal_actions.addStretch(1)
        ssh_layout.addLayout(terminal_actions)

        self.quick_bar = QHBoxLayout()
        self.quick_bar.setSpacing(6)
        ssh_layout.addLayout(self.quick_bar)
        self.rebuild_quick_bar()

        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.terminal_output.setPlaceholderText("SSH terminal output will appear here.")
        self.terminal_font = QFont("Consolas", int(self.app_config.get("terminal_font_size", 10)))
        self.terminal_font.setStyleHint(QFont.StyleHint.Monospace)
        self.terminal_font.setFixedPitch(True)
        self.terminal_output.setFont(self.terminal_font)
        ssh_layout.addWidget(self.terminal_output, 1)

        terminal_input_row = QHBoxLayout()
        self.terminal_input = QLineEdit()
        self.terminal_input.setPlaceholderText("Enter a shell command. Ctrl+Up / Ctrl+Down browses history.")
        self.terminal_input.returnPressed.connect(self.send_terminal_input)
        self.terminal_send_button = QPushButton("Send")
        self.terminal_send_button.setObjectName("primaryAction")
        self.terminal_send_button.setEnabled(False)
        self.terminal_send_button.clicked.connect(self.send_terminal_input)
        terminal_input_row.addWidget(self.terminal_input, 1)
        terminal_input_row.addWidget(self.terminal_send_button)
        ssh_layout.addLayout(terminal_input_row)
        self.tabs.addTab(ssh_page, "SSH")
        self.ssh_page = ssh_page

        history_prev = QAction(self)
        history_prev.setShortcut(QKeySequence("Ctrl+Up"))
        history_prev.triggered.connect(self.history_prev)
        self.addAction(history_prev)
        history_next = QAction(self)
        history_next.setShortcut(QKeySequence("Ctrl+Down"))
        history_next.triggered.connect(self.history_next)
        self.addAction(history_next)

    def rebuild_quick_bar(self) -> None:
        while self.quick_bar.count():
            item = self.quick_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for entry in self.app_config.get("quick_commands", []):
            btn = QPushButton(entry["label"])
            btn.setToolTip(entry["command"])
            btn.clicked.connect(lambda _checked, cmd=entry["command"]: self.run_quick_command(cmd))
            self.quick_bar.addWidget(btn)
        self.quick_bar.addStretch(1)

    def run_quick_command(self, command: str) -> None:
        if self.shell_worker is not None and self.shell_worker.isRunning():
            self.terminal_input.setText(command)
            self.send_terminal_input()
            return
        settings = self.current_settings(silent=True)
        if settings is None:
            return
        if self._looks_like_reboot(command):
            # A one-off exec would just die mid-reboot; use the reboot flow so
            # the tool waits and reconnects afterwards.
            self.reboot_device()
            return
        if self.exec_worker is not None and self.exec_worker.isRunning():
            QMessageBox.information(self, "Busy", "Another command is still running.")
            return
        self.exec_worker = RemoteExecWorker(settings, [command], parent=self)
        self.exec_worker.log.connect(self.log)
        self.exec_worker.finished_ok.connect(lambda: self.statusBar().showMessage("Quick command completed", 4000))
        self.exec_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Command failed", msg))
        self.tabs.setCurrentWidget(self.sftp_page)
        self.log(f"Running quick command: {command}")
        self.exec_worker.start()

    def history_prev(self) -> None:
        if not self.terminal_history:
            return
        self.terminal_history_pos = max(0, self.terminal_history_pos - 1)
        self.terminal_input.setText(self.terminal_history[self.terminal_history_pos])

    def history_next(self) -> None:
        if not self.terminal_history:
            return
        self.terminal_history_pos = min(len(self.terminal_history) - 1, self.terminal_history_pos + 1)
        self.terminal_input.setText(self.terminal_history[self.terminal_history_pos])

    def clear_command_history(self) -> None:
        self.terminal_history = []
        self.terminal_history_pos = 0
        save_command_history([])
        self.log("Terminal command history cleared")

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

        layout.addWidget(view, 1)
        return url_input, view, page

    def _tab_def_for(self, url_input: QLineEdit) -> dict:
        """Look up the stored tab definition that owns the given url input."""
        for entry in getattr(self, "_web_tab_entries", []):
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
        in front of the Settings tab. Used after adding, editing or removing one."""
        for entry in getattr(self, "_web_tab_entries", []):
            idx = self.tabs.indexOf(entry["page"])
            if idx >= 0:
                self.tabs.removeTab(idx)
                entry["page"].deleteLater()
        self._web_tab_entries = []
        default_host = self.app_config.get("host", "192.168.1.1")
        settings_index = self.tabs.indexOf(self.settings_page)
        position = settings_index if settings_index >= 0 else self.tabs.count()
        for tab in self.app_config.get("web_tabs", []):
            self._add_web_tab(tab, default_host, position)
            position += 1

    def refresh_web_tab_urls(self) -> None:
        """Re-point host-following tabs at the active profile host without a reload."""
        default_host = self.app_config.get("host", "192.168.1.1")
        for entry in getattr(self, "_web_tab_entries", []):
            entry["url_input"].setText(web_tab_url(entry["tab"], default_host))

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

        # --- Startup & defaults (always shown) ---
        startup_group = QGroupBox("Startup & defaults")
        startup_form = QFormLayout(startup_group)
        startup_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        startup_form.setHorizontalSpacing(12)
        startup_form.setVerticalSpacing(8)

        self.default_tab_combo = QComboBox()
        self.default_tab_combo.setToolTip("Tab selected when the app starts (only visible tabs are listed).")
        startup_form.addRow("Default tab", self.default_tab_combo)
        vbox.addWidget(startup_group)

        # --- SFTP tab (can be switched off) and its related settings ---
        self.sftp_group, sftp_body = self._make_category(
            "SFTP", bool(self.app_config.get("sftp_enabled", True)),
            "Show the SFTP file-transfer tab. Untick to hide it.",
        )

        self.sftp_auto_connect_check = ToggleSwitch("Auto-connect at startup")
        self.sftp_auto_connect_check.setToolTip("Open the SFTP connection automatically when the app starts.")
        self.sftp_auto_connect_check.setChecked(bool(self.app_config.get("auto_connect_sftp", False)))
        sftp_body.addWidget(self.sftp_auto_connect_check)

        self.confirm_destructive_check = ToggleSwitch("Confirm destructive operations (delete, mirror)")
        self.confirm_destructive_check.setChecked(bool(self.app_config.get("confirm_destructive", True)))
        sftp_body.addWidget(self.confirm_destructive_check)

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
        sftp_body.addWidget(self.logging_group)

        self.remote_backup_group, remote_form = self._make_toggle_group(
            "Backup remote files before deploy",
            bool(self.app_config.get("remote_backup_enabled", False)),
        )
        self.backup_dir_input = QLineEdit(self.app_config.get("backup_directory", "/tmp/rasconf_backups"))
        self.backup_dir_input.setToolTip("Directory on the remote device where backups are stored")
        remote_form.addRow("Remote backup directory", self.backup_dir_input)
        self.keep_backups_spin = QSpinBox()
        self.keep_backups_spin.setRange(0, 100)
        self.keep_backups_spin.setValue(int(self.app_config.get("keep_last_n_backups", 5)))
        self.keep_backups_spin.setToolTip("Older backups beyond this count are deleted (0 = keep all)")
        remote_form.addRow("Keep last N backups", self.keep_backups_spin)
        sftp_body.addWidget(self.remote_backup_group)

        self.local_backup_group, local_form = self._make_toggle_group(
            "Save a copy of the remote to this PC before deploy",
            bool(self.app_config.get("local_backup_enabled", False)),
        )
        self.local_backup_dir_input = QLineEdit(
            self.app_config.get("local_backup_directory", str(BASE_DIR / "backups"))
        )
        self.local_backup_dir_input.setToolTip("Local folder where timestamped copies of the remote are saved")
        local_form.addRow("Local backup folder", self.local_backup_dir_input)
        browse_local = QPushButton("Browse...")
        browse_local.clicked.connect(self.choose_local_backup_dir)
        local_form.addRow("", browse_local)
        sftp_body.addWidget(self.local_backup_group)

        self.mirror_group, mirror_form = self._make_toggle_group(
            "Mirror cleanup on deploy (destructive)",
            bool(
                self.app_config.get("mirror_delete_remote", False)
                or self.app_config.get("mirror_delete_local", False)
            ),
        )
        self.mirror_delete_remote_check = ToggleSwitch("Delete server files not on local")
        self.mirror_delete_remote_check.setToolTip(
            "After uploading, remove remote files and directories that do not exist in the local source. "
            "Files matching the deploy ignore patterns are never touched."
        )
        self.mirror_delete_remote_check.setChecked(bool(self.app_config.get("mirror_delete_remote", False)))
        mirror_form.addRow(self.mirror_delete_remote_check)
        self.mirror_delete_local_check = ToggleSwitch("Delete local files not on server")
        self.mirror_delete_local_check.setToolTip(
            "Remove files and directories from the local source that do not exist on the server "
            "(compared before the deploy starts). Files matching the deploy ignore patterns are never touched."
        )
        self.mirror_delete_local_check.setChecked(bool(self.app_config.get("mirror_delete_local", False)))
        mirror_form.addRow(self.mirror_delete_local_check)
        sftp_body.addWidget(self.mirror_group)

        sftp_edit_row = QHBoxLayout()
        edit_ignores_btn = QPushButton("Edit deploy ignore patterns...")
        edit_ignores_btn.clicked.connect(self.edit_ignore_patterns)
        edit_post_btn = QPushButton("Edit post-deploy commands...")
        edit_post_btn.clicked.connect(self.edit_post_deploy_commands)
        sftp_edit_row.addWidget(edit_ignores_btn)
        sftp_edit_row.addWidget(edit_post_btn)
        sftp_edit_row.addStretch(1)
        sftp_body.addLayout(sftp_edit_row)
        vbox.addWidget(self.sftp_group)

        # --- SSH tab (can be switched off) and its related settings ---
        self.ssh_group, ssh_body = self._make_category(
            "SSH", bool(self.app_config.get("ssh_enabled", True)),
            "Show the SSH terminal tab. Untick to hide it.",
        )

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
        vbox.addWidget(self.ssh_group)

        # --- Custom browser tabs (always shown, cannot be switched off) ---
        tabs_category = QGroupBox("Custom tabs")
        tabs_category.setToolTip("Embedded browser tabs. These are always shown.")
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
        vbox.addWidget(tabs_category)

        vbox.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        save_btn = QPushButton("Apply settings")
        save_btn.setObjectName("primaryAction")
        save_btn.clicked.connect(self.apply_settings_from_form)
        outer.addWidget(save_btn)

        self.tabs.addTab(page, "Settings")
        self.settings_page = page
        self._reload_tabs_combo()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "SFTP"))
        # Unticking the SFTP/SSH category hides the matching tab right away.
        self.sftp_group.toggled.connect(self._on_tab_category_toggled)
        self.ssh_group.toggled.connect(self._on_tab_category_toggled)

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

    def _reload_default_tab_combo(self, select: str | None = None) -> None:
        """List every real tab as the possible startup tab, keeping the choice."""
        chosen = select or self.default_tab_combo.currentText() or self.app_config.get("default_tab", "SFTP")
        self.default_tab_combo.blockSignals(True)
        self.default_tab_combo.clear()
        self.default_tab_combo.addItems([self.tabs.tabText(i) for i in range(self.tabs.count())])
        idx = self.default_tab_combo.findText(chosen)
        self.default_tab_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.default_tab_combo.blockSignals(False)

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

    def update_backup_status_label(self) -> None:
        if hasattr(self, "mirror_status_label"):
            mirror_parts = []
            if self.app_config.get("mirror_delete_remote"):
                mirror_parts.append("server cleanup ON")
            if self.app_config.get("mirror_delete_local"):
                mirror_parts.append("local cleanup ON")
            self.mirror_status_label.setText(
                ("mirror: " + "   |   ".join(mirror_parts)) if mirror_parts else "mirror OFF (enable in Settings)"
            )
        if not hasattr(self, "backup_status_label"):
            return
        parts = []
        if self.app_config.get("remote_backup_enabled"):
            parts.append("remote backup ON")
        if self.app_config.get("local_backup_enabled"):
            parts.append("local backup ON")
        self.backup_status_label.setText("   |   ".join(parts) if parts else "backups OFF (enable in Settings)")

    def choose_local_backup_dir(self) -> None:
        start = self.local_backup_dir_input.text().strip() or str(BASE_DIR)
        path = QFileDialog.getExistingDirectory(self, "Choose local backup folder", start)
        if path:
            self.local_backup_dir_input.setText(path)

    def apply_settings_from_form(self) -> None:
        self.app_config["default_tab"] = self.default_tab_combo.currentText()
        self.app_config["auto_connect_sftp"] = self.sftp_auto_connect_check.isChecked()
        self.app_config["auto_connect_ssh"] = self.ssh_auto_connect_check.isChecked()
        self.app_config["confirm_destructive"] = self.confirm_destructive_check.isChecked()
        self.app_config["terminal_font_size"] = self.terminal_font_spin.value()
        self.app_config["max_log_lines"] = self.max_log_spin.value()
        self.app_config["logging_enabled"] = self.logging_group.isChecked()
        self.app_config["remote_backup_enabled"] = self.remote_backup_group.isChecked()
        self.app_config["local_backup_enabled"] = self.local_backup_group.isChecked()
        self.app_config["mirror_delete_remote"] = (
            self.mirror_group.isChecked() and self.mirror_delete_remote_check.isChecked()
        )
        self.app_config["mirror_delete_local"] = (
            self.mirror_group.isChecked() and self.mirror_delete_local_check.isChecked()
        )
        self.app_config["backup_directory"] = self.backup_dir_input.text().strip() or "/tmp/rasconf_backups"
        self.app_config["local_backup_directory"] = (
            self.local_backup_dir_input.text().strip() or str(BASE_DIR / "backups")
        )
        self.app_config["keep_last_n_backups"] = self.keep_backups_spin.value()
        self.app_config["sftp_enabled"] = self.sftp_group.isChecked()
        self.app_config["ssh_enabled"] = self.ssh_group.isChecked()
        self.app_config["reboot_wait_seconds"] = self.reboot_wait_spin.value()
        self.app_config["reboot_retry_interval"] = self.reboot_retry_spin.value()
        self.app_config["reboot_auto_watch"] = self.reboot_auto_watch_check.isChecked()
        self.save_app_config()
        self.apply_runtime_settings()
        self.refresh_web_tab_urls()
        self.update_backup_status_label()
        self.log("Settings applied")

    def _on_tab_category_toggled(self, _checked: bool = False) -> None:
        """Persist the SFTP/SSH category state and show/hide the matching tab."""
        self.app_config["sftp_enabled"] = self.sftp_group.isChecked()
        self.app_config["ssh_enabled"] = self.ssh_group.isChecked()
        self.save_app_config()
        self.ensure_main_tabs()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "SFTP"))

    def ensure_main_tabs(self) -> None:
        """Add or remove the SFTP / SSH tabs to match the enabled flags. Custom
        browser tabs and Settings are always present."""
        sftp_on = bool(self.app_config.get("sftp_enabled", True))
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        self._set_main_tab(self.sftp_page, "SFTP", sftp_on, 0)
        self._set_main_tab(self.ssh_page, "SSH", ssh_on, 1 if sftp_on else 0)
        self.update_connection_ui()

    def update_connection_ui(self) -> None:
        """Hide the connect/disconnect buttons and header status dots of any
        disabled protocol, and name the panel toggle after the enabled ones.
        When both are off the whole connection panel disappears."""
        sftp_on = bool(self.app_config.get("sftp_enabled", True))
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        self.connect_button.setVisible(sftp_on)
        self.disconnect_button.setVisible(sftp_on)
        self.shell_connect_button.setVisible(ssh_on)
        self.shell_disconnect_button.setVisible(ssh_on)
        self.sftp_caption.setVisible(sftp_on)
        self.sftp_dot.setVisible(sftp_on)
        self.ssh_caption.setVisible(ssh_on)
        self.ssh_dot.setVisible(ssh_on)
        if sftp_on and ssh_on:
            label = "SFTP/SSH settings"
        elif sftp_on:
            label = "SFTP settings"
        elif ssh_on:
            label = "SSH settings"
        else:
            label = "Connection settings"
        self.connection_toggle.setText(label)
        any_on = sftp_on or ssh_on
        self.connection_toggle.setVisible(any_on)
        self.action_toggle_conn.setVisible(any_on)
        self.connection_panel.setVisible(any_on and self.connection_toggle.isChecked())

    def _set_main_tab(self, page: QWidget, title: str, want: bool, desired_index: int) -> None:
        current = self.tabs.indexOf(page)
        if want:
            if current == -1:
                self.tabs.insertTab(min(desired_index, self.tabs.count()), page, title)
        elif current != -1:
            self.tabs.removeTab(current)

    def apply_runtime_settings(self) -> None:
        self.terminal_font.setPointSize(int(self.app_config.get("terminal_font_size", 10)))
        self.terminal_output.setFont(self.terminal_font)
        self.ensure_main_tabs()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "SFTP"))
        default_tab = self.app_config.get("default_tab", "SFTP")
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == default_tab:
                self.tabs.setCurrentIndex(i)
                break
        self.rebuild_quick_bar()

    def _icon_path(self, filename: str):
        """Resolve an icon file from the PyInstaller resources or the source dir."""
        for candidate in (RESOURCE_DIR / filename, BASE_DIR / filename):
            if candidate.is_file():
                return candidate
        return None

    def apply_window_icon(self) -> None:
        """Show the selected icon in the top toolbar and as the window icon."""
        use_alt = bool(self.app_config.get("use_alt_icon"))
        path = self._icon_path("icon2.png" if use_alt else "icon.png") or self._icon_path("icon.png")
        if path is None:
            return
        icon = QIcon(str(path))
        self.setWindowIcon(icon)
        QApplication.setWindowIcon(icon)
        if hasattr(self, "header_icon"):
            pixmap = QPixmap(str(path)).scaled(
                22, 22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.header_icon.setPixmap(pixmap)

    def toggle_window_icon(self, checked: bool) -> None:
        self.app_config["use_alt_icon"] = bool(checked)
        self.save_app_config()
        self.apply_window_icon()

    def refresh_profile_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for name in profile_names():
            self.profile_combo.addItem(name)
        idx = self.profile_combo.findText(active_profile_name())
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.blockSignals(False)

    def on_profile_changed(self, index: int) -> None:
        if index < 0:
            return
        name = self.profile_combo.itemText(index)
        if not name or name == active_profile_name():
            return
        # Every profile now carries the full settings, so switching means
        # flushing the current widgets, swapping the working dict and
        # reloading the whole UI from the newly active profile.
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
        self.source_input.setText(str(cfg.get("source", DEFAULT_PROFILE["source"])))
        self.timeout_input.setValue(int(cfg.get("ssh_timeout", 12)))
        self.trust_host_checkbox.setChecked(bool(cfg.get("trust_unknown_host", False)))
        # Changing this checkbox saves/removes the stored credential, so it
        # must not react while the widgets are being repopulated.
        self.remember_password_checkbox.blockSignals(True)
        self.remember_password_checkbox.setChecked(bool(cfg.get("remember_password", False)))
        self.remember_password_checkbox.blockSignals(False)
        self.password_input.clear()
        self.load_saved_password()

        # Settings tab widgets mirror the profile's settings section.
        self.sftp_auto_connect_check.setChecked(bool(cfg.get("auto_connect_sftp", False)))
        self.ssh_auto_connect_check.setChecked(bool(cfg.get("auto_connect_ssh", False)))
        self.confirm_destructive_check.setChecked(bool(cfg.get("confirm_destructive", True)))
        self.logging_group.setChecked(bool(cfg.get("logging_enabled", True)))
        self.max_log_spin.setValue(int(cfg.get("max_log_lines", 500)))
        self.remote_backup_group.setChecked(bool(cfg.get("remote_backup_enabled", False)))
        self.backup_dir_input.setText(str(cfg.get("backup_directory", "/tmp/rasconf_backups")))
        self.keep_backups_spin.setValue(int(cfg.get("keep_last_n_backups", 5)))
        self.local_backup_group.setChecked(bool(cfg.get("local_backup_enabled", False)))
        self.local_backup_dir_input.setText(str(cfg.get("local_backup_directory", str(BASE_DIR / "backups"))))
        self.mirror_group.setChecked(
            bool(cfg.get("mirror_delete_remote") or cfg.get("mirror_delete_local"))
        )
        self.mirror_delete_remote_check.setChecked(bool(cfg.get("mirror_delete_remote", False)))
        self.mirror_delete_local_check.setChecked(bool(cfg.get("mirror_delete_local", False)))
        self.terminal_font_spin.setValue(int(cfg.get("terminal_font_size", 10)))
        self.reboot_wait_spin.setValue(int(cfg.get("reboot_wait_seconds", 30)))
        self.reboot_retry_spin.setValue(int(cfg.get("reboot_retry_interval", 10)))
        self.reboot_auto_watch_check.setChecked(bool(cfg.get("reboot_auto_watch", True)))
        self.show_hidden_checkbox.setChecked(bool(cfg.get("show_hidden_files", False)))
        self.action_alt_icon.setChecked(bool(cfg.get("use_alt_icon", False)))
        # These two fire _on_tab_category_toggled, which is harmless: it just
        # re-persists the flags and refreshes the tabs done below anyway.
        self.sftp_group.setChecked(bool(cfg.get("sftp_enabled", True)))
        self.ssh_group.setChecked(bool(cfg.get("ssh_enabled", True)))

        self.rebuild_web_tabs()
        self._reload_tabs_combo()
        self.apply_runtime_settings()
        self.update_backup_status_label()
        self.refresh_local_tree()
        self.refresh_web_tab_urls()
        self.save_app_config()

    def new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in profile_names():
            QMessageBox.warning(self, "Duplicate", "A profile with that name already exists.")
            return
        self.save_current_profile_edits()
        # A new profile starts as an exact copy of the current one, settings
        # included, and becomes the active profile right away.
        self.app_config = create_profile(name, self.app_config)
        self.refresh_profile_combo()
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
            self.refresh_profile_combo()
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
        self.refresh_profile_combo()
        self.reload_ui_from_config()
        self.log(f"Deleted profile '{current}'")

    def save_current_profile_edits(self, _value=None) -> None:
        cfg = self.app_config
        cfg["host"] = self.host_input.text().strip() or DEFAULT_PROFILE["host"]
        cfg["port"] = self.port_input.value()
        cfg["username"] = self.username_input.text().strip() or DEFAULT_PROFILE["username"]
        cfg["key_file"] = self.key_input.text().strip()
        cfg["remote_root"] = self.remote_root_input.text().strip() or DEFAULT_PROFILE["remote_root"]
        cfg["source"] = self.source_input.text().strip() or DEFAULT_PROFILE["source"]
        cfg["trust_unknown_host"] = self.trust_host_checkbox.isChecked()
        cfg["remember_password"] = self.remember_password_checkbox.isChecked()
        self.save_app_config()

    def toggle_connection_panel(self, expanded: bool) -> None:
        self.connection_panel.setVisible(expanded)
        self.connection_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.action_toggle_conn.blockSignals(True)
        self.action_toggle_conn.setChecked(expanded)
        self.action_toggle_conn.blockSignals(False)
        self.connection_toggle.blockSignals(True)
        self.connection_toggle.setChecked(expanded)
        self.connection_toggle.blockSignals(False)
        # Re-applies the "both protocols off hides everything" rule, since the
        # panel was just re-shown by the toggle rather than by the flags.
        self.update_connection_ui()

    def set_fullscreen(self, enabled: bool) -> None:
        self.showFullScreen() if enabled else self.showNormal()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "action_fullscreen"):
            self.action_fullscreen.blockSignals(True)
            self.action_fullscreen.setChecked(self.isFullScreen())
            self.action_fullscreen.blockSignals(False)
        super().changeEvent(event)

    def fit_window_to_screen(self) -> None:
        """One-time startup guard: shrink the window only if the default size is
        larger than the screen. It never repositions the window and is not called
        on panel toggle, so the window does not jump around or resize itself."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        max_w = max(320, int(area.width() * 0.95))
        max_h = max(240, int(area.height() * 0.95))
        if self.width() > max_w or self.height() > max_h:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))

    @staticmethod
    def create_tree(title: str) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels([title, "Size", "Modified"])
        tree.setRootIsDecorated(True)
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        tree.setColumnWidth(1, 90)
        tree.setColumnWidth(2, 140)
        tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        return tree

    def current_settings(self, silent: bool = False) -> dict | None:
        try:
            source = Path(self.source_input.text()).expanduser().resolve()
            remote_root = self.remote_root_input.text().strip()
            if not remote_root.startswith("/"):
                raise ValueError("Remote directory must be an absolute path, such as /www_rasconf")
            remote_root = posixpath.normpath(remote_root)
            if remote_root == "/":
                raise ValueError("Remote directory cannot be the filesystem root")
            if not source.is_dir():
                raise ValueError(f"Local source directory does not exist: {source}")
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
            "source": str(source),
            "trust_unknown_host": self.trust_host_checkbox.isChecked(),
            "deploy_ignore": self.app_config.get("deploy_ignore", []),
            "remember_password": self.remember_password_checkbox.isChecked(),
            "ssh_timeout": self.timeout_input.value(),
            "post_deploy_commands": self.app_config.get("post_deploy_commands", []),
            "backup_directory": self.app_config.get("backup_directory", "/tmp/rasconf_backups"),
            "local_backup_directory": self.app_config.get("local_backup_directory", str(BASE_DIR / "backups")),
            "keep_last_n_backups": int(self.app_config.get("keep_last_n_backups", 5)),
            "show_hidden_files": bool(self.app_config.get("show_hidden_files", False)),
        }

    def save_app_config(self, _value=None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        self.app_config["show_hidden_files"] = self.show_hidden_checkbox.isChecked()
        save_app_config_static(self.app_config)

    def save_runtime_settings(self, _value=None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        self.save_app_config()

    def on_show_hidden_toggled(self, show: bool) -> None:
        self.app_config["show_hidden_files"] = show
        self.save_app_config()
        self.refresh_local_tree()

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

    def start_worker(self, operation: str, options: dict | None = None) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if operation != "list" and not self.sftp_connected:
            QMessageBox.information(self, "Not connected", "Connect over SFTP first.")
            return
        settings = self.current_settings()
        if settings is None:
            return

        if not settings["password"] and not settings["key_file"]:
            password, accepted = QInputDialog.getText(
                self,
                "SSH password",
                f"Password for {settings['username']}@{settings['host']} "
                "(leave blank to try SSH agent/default keys):",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return
            self.password_input.setText(password)
            settings["password"] = password

        self.store_password()
        self.save_current_profile_edits()

        self.worker = SftpWorker(operation, settings, options or {}, parent=self)
        self.worker.progress.connect(self.log)
        self.worker.stage.connect(self.on_stage_progress)
        self.worker.succeeded.connect(self.operation_succeeded)
        self.worker.failed.connect(self.operation_failed)
        self.worker.finished.connect(self.operation_finished)
        self.set_busy(True)
        self.log(f"Starting {operation}...")
        self.worker.start()

    def on_stage_progress(self, label: str, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.progress_bar.setFormat(f"{label} - {done}/{total} ({done * 100 // total}%)")

    def set_busy(self, busy: bool) -> None:
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setFormat("Done")
        self.connect_button.setEnabled(not busy)
        for button in (
            self.upload_button,
            self.download_button,
            self.delete_button,
            self.deploy_button,
            self.preview_button,
        ):
            button.setEnabled(not busy and self.sftp_connected)
        self.disconnect_button.setEnabled(not busy and self.sftp_connected)

    def operation_succeeded(self, result) -> None:
        if isinstance(result, dict) and result.get("dry_run"):
            DryRunDialog(result, self).exec()
        elif isinstance(result, list):
            self.remote_tree_entries = result
            self.populate_remote_tree(result)
            self.remote_tree.setEnabled(True)
        self.statusBar().showMessage("Operation completed", 5000)
        self.sftp_connected = True
        self.set_status_dot("sftp", "online")
        self.log("Operation completed successfully")

    def operation_failed(self, message: str) -> None:
        self.statusBar().showMessage("Operation failed", 5000)
        self.log("Error: " + message)
        self.sftp_connected = False
        self.set_status_dot("sftp", "offline")
        QMessageBox.critical(self, "SFTP operation failed", message)

    def operation_finished(self) -> None:
        self.set_busy(False)
        if self.worker is not None:
            operation = self.worker.operation
            self.worker.deleteLater()
            self.worker = None
            # Every SFTP action can change both trees (deploy, upload, delete,
            # mirror cleanup...), so refresh the local view and re-list the
            # remote one. The plain "list" refresh must not re-trigger itself.
            if operation != "list" and self.sftp_connected:
                self.refresh_local_tree()
                QTimer.singleShot(0, lambda: self.start_worker("list"))

    def set_status_dot(self, which: str, mode: str) -> None:
        dot = self.ssh_dot if which == "ssh" else self.sftp_dot
        if mode == "online":
            dot.setObjectName("statusDotOnline")
            dot.setToolTip(f"{which.upper()}: connected")
            self.connected_once = True
        else:
            dot.setObjectName("statusDotOffline")
            dot.setToolTip(f"{which.upper()}: not connected")
        dot.style().unpolish(dot)
        dot.style().polish(dot)

    def log(self, message: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.log_output.append(timestamped)
        if self.app_config.get("logging_enabled", True):
            try:
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

    def connect_remote(self, _checked: bool = False) -> None:
        self.remote_tree.setEnabled(False)
        self.start_worker("list")

    def disconnect_remote(self, _checked: bool = False) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.sftp_connected = False
        self.remote_tree.clear()
        self.remote_tree.setEnabled(False)
        self.remote_tree_entries = []
        self.set_status_dot("sftp", "offline")
        self.set_busy(False)
        self.log("SFTP session closed")
        self.statusBar().showMessage("Disconnected (SFTP)", 4000)

    def preview_deploy(self) -> None:
        self.start_worker(
            "deploy",
            {
                "mirror_remote": bool(self.app_config.get("mirror_delete_remote", False)),
                "mirror_local": bool(self.app_config.get("mirror_delete_local", False)),
                "dry_run": True,
            },
        )

    def refresh_local_tree(self, _checked: bool = False) -> None:
        source = Path(self.source_input.text()).expanduser()
        self.local_tree.clear()
        if not source.is_dir():
            self.local_tree.setHeaderLabels(["Local source files (directory not found)", "Size", "Modified"])
            return
        self.local_tree.setHeaderLabels([f"Local: {source}", "Size", "Modified"])
        root_item = QTreeWidgetItem([source.name or str(source)])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        self.local_tree.addTopLevelItem(root_item)
        self.populate_local_children(root_item, source)
        root_item.setExpanded(True)
        self.apply_filters()

    def on_local_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).text(0) == "Loading...":
            item.takeChildren()
            source = Path(self.source_input.text()).expanduser().resolve()
            relative = item.data(0, Qt.ItemDataRole.UserRole)
            dir_path = (source / relative).resolve() if relative else source
            self.populate_local_children(item, dir_path)

    def populate_local_children(self, parent_item: QTreeWidgetItem, directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
        except OSError as error:
            self.log(f"Unable to list {directory}: {error}")
            return
        source = Path(self.source_input.text()).expanduser().resolve()
        show_hidden = bool(self.app_config.get("show_hidden_files", False))
        for child in children:
            if child.is_symlink():
                continue
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                relative = child.relative_to(source).as_posix()
            except ValueError:
                continue
            item = self._make_local_item(child, relative)
            parent_item.addChild(item)
            if child.is_dir():
                item.addChild(QTreeWidgetItem(["Loading..."]))

    def _make_local_item(self, child: Path, relative: str) -> QTreeWidgetItem:
        size_text = ""
        modified_text = ""
        try:
            info = child.stat()
            if child.is_file():
                size_text = human_size(info.st_size)
            modified_text = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            pass
        item = QTreeWidgetItem([child.name, size_text, modified_text])
        item.setData(0, Qt.ItemDataRole.UserRole, relative)
        item.setToolTip(0, relative or child.name)
        icon = self.style().StandardPixmap.SP_DirIcon if child.is_dir() else self.style().StandardPixmap.SP_FileIcon
        item.setIcon(0, self.style().standardIcon(icon))
        return item

    def populate_remote_tree(self, entries: list[dict]) -> None:
        self.remote_tree.clear()
        self.remote_tree.setHeaderLabels([f"Remote: {self.remote_root_input.text()}", "Size", "Modified"])
        root_item = QTreeWidgetItem([self.remote_root_input.text()])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        self.remote_tree.addTopLevelItem(root_item)
        self.add_remote_children(root_item, entries)
        root_item.setExpanded(True)
        self.apply_filters()

    def add_remote_children(self, parent_item: QTreeWidgetItem, entries: list[dict]) -> None:
        for entry in entries:
            size_text = "" if entry["directory"] else human_size(int(entry.get("size", 0)))
            modified = entry.get("mtime", 0)
            modified_text = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M") if modified else ""
            item = QTreeWidgetItem([entry["name"], size_text, modified_text])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(0, entry["path"] or entry["name"])
            icon = self.style().StandardPixmap.SP_DirIcon if entry["directory"] else self.style().StandardPixmap.SP_FileIcon
            item.setIcon(0, self.style().standardIcon(icon))
            parent_item.addChild(item)
            if entry["directory"]:
                self.add_remote_children(item, entry["children"])

    def apply_filters(self) -> None:
        pattern = self.filter_input.text().strip()
        for tree in (self.local_tree, self.remote_tree):
            self._filter_tree(tree, pattern)

    @staticmethod
    def _filter_match(name: str, pattern: str) -> bool:
        if not pattern:
            return True
        if "*" in pattern or "?" in pattern:
            return GitIgnoreSpec.from_lines([pattern]).match_file(name)
        return pattern.lower() in name.lower()

    def _filter_tree(self, tree: QTreeWidget, pattern: str) -> None:
        def walk(item: QTreeWidgetItem) -> bool:
            visible_children = 0
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    visible_children += 1
            matches = self._filter_match(item.text(0), pattern)
            visible = matches or visible_children > 0 or not pattern
            item.setHidden(not visible)
            return visible

        root = tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))

    def show_local_context_menu(self, position) -> None:
        menu = QMenu(self)
        menu.addAction("Upload selected", self.upload_selected)
        menu.addAction("Refresh", self.refresh_local_tree)
        # position is in the tree's local coordinates; the menu needs global
        # ones, otherwise it lands at the same offset on the primary screen.
        menu.exec(self.local_tree.mapToGlobal(position))

    def show_remote_context_menu(self, position) -> None:
        menu = QMenu(self)
        selected = self.remote_tree.selectedItems()
        if selected:
            menu.addAction("Download selected", self.download_selected)
            menu.addAction("Delete selected", self.delete_selected)
            if len(selected) == 1:
                menu.addAction("Rename...", self.rename_remote_item)
                menu.addAction("Copy path", self.copy_selected_remote_path)
        menu.addSeparator()
        menu.addAction("Refresh remote", self.connect_remote)
        menu.exec(self.remote_tree.mapToGlobal(position))

    def copy_selected_remote_path(self) -> None:
        items = self.remote_tree.selectedItems()
        if not items:
            return
        path = items[0].data(0, Qt.ItemDataRole.UserRole)
        QApplication.clipboard().setText(path)
        self.statusBar().showMessage(f"Copied: {path}", 3000)

    def connect_args_for_selection(self, tree: QTreeWidget, title: str) -> list[str] | None:
        paths = self.selected_paths(tree)
        if not paths:
            QMessageBox.information(self, title, "Select one or more files or directories first.")
            return None
        return paths

    @staticmethod
    def selected_paths(tree: QTreeWidget) -> list[str]:
        paths = [item.data(0, Qt.ItemDataRole.UserRole) for item in tree.selectedItems()]
        return SftpWorker.normalize_paths(paths)

    def upload_selected(self, _checked: bool = False) -> None:
        paths = self.connect_args_for_selection(self.local_tree, "Upload")
        if paths is not None:
            self.start_worker("upload", {"paths": paths})

    def download_selected(self, _checked: bool = False) -> None:
        paths = self.connect_args_for_selection(self.remote_tree, "Download")
        if paths is None:
            return
        destination = QFileDialog.getExistingDirectory(self, "Download into", str(DEFAULT_SOURCE.parent))
        if destination:
            self.start_worker("download", {"paths": paths, "destination": destination})

    def delete_selected(self, _checked: bool = False) -> None:
        paths = self.connect_args_for_selection(self.remote_tree, "Delete remote selection")
        if paths is None:
            return
        if "" in paths:
            QMessageBox.warning(self, "Delete remote selection", "The remote root cannot be deleted.")
            return
        if self.app_config.get("confirm_destructive", True):
            answer = QMessageBox.question(
                self,
                "Confirm remote deletion",
                "Permanently delete the selected remote files/directories?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_worker("delete", {"paths": paths})

    def rename_remote_item(self) -> None:
        items = self.remote_tree.selectedItems()
        if len(items) != 1:
            return
        path = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        current_name = posixpath.basename(path)
        new_name, ok = QInputDialog.getText(self, "Rename remote item", "New name:", text=current_name)
        if ok and new_name and new_name != current_name:
            self.start_worker("rename", {"path": path, "new_name": new_name})

    def deploy_source(self, _checked: bool = False) -> None:
        mirror_remote = bool(self.app_config.get("mirror_delete_remote", False))
        mirror_local = bool(self.app_config.get("mirror_delete_local", False))
        if self.app_config.get("confirm_destructive", True) or mirror_remote or mirror_local:
            message = (
                "Upload changed files from the local source to the configured remote directory?\n\n"
                "Unchanged files are skipped, and deploy ignore patterns are never uploaded."
            )
            if mirror_remote:
                message += "\n\nServer cleanup is ON: remote files absent locally will be DELETED."
            if mirror_local:
                message += "\n\nLocal cleanup is ON: local files absent on the server will be DELETED."
            answer = QMessageBox.question(
                self,
                "Deploy source files",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_worker(
            "deploy",
            {
                "mirror_remote": mirror_remote,
                "mirror_local": mirror_local,
                "backup": bool(self.app_config.get("remote_backup_enabled", False)),
                "backup_local": bool(self.app_config.get("local_backup_enabled", False)),
                "dry_run": False,
            },
        )

    def choose_key_file(self, _checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH private key")
        if path:
            self.key_input.setText(path)
            self.save_current_profile_edits()

    def choose_source(self, _checked: bool = False) -> None:
        start_dir = self.source_input.text()
        if not Path(start_dir).is_dir():
            start_dir = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose local source", start_dir)
        if path:
            self.source_input.setText(path)
            self.save_current_profile_edits()
            self.refresh_local_tree()

    def adjust_terminal_font(self, delta: int) -> None:
        size = max(6, min(24, self.terminal_font.pointSize() + delta))
        self.terminal_font.setPointSize(size)
        self.terminal_output.setFont(self.terminal_font)
        self.app_config["terminal_font_size"] = size
        self.terminal_font_spin.setValue(size)
        self.save_app_config()

    def clear_terminal(self) -> None:
        self.terminal_output.clear()
        self.reset_terminal_state()

    def reset_terminal_state(self) -> None:
        """Drop all pending terminal rendering state and start a fresh output line."""
        self.term_partial = ""
        self.term_cells = []
        self.term_pen = 0
        self.term_saved_pen = 0
        self.current_term_color = "#e0e0e0"
        cursor = QTextCursor(self.terminal_output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.term_line_doc_pos = cursor.position()

    def append_terminal_output(self, text: str) -> None:
        """Feed raw PTY bytes through a minimal line renderer.

        Emulates carriage-return overwriting and the ESC 7 / ESC 8 (save and
        restore cursor) sequences that apk's progress bar uses, so repeated
        redraws replace the current line instead of being appended as garbage.
        """
        text = self.term_partial + text
        self.term_partial = ""
        index = text.rfind("\x1b")
        if index != -1:
            tail = text[index:]
            incomplete = (
                tail == "\x1b"
                or (tail.startswith("\x1b[") and not re.fullmatch(r"\x1b\[[0-9;?]*[a-zA-Z]", tail))
                or (tail.startswith("\x1b]") and "\x07" not in tail[2:] and not tail.endswith("\x1b\\"))
            )
            if incomplete:
                self.term_partial = tail
                text = text[:index]
        dirty = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                consumed = self.handle_terminal_escape(text, i)
                dirty = True
                i += max(consumed, 1)
                continue
            if ch == "\n":
                self.flush_terminal_line(newline=True)
                dirty = False
                i += 1
                continue
            if ch == "\r":
                self.term_pen = 0
                dirty = True
                i += 1
                continue
            if ch == "\x08":
                self.term_pen = max(0, self.term_pen - 1)
                dirty = True
                i += 1
                continue
            if ch == "\t":
                self.term_pen = (self.term_pen // 8 + 1) * 8
                dirty = True
                i += 1
                continue
            if ch == "\x07" or ch in "\x00\x0b\x0c":
                i += 1
                continue
            self.put_terminal_char(ch)
            dirty = True
            i += 1
        if dirty:
            self.flush_terminal_line(newline=False)

    def handle_terminal_escape(self, text: str, index: int) -> int:
        """Consume one escape sequence starting at index; return characters used."""
        rest = text[index:]
        if len(rest) < 2:
            return 1
        if rest[1] == "7":
            self.term_saved_pen = self.term_pen
            return 2
        if rest[1] == "8":
            self.term_pen = self.term_saved_pen
            return 2
        match = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z])", rest)
        if match:
            self.apply_terminal_csi(match.group(1), match.group(2))
            return match.end()
        match = re.match(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", rest)
        if match:
            return match.end()
        match = re.match(r"\x1b[()#][A-Z0-9]?", rest)
        if match:
            return match.end()
        return 2  # any other two-character escape (ESC c, ESC M, ...)

    def apply_terminal_csi(self, params: str, command: str) -> None:
        if command == "m":
            for token in params.split(";"):
                token = token.strip("?")
                code = int(token) if token.isdigit() else 0
                if code in (0, 39):
                    self.current_term_color = "#e0e0e0"
                elif 30 <= code <= 37:
                    palette = ['#1a1a1a', '#c51a4a', '#23d18b', '#d7ba7d', '#3b8eea', '#c586c0', '#29b8db', '#e5e5e5']
                    self.current_term_color = palette[code - 30]
                elif 90 <= code <= 97:
                    palette = ['#666666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#e5e5e5']
                    self.current_term_color = palette[code - 90]
            return
        if command == "K":
            mode = int(params) if params.isdigit() else 0
            if mode == 0:
                self.term_cells = self.term_cells[:self.term_pen]
            elif mode == 1:
                self.term_cells = self.term_cells[self.term_pen:]
                self.term_pen = 0
            else:
                self.term_cells = []
                self.term_pen = 0
                self.term_saved_pen = 0
            return
        # Cursor moves, erase display, and mode sets have no meaning in the
        # single-overwritable-line model; ignore them like before.

    def put_terminal_char(self, ch: str) -> None:
        while len(self.term_cells) <= self.term_pen:
            pad_color = self.term_cells[-1][1] if self.term_cells else self.current_term_color
            self.term_cells.append((" ", pad_color))
        self.term_cells[self.term_pen] = (ch, self.current_term_color)
        self.term_pen += 1

    def render_terminal_cells(self) -> str:
        parts: list[str] = []
        run_color = None
        run: list[str] = []

        def flush_run() -> None:
            if not run:
                return
            text = "".join(run)
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text = text.replace(" ", "&nbsp;")
            parts.append(f'<span style="color: {run_color};">{text}</span>')

        for ch, color in self.term_cells:
            if color != run_color:
                flush_run()
                run.clear()
                run_color = color
            run.append(ch)
        flush_run()
        return "".join(parts)

    def flush_terminal_line(self, newline: bool) -> None:
        """Replace the current output line in the widget with the rendered cells."""
        html = self.render_terminal_cells()
        if newline:
            html += "<br>"
        cursor = self.terminal_output.textCursor()
        cursor.setPosition(self.term_line_doc_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        if html:
            cursor.insertHtml(html)
        self.terminal_output.setTextCursor(cursor)
        if newline:
            self.term_cells = []
            self.term_pen = 0
            self.term_saved_pen = 0
            self.term_line_doc_pos = cursor.position()
        self.terminal_output.ensureCursorVisible()

    def terminal_write_message(self, text: str) -> None:
        """Write a local status message through the same renderer as shell data."""
        for ch in text:
            if ch == "\n":
                self.flush_terminal_line(newline=True)
            else:
                self.put_terminal_char(ch)
        self.flush_terminal_line(newline=False)

    def connect_shell(self, _checked: bool = False) -> None:
        if self.shell_worker is not None and self.shell_worker.isRunning():
            return
        settings = self.current_settings()
        if settings is None:
            return
        if not settings["password"] and not settings["key_file"]:
            password, accepted = QInputDialog.getText(
                self,
                "SSH password",
                f"Password for {settings['username']}@{settings['host']} "
                "(leave blank to try SSH agent/default keys):",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return
            self.password_input.setText(password)
            settings["password"] = password

        self.store_password()
        self.save_current_profile_edits()
        self.terminal_output.clear()
        self.reset_terminal_state()
        self.terminal_write_message(
            f"Connecting to {settings['username']}@{settings['host']}:{settings['port']}...\n"
        )
        self.shell_worker = SSHShellWorker(settings, parent=self)
        self.shell_worker.connected.connect(self.shell_connected)
        self.shell_worker.received.connect(self.append_terminal_output)
        self.shell_worker.failed.connect(self.shell_failed)
        self.shell_worker.disconnected.connect(self.shell_disconnected)
        self.shell_worker.start()
        self.shell_connect_button.setEnabled(False)

    def shell_connected(self) -> None:
        self.terminal_write_message("SSH shell connected.\n")
        self.shell_disconnect_button.setEnabled(True)
        self.terminal_send_button.setEnabled(True)
        self.terminal_input.setFocus()
        self.set_status_dot("ssh", "online")

    def shell_failed(self, message: str) -> None:
        self.terminal_write_message(f"SSH error: {message}\n")
        QMessageBox.critical(self, "SSH terminal failed", message)

    def shell_disconnected(self) -> None:
        self.terminal_write_message("SSH shell disconnected.\n")
        self.shell_connect_button.setEnabled(True)
        self.shell_disconnect_button.setEnabled(False)
        self.terminal_send_button.setEnabled(False)
        self.set_status_dot("ssh", "offline")
        if self.shell_worker is not None:
            self.shell_worker.deleteLater()
            self.shell_worker = None

    def disconnect_shell(self, _checked: bool = False) -> None:
        if self.shell_worker is not None:
            self.shell_worker.stop()

    # ------------------------------------------------------------------
    # Reboot watch - wait out a reboot, then retry until the device answers
    # ------------------------------------------------------------------

    REBOOT_COMMAND_PATTERN = re.compile(r"^(?:sudo\s+)?(?:busybox\s+)?(?:reboot|shutdown\s+-r)\b")

    def _looks_like_reboot(self, command: str) -> bool:
        """Match the commands that take the device down; ``shutdown -n`` only
        reboots in memory, so it is left alone."""
        stripped = command.strip()
        return bool(self.REBOOT_COMMAND_PATTERN.match(stripped)) and "-n" not in stripped

    def reboot_device(self) -> None:
        """Send the reboot command and start the wait-and-retry cycle."""
        if self.reboot_worker is not None and self.reboot_worker.isRunning():
            return
        settings = self.current_settings()
        if settings is None:
            return
        answer = QMessageBox.question(
            self,
            "Reboot device",
            f"Reboot {settings['username']}@{settings['host']} and wait for it to come back?\n\n"
            f"First attempt after {int(self.app_config.get('reboot_wait_seconds', 30))} s, "
            f"then every {int(self.app_config.get('reboot_retry_interval', 10))} s "
            "(both set in the Settings tab).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.shell_worker is not None and self.shell_worker.isRunning():
            # A live shell: send it there so the output shows in the terminal.
            self.terminal_write_message("Sending: reboot\n")
            self.shell_worker.send_line("reboot")
            self._start_reboot_watch(None)
        else:
            self._start_reboot_watch("reboot")

    def offer_reboot_watch(self) -> None:
        """Ask whether to start the watch after a reboot was sent by hand. The
        offer itself is disabled by the Settings checkbox."""
        if not self.app_config.get("reboot_auto_watch", True):
            return
        if self.reboot_worker is not None and self.reboot_worker.isRunning():
            return
        answer = QMessageBox.question(
            self,
            "Watch for the device?",
            "That looks like a reboot command. Wait for the device to go down and "
            "then retry the connection until it is back?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_reboot_watch(None)

    def _start_reboot_watch(self, reboot_command: str | None) -> None:
        """Run the wait/retry loop off the UI thread. `reboot_command` is only
        passed when nothing has sent the reboot yet."""
        settings = self.current_settings(silent=True)
        if settings is None:
            self.log("Cannot start the reboot wait: the connection details are incomplete")
            return
        self.reboot_worker = RebootWaitWorker(
            settings,
            int(self.app_config.get("reboot_wait_seconds", 30)),
            int(self.app_config.get("reboot_retry_interval", 10)),
            reboot_command,
            parent=self,
        )
        self.reboot_worker.status.connect(self.on_reboot_status)
        self.reboot_worker.connected.connect(self.on_reboot_back)
        self.reboot_worker.failed.connect(lambda msg: self.on_reboot_status(f"Reboot watch failed: {msg}"))
        self.reboot_worker.finished.connect(self.reboot_watch_finished)
        self.reboot_button.setEnabled(False)
        self.reboot_cancel_button.setEnabled(True)
        self.tabs.setCurrentWidget(self.ssh_page)
        self.reboot_worker.start()

    def on_reboot_status(self, message: str) -> None:
        self.terminal_write_message(f"[reboot] {message}\n")
        self.statusBar().showMessage(message)

    def on_reboot_back(self) -> None:
        self.statusBar().showMessage("Device is back online", 5000)
        self.log("Device is back online after reboot")
        if self.app_config.get("ssh_enabled", True):
            self.connect_shell()

    def reboot_watch_finished(self) -> None:
        if self.reboot_worker is not None:
            self.reboot_worker.deleteLater()
            self.reboot_worker = None
        self.reboot_button.setEnabled(True)
        self.reboot_cancel_button.setEnabled(False)

    def cancel_reboot_watch(self, _checked: bool = False) -> None:
        if self.reboot_worker is not None and self.reboot_worker.isRunning():
            self.reboot_worker.cancel()
            self.statusBar().showMessage("Reboot wait cancelled", 4000)
            self.log("Reboot wait cancelled")

    def send_terminal_input(self) -> None:
        if self.shell_worker is None or not self.shell_worker.isRunning():
            return
        command = self.terminal_input.text()
        if command.strip():
            if not self.terminal_history or self.terminal_history[-1] != command:
                self.terminal_history.append(command)
            limit = int(self.app_config.get("terminal_history_limit", 500))
            self.terminal_history = self.terminal_history[-limit:]
            self.terminal_history_pos = len(self.terminal_history)
            save_command_history(self.terminal_history)
        self.shell_worker.send_line(command)
        if self._looks_like_reboot(command):
            QTimer.singleShot(300, self.offer_reboot_watch)
        self.terminal_input.clear()

    def edit_ignore_patterns(self) -> None:
        dialog = StringListDialog(
            "Deploy ignore patterns",
            "Git-ignore-style patterns relative to the local source directory.",
            self.app_config.get("deploy_ignore", []),
            self,
        )
        if dialog.exec():
            self.app_config["deploy_ignore"] = dialog.values()
            self.save_app_config()
            self.log(f"Updated deploy ignore patterns ({len(self.app_config['deploy_ignore'])} entries)")

    def edit_post_deploy_commands(self) -> None:
        dialog = StringListDialog(
            "Post-deploy commands",
            "Shell commands run on the remote device after each successful deploy.",
            self.app_config.get("post_deploy_commands", []),
            self,
            monospace=True,
        )
        if dialog.exec():
            self.app_config["post_deploy_commands"] = dialog.values()
            self.save_app_config()
            self.log(f"Updated post-deploy commands ({len(self.app_config['post_deploy_commands'])} entries)")

    def edit_quick_commands(self) -> None:
        current = self.app_config.get("quick_commands", [])
        lines = [f"{c['label']}||{c['command']}" for c in current]
        dialog = StringListDialog(
            "Quick commands",
            "Each entry is 'Label||command'. Runs in the SSH tab, or as a one-off if not connected.",
            lines,
            self,
            monospace=True,
        )
        if dialog.exec():
            updated = []
            for line in dialog.values():
                if "||" in line:
                    label, command = line.split("||", 1)
                else:
                    label = command = line
                command = command.strip()
                if not command:
                    continue
                updated.append({"label": label.strip() or command, "command": command})
            self.app_config["quick_commands"] = updated
            self.save_app_config()
            self.rebuild_quick_bar()

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
        # Accepts both the current profile document and any older flat layout.
        self.app_config = import_app_config(loaded)
        self.refresh_profile_combo()
        self.reload_ui_from_config()
        QMessageBox.information(self, "Imported", "Configuration replaced from file.")

    def save_log_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log",
            str(BASE_DIR / f"rasconf_log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"),
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
        box.setWindowTitle("About Rasconf Manager")
        box.setText(
            "<b>Rasconf Manager V1.3 (10/9/2026)</b><br>"
            "Manage Router & Web interfaces.<br>"
            'GitHub: <a href="{repo}" style="color: #c51a4a">fligma/raspiOpenWrt</a><br>'
            'License: <a href="{license}" style="color: #c51a4a">MIT License</a><br><br>'
            "Copyright &copy; 2026 fligma. Licensed under the MIT License.".format(
                repo=PROJECT_GITHUB_URL, license=PROJECT_LICENSE_URL
            )
        )
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        for candidate in (RESOURCE_DIR / "icon.png", BASE_DIR / "icon.png"):
            if candidate.is_file():
                box.layout().activate()
                side = max(64, box.sizeHint().height())
                logo = QPixmap(str(candidate)).scaled(
                    side, side,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                box.setIconPixmap(logo)
                break
        box.exec()

    def closeEvent(self, event) -> None:
        self.save_current_profile_edits()
        if self.reboot_worker is not None and self.reboot_worker.isRunning():
            self.reboot_worker.cancel()
            self.reboot_worker.wait(1500)
        if self.shell_worker is not None:
            self.shell_worker.stop()
            self.shell_worker.wait(1500)
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Transfer in progress",
                "A file operation is still running. Abort and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)
