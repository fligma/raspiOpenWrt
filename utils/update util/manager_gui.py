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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer, QEvent
from PyQt6.QtGui import QAction, QFont, QIcon, QKeySequence, QTextCursor

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
    DEFAULT_APP_CONFIG,
    DEFAULT_PROFILE,
    DEFAULT_SOURCE,
    HISTORY_FILE,
    KEYRING_SERVICE,
    KNOWN_HOSTS_FILE,
    LOG_FILE,
    MAX_COMMAND_HISTORY,
    REPOSITORY_ROOT,
    RESOURCE_DIR,
)
from manager_config import (
    _coerce_int,
    _migrate_legacy_config,
    _sync_flat_mirror,
    load_app_config,
    load_command_history,
    save_app_config_static,
    save_command_history,
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
from manager_workers import RemoteExecWorker, SftpWorker, SSHShellWorker
from manager_dialogs import DryRunDialog, StringListDialog


class RasconfManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: SftpWorker | None = None
        self.shell_worker: SSHShellWorker | None = None
        self.exec_worker: RemoteExecWorker | None = None
        self.remote_tree_entries: list[dict] = []
        self.app_config = load_app_config()
        self.current_term_color = "#e0e0e0"
        self.terminal_history: list[str] = load_command_history()
        self.terminal_history_pos = len(self.terminal_history)
        self.connected_once = False
        self.setWindowTitle("rasconf Manager")
        self.resize(1180, 780)

        self.build_menu()
        self.build_ui()
        self.apply_runtime_settings()
        self.refresh_local_tree()

        if self.app_config.get("auto_connect"):
            self.toggle_connection_panel(False)
            QTimer.singleShot(400, self.connect_remote)

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
        self.action_clear_terminal.triggered.connect(lambda: self.terminal_output.clear())
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
        header_row = QHBoxLayout()
        header_row.addWidget(self.connection_toggle)
        self.status_dot = QLabel("\u25CF")
        self.status_dot.setObjectName("statusDotOffline")
        self.status_dot.setToolTip("Not connected")
        header_row.addWidget(self.status_dot)
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
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primaryAction")
        self.connect_button.clicked.connect(self.connect_remote)

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
        connection_layout.addWidget(self.trust_host_checkbox, 5, 0, 1, 2)
        connection_layout.addWidget(self.connect_button, 5, 3)
        root_layout.addWidget(connection_group)
        self.connection_panel = connection_group

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, 1)

        self.build_sftp_tab()
        self.build_ssh_tab()
        self.build_web_tab()
        self.build_luci_tab()
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
        self.shell_connect_button = QPushButton("Open SSH terminal")
        self.shell_connect_button.setObjectName("primaryAction")
        self.shell_connect_button.clicked.connect(self.connect_shell)
        self.shell_disconnect_button = QPushButton("Disconnect")
        self.shell_disconnect_button.setEnabled(False)
        self.shell_disconnect_button.clicked.connect(self.disconnect_shell)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(lambda: self.terminal_output.clear())
        terminal_actions.addWidget(self.shell_connect_button)
        terminal_actions.addWidget(self.shell_disconnect_button)
        terminal_actions.addWidget(clear_button)
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
        if self.exec_worker is not None and self.exec_worker.isRunning():
            QMessageBox.information(self, "Busy", "Another command is still running.")
            return
        self.exec_worker = RemoteExecWorker(settings, [command], parent=self)
        self.exec_worker.log.connect(self.log)
        self.exec_worker.finished_ok.connect(lambda: self.statusBar().showMessage("Quick command completed", 4000))
        self.exec_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Command failed", msg))
        self.tabs.setCurrentIndex(0)
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

    def _build_web_like_tab(self, initial_url: str, title: str):
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
            self.tabs.addTab(page, title)
            return url_input, None

        view = QWebEngineView()

        def load_url():
            view.setUrl(QUrl(url_input.text()))

        def go_home():
            url_input.setText(initial_url)
            load_url()

        go_button.clicked.connect(load_url)
        home_button.clicked.connect(go_home)
        url_input.returnPressed.connect(load_url)
        layout.addWidget(view, 1)
        self.tabs.addTab(page, title)
        return url_input, view

    def build_web_tab(self) -> None:
        host = self.app_config.get("host", "192.168.1.1")
        port = int(self.app_config.get("web_port", 8989))
        path = self.app_config.get("web_path", "/cgi-bin/index.py")
        url = f"http://{host}:{port}{path}"
        self.web_url_input, self.web_view = self._build_web_like_tab(url, "Web Interface")
        if self.web_view is not None:
            QTimer.singleShot(200, lambda: self.web_view.setUrl(QUrl(self.web_url_input.text())))

    def build_luci_tab(self) -> None:
        url = self.app_config.get("luci_url", "http://192.168.1.1/")
        self.luci_url_input, self.luci_view = self._build_web_like_tab(url, "LuCI")
        if self.luci_view is not None:
            QTimer.singleShot(200, lambda: self.luci_view.setUrl(QUrl(self.luci_url_input.text())))

    def _make_toggle_group(self, title: str, checked: bool) -> tuple[QGroupBox, QFormLayout]:
        """Return a checkable group box whose indented body hides when the toggle is off."""
        group = QGroupBox(title)
        group.setCheckable(True)
        group.setChecked(checked)
        outer = QVBoxLayout(group)
        outer.setContentsMargins(22, 14, 12, 10)
        body = QWidget()
        body.setVisible(checked)
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        outer.addWidget(body)
        group.toggled.connect(body.setVisible)
        return group, form

    def build_settings_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(16)

        iface_group = QGroupBox("Interface & connection")
        iface_form = QFormLayout(iface_group)
        iface_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        iface_form.setHorizontalSpacing(12)
        iface_form.setVerticalSpacing(8)

        self.web_port_input = QSpinBox()
        self.web_port_input.setRange(1, 65535)
        self.web_port_input.setValue(int(self.app_config.get("web_port", 8989)))
        iface_form.addRow("Web interface port", self.web_port_input)

        self.web_path_input = QLineEdit(self.app_config.get("web_path", "/cgi-bin/index.py"))
        iface_form.addRow("Web interface path", self.web_path_input)

        self.luci_url_field = QLineEdit(self.app_config.get("luci_url", "http://192.168.1.1/"))
        iface_form.addRow("LuCI URL", self.luci_url_field)

        self.default_tab_combo = QComboBox()
        self.default_tab_combo.addItems(["SFTP", "SSH", "Web Interface", "LuCI", "Settings"])
        idx = self.default_tab_combo.findText(self.app_config.get("default_tab", "SFTP"))
        self.default_tab_combo.setCurrentIndex(idx if idx >= 0 else 0)
        iface_form.addRow("Default tab", self.default_tab_combo)

        self.auto_connect_check = QCheckBox("Connect automatically at startup")
        self.auto_connect_check.setChecked(bool(self.app_config.get("auto_connect", False)))
        iface_form.addRow(self.auto_connect_check)

        self.confirm_destructive_check = QCheckBox("Confirm destructive operations (delete, mirror)")
        self.confirm_destructive_check.setChecked(bool(self.app_config.get("confirm_destructive", True)))
        iface_form.addRow(self.confirm_destructive_check)

        self.terminal_font_spin = QSpinBox()
        self.terminal_font_spin.setRange(6, 24)
        self.terminal_font_spin.setValue(int(self.app_config.get("terminal_font_size", 10)))
        iface_form.addRow("Terminal font size", self.terminal_font_spin)

        vbox.addWidget(iface_group)

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
        vbox.addWidget(self.logging_group)

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
        vbox.addWidget(self.remote_backup_group)

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
        vbox.addWidget(self.local_backup_group)

        self.mirror_group, mirror_form = self._make_toggle_group(
            "Mirror cleanup on deploy (destructive)",
            bool(
                self.app_config.get("mirror_delete_remote", False)
                or self.app_config.get("mirror_delete_local", False)
            ),
        )
        self.mirror_delete_remote_check = QCheckBox("Delete server files not on local")
        self.mirror_delete_remote_check.setToolTip(
            "After uploading, remove remote files and directories that do not exist in the local source. "
            "Files matching the deploy ignore patterns are never touched."
        )
        self.mirror_delete_remote_check.setChecked(bool(self.app_config.get("mirror_delete_remote", False)))
        mirror_form.addRow(self.mirror_delete_remote_check)
        self.mirror_delete_local_check = QCheckBox("Delete local files not on server")
        self.mirror_delete_local_check.setToolTip(
            "Remove files and directories from the local source that do not exist on the server "
            "(compared before the deploy starts). Files matching the deploy ignore patterns are never touched."
        )
        self.mirror_delete_local_check.setChecked(bool(self.app_config.get("mirror_delete_local", False)))
        mirror_form.addRow(self.mirror_delete_local_check)
        vbox.addWidget(self.mirror_group)

        vbox.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        buttons_row = QHBoxLayout()
        edit_ignores_btn = QPushButton("Edit deploy ignore patterns...")
        edit_ignores_btn.clicked.connect(self.edit_ignore_patterns)
        edit_post_btn = QPushButton("Edit post-deploy commands...")
        edit_post_btn.clicked.connect(self.edit_post_deploy_commands)
        edit_quick_btn = QPushButton("Edit quick commands...")
        edit_quick_btn.clicked.connect(self.edit_quick_commands)
        buttons_row.addWidget(edit_ignores_btn)
        buttons_row.addWidget(edit_post_btn)
        buttons_row.addWidget(edit_quick_btn)
        outer.addLayout(buttons_row)

        save_btn = QPushButton("Apply settings")
        save_btn.setObjectName("primaryAction")
        save_btn.clicked.connect(self.apply_settings_from_form)
        outer.addWidget(save_btn)
        outer.addStretch(1)

        self.tabs.addTab(page, "Settings")

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
        self.app_config["web_port"] = self.web_port_input.value()
        self.app_config["web_path"] = self.web_path_input.text().strip() or "/cgi-bin/index.py"
        self.app_config["luci_url"] = self.luci_url_field.text().strip()
        self.app_config["default_tab"] = self.default_tab_combo.currentText()
        self.app_config["auto_connect"] = self.auto_connect_check.isChecked()
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
        self.save_app_config()
        self.apply_runtime_settings()
        host = self.host_input.text().strip() or self.app_config.get("host", "192.168.1.1")
        self.web_url_input.setText(f"http://{host}:{self.app_config['web_port']}{self.app_config['web_path']}")
        self.luci_url_input.setText(self.app_config["luci_url"])
        self.update_backup_status_label()
        self.log("Settings applied")

    def apply_runtime_settings(self) -> None:
        self.terminal_font.setPointSize(int(self.app_config.get("terminal_font_size", 10)))
        self.terminal_output.setFont(self.terminal_font)
        default_tab = self.app_config.get("default_tab", "SFTP")
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == default_tab:
                self.tabs.setCurrentIndex(i)
                break
        self.rebuild_quick_bar()

    def refresh_profile_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for prof in self.app_config["profiles"]:
            self.profile_combo.addItem(prof["name"])
        idx = self.profile_combo.findText(self.app_config.get("active_profile", ""))
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.blockSignals(False)

    def on_profile_changed(self, index: int) -> None:
        if index < 0:
            return
        name = self.profile_combo.itemText(index)
        profile = next((p for p in self.app_config["profiles"] if p["name"] == name), None)
        if not profile:
            return
        self.app_config["active_profile"] = name
        self.app_config = _sync_flat_mirror(self.app_config)
        self.host_input.setText(profile["host"])
        self.port_input.setValue(profile["port"])
        self.username_input.setText(profile["username"])
        self.key_input.setText(profile["key_file"])
        self.remote_root_input.setText(profile["remote_root"])
        self.source_input.setText(profile["source"])
        self.trust_host_checkbox.setChecked(profile["trust_unknown_host"])
        self.remember_password_checkbox.setChecked(profile["remember_password"])
        self.password_input.clear()
        self.load_saved_password()
        self.refresh_local_tree()
        self.save_app_config()

    def new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p["name"] == name for p in self.app_config["profiles"]):
            QMessageBox.warning(self, "Duplicate", "A profile with that name already exists.")
            return
        self.save_current_profile_edits()
        base = {
            "name": name,
            "host": self.host_input.text().strip() or DEFAULT_PROFILE["host"],
            "port": self.port_input.value(),
            "username": self.username_input.text().strip() or DEFAULT_PROFILE["username"],
            "key_file": self.key_input.text().strip(),
            "remote_root": self.remote_root_input.text().strip() or DEFAULT_PROFILE["remote_root"],
            "source": self.source_input.text().strip() or str(DEFAULT_SOURCE),
            "trust_unknown_host": self.trust_host_checkbox.isChecked(),
            "remember_password": False,
        }
        self.app_config["profiles"].append(base)
        self.app_config["active_profile"] = name
        self.app_config = _sync_flat_mirror(self.app_config)
        self.refresh_profile_combo()
        idx = self.profile_combo.findText(name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.save_app_config()

    def rename_current_profile(self) -> None:
        current = self.app_config.get("active_profile")
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=current or "")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p["name"] == name for p in self.app_config["profiles"]):
            QMessageBox.warning(self, "Duplicate", "Another profile already has that name.")
            return
        for p in self.app_config["profiles"]:
            if p["name"] == current:
                p["name"] = name
                break
        self.app_config["active_profile"] = name
        self.refresh_profile_combo()
        self.save_app_config()

    def delete_current_profile(self) -> None:
        current = self.app_config.get("active_profile")
        if len(self.app_config["profiles"]) <= 1:
            QMessageBox.information(self, "Cannot delete", "At least one profile must exist.")
            return
        answer = QMessageBox.question(
            self, "Delete profile",
            f"Delete profile '{current}'? (This only removes saved connection details.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.app_config["profiles"] = [p for p in self.app_config["profiles"] if p["name"] != current]
        self.app_config["active_profile"] = self.app_config["profiles"][0]["name"]
        self.app_config = _sync_flat_mirror(self.app_config)
        self.refresh_profile_combo()
        self.on_profile_changed(max(0, self.profile_combo.currentIndex()))
        self.save_app_config()

    def save_current_profile_edits(self, _value=None) -> None:
        current = self.app_config.get("active_profile")
        target = next((p for p in self.app_config["profiles"] if p["name"] == current), None)
        if target is None:
            target = self.app_config["profiles"][0]
            self.app_config["active_profile"] = target["name"]
        target["host"] = self.host_input.text().strip() or DEFAULT_PROFILE["host"]
        target["port"] = self.port_input.value()
        target["username"] = self.username_input.text().strip() or DEFAULT_PROFILE["username"]
        target["key_file"] = self.key_input.text().strip()
        target["remote_root"] = self.remote_root_input.text().strip() or DEFAULT_PROFILE["remote_root"]
        target["source"] = self.source_input.text().strip() or DEFAULT_PROFILE["source"]
        target["trust_unknown_host"] = self.trust_host_checkbox.isChecked()
        target["remember_password"] = self.remember_password_checkbox.isChecked()
        self.app_config = _sync_flat_mirror(self.app_config)
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
        for button in (
            self.connect_button,
            self.upload_button,
            self.download_button,
            self.delete_button,
            self.deploy_button,
        ):
            button.setEnabled(not busy)

    def operation_succeeded(self, result) -> None:
        if isinstance(result, dict) and result.get("dry_run"):
            DryRunDialog(result, self).exec()
        elif isinstance(result, list):
            self.remote_tree_entries = result
            self.populate_remote_tree(result)
            self.remote_tree.setEnabled(True)
            if (
                self.worker is not None
                and self.worker.operation == "deploy"
                and self.app_config.get("mirror_delete_local")
            ):
                self.refresh_local_tree()
        self.statusBar().showMessage("Operation completed", 5000)
        self.set_status_dot("online")
        self.log("Operation completed successfully")

    def operation_failed(self, message: str) -> None:
        self.statusBar().showMessage("Operation failed", 5000)
        self.log("Error: " + message)
        self.set_status_dot("offline")
        QMessageBox.critical(self, "SFTP operation failed", message)

    def operation_finished(self) -> None:
        self.set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def set_status_dot(self, mode: str) -> None:
        if mode == "online":
            self.status_dot.setObjectName("statusDotOnline")
            self.status_dot.setToolTip("Connected")
            self.connected_once = True
        else:
            self.status_dot.setObjectName("statusDotOffline")
            self.status_dot.setToolTip("Not connected")
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

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
        menu.exec(position)

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
        menu.exec(position)

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

    def append_terminal_output(self, text: str) -> None:
        text = re.sub(r'\x1b\][0-9;]*[^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
        parts = re.split(r'\x1b\[([\d;]*)m', text)
        cursor = self.terminal_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                chunk = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', part)
                chunk = re.sub(r'\x1b[()][A-Z]', '', chunk)
                chunk = re.sub(r'[\x07\x08\r]', '', chunk)
                if chunk:
                    chunk = chunk.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    chunk = chunk.replace('\n', '<br>').replace(' ', '&nbsp;')
                    cursor.insertHtml(f'<span style="color: {self.current_term_color};">{chunk}</span>')
            else:
                for token in part.split(';'):
                    token = token.strip()
                    code = int(token) if token.isdigit() else 0
                    if code == 0:
                        self.current_term_color = '#e0e0e0'
                    elif 30 <= code <= 37:
                        palette = ['#1a1a1a', '#c51a4a', '#23d18b', '#d7ba7d', '#3b8eea', '#c586c0', '#29b8db', '#e5e5e5']
                        self.current_term_color = palette[code - 30]
                    elif 90 <= code <= 97:
                        palette = ['#666666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#e5e5e5']
                        self.current_term_color = palette[code - 90]
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.ensureCursorVisible()

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
        self.terminal_output.append(
            f"Connecting to {settings['username']}@{settings['host']}:{settings['port']}..."
        )
        self.current_term_color = "#e0e0e0"
        self.shell_worker = SSHShellWorker(settings, parent=self)
        self.shell_worker.connected.connect(self.shell_connected)
        self.shell_worker.received.connect(self.append_terminal_output)
        self.shell_worker.failed.connect(self.shell_failed)
        self.shell_worker.disconnected.connect(self.shell_disconnected)
        self.shell_worker.start()
        self.shell_connect_button.setEnabled(False)

    def shell_connected(self) -> None:
        self.terminal_output.append("\nSSH shell connected.")
        self.shell_disconnect_button.setEnabled(True)
        self.terminal_send_button.setEnabled(True)
        self.terminal_input.setFocus()
        self.set_status_dot("online")

    def shell_failed(self, message: str) -> None:
        self.terminal_output.append(f"\nSSH error: {message}")
        QMessageBox.critical(self, "SSH terminal failed", message)

    def shell_disconnected(self) -> None:
        self.terminal_output.append("\nSSH shell disconnected.")
        self.shell_connect_button.setEnabled(True)
        self.shell_disconnect_button.setEnabled(False)
        self.terminal_send_button.setEnabled(False)
        if self.shell_worker is not None:
            self.shell_worker.deleteLater()
            self.shell_worker = None

    def disconnect_shell(self, _checked: bool = False) -> None:
        if self.shell_worker is not None:
            self.shell_worker.stop()

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
        sanitized = json.loads(json.dumps(self.app_config))
        try:
            Path(path).write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Exported", f"Saved to {path}\n\nPasswords are never stored in this file.")
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
        merged = json.loads(json.dumps(DEFAULT_APP_CONFIG))
        merged.update(loaded)
        self.app_config = _sync_flat_mirror(_migrate_legacy_config(merged))
        self.refresh_profile_combo()
        self.on_profile_changed(max(0, self.profile_combo.currentIndex()))
        self.save_app_config()
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
        QMessageBox.about(
            self,
            "About rasconf Manager",
            "Deploy and manage the rasconf web interface over SSH/SFTP.\n\n"
            "Supports connection profiles, dry-run preview, remote backup, post-deploy actions, "
            "and an integrated SSH terminal with command history.",
        )

    def closeEvent(self, event) -> None:
        self.save_current_profile_edits()
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
