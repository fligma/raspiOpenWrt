#!/usr/bin/env python3
"""Manage, deploy, and monitor the rasconf web interface on a remote OpenWrt host."""

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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer
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
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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

def _coerce_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _migrate_legacy_config(config: dict) -> dict:
    """Ensure a profiles list exists; if the file only has flat keys, wrap them."""
    profiles = config.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        legacy = {
            "name": "Default",
            "host": str(config.get("host", DEFAULT_PROFILE["host"])),
            "port": _coerce_int(config.get("port"), DEFAULT_PROFILE["port"], 1, 65535),
            "username": str(config.get("username", DEFAULT_PROFILE["username"])),
            "key_file": str(config.get("key_file", "")),
            "remote_root": str(config.get("remote_root", DEFAULT_PROFILE["remote_root"])),
            "source": str(config.get("source", DEFAULT_PROFILE["source"])),
            "trust_unknown_host": bool(config.get("trust_unknown_host", False)),
            "remember_password": bool(config.get("remember_password", False)),
        }
        profiles = [legacy]
        config["profiles"] = profiles
        config.setdefault("active_profile", "Default")
    # sanitize each profile
    cleaned = []
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        prof = {
            "name": str(raw.get("name") or f"Profile {len(cleaned) + 1}"),
            "host": str(raw.get("host", DEFAULT_PROFILE["host"])),
            "port": _coerce_int(raw.get("port"), 22, 1, 65535),
            "username": str(raw.get("username", "root")),
            "key_file": str(raw.get("key_file", "")),
            "remote_root": str(raw.get("remote_root", DEFAULT_PROFILE["remote_root"])),
            "source": str(raw.get("source", str(DEFAULT_SOURCE))),
            "trust_unknown_host": bool(raw.get("trust_unknown_host", False)),
            "remember_password": bool(raw.get("remember_password", False)),
        }
        cleaned.append(prof)
    if not cleaned:
        cleaned = [DEFAULT_PROFILE.copy()]
    config["profiles"] = cleaned
    names = {p["name"] for p in cleaned}
    if config.get("active_profile") not in names:
        config["active_profile"] = cleaned[0]["name"]
    return config


def _sync_flat_mirror(config: dict) -> dict:
    """Copy the active profile values back to the legacy flat keys so
    downstream code that reads config['host'] etc still works."""
    active = next(
        (p for p in config["profiles"] if p["name"] == config["active_profile"]),
        config["profiles"][0],
    )
    for key, value in active.items():
        if key == "name":
            continue
        config[key] = value
    return config


def load_app_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_APP_CONFIG))  # deep copy
    for config_path in (APP_CONFIG_FILE, APP_CONFIG_EXAMPLE_FILE):
        if not config_path.is_file():
            continue
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            config.update(loaded)
            break

    config = _migrate_legacy_config(config)
    config = _sync_flat_mirror(config)

    if not config.get("source") or not Path(config["source"]).is_dir():
        if DEFAULT_SOURCE.is_dir():
            config["source"] = str(DEFAULT_SOURCE)
        else:
            config["source"] = str(Path.home())

    config["ssh_timeout"] = _coerce_int(config.get("ssh_timeout"), 12, 3, 300)
    config["terminal_font_size"] = _coerce_int(config.get("terminal_font_size"), 10, 6, 24)
    config["max_log_lines"] = _coerce_int(config.get("max_log_lines"), 500, 50, 10000)
    config["terminal_history_limit"] = _coerce_int(config.get("terminal_history_limit"), 500, 20, 5000)
    config["web_port"] = _coerce_int(config.get("web_port"), 8989, 1, 65535)
    config["keep_last_n_backups"] = _coerce_int(config.get("keep_last_n_backups"), 5, 0, 100)
    config["default_tab"] = str(config.get("default_tab", "SFTP"))
    config["backup_directory"] = str(config.get("backup_directory", "/tmp/rasconf_backups"))
    config["local_backup_directory"] = str(config.get("local_backup_directory", str(BASE_DIR / "backups")))
    config["luci_url"] = str(config.get("luci_url", "http://192.168.1.1/"))
    config["web_path"] = str(config.get("web_path", "/cgi-bin/index.py"))

    for key in ("deploy_ignore", "post_deploy_commands"):
        value = config.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            config[key] = []

    quick = config.get("quick_commands")
    if not isinstance(quick, list):
        quick = []
    sanitized_quick = []
    for entry in quick:
        if isinstance(entry, dict) and entry.get("command"):
            sanitized_quick.append({"label": str(entry.get("label", entry["command"])), "command": str(entry["command"])})
    config["quick_commands"] = sanitized_quick

    for key in ("confirm_destructive", "show_hidden_files", "auto_connect"):
        config[key] = bool(config.get(key, DEFAULT_APP_CONFIG[key]))

    legacy_backup = config.get("backup_before_deploy")
    config["remote_backup_enabled"] = bool(
        config.get("remote_backup_enabled", legacy_backup if legacy_backup is not None else False)
    )
    config["local_backup_enabled"] = bool(config.get("local_backup_enabled", False))
    config["logging_enabled"] = bool(config.get("logging_enabled", True))
    config.pop("backup_before_deploy", None)

    if not APP_CONFIG_FILE.is_file():
        save_app_config_static(config)

    return config


def save_app_config_static(config: dict) -> None:
    try:
        APP_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_command_history() -> list[str]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [str(item) for item in data if isinstance(item, str)]
    return []


def save_command_history(history: list[str]) -> None:
    try:
        HISTORY_FILE.write_text(json.dumps(history[-MAX_COMMAND_HISTORY:], indent=2), encoding="utf-8")
    except OSError:
        pass


def safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {value}")
    return path.as_posix() if value else ""


def remote_join(root: str, relative: str) -> str:
    safe_relative_path(relative)
    return posixpath.join(root, relative) if relative else root


def make_remote_directories(sftp: paramiko.SFTPClient, path: str) -> None:
    if not path.startswith("/"):
        raise ValueError("The remote directory must be an absolute path")
    current = "/"
    for part in path.strip("/").split("/"):
        if not part:
            continue
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def credential_id(username: str, host: str, port: int) -> str:
    return f"{username}@{host}:{port}"


def create_ssh_client(settings: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if KNOWN_HOSTS_FILE.exists():
        client.load_host_keys(str(KNOWN_HOSTS_FILE))
    if settings["trust_unknown_host"]:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    password = settings["password"] or None
    key_file = settings["key_file"] or None
    timeout = _coerce_int(settings.get("ssh_timeout"), 12, 3, 300)
    client.connect(
        hostname=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=password,
        key_filename=key_file,
        look_for_keys=not password and not key_file,
        allow_agent=not password and not key_file,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    if settings["trust_unknown_host"]:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(KNOWN_HOSTS_FILE))
    return client


def exec_remote(client: paramiko.SSHClient, command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a single non-interactive command. Returns (exit_code, stdout, stderr)."""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    del stdin
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


class SSHShellWorker(QThread):
    connected = pyqtSignal()
    received = pyqtSignal(str)
    failed = pyqtSignal(str)
    disconnected = pyqtSignal()
    command_finished = pyqtSignal(int, str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.outgoing: queue.Queue[str] = queue.Queue()
        self.stop_requested = threading.Event()
        self.channel: paramiko.Channel | None = None
        self.client: paramiko.SSHClient | None = None

    def send_line(self, text: str) -> None:
        self.outgoing.put(text + "\n")

    def stop(self) -> None:
        self.stop_requested.set()
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass

    def run(self) -> None:
        try:
            self.client = create_ssh_client(self.settings)
            self.channel = self.client.invoke_shell(term="xterm", width=120, height=32)
            self.channel.settimeout(0.1)
            self.connected.emit()
            while not self.stop_requested.is_set() and not self.channel.closed:
                if self.channel.recv_ready():
                    data = self.channel.recv(65536).decode("utf-8", errors="replace")
                    if data:
                        self.received.emit(data)
                try:
                    outgoing = self.outgoing.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.channel.sendall(outgoing.encode("utf-8"))
        except Exception as error:
            if not self.stop_requested.is_set():
                self.failed.emit(str(error))
        finally:
            if self.channel is not None:
                try:
                    self.channel.close()
                except Exception:
                    pass
            if self.client is not None:
                self.client.close()
            self.disconnected.emit()


class RemoteExecWorker(QThread):
    """Run a list of one-off commands over a fresh SSH connection."""
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, settings: dict, commands: list[str], parent=None):
        super().__init__(parent)
        self.settings = settings
        self.commands = commands

    def run(self) -> None:
        client = None
        try:
            client = create_ssh_client(self.settings)
            for command in self.commands:
                self.log.emit(f"$ {command}")
                code, out, err = exec_remote(client, command, timeout=_coerce_int(self.settings.get("ssh_timeout"), 12, 3, 300) * 3)
                if out.strip():
                    self.log.emit(out.rstrip())
                if err.strip():
                    self.log.emit("[stderr] " + err.rstrip())
                if code != 0:
                    self.failed.emit(f"Command exited with code {code}: {command}")
                    return
            self.finished_ok.emit()
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if client is not None:
                client.close()


def local_entries(
    source: Path,
    ignore_spec: GitIgnoreSpec | None = None,
    show_hidden: bool = False,
) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    files: list[str] = []
    for current, dir_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        if not show_hidden:
            dir_names[:] = [d for d in dir_names if not d.startswith(".")]
        else:
            dir_names[:] = list(dir_names)
        retained_dirs = []
        for name in dir_names:
            directory = current_path / name
            if directory.is_symlink():
                continue
            relative = (current_path / name).relative_to(source).as_posix()
            if ignore_spec and ignore_spec.match_file(relative + "/"):
                continue
            directories.append(relative)
            retained_dirs.append(name)
        dir_names[:] = retained_dirs
        names = file_names if show_hidden else [f for f in file_names if not f.startswith(".")]
        for name in names:
            file_path = current_path / name
            if file_path.is_symlink():
                continue
            relative = file_path.relative_to(source).as_posix()
            if not ignore_spec or not ignore_spec.match_file(relative):
                files.append(relative)
    return directories, files

class SftpWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    stage = pyqtSignal(str, int, int)

    def __init__(self, operation: str, settings: dict, options: dict, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.settings = settings
        self.options = options

    def run(self) -> None:
        client = None
        sftp = None
        stage = "SSH connection"
        try:
            client = create_ssh_client(self.settings)
            stage = "SFTP subsystem startup"
            sftp = client.open_sftp()
            stage = "SFTP operation"
            result = self.perform(sftp, client)
            self.succeeded.emit(result)
        except Exception as error:
            if stage == "SFTP subsystem startup":
                self.failed.emit(
                    "SSH connected, but the server did not start its SFTP subsystem. "
                    f"Details: {error}\n\n"
                    "On OpenWrt, install the SFTP server package and reconnect:\n"
                    "apk update && apk add openssh-sftp-server\n"
                    "(On older opkg-based releases: opkg update && opkg install openssh-sftp-server.)"
                )
            else:
                self.failed.emit(f"{stage} failed: {error}")
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()

    def perform(self, sftp: paramiko.SFTPClient, client: paramiko.SSHClient):
        operation = self.operation
        remote_root = self.settings["remote_root"]

        if operation == "list":
            return self.read_remote_tree(sftp, remote_root)
        if operation == "deploy":
            return self.deploy(sftp, client, remote_root)
        if operation == "upload":
            self.upload_selected(sftp, remote_root, self.options["paths"])
            return self.read_remote_tree(sftp, remote_root)
        if operation == "download":
            self.download_selected(sftp, remote_root, self.options["paths"])
            return None
        if operation == "delete":
            self.delete_selected(sftp, remote_root, self.options["paths"])
            return self.read_remote_tree(sftp, remote_root)
        if operation == "rename":
            self.rename_remote(sftp, self.options["path"], self.options["new_name"])
            return self.read_remote_tree(sftp, remote_root)
        raise ValueError(f"Unknown operation: {operation}")

    def read_remote_tree(self, sftp: paramiko.SFTPClient, directory: str) -> list[dict]:
        try:
            attributes = sftp.listdir_attr(directory)
        except OSError:
            return []

        entries = []
        for attribute in sorted(attributes, key=lambda item: item.filename.lower()):
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            is_directory = stat.S_ISDIR(attribute.st_mode)
            child_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(child_path, self.settings["remote_root"])
            entry = {
                "name": attribute.filename,
                "path": relative,
                "directory": is_directory,
                "size": int(getattr(attribute, "st_size", 0) or 0),
                "mtime": int(getattr(attribute, "st_mtime", 0) or 0),
                "children": [],
            }
            if is_directory:
                entry["children"] = self.read_remote_tree(sftp, child_path)
            entries.append(entry)
        return entries

    def ensure_remote_root(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        make_remote_directories(sftp, remote_root)

    def upload_relative_paths(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
        ignore_spec: GitIgnoreSpec | None = None,
    ) -> tuple[int, int]:
        source = Path(self.settings["source"])
        self.ensure_remote_root(sftp, remote_root)
        directories, files = local_entries(source, ignore_spec, self.settings.get("show_hidden_files", False))
        selected_paths = self.normalize_paths(selected_paths)

        def included(path: str) -> bool:
            return any(
                not selected or path == selected or path.startswith(selected + "/")
                for selected in selected_paths
            )

        planned_dirs = [r for r in directories if included(r)]
        planned_files = [r for r in files if included(r)]
        total = len(planned_dirs) + len(planned_files)
        done = 0

        for relative in planned_dirs:
            make_remote_directories(sftp, remote_join(remote_root, relative))
            done += 1
            self.stage.emit("Preparing directories", done, total)
        for relative in planned_files:
            local_path = source / Path(*PurePosixPath(relative).parts)
            destination = remote_join(remote_root, relative)
            make_remote_directories(sftp, posixpath.dirname(destination))
            self.progress.emit(f"Uploading {relative}")
            sftp.put(str(local_path), destination)
            done += 1
            self.stage.emit(f"Uploading {relative}", done, total)
        return done, total

    def collect_deploy_plan(
        self, sftp: paramiko.SFTPClient, remote_root: str
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Return (dirs_to_create, files_to_upload, stale_files, stale_dirs)."""
        source = Path(self.settings["source"])
        ignore_spec = GitIgnoreSpec.from_lines(self.settings.get("deploy_ignore", []))
        directories, files = local_entries(source, ignore_spec, self.settings.get("show_hidden_files", False))
        expected = set(directories + files)

        try:
            remote_entries = self.remote_inventory(sftp, remote_root)
        except OSError:
            remote_entries = []

        stale_files = [
            path for path, is_dir in remote_entries
            if not is_dir and path not in expected and not ignore_spec.match_file(path)
        ]
        stale_dirs = [
            path for path, is_dir in remote_entries
            if is_dir and path not in expected and not ignore_spec.match_file(path + "/")
        ]
        return directories, files, stale_files, stale_dirs

    def deploy(self, sftp: paramiko.SFTPClient, client: paramiko.SSHClient, remote_root: str) -> list[dict]:
        if self.options.get("dry_run"):
            directories, files, stale_files, stale_dirs = self.collect_deploy_plan(sftp, remote_root)
            return {
                "dry_run": True,
                "directories": directories,
                "files": files,
                "stale_files": stale_files if self.options.get("mirror") else [],
                "stale_dirs": stale_dirs if self.options.get("mirror") else [],
                "would_delete": bool(self.options.get("mirror")),
            }

        if self.options.get("backup_local"):
            self.backup_local(sftp, remote_root)

        if self.options.get("backup"):
            self.backup_remote(sftp, remote_root)

        self.upload_relative_paths(sftp, remote_root, [""])

        if self.options.get("mirror"):
            _, _, stale_files, stale_dirs = self.collect_deploy_plan(sftp, remote_root)
            for relative in sorted(stale_files, key=lambda item: item.count("/"), reverse=True):
                self.progress.emit(f"Removing remote file {relative}")
                try:
                    sftp.remove(remote_join(remote_root, relative))
                except OSError:
                    pass
            for relative in sorted(stale_dirs, key=lambda item: item.count("/"), reverse=True):
                try:
                    self.progress.emit(f"Removing remote directory {relative}")
                    sftp.rmdir(remote_join(remote_root, relative))
                except OSError:
                    pass

        for command in self.settings.get("post_deploy_commands", []):
            if not command.strip():
                continue
            self.progress.emit(f"Post-deploy: {command}")
            try:
                code, out, err = exec_remote(client, command, timeout=30)
                if out.strip():
                    self.progress.emit(out.strip())
                if code != 0:
                    self.progress.emit(f"[warn] command exited {code}: {err.strip()}")
            except Exception as error:
                self.progress.emit(f"[warn] post-deploy command failed: {error}")

        return self.read_remote_tree(sftp, remote_root)

    def backup_remote(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = posixpath.normpath(
            posixpath.join(self.settings.get("backup_directory", "/tmp/rasconf_backups"), f"backup-{stamp}")
        )
        self.progress.emit(f"Backing up {remote_root} to {backup_dir}")
        make_remote_directories(sftp, backup_dir)
        entries = self.remote_inventory(sftp, remote_root)
        total = len(entries)
        done = 0
        for relative, is_directory in entries:
            target = remote_join(backup_dir, relative)
            source_path = remote_join(remote_root, relative)
            if is_directory:
                make_remote_directories(sftp, target)
            else:
                make_remote_directories(sftp, posixpath.dirname(target))
                try:
                    attr = sftp.stat(source_path)
                    if int(getattr(attr, "st_size", 0) or 0) > 200 * 1024 * 1024:
                        self.progress.emit(f"Skipping large file in backup: {relative}")
                        done += 1
                        continue
                except OSError:
                    pass
                sftp.get(source_path, target)
            done += 1
            self.stage.emit(f"Backup {relative}", done, total)
        self.prune_remote_backups(sftp)

    def backup_local(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        """Save a timestamped local copy of the current remote tree before deploying."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_dir = Path(self.settings.get("local_backup_directory", str(BASE_DIR / "backups"))).expanduser()
        backup_dir = base_dir / f"remote-{datetime.now().strftime('%Y%m%d')}-{stamp}"
        self.progress.emit(f"Saving local copy of {remote_root} to {backup_dir}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        entries = self.remote_inventory(sftp, remote_root)
        total = len(entries)
        done = 0
        for relative, is_directory in entries:
            if relative:
                target = backup_dir / Path(*PurePosixPath(relative).parts)
            else:
                target = backup_dir
            source_path = remote_join(remote_root, relative)
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    attr = sftp.stat(source_path)
                    if int(getattr(attr, "st_size", 0) or 0) > 200 * 1024 * 1024:
                        self.progress.emit(f"Skipping large file in local backup: {relative}")
                        done += 1
                        continue
                except OSError:
                    pass
                sftp.get(source_path, str(target))
            done += 1
            self.stage.emit(f"Local backup {relative}", done, total)
        self.prune_local_backups(base_dir)

    def prune_remote_backups(self, sftp: paramiko.SFTPClient) -> None:
        keep = int(self.settings.get("keep_last_n_backups", 5))
        if keep <= 0:
            return
        base = posixpath.normpath(self.settings.get("backup_directory", "/tmp/rasconf_backups"))
        try:
            names = sorted(name for name in sftp.listdir(base) if name.startswith("backup-"))
        except OSError:
            return
        for old in names[:-keep]:
            try:
                self._remove_remote_tree(sftp, posixpath.join(base, old))
                self.progress.emit(f"Removed old remote backup: {old}")
            except OSError:
                pass

    def prune_local_backups(self, base_dir: Path) -> None:
        keep = int(self.settings.get("keep_last_n_backups", 5))
        if keep <= 0:
            return
        try:
            backups = sorted(
                path for path in base_dir.iterdir() if path.is_dir() and path.name.startswith("remote-")
            )
        except OSError:
            return
        for old in backups[:-keep]:
            try:
                shutil.rmtree(old)
                self.progress.emit(f"Removed old local backup: {old.name}")
            except OSError:
                pass

    @staticmethod
    def _remove_remote_tree(sftp: paramiko.SFTPClient, path: str) -> None:
        for attribute in sftp.listdir_attr(path):
            if attribute.filename in {".", ".."}:
                continue
            child = posixpath.join(path, attribute.filename)
            if stat.S_ISDIR(attribute.st_mode) and not stat.S_ISLNK(attribute.st_mode):
                SftpWorker._remove_remote_tree(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    def remote_inventory(self, sftp: paramiko.SFTPClient, directory: str) -> list[tuple[str, bool]]:
        inventory: list[tuple[str, bool]] = []
        try:
            attributes = sftp.listdir_attr(directory)
        except OSError:
            return inventory
        for attribute in attributes:
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            full_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(full_path, self.settings["remote_root"])
            is_directory = stat.S_ISDIR(attribute.st_mode)
            inventory.append((relative, is_directory))
            if is_directory:
                inventory.extend(self.remote_inventory(sftp, full_path))
        return inventory

    def upload_selected(
        self, sftp: paramiko.SFTPClient, remote_root: str, selected_paths: list[str]
    ) -> None:
        self.upload_relative_paths(sftp, remote_root, selected_paths, None)

    def download_selected(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
    ) -> None:
        destination_root = Path(self.options["destination"]).resolve()
        destination_root.mkdir(parents=True, exist_ok=True)

        collected: list[str] = []

        def walk(relative: str) -> None:
            attributes = sftp.stat(remote_join(remote_root, relative))
            if stat.S_ISDIR(attributes.st_mode):
                for child in sftp.listdir_attr(remote_join(remote_root, relative)):
                    if child.filename in {".", ".."} or stat.S_ISLNK(child.st_mode):
                        continue
                    child_relative = posixpath.join(relative, child.filename) if relative else child.filename
                    if stat.S_ISDIR(child.st_mode):
                        walk(child_relative)
                    else:
                        collected.append(child_relative)
            else:
                collected.append(relative)

        for relative in self.normalize_paths(selected_paths):
            walk(relative)

        total = len(collected)
        for index, relative in enumerate(collected, start=1):
            safe_relative_path(relative)
            remote_path = remote_join(remote_root, relative)
            local_path = (destination_root / Path(*PurePosixPath(relative).parts)).resolve()
            if destination_root not in local_path.parents and local_path != destination_root:
                raise ValueError(f"Unsafe download path: {relative}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.progress.emit(f"Downloading {relative}")
            sftp.get(remote_path, str(local_path))
            self.stage.emit(f"Downloading {relative}", index, total)

    def delete_selected(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
    ) -> None:
        paths = self.normalize_paths(selected_paths)
        if not paths or "" in paths:
            raise ValueError("Select one or more remote files or directories, not the remote root")

        def remove(relative: str) -> None:
            full_path = remote_join(remote_root, relative)
            attributes = sftp.stat(full_path)
            if stat.S_ISDIR(attributes.st_mode):
                for child in sftp.listdir_attr(full_path):
                    if child.filename in {".", ".."} or stat.S_ISLNK(child.st_mode):
                        continue
                    remove(posixpath.join(relative, child.filename))
                sftp.rmdir(full_path)
            else:
                sftp.remove(full_path)

        for relative in paths:
            self.progress.emit(f"Deleting remote path {relative}")
            remove(relative)

    def rename_remote(self, sftp: paramiko.SFTPClient, relative: str, new_name: str) -> None:
        safe_relative_path(relative)
        if not relative:
            raise ValueError("Cannot rename the remote root")
        if "/" in new_name or new_name in {".", "..", ""}:
            raise ValueError("New name must be a plain filename")
        source_path = remote_join(self.settings["remote_root"], relative)
        parent = posixpath.dirname(relative)
        target_relative = posixpath.join(parent, new_name) if parent else new_name
        target_path = remote_join(self.settings["remote_root"], target_relative)
        sftp.rename(source_path, target_path)

    @staticmethod
    def normalize_paths(paths: list[str]) -> list[str]:
        normalized = sorted({safe_relative_path(path) for path in paths}, key=lambda item: (item.count("/"), item))
        result: list[str] = []
        for path in normalized:
            if not any(parent == "" or path.startswith(parent + "/") for parent in result):
                result.append(path)
        return result


class StringListDialog(QDialog):
    """Editor for a list of strings (used for ignore patterns and post-deploy commands)."""

    def __init__(self, title: str, label: str, items: list[str], parent=None, monospace: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.list_widget = QListWidget()
        for item in items:
            self.list_widget.addItem(item)
        if monospace:
            font = QFont("Consolas", 10)
            font.setStyleHint(QFont.StyleHint.Monospace)
            self.list_widget.setFont(font)
        layout.addWidget(self.list_widget)
        row = QHBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self.add_item)
        edit_button = QPushButton("Edit selected")
        edit_button.clicked.connect(self.edit_selected)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        row.addWidget(add_button)
        row.addWidget(edit_button)
        row.addWidget(remove_button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_item(self) -> None:
        text, ok = QInputDialog.getText(self, "Add entry", "Value:")
        if ok and text.strip():
            self.list_widget.addItem(text.strip())

    def edit_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "Edit entry", "Value:", text=item.text())
        if ok:
            item.setText(text.strip())

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]


class DryRunDialog(QDialog):
    def __init__(self, plan: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deploy preview (dry run)")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        summary = (
            f"Directories to create: {len(plan['directories'])}\n"
            f"Files to upload: {len(plan['files'])}\n"
        )
        if plan["would_delete"]:
            summary += f"Remote files to remove (mirror): {len(plan['stale_files'])}\n"
            summary += f"Remote directories to remove (mirror): {len(plan['stale_dirs'])}"
        else:
            summary += "Mirror is OFF - nothing will be deleted."
        label = QLabel(summary)
        label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(label)

        tabs = QTabWidget()
        tabs.addTab(self._make_list(plan["directories"]), "New / update dirs")
        tabs.addTab(self._make_list(plan["files"]), "Files to upload")
        tabs.addTab(self._make_list(plan["stale_files"] + plan["stale_dirs"]), "Would delete")
        layout.addWidget(tabs, 1)

        note = QLabel("This is a preview. No changes were made. Close this dialog and press Deploy to apply.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #ffb020;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _make_list(items: list[str]) -> QListWidget:
        widget = QListWidget()
        for item in sorted(items, key=lambda entry: entry.lower()):
            widget.addItem(item)
        return widget


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
        self.connection_toggle.setText("Connection settings")
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
        self.connect_button = QPushButton("Connect SFTP")
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
        self.deploy_button.setToolTip("Upload every file from the local source into the remote directory (Ctrl+D)")
        self.deploy_button.clicked.connect(self.deploy_source)
        self.mirror_checkbox = QCheckBox("Mirror (delete remote files absent locally)")
        self.mirror_checkbox.setToolTip("Destructive: removes extra remote files and directories after upload")
        preview_button = QPushButton("Preview deploy")
        preview_button.setToolTip("Show what deploy would change, without uploading anything (Ctrl+Shift+D)")
        preview_button.clicked.connect(self.preview_deploy)
        action_row.addWidget(self.upload_button)
        action_row.addWidget(self.download_button)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.deploy_button)
        action_row.addWidget(preview_button)
        action_row.addWidget(self.mirror_checkbox)
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

    @staticmethod
    def create_tree(title: str) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels([title, "Size", "Modified"])
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
        self.start_worker("deploy", {"mirror": self.mirror_checkbox.isChecked(), "dry_run": True})

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
        if self.app_config.get("confirm_destructive", True) or self.mirror_checkbox.isChecked():
            message = "Upload all files from the local source to the configured remote directory?"
            if self.mirror_checkbox.isChecked():
                message += "\n\nMirror is ON: extra remote files will be DELETED."
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
                "mirror": self.mirror_checkbox.isChecked(),
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

def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("rasconf Manager")
    for candidate in (RESOURCE_DIR / "icon.png", BASE_DIR / "icon.png"):
        if candidate.is_file():
            app.setWindowIcon(QIcon(str(candidate)))
            break
    window = RasconfManager()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
