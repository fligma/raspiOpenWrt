#!/usr/bin/env python3
"""The tabs that show a device: the terminal, the files and the web page. Each
is a mixin the window in :mod:`core.window` builds itself from, so the three
halves of one screen live together and reach :mod:`core.net` for the connection
they share.

None of them imports the window - it is named for the type checker only, and
it is the window that puts these tabs up.
"""

from __future__ import annotations


###############################################################################
# was manager_terminal.py
# The SSH tab: a terminal channel on the main connection, quick commands and the reboot watch.
###############################################################################


import re
from typing import TYPE_CHECKING

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.ui import save_command_history
from core.ui import StringListDialog, terminal_tab_icon
from core.net import RebootWaitWorker, RemoteExecWorker, SSHShellWorker
from core.net import TerminalScreen


if TYPE_CHECKING:
    from core.window import BerryManager


class TerminalMixin:
    """The SSH terminal tab, mixed into the main window.

    The terminal shows one shell channel of the app's main connection: it is
    opened from that session, never by connecting again, so closing it leaves
    the connection and SFTP alone. Every method here runs on the main window;
    the attributes it uses are set up by :class:`core.window.BerryManager`, the
    class that mixes this in.
    """

    def build_ssh_tab(self) -> None:
        ssh_page = QWidget()
        ssh_layout = QVBoxLayout(ssh_page)
        terminal_actions = QHBoxLayout()
        # The terminal attaches to the app's main connection, so its own pair of
        # buttons opens and closes the shell only - the session itself is the
        # Connection panel's business, and this is the place to look for a shell.
        self.shell_connect_button = QPushButton("Open terminal")
        self.shell_connect_button.setObjectName("primaryAction")
        self.shell_connect_button.setToolTip(
            "Attach a shell to the main connection (Connect above). Nothing "
            "opens while the main connection is down."
        )
        self.shell_connect_button.clicked.connect(self.connect_shell)
        self.shell_disconnect_button = QPushButton("Close terminal")
        self.shell_disconnect_button.setObjectName("dangerAction")
        self.shell_disconnect_button.setToolTip(
            "Close this shell; the main connection, and SFTP with it, stay open"
        )
        self.shell_disconnect_button.setEnabled(False)
        self.shell_disconnect_button.clicked.connect(self.disconnect_shell)
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
        terminal_actions.addWidget(self.shell_connect_button)
        terminal_actions.addWidget(self.shell_disconnect_button)
        terminal_actions.addWidget(clear_button)
        terminal_actions.addWidget(self.reboot_button)
        terminal_actions.addWidget(self.reboot_cancel_button)
        terminal_actions.addStretch(1)
        ssh_layout.addLayout(terminal_actions)

        self.quick_bar = QHBoxLayout()
        self.quick_bar.setSpacing(6)
        ssh_layout.addLayout(self.quick_bar)
        self.rebuild_quick_bar()

        # The terminal is one focused widget: keys go to the shell raw, the
        # shell's output drives the screen. core.net does all of it.
        self.terminal = TerminalScreen(ssh_page)
        # The size typed into Settings is the base: View > zoom multiplies it, and
        # core.window.apply_zoom() is what puts the two together on the screen.
        self.terminal_font = QFont("Consolas", int(self.app_config.get("terminal_font_size", 10)))
        self.terminal_font.setStyleHint(QFont.StyleHint.Monospace)
        self.terminal_font.setFixedPitch(True)
        self.terminal.set_font(self.terminal_font)
        self.terminal.bell.connect(lambda: self.statusBar().showMessage("Terminal bell", 2000))
        self.terminal.title_changed.connect(
            lambda title: self.tabs.setTabToolTip(self.tabs.indexOf(self.ssh_page), title) if title else None
        )
        ssh_layout.addWidget(self.terminal, 1)
        index = self.tabs.addTab(ssh_page, "SSH")
        # The terminal names itself with its own picture, straight from the
        # assets folder (see core.ui.terminal_tab_icon).
        self.tabs.setTabIcon(index, terminal_tab_icon())
        self.ssh_page = ssh_page

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
            # Live shell: type the command and press Enter for the user.
            self.save_history_entry(command)
            if self._looks_like_reboot(command):
                self.reboot_device()
                return
            self.shell_worker.send(command + "\n")
            self.terminal.setFocus()
            return
        settings = self.current_settings(silent=True)
        if settings is None:
            return
        if self._looks_like_reboot(command):
            # A one-off exec would just die mid-reboot; use the reboot flow so
            # the tool waits and reconnects afterwards.
            self.reboot_device()
            return
        if not self.main_open():
            # One connection for the whole app: no hidden session for a button.
            self.log("Connect first (Connection panel) to run a quick command")
            return
        if self.exec_worker is not None and self.exec_worker.isRunning():
            QMessageBox.information(self, "Busy", "Another command is still running.")
            return
        self.save_history_entry(command)
        self.exec_worker = RemoteExecWorker(settings, [command],
                                            shared=self.shared_conn, parent=self)
        self.exec_worker.log.connect(self.log)
        self.exec_worker.finished_ok.connect(lambda: self.statusBar().showMessage("Quick command completed", 4000))
        self.exec_worker.failed.connect(lambda msg: QMessageBox.critical(self, "Command failed", msg))
        self.tabs.setCurrentWidget(self.ssh_page)
        self.log(f"Running quick command: {command}")
        self.exec_worker.start()

    def save_history_entry(self, command: str) -> None:
        """Quick commands still land in the saved history file; typed commands
        are the shell's own business (its arrow-key history sees them)."""
        if not command.strip():
            return
        if not self.terminal_history or self.terminal_history[-1] != command:
            self.terminal_history.append(command)
        limit = int(self.app_config.get("terminal_history_limit", 500))
        self.terminal_history = self.terminal_history[-limit:]
        self.terminal_history_pos = len(self.terminal_history)
        save_command_history(self.terminal_history)

    def clear_command_history(self) -> None:
        self.terminal_history = []
        self.terminal_history_pos = 0
        save_command_history([])
        self.log("Terminal command history cleared")

    def clear_terminal(self) -> None:
        self.terminal.clear()

    def connect_shell(self, _checked: bool = False) -> None:
        """Attach a PTY channel to the app's main connection; never opens a
        session of its own, so a shell cannot appear without the connection."""
        if not self.connections_enabled():
            self.terminal.write_local(
                "Connections are switched off (Settings > Connection).\n")
            return
        if self.shell_worker is not None and self.shell_worker.isRunning():
            return
        if not self.main_open():
            self.terminal.write_local(
                "The main connection is not open. Press Connect in the "
                "Connection panel first.\n")
            return
        self.save_current_profile_edits()
        self.terminal.clear()
        cols, rows = self.terminal.grid_size()
        self.terminal.write_local(
            f"Opening a terminal on {self.host_input.text().strip()}...\n"
        )
        self.shell_worker = SSHShellWorker(self.shared_conn, cols=cols, rows=rows,
                                           parent=self)
        self.shell_worker.connected.connect(self.shell_connected)
        self.shell_worker.received.connect(self.terminal.feed)
        self.shell_worker.failed.connect(self.shell_failed)
        self.shell_worker.disconnected.connect(self.shell_disconnected)
        self.terminal.send_data.connect(self.shell_worker.send)
        self.terminal.resized.connect(self.shell_worker.resize)
        self.shell_worker.start()
        self.shell_connect_button.setEnabled(False)

    def shell_connected(self) -> None:
        if getattr(self, "_closing", False):
            return
        self.terminal.write_local("Terminal attached to the main connection.\n")
        self.terminal.setFocus()
        self.refresh_terminal_buttons()

    def shell_failed(self, message: str) -> None:
        if getattr(self, "_closing", False):
            return                     # a stop at close is not worth a dialog
        self.terminal.write_local(f"SSH error: {message}\n")
        QMessageBox.critical(self, "SSH terminal failed", message)

    def shell_disconnected(self) -> None:
        if getattr(self, "_closing", False):
            return                     # the whole window goes down anyway
        self.terminal.write_local("Terminal closed.\n")
        self.refresh_terminal_buttons()
        if self.shell_worker is not None:
            # The terminal is no one's to type into until the next shell opens.
            try:
                self.terminal.send_data.disconnect(self.shell_worker.send)
                self.terminal.resized.disconnect(self.shell_worker.resize)
            except (TypeError, RuntimeError):
                pass                      # a connect that failed never wired them
            self.shell_worker.deleteLater()
            self.shell_worker = None

    def disconnect_shell(self, _checked: bool = False) -> None:
        """Close the shell channel; the main connection stays as it was."""
        if self.shell_worker is not None:
            self.shell_worker.stop()

    def refresh_terminal_buttons(self) -> None:
        """Match the terminal's two buttons to the main connection and the shell."""
        shell_live = self.shell_worker is not None and self.shell_worker.isRunning()
        busy = self.worker is not None and self.worker.isRunning()
        can_attach = (self.connections_enabled() and self.main_open()
                      and not shell_live and not busy)
        self.shell_connect_button.setEnabled(can_attach)
        self.shell_disconnect_button.setEnabled(shell_live)

    # --- reboot watch ---

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
            self.terminal.write_local("Sending: reboot\n")
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
        self.terminal.write_local(f"[reboot] {message}\n")
        self.statusBar().showMessage(message)

    def on_reboot_back(self) -> None:
        if getattr(self, "_closing", False):
            return                     # must not open a fresh shell mid-close
        self.statusBar().showMessage("Device is back online", 5000)
        self.log("Device is back online after reboot")
        # The session this app held is gone with the device; bring the one main
        # connection back, and the terminal comes up with it.
        self.shared_conn.close()
        self.mark_main_closed("the device rebooted")
        self.connect_remote()

    def reboot_watch_finished(self) -> None:
        if getattr(self, "_closing", False):
            return
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


###############################################################################
# was manager_files.py
# The app's main connection.
#
# Berry owns the file views now, so the app no longer builds an SFTP tab or moves
# files here. What stays is the connection itself: the one main SSH/SFTP session
# the Connect button opens, the ``main_connected`` flag, the two status dots in
# Settings > Connection, and the ``sftp_connected`` / ``sftp_disconnected`` /
# ``ssh_connected`` / ``ssh_disconnected`` events - all of which Berry, the other
# addons and ``device.is_connected()`` depend on.
#
# One session carries both transports, so there is one Connect, one Disconnect and
# one startup switch: opening it lights the SSH and the SFTP dot together, and the
# terminal tab is only a PTY channel attached to that session. Addons set to the
# main connection work over it and wait for it to be open; an addon on a
# connection of its own opens that one per call and needs no main connection at
# all (see the guards in ``addon_api`` and ``core.net.SharedConnection``).
#
###############################################################################


from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from core.net import SharedSessionWorker

if TYPE_CHECKING:
    from core.window import BerryManager


class MainConnectionMixin:
    """Open and close the app's one main connection, mixed into the main window.

    Every method here runs on the main window; the attributes it uses
    (``worker``, ``main_connected``, the connection widgets, ``log``,
    ``set_status_dot``, ``addon_event``) are set up by
    :class:`core.window.BerryManager`, the class that mixes this in.
    """

    def main_open(self) -> bool:
        """Is the main connection up? Asked in plain words by the terminal tab."""
        return self.shared_conn.active()

    def connect_remote(self, _checked: bool = False) -> None:
        """Open the app's main connection: one SSH session with SFTP on it."""
        if not self.connections_enabled():
            self.log("Connections are switched off (Settings > Connection)")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        if self.shared_conn.active():
            # Open already (a double click, or the startup switch won the race):
            # just agree with what the session says.
            self.mark_main_connected()
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

        self.worker = SharedSessionWorker(self.shared_conn, settings, parent=self)
        self.worker.succeeded.connect(self.operation_succeeded)
        self.worker.failed.connect(self.operation_failed)
        self.worker.finished.connect(self.operation_finished)
        self.set_busy(True)
        self.log("Connecting (SSH + SFTP)...")
        self.worker.start()

    def disconnect_remote(self, _checked: bool = False) -> None:
        """Close the main connection: the terminal channel goes with the session."""
        if self.worker is not None and self.worker.isRunning():
            return
        if self.shell_worker is not None and self.shell_worker.isRunning():
            self.shell_worker.stop()
        self.shared_conn.close()
        self.mark_main_closed("closed by the user")

    def mark_main_connected(self) -> None:
        """Agree the main session is open: flag, both dots, words, events (GUI thread only)."""
        first_connection = not self.main_connected
        self.main_connected = True
        self.set_status_dot("sftp", "online")
        self.set_status_dot("ssh", "online")
        self.statusBar().showMessage("Connected (SSH + SFTP)", 4000)
        if first_connection:
            host = self.host_input.text().strip()
            self.log("Main connection established (SSH + SFTP)")
            self.addon_event("sftp_connected", host=host)
            self.addon_event("ssh_connected", host=host)
        self.update_connection_ui()

    def mark_main_closed(self, reason: str = "the session closed") -> None:
        """Agree that the main session is gone, and tell the addons once."""
        was_connected = self.main_connected
        self.main_connected = False
        self.set_status_dot("sftp", "offline")
        self.set_status_dot("ssh", "offline")
        if was_connected:
            self.log("Main connection closed")
            self.statusBar().showMessage("Disconnected", 4000)
            self.addon_event("sftp_disconnected", reason=reason)
            self.addon_event("ssh_disconnected", reason=reason)
        self.update_connection_ui()

    def set_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy and not self.main_connected)
        self.disconnect_button.setEnabled(not busy and self.main_connected)

    def operation_succeeded(self) -> None:
        if getattr(self, "_closing", False):
            return                     # the app is going down; no news needed
        if not self.connections_enabled():
            # The master switch went off while the connect was in flight.
            self.shared_conn.close()
            return
        self.mark_main_connected()
        # The terminal is a channel on this session: bring it up with it, when
        # the SSH tab is switched on at all.
        if bool(self.app_config.get("ssh_enabled", True)):
            self.connect_shell()

    def operation_failed(self, message: str) -> None:
        if getattr(self, "_closing", False):
            return                     # a cancel at close is not worth a dialog
        self.statusBar().showMessage("Connection failed", 5000)
        self.log("Error: " + message)
        self.main_connected = False
        self.set_status_dot("sftp", "offline")
        self.set_status_dot("ssh", "offline")
        QMessageBox.critical(self, "Connection failed", message)
        self.update_connection_ui()

    def operation_finished(self) -> None:
        if getattr(self, "_closing", False):
            return                     # the window keeps no buttons to free
        self.set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None


###############################################################################
# was manager_browser.py
# Everything the embedded browser tabs need.
#
# Two halves live here: the plain helpers below - the certificate bypass switch
# and the app relaunch - and :class:`BrowserMixin`, the web tabs themselves.
#
# QtWebEngine offers no callback for a certificate it does not trust - the page
# simply fails with the Chromium error. The one supported way to accept such a
# page is the Chromium command-line switch ``--ignore-certificate-errors``, which
# is read from ``QTWEBENGINE_CHROMIUM_FLAGS`` when QtWebEngine starts its browser
# process. That happens with the first embedded view, so the switch has to be in
# the environment before any tab is built; changing it later only takes effect on
# a restart (see :func:`restart_process`).
#
###############################################################################


import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from addon_common import clean_child_env
from core.ui import web_tab_url
from core.ui import TabCloseButton, TabEditDialog

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
    from core.window import BerryManager


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


if QWebEngineView is not None:
    class QuietWebView(QWebEngineView):
        """A web view that never asks the window for more room than it has.

        Booting the browser process moves a plain view's size hints: the
        page announces a preferred size and the layout minimums the window
        obeys rise with it, so opening a browser tab resizes the whole
        window on its own. The view fills whatever room it is given, and
        that is the only size it should ever have asked for - both hints
        are pinned at nothing here, and the layout's own stretch keeps
        the page as big as the tab it sits in."""

        def minimumSizeHint(self) -> QSize:
            return QSize(0, 0)

        def sizeHint(self) -> QSize:
            return QSize(0, 0)
else:
    QuietWebView = None


class _CornerKeeper(QObject):
    """Keeps a small button pinned to the corner of the web view it floats on.

    The view is resized with the window, split by the page's own scrolling and
    moved when the tab bar re-measures; a button placed once would end up
    anywhere but the corner, so this re-places it on every resize and show."""

    def __init__(self, place: Callable[[], None]) -> None:
        super().__init__()
        self._place = place

    def eventFilter(self, watched: object, event: object) -> bool:
        try:
            kind = event.type()
        except AttributeError:
            return False
        if kind in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return False


class _FirstShowLoad(QObject):
    """Loads a web tab's address the first moment the tab is actually seen.

    Making the view is cheap; filling it is not - every load hands work to
    Chromium and keeps a rendered page in memory, and at start-up most of the
    profile's tabs are the ones the user is not looking at. So a tab born out
    of sight waits: nothing is fetched until the page becomes visible, and the
    address is read at that moment, which means a host change or an addon's
    navigation in the meantime is what gets loaded. Whoever arms this must
    also ``cancel`` it when something else loads the view first."""

    def __init__(self, load: Callable[[], None]) -> None:
        super().__init__()
        self._load = load
        self._armed = True

    def cancel(self) -> None:
        """The page has been loaded, or told to load; nothing is owed to it."""
        self._armed = False

    def fire(self) -> bool:
        """Load now rather than wait to be seen; ``False`` when it had already
        loaded, so the caller knows its own request still stands."""
        if not self._armed:
            return False
        self._armed = False
        self._load()
        return True

    def eventFilter(self, watched: object, event: object) -> bool:
        if not self._armed:
            return False
        try:
            kind = event.type()
        except AttributeError:
            return False
        if kind == QEvent.Type.Show:
            self.fire()                 # once, whatever the load does next
        return False


class BrowserMixin:
    """Embedded browser tabs, mixed into the main window; its attributes come from ``core.window.BerryManager``."""

    def _build_web_like_tab(self, initial_url: str, title: str,
                            position: int | None = None,
                            home: bool = True,
                            menu: list | None = None,
                            on_menu: Callable[[str], None] | None = None,
                            defer: bool = False,
                            ) -> tuple[QLineEdit, QWebEngineView | None, QWidget]:
        """Build one browser tab and hand back its parts.

        ``defer`` holds the first load back until the tab is shown; the
        address a page opens itself is never held, since it arrives already
        wanted."""
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

        if QuietWebView is None:
            notice = QLabel(
                "Embedded browser is unavailable.\n"
                "Install PyQt6-WebEngine to view this page inside the app, "
                "or open the URL in your system browser:\n\n" + initial_url
            )
            notice.setWordWrap(True)
            notice.setStyleSheet("color: #ffb020; padding: 12px;")
            layout.addWidget(notice, 1)
            open_btn = QPushButton("Open in browser")
            open_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(url_input.text())))
            layout.addWidget(open_btn)
            go_button.clicked.connect(lambda: None)
            add_tab()
            return url_input, None, page

        view = QuietWebView()
        if WebTabPage is not None:
            view.setPage(WebTabPage(self))
        add_tab()

        # A tab held back from loading still answers to Go, to Enter in the
        # address bar and to the api; whichever of those starts a load ends
        # the waiting, so the show filter has nothing left to do.
        hold: _FirstShowLoad | None = None

        def load_url() -> None:
            if hold is not None:
                hold.cancel()
            view.setUrl(QUrl(url_input.text()))

        def go_home() -> None:
            url_input.setText(web_tab_url(self._tab_def_for(url_input), self.app_config.get("host", "192.168.1.1")))
            load_url()

        go_button.clicked.connect(load_url)
        if home:
            home_button.clicked.connect(go_home)
        else:
            # A tab a page opened itself belongs to no profile, so the address
            # behind the Home button would be the active host's - the router,
            # not the site. Offered, it moves the tab somewhere the user never
            # asked for; it is left off instead.
            home_button.setVisible(False)
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

        # "Open in system browser" reads the address bar rather than the view,
        # so it is useful on a tab that has not loaded yet.
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(url_input.text())))

        if defer:
            hold = _FirstShowLoad(load_url)
            page.installEventFilter(hold)
            page._first_show_load = hold    # the filter lives with the page
            # Anything that puts a page into the view - the api navigating it,
            # or set_html serving one - settles the tab without this filter.
            view.urlChanged.connect(lambda _url, h=hold: h.cancel())
            view.loadStarted.connect(lambda h=hold: h.cancel())
            if page.isVisible():
                # The tab came up in front of the user, so its showing event
                # passed before this filter was fitted: load it here rather
                # than wait for a second one that may never come.
                load_url()

        if menu:
            self._attach_overlay_menu(view, menu, on_menu)

        layout.addWidget(view, 1)
        return url_input, view, page

    def _attach_overlay_menu(self, view: "QWebEngineView", menu: list,
                             on_pick: Callable[[str], None] | None) -> None:
        """Float a small round button on the page's bottom-right corner: it pops
        up the entries the addon asked for, and hands the picked one back through
        ``on_pick``. The button is a child of the view and native like it, so it
        sits over the web content instead of under it; a nameless event filter
        keeps it in the corner while the page resizes."""
        button = QPushButton("\u2261", view)
        button.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        button.setFixedSize(30, 30)
        button.setToolTip("Actions for this page")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton { background: rgba(38, 38, 38, 210); color: #e8e8e8;"
            " border: 1px solid #6a6a6a; border-radius: 15px;"
            " font-size: 15px; padding: 0px; }"
            "QPushButton:hover { background: rgba(70, 70, 70, 225); }")
        popup = QMenu(button)
        for item in menu:
            entry = item if isinstance(item, dict) else {}
            identifier = str(entry.get("id") or "")
            if not identifier:
                continue
            action = popup.addAction(str(entry.get("label") or identifier))
            action.triggered.connect(
                lambda _=False, i=identifier: on_pick(i) if callable(on_pick)
                else None)
        button.clicked.connect(lambda: popup.popup(
            button.mapToGlobal(QPoint(0, button.height()))))

        def place() -> None:
            button.move(max(0, view.width() - button.width() - 14),
                        max(0, view.height() - button.height() - 14))
            button.raise_()

        keeper = _CornerKeeper(place)
        view.installEventFilter(keeper)
        button.installEventFilter(keeper)
        button._corner_keeper = keeper        # the filter lives with the button
        place()

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
        url_input, view, page = self._build_web_like_tab(
            url, tab.get("name", "Tab"), position, defer=True)
        self._web_tab_entries.append({"page": page, "url_input": url_input, "view": view, "tab": tab})

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
                              on_closed: Callable[[], None] | None = None,
                              on_menu: Callable[[str], None] | None = None) -> dict:
        """Open a browser tab that belongs to no profile (addons use it): not saved, not listed, not rebuilt. It wears an X for the user to close, and whoever opened it is told when that happens. ``tab["menu"]`` gives the entries of the small button floated over the page, and ``on_menu`` is told which one was picked."""
        default_host = self.app_config.get("host", "192.168.1.1")
        url = web_tab_url(tab, tab.get("ip") or default_host)
        url_input, view, page = self._build_web_like_tab(
            url, tab.get("name", "Tab"), position,
            menu=list(tab.get("menu") or []), on_menu=on_menu, defer=True)
        entry = {"page": page, "url_input": url_input, "view": view, "tab": tab}
        if callable(on_closed):
            entry["on_closed"] = on_closed
        self._transient_tab_entries.append(entry)
        self._mark_tab_closable(page)
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
        button = TabCloseButton(bar)
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
            "", "New page", self._content_insert_index(), home=False)
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
