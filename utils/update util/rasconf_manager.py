#!/usr/bin/env python3
"""Manage and deploy the rasconf web interface"""

from __future__ import annotations

import os
import json
import posixpath
import queue
import re
import stat
import sys
import threading
from pathlib import Path, PurePosixPath

import keyring
import paramiko
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtGui import QFont, QIcon
from pathspec import GitIgnoreSpec
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPOSITORY_ROOT / "src" / "www_rasconf"
KNOWN_HOSTS_FILE = Path.home() / ".ssh" / "known_hosts_rasconf"
KEYRING_SERVICE = "rasconf-sftp-manager"
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

APP_CONFIG_FILE = BASE_DIR / "config.json"
if getattr(sys, 'frozen', False):
    RESOURCE_DIR = Path(sys._MEIPASS)
else:
    RESOURCE_DIR = Path(__file__).parent

APP_CONFIG_EXAMPLE_FILE = RESOURCE_DIR / "config.example.json"
DEFAULT_APP_CONFIG = {
    "host": "192.168.1.1",
    "port": 22,
    "username": "root",
    "key_file": "",
    "remote_root": "/www_rasconf",
    "source": str(DEFAULT_SOURCE),
    "trust_unknown_host": False,
    "deploy_ignore": [],
}
APP_STYLESHEET = """
QWidget {
    background-color: #121212;
    color: #e0e0e0;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}
QMainWindow, QTabWidget::pane, QGroupBox, QPlainTextEdit, QTextEdit,
QTreeWidget, QLineEdit, QSpinBox {
    background-color: #212121;
}
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 6px;
    margin-top: 7px;
    padding: 10px;
}
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QTreeWidget {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    selection-background-color: #c51a4a;
    padding: 5px;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus {
    border: 1px solid #c51a4a;
}
QPushButton, QToolButton {
    background-color: #333333;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 7px 12px;
}
QPushButton:hover, QToolButton:hover {
    background-color: #414141;
    border-color: #c51a4a;
}
QPushButton:pressed, QToolButton:checked {
    background-color: #a0153c;
    border-color: #c51a4a;
}
QPushButton:disabled {
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
QTreeWidget::item {
    padding: 4px;
}
QTreeWidget::item:selected {
    background-color: #6b1730;
    color: #ffffff;
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
}
QProgressBar::chunk {
    background-color: #c51a4a;
}
QStatusBar {
    background-color: #1a1a1a;
    color: #aaaaaa;
}
QToolTip {
    background-color: #2e2e2e;
    color: #e0e0e0;
    border: 1px solid #c51a4a;
    padding: 5px;
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
"""


def load_app_config() -> dict:
    config = DEFAULT_APP_CONFIG.copy()
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

    if not config.get("source"):
        config["source"] = str(DEFAULT_SOURCE)
    try:
        config["port"] = int(config["port"])
    except (KeyError, TypeError, ValueError):
        config["port"] = DEFAULT_APP_CONFIG["port"]
    if not 1 <= config["port"] <= 65535:
        config["port"] = DEFAULT_APP_CONFIG["port"]
    if not isinstance(config.get("deploy_ignore"), list) or not all(
        isinstance(pattern, str) for pattern in config["deploy_ignore"]
    ):
        config["deploy_ignore"] = []

    if not APP_CONFIG_FILE.is_file():
        try:
            APP_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    return config

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
    client.connect(
        hostname=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=password,
        key_filename=key_file,
        look_for_keys=not password and not key_file,
        allow_agent=not password and not key_file,
        timeout=12,
        banner_timeout=12,
        auth_timeout=12,
    )
    if settings["trust_unknown_host"]:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(KNOWN_HOSTS_FILE))
    return client

class SSHShellWorker(QThread):
    connected = pyqtSignal()
    received = pyqtSignal(str)
    failed = pyqtSignal(str)
    disconnected = pyqtSignal()

    def __init__(self, settings: dict):
        super().__init__()
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
            self.channel.close()

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
                self.channel.close()
            if self.client is not None:
                self.client.close()
            self.disconnected.emit()


def local_entries(
    source: Path,
    ignore_spec: GitIgnoreSpec | None = None,
) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    files: list[str] = []
    for current, dir_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
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
        for name in file_names:
            file_path = current_path / name
            if not file_path.is_symlink():
                relative = file_path.relative_to(source).as_posix()
                if not ignore_spec or not ignore_spec.match_file(relative):
                    files.append(relative)
    return directories, files

class SftpWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, operation: str, settings: dict, options: dict):
        super().__init__()
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
            result = self.perform(sftp)
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

    def perform(self, sftp: paramiko.SFTPClient):
        operation = self.operation
        remote_root = self.settings["remote_root"]

        if operation == "list":
            return self.read_remote_tree(sftp, remote_root)
        if operation == "deploy":
            return self.deploy(sftp, remote_root)
        if operation == "upload":
            self.upload_selected(sftp, remote_root, self.options["paths"])
            return self.read_remote_tree(sftp, remote_root)
        if operation == "download":
            self.download_selected(sftp, remote_root, self.options["paths"])
            return None
        if operation == "delete":
            self.delete_selected(sftp, remote_root, self.options["paths"])
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
    ) -> None:
        source = Path(self.settings["source"])
        self.ensure_remote_root(sftp, remote_root)
        directories, files = local_entries(source, ignore_spec)
        selected_paths = self.normalize_paths(selected_paths)

        def included(path: str) -> bool:
            return any(
                not selected or path == selected or path.startswith(selected + "/")
                for selected in selected_paths
            )

        for relative in directories:
            if included(relative):
                make_remote_directories(sftp, remote_join(remote_root, relative))
        for relative in files:
            if included(relative):
                local_path = source / Path(*PurePosixPath(relative).parts)
                destination = remote_join(remote_root, relative)
                make_remote_directories(sftp, posixpath.dirname(destination))
                self.progress.emit(f"Uploading {relative}")
                sftp.put(str(local_path), destination)

    def deploy(self, sftp: paramiko.SFTPClient, remote_root: str) -> list[dict]:
        source = Path(self.settings["source"])
        ignore_spec = GitIgnoreSpec.from_lines(self.settings.get("deploy_ignore", []))
        directories, files = local_entries(source, ignore_spec)
        expected = set(directories + files)
        self.upload_relative_paths(sftp, remote_root, [""], ignore_spec)

        if self.options["mirror"]:
            remote_entries = self.remote_inventory(sftp, remote_root)
            stale_files = [
                path for path, is_dir in remote_entries
                if not is_dir and path not in expected and not ignore_spec.match_file(path)
            ]
            stale_dirs = [
                path for path, is_dir in remote_entries
                if is_dir and path not in expected and not ignore_spec.match_file(path + "/")
            ]
            for relative in sorted(stale_files, key=lambda item: item.count("/"), reverse=True):
                self.progress.emit(f"Removing remote file {relative}")
                sftp.remove(remote_join(remote_root, relative))
            for relative in sorted(stale_dirs, key=lambda item: item.count("/"), reverse=True):
                try:
                    self.progress.emit(f"Removing remote directory {relative}")
                    sftp.rmdir(remote_join(remote_root, relative))
                except OSError:
                    pass

        return self.read_remote_tree(sftp, remote_root)

    def remote_inventory(self, sftp: paramiko.SFTPClient, directory: str) -> list[tuple[str, bool]]:
        inventory: list[tuple[str, bool]] = []
        for attribute in sftp.listdir_attr(directory):
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            full_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(full_path, self.settings["remote_root"])
            is_directory = stat.S_ISDIR(attribute.st_mode)
            inventory.append((relative, is_directory))
            if is_directory:
                inventory.extend(self.remote_inventory(sftp, full_path))
        return inventory

    def download_selected(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
    ) -> None:
        destination_root = Path(self.options["destination"]).resolve()
        destination_root.mkdir(parents=True, exist_ok=True)

        def download(relative: str) -> None:
            safe_relative_path(relative)
            remote_path = remote_join(remote_root, relative)
            local_path = (destination_root / Path(*PurePosixPath(relative).parts)).resolve()
            if destination_root not in local_path.parents and local_path != destination_root:
                raise ValueError(f"Unsafe download path: {relative}")
            attributes = sftp.stat(remote_path)
            if stat.S_ISDIR(attributes.st_mode):
                local_path.mkdir(parents=True, exist_ok=True)
                for child in sftp.listdir_attr(remote_path):
                    if child.filename in {".", ".."} or stat.S_ISLNK(child.st_mode):
                        continue
                    child_relative = posixpath.join(relative, child.filename) if relative else child.filename
                    download(child_relative)
            else:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                self.progress.emit(f"Downloading {relative}")
                sftp.get(remote_path, str(local_path))

        for relative in self.normalize_paths(selected_paths):
            download(relative)

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

    @staticmethod
    def normalize_paths(paths: list[str]) -> list[str]:
        normalized = sorted({safe_relative_path(path) for path in paths}, key=lambda item: (item.count("/"), item))
        result: list[str] = []
        for path in normalized:
            if not any(parent == "" or path.startswith(parent + "/") for parent in result):
                result.append(path)
        return result

class RasconfManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: SftpWorker | None = None
        self.shell_worker: SSHShellWorker | None = None
        self.remote_tree_entries: list[dict] = []
        self.app_config = load_app_config()
        self.current_term_color = "#e0e0e0"
        self.setWindowTitle("rasconf Manager")
        self.resize(1100, 760)
        self.build_ui()
        self.refresh_local_tree()

    def build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)

        # sftp connection header
        connection_header = QHBoxLayout()
        self.connection_toggle = QToolButton()
        self.connection_toggle.setText("Connection settings")
        self.connection_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.connection_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.connection_toggle.setCheckable(True)
        self.connection_toggle.setChecked(True)
        self.connection_toggle.toggled.connect(self.toggle_connection_panel)
        connection_header.addWidget(self.connection_toggle)
        connection_header.addStretch(1)
        root_layout.addLayout(connection_header)

        connection_group = QGroupBox()
        connection_layout = QGridLayout(connection_group)
        self.host_input = QLineEdit(str(self.app_config["host"]))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.app_config["port"])
        self.username_input = QLineEdit(str(self.app_config["username"]))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Optional when using an SSH key")
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
        self.trust_host_checkbox = QCheckBox("Trust and save an unknown host key on first connection")
        self.trust_host_checkbox.setChecked(bool(self.app_config["trust_unknown_host"]))
        self.trust_host_checkbox.setToolTip(
            "Leave unchecked for strict known-host verification. Enable only when you have verified the Pi's identity."
        )
        self.connect_button = QPushButton("Connect SFTP")
        self.connect_button.setObjectName("primaryAction")
        self.connect_button.clicked.connect(self.connect_remote)

        connection_layout.addWidget(QLabel("Host"), 0, 0)
        connection_layout.addWidget(self.host_input, 0, 1)
        connection_layout.addWidget(QLabel("Port"), 0, 2)
        connection_layout.addWidget(self.port_input, 0, 3)
        connection_layout.addWidget(QLabel("Username"), 1, 0)
        connection_layout.addWidget(self.username_input, 1, 1)
        connection_layout.addWidget(QLabel("Password"), 1, 2)
        connection_layout.addWidget(self.password_input, 1, 3)
        connection_layout.addWidget(self.remember_password_checkbox, 2, 0, 1, 4)
        connection_layout.addWidget(QLabel("Private key"), 3, 0)
        connection_layout.addWidget(self.key_input, 3, 1, 1, 2)
        connection_layout.addWidget(key_browse, 3, 3)
        connection_layout.addWidget(QLabel("Remote directory"), 4, 0)
        connection_layout.addWidget(self.remote_root_input, 4, 1, 1, 2)
        connection_layout.addWidget(self.connect_button, 4, 3)
        connection_layout.addWidget(self.trust_host_checkbox, 5, 0, 1, 4)
        root_layout.addWidget(connection_group)
        self.connection_panel = connection_group

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, 1)

        sftp_page = QWidget()
        sftp_layout = QVBoxLayout(sftp_page)
        source_row = QHBoxLayout()
        self.source_input = QLineEdit(str(self.app_config["source"]))
        source_browse = QPushButton("Browse source...")
        source_browse.clicked.connect(self.choose_source)
        source_refresh = QPushButton("Refresh local")
        source_refresh.clicked.connect(self.refresh_local_tree)
        source_row.addWidget(QLabel("Local source"))
        source_row.addWidget(self.source_input, 1)
        source_row.addWidget(source_browse)
        source_row.addWidget(source_refresh)
        sftp_layout.addLayout(source_row)

        self.local_tree = self.create_tree("Local source files")
        self.remote_tree = self.create_tree("Remote /www_rasconf")
        self.remote_tree.setEnabled(False)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.local_tree)
        splitter.addWidget(self.remote_tree)
        sftp_layout.addWidget(splitter, 1)

        action_row = QHBoxLayout()
        self.upload_button = QPushButton("Upload selected →")
        self.upload_button.clicked.connect(self.upload_selected)
        self.download_button = QPushButton("← Download selected")
        self.download_button.clicked.connect(self.download_selected)
        self.delete_button = QPushButton("Delete remote selection")
        self.delete_button.clicked.connect(self.delete_selected)
        self.deploy_button = QPushButton("Deploy source")
        self.deploy_button.setObjectName("primaryAction")
        self.deploy_button.setToolTip("Upload every file from the local source into the remote directory")
        self.deploy_button.clicked.connect(self.deploy_source)
        self.mirror_checkbox = QCheckBox("Mirror (delete remote files absent locally)")
        self.mirror_checkbox.setToolTip("Destructive: removes extra remote files and directories after upload")
        action_row.addWidget(self.upload_button)
        action_row.addWidget(self.download_button)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.deploy_button)
        action_row.addWidget(self.mirror_checkbox)
        sftp_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        sftp_layout.addWidget(self.progress_bar)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(110)
        sftp_layout.addWidget(self.log_output)
        self.tabs.addTab(sftp_page, "SFTP")

        ssh_page = QWidget()
        ssh_layout = QVBoxLayout(ssh_page)
        terminal_actions = QHBoxLayout()
        self.shell_connect_button = QPushButton("Open SSH terminal")
        self.shell_connect_button.setObjectName("primaryAction")
        self.shell_connect_button.clicked.connect(self.connect_shell)
        self.shell_disconnect_button = QPushButton("Disconnect")
        self.shell_disconnect_button.setEnabled(False)
        self.shell_disconnect_button.clicked.connect(self.disconnect_shell)
        terminal_actions.addWidget(self.shell_connect_button)
        terminal_actions.addWidget(self.shell_disconnect_button)
        terminal_actions.addStretch(1)
        ssh_layout.addLayout(terminal_actions)

        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.terminal_output.setPlaceholderText("SSH terminal output will appear here.")
        terminal_font = QFont("Consolas", 10)
        terminal_font.setStyleHint(QFont.StyleHint.Monospace)
        terminal_font.setFixedPitch(True)
        self.terminal_output.setFont(terminal_font)
        ssh_layout.addWidget(self.terminal_output, 1)

        terminal_input_row = QHBoxLayout()
        self.terminal_input = QLineEdit()
        self.terminal_input.setPlaceholderText("Enter a shell command and press Enter")
        self.terminal_input.returnPressed.connect(self.send_terminal_input)
        self.terminal_send_button = QPushButton("Send")
        self.terminal_send_button.setObjectName("primaryAction")
        self.terminal_send_button.setEnabled(False)
        self.terminal_send_button.clicked.connect(self.send_terminal_input)
        terminal_input_row.addWidget(self.terminal_input, 1)
        terminal_input_row.addWidget(self.terminal_send_button)
        ssh_layout.addLayout(terminal_input_row)
        self.tabs.addTab(ssh_page, "SSH")

        # Rasconf web interface
        web_page = QWidget()
        web_layout = QVBoxLayout(web_page)
        
        web_controls = QHBoxLayout()
        host = self.app_config.get("host", "192.168.50.1")
        target_url = f"http://{host}:8989/cgi-bin/index.py"
        
        self.web_url_input = QLineEdit(target_url)
        web_go_button = QPushButton("Go / Refresh")
        web_go_button.setObjectName("primaryAction")
        
        self.web_view = QWebEngineView()
        
        def load_web_url():
            self.web_view.setUrl(QUrl(self.web_url_input.text()))
            
        web_go_button.clicked.connect(load_web_url)
        self.web_url_input.returnPressed.connect(load_web_url)
        
        web_controls.addWidget(self.web_url_input, 1)
        web_controls.addWidget(web_go_button)
        
        web_layout.addLayout(web_controls)
        web_layout.addWidget(self.web_view, 1)
        
        self.tabs.addTab(web_page, "Web Interface")
        
        load_web_url()

        # Luci web interface
        luci_page = QWidget()
        luci_layout = QVBoxLayout(luci_page)
        
        luci_controls = QHBoxLayout()
        self.luci_url_input = QLineEdit("http://192.168.50.1/")
        
        luci_go_button = QPushButton("Go / Refresh")
        luci_go_button.setObjectName("primaryAction")
        
        self.luci_view = QWebEngineView()
        
        def load_luci_url():
            self.luci_view.setUrl(QUrl(self.luci_url_input.text()))
            
        luci_go_button.clicked.connect(load_luci_url)
        self.luci_url_input.returnPressed.connect(load_luci_url)
        
        luci_controls.addWidget(self.luci_url_input, 1)
        luci_controls.addWidget(luci_go_button)
        
        luci_layout.addLayout(luci_controls)
        luci_layout.addWidget(self.luci_view, 1)
        
        self.tabs.addTab(luci_page, "Luci")
        
        load_luci_url()

        for field in (
            self.host_input,
            self.username_input,
            self.key_input,
            self.remote_root_input,
            self.source_input,
        ):
            field.editingFinished.connect(self.save_app_config)
        self.port_input.valueChanged.connect(self.save_app_config)
        self.trust_host_checkbox.stateChanged.connect(self.save_app_config)
        self.remember_password_checkbox.toggled.connect(self.remember_password_changed)

        self.setCentralWidget(central)
        self.setStyleSheet(APP_STYLESHEET)
        self.setStatusBar(self.statusBar())
        self.statusBar().showMessage("Choose connection details, then connect to the Pi")
        self.load_saved_password()

    def toggle_connection_panel(self, expanded: bool) -> None:
        self.connection_panel.setVisible(expanded)
        self.connection_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    @staticmethod
    def create_tree(title: str) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabel(title)
        tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        return tree

    def current_settings(self) -> dict:
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
            raise ValueError("Enter the Raspberry Pi hostname or IP address")
        if not self.username_input.text().strip():
            raise ValueError("Enter the SSH username")
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
        }

    def save_app_config(self, _value=None) -> None:
        settings = {
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "key_file": self.key_input.text().strip(),
            "remote_root": self.remote_root_input.text().strip(),
            "source": self.source_input.text().strip(),
            "trust_unknown_host": self.trust_host_checkbox.isChecked(),
            "deploy_ignore": self.app_config.get("deploy_ignore", []),
            "remember_password": self.remember_password_checkbox.isChecked(),
        }
        try:
            APP_CONFIG_FILE.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            self.log(f"Unable to save local settings: {error}")

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
        self.save_app_config()
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
        try:
            settings = self.current_settings()
        except ValueError as error:
            QMessageBox.warning(self, "Check settings", str(error))
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
        self.save_app_config()

        self.worker = SftpWorker(operation, settings, options or {})
        self.worker.progress.connect(self.log)
        self.worker.succeeded.connect(self.operation_succeeded)
        self.worker.failed.connect(self.operation_failed)
        self.worker.finished.connect(self.operation_finished)
        self.set_busy(True)
        self.log(f"Starting {operation}...")
        self.worker.start()

    def append_terminal_output(self, text: str) -> None:
        """Parses standard ANSI escape codes and appends formatted HTML to the terminal"""
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
                    chunk = chunk.replace('\n', '<br>')
                    chunk = chunk.replace(' ', '&nbsp;')
                    cursor.insertHtml(f'<span style="color: {self.current_term_color};">{chunk}</span>')
            else:
                for c in part.split(';'):
                    c = c.strip()
                    code = int(c) if c.isdigit() else 0
                    if code == 0:
                        self.current_term_color = '#e0e0e0'
                    elif 30 <= code <= 37:
                        colors = ['#1a1a1a', '#c51a4a', '#23d18b', '#d7ba7d', '#3b8eea', '#c586c0', '#29b8db', '#e5e5e5']
                        self.current_term_color = colors[code - 30]
                    elif 90 <= code <= 97:
                        colors = ['#666666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#e5e5e5']
                        self.current_term_color = colors[code - 90]
        
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.ensureCursorVisible()

    def connect_shell(self, _checked: bool = False) -> None:
        if self.shell_worker is not None and self.shell_worker.isRunning():
            return
        try:
            settings = self.current_settings()
        except ValueError as error:
            QMessageBox.warning(self, "Check settings", str(error))
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
        self.save_app_config()
        self.terminal_output.clear()
        self.terminal_output.append(
            f"Connecting to {settings['username']}@{settings['host']}:{settings['port']}..."
        )
        self.current_term_color = "#e0e0e0"
        self.shell_worker = SSHShellWorker(settings)
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

    def shell_failed(self, message: str) -> None:
        self.terminal_output.append(f"\nSSH error: {message}")
        QMessageBox.critical(self, "SSH terminal failed", message)

    def shell_disconnected(self) -> None:
        self.terminal_output.append("\nSSH shell disconnected.")
        self.shell_connect_button.setEnabled(True)
        self.shell_disconnect_button.setEnabled(False)
        self.terminal_send_button.setEnabled(False)
        self.shell_worker = None

    def disconnect_shell(self, _checked: bool = False) -> None:
        if self.shell_worker is not None:
            self.shell_worker.stop()

    def send_terminal_input(self) -> None:
        if self.shell_worker is None or not self.shell_worker.isRunning():
            return
        command = self.terminal_input.text()
        self.shell_worker.send_line(command)
        self.terminal_input.clear()

    def set_busy(self, busy: bool) -> None:
        self.progress_bar.setVisible(busy)
        for button in (
            self.connect_button,
            self.upload_button,
            self.download_button,
            self.delete_button,
            self.deploy_button,
        ):
            button.setEnabled(not busy)

    def operation_succeeded(self, result) -> None:
        if isinstance(result, list):
            self.remote_tree_entries = result
            self.populate_remote_tree(result)
            self.remote_tree.setEnabled(True)
        self.statusBar().showMessage("Operation completed", 5000)
        self.log("Operation completed successfully")

    def operation_failed(self, message: str) -> None:
        self.statusBar().showMessage("Operation failed", 5000)
        self.log("Error: " + message)
        QMessageBox.critical(self, "SFTP operation failed", message)

    def operation_finished(self) -> None:
        self.set_busy(False)
        self.worker = None

    def log(self, message: str) -> None:
        self.log_output.append(message)

    def connect_remote(self, _checked: bool = False) -> None:
        self.remote_tree.setEnabled(False)
        self.start_worker("list")

    def refresh_local_tree(self, _checked: bool = False) -> None:
        source = Path(self.source_input.text()).expanduser()
        self.local_tree.clear()
        if not source.is_dir():
            self.local_tree.setHeaderLabel("Local source files (directory not found)")
            return
        self.local_tree.setHeaderLabel(f"Local: {source}")
        root_item = QTreeWidgetItem([source.name or str(source)])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        self.local_tree.addTopLevelItem(root_item)
        self.populate_local_children(root_item, source)
        root_item.setExpanded(True)

    def populate_local_children(self, parent_item: QTreeWidgetItem, directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
        except OSError as error:
            self.log(f"Unable to list {directory}: {error}")
            return
        source = Path(self.source_input.text()).expanduser().resolve()
        for child in children:
            if child.is_symlink():
                continue
            try:
                relative = child.relative_to(source).as_posix()
            except ValueError:
                continue
            item = QTreeWidgetItem([child.name])
            item.setData(0, Qt.ItemDataRole.UserRole, relative)
            icon = self.style().StandardPixmap.SP_DirIcon if child.is_dir() else self.style().StandardPixmap.SP_FileIcon
            item.setIcon(0, self.style().standardIcon(icon))
            parent_item.addChild(item)
            if child.is_dir():
                self.populate_local_children(item, child)

    def populate_remote_tree(self, entries: list[dict]) -> None:
        self.remote_tree.clear()
        self.remote_tree.setHeaderLabel(f"Remote: {self.remote_root_input.text()}")
        root_item = QTreeWidgetItem([self.remote_root_input.text()])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        self.remote_tree.addTopLevelItem(root_item)
        self.add_remote_children(root_item, entries)
        root_item.setExpanded(True)

    def add_remote_children(self, parent_item: QTreeWidgetItem, entries: list[dict]) -> None:
        for entry in entries:
            item = QTreeWidgetItem([entry["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["path"])
            icon = self.style().StandardPixmap.SP_DirIcon if entry["directory"] else self.style().StandardPixmap.SP_FileIcon
            item.setIcon(0, self.style().standardIcon(icon))
            parent_item.addChild(item)
            if entry["directory"]:
                self.add_remote_children(item, entry["children"])

    @staticmethod
    def selected_paths(tree: QTreeWidget) -> list[str]:
        paths = [item.data(0, Qt.ItemDataRole.UserRole) for item in tree.selectedItems()]
        return SftpWorker.normalize_paths(paths)

    def choose_key_file(self, _checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH private key")
        if path:
            self.key_input.setText(path)
            self.save_app_config()

    def choose_source(self, _checked: bool = False) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose local source", self.source_input.text())
        if path:
            self.source_input.setText(path)
            self.save_app_config()
            self.refresh_local_tree()

    def closeEvent(self, event) -> None:
        self.save_app_config()
        if self.shell_worker is not None:
            self.shell_worker.stop()
            self.shell_worker.wait(1500)
        super().closeEvent(event)

    def connect_args_for_selection(self, tree: QTreeWidget, title: str) -> list[str] | None:
        paths = self.selected_paths(tree)
        if not paths:
            QMessageBox.information(self, title, "Select one or more files or directories first.")
            return None
        return paths

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
        answer = QMessageBox.question(
            self,
            "Confirm remote deletion",
            "Permanently delete the selected remote files/directories?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start_worker("delete", {"paths": paths})

    def deploy_source(self, _checked: bool = False) -> None:
        answer = QMessageBox.question(
            self,
            "Deploy source files",
            "Upload all files from the local source to the configured remote directory?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start_worker("deploy", {"mirror": self.mirror_checkbox.isChecked()})


def main() -> int:
    app = QApplication(sys.argv)
    icon_path = Path(__file__).parent / "icon.png"
    app.setWindowIcon(QIcon(str(icon_path)))
    window = RasconfManager()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())