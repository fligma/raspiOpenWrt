#!/usr/bin/env python3
"""rasconf Manager support module (auto-generated split)."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(sys._MEIPASS)
    REPOSITORY_ROOT = BASE_DIR
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR
    REPOSITORY_ROOT = BASE_DIR.parents[2] if len(BASE_DIR.parents) >= 2 else BASE_DIR

DEFAULT_SOURCE = REPOSITORY_ROOT / "src" / "www_rasconf"
KNOWN_HOSTS_FILE = Path.home() / ".ssh" / "known_hosts_rasconf"
KEYRING_SERVICE = "rasconf-sftp-manager"
APP_CONFIG_FILE = BASE_DIR / "config.json"
APP_CONFIG_EXAMPLE_FILE = RESOURCE_DIR / "config.example.json"
HISTORY_FILE = BASE_DIR / "command_history.json"
LOG_FILE = BASE_DIR / "rasconf_manager.log"
MAX_COMMAND_HISTORY = 200

DEFAULT_PROFILE = {
    "name": "Default",
    "host": "192.168.1.1",
    "port": 22,
    "username": "root",
    "key_file": "",
    "remote_root": "/www_rasconf",
    "source": str(DEFAULT_SOURCE),
    "trust_unknown_host": False,
    "remember_password": False,
}

DEFAULT_APP_CONFIG = {
    "profiles": [],
    "active_profile": "Default",
    "host": DEFAULT_PROFILE["host"],
    "port": DEFAULT_PROFILE["port"],
    "username": DEFAULT_PROFILE["username"],
    "key_file": DEFAULT_PROFILE["key_file"],
    "remote_root": DEFAULT_PROFILE["remote_root"],
    "source": DEFAULT_PROFILE["source"],
    "trust_unknown_host": DEFAULT_PROFILE["trust_unknown_host"],
    "remember_password": DEFAULT_PROFILE["remember_password"],
    "deploy_ignore": [],
    "ssh_timeout": 12,
    "post_deploy_commands": [],
    "logging_enabled": True,
    "remote_backup_enabled": False,
    "local_backup_enabled": False,
    "mirror_delete_remote": False,
    "mirror_delete_local": False,
    "backup_directory": "/tmp/rasconf_backups",
    "local_backup_directory": str(BASE_DIR / "backups"),
    "keep_last_n_backups": 5,
    "confirm_destructive": True,
    "show_hidden_files": False,
    "auto_connect": False,
    "default_tab": "SFTP",
    "terminal_font_size": 10,
    "max_log_lines": 500,
    "terminal_history_limit": 500,
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
    "luci_url": "http://192.168.1.1/",
    "quick_commands": [
        {"label": "Reload uhttpd", "command": "killall -HUP uhttpd"},
        {"label": "Restart cgi wrapper", "command": "/etc/init.d/cgiwrapper restart"},
        {"label": "Show openwrt version", "command": "cat /etc/openwrt_release"},
        {"label": "Reboot", "command": "reboot"},
    ],
}


APP_STYLESHEET = """
QWidget {
    background-color: #121212;
    color: #e0e0e0;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}
QMainWindow, QTabWidget::pane, QGroupBox, QPlainTextEdit, QTextEdit,
QTreeWidget, QLineEdit, QSpinBox, QComboBox, QListWidget {
    background-color: #212121;
}
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #c51a4a;
}
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QTreeWidget,
QComboBox, QListWidget {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    selection-background-color: #c51a4a;
    padding: 5px;
}
QTreeView, QListView {
    background-color: #212121;
    alternate-background-color: #1a1a1a;
    color: #e0e0e0;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus, QComboBox:focus, QListWidget:focus {
    border: 1px solid #c51a4a;
}
QPushButton, QToolButton, QComboBox::drop-down {
    background-color: #333333;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton:hover, QToolButton:hover {
    background-color: #414141;
    border-color: #c51a4a;
}
QPushButton:pressed, QToolButton:checked {
    background-color: #a0153c;
    border-color: #c51a4a;
}
QPushButton:disabled, QToolButton:disabled {
    color: #777777;
    background-color: #292929;
    border-color: #383838;
}
QPushButton#primaryAction {
    background-color: #c51a4a;
    border-color: #c51a4a;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryAction:hover {
    background-color: #a0153c;
    border-color: #a0153c;
}
QPushButton#dangerAction {
    background-color: #6e1a1a;
    border-color: #8a2a2a;
    color: #ffe4e4;
}
QPushButton#dangerAction:hover {
    background-color: #8a2a2a;
}
QTabBar::tab {
    background-color: #2a2a2a;
    color: #aaaaaa;
    border: 0;
    border-bottom: 3px solid transparent;
    padding: 10px 18px;
    min-width: 80px;
}
QTabBar::tab:selected {
    color: #e0e0e0;
    background-color: #333333;
    border-bottom-color: #c51a4a;
}
QTabBar::tab:hover:!selected {
    color: #ffffff;
    background-color: #3a3a3a;
}
QTreeWidget::item, QListWidget::item {
    padding: 4px;
    color: #e0e0e0;
}
QTreeWidget::item:alternate, QListWidget::item:alternate {
    background-color: #1a1a1a;
}
QTreeWidget::item:selected:!alternate, QListWidget::item:selected:!alternate {
    background-color: #6b1730;
    color: #ffffff;
}
QTreeWidget::item:selected:alternate, QListWidget::item:selected:alternate {
    background-color: #6b1730;
    color: #ffffff;
}
QTreeWidget::item:hover:!selected, QListWidget::item:hover:!selected {
    background-color: #2c2c2c;
}
QHeaderView::section {
    background-color: #2a2a2a;
    color: #aaaaaa;
    border: 0;
    border-bottom: 1px solid #3c3c3c;
    padding: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}
QCheckBox::indicator:unchecked {
    background-color: #1a1a1a;
    border: 1px solid #666666;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #c51a4a;
    border: 1px solid #c51a4a;
    border-radius: 3px;
}
QProgressBar {
    background-color: #2e2e2e;
    border: 1px solid #3c3c3c;
    border-radius: 3px;
    text-align: center;
    color: #e0e0e0;
    min-height: 18px;
}
QProgressBar::chunk {
    background-color: #c51a4a;
}
QStatusBar {
    background-color: #1a1a1a;
    color: #aaaaaa;
}
QStatusBar::item { border: 0; }
QToolTip {
    background-color: #2e2e2e;
    color: #e0e0e0;
    border: 1px solid #c51a4a;
    padding: 5px;
}
QMenu {
    background-color: #1e1e1e;
    border: 1px solid #3c3c3c;
}
QMenu::item:selected {
    background-color: #6b1730;
}
QMenuBar {
    background-color: #1a1a1a;
    color: #cfcfcf;
}
QMenuBar::item:selected {
    background-color: #6b1730;
}
QToolBar {
    background-color: #1a1a1a;
    border: 0;
    spacing: 4px;
    padding: 4px;
}
QScrollBar:vertical {
    width: 12px;
    background-color: #1a1a1a;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    background-color: #555555;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background-color: #c51a4a;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: none;
}
QLabel#statusDot {
    color: #ffb020;
    font-size: 14pt;
}
QLabel#statusDotOnline { color: #23d18b; }
QLabel#statusDotOffline { color: #c51a4a; }
"""
