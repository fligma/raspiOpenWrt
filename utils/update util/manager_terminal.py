#!/usr/bin/env python3

"""The SSH tab: an interactive terminal screen, quick commands and the reboot watch."""


from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manager_config import save_command_history
from manager_widgets import StringListDialog, terminal_tab_icon
from manager_net import RebootWaitWorker, RemoteExecWorker, SSHShellWorker
from manager_term import TerminalScreen


if TYPE_CHECKING:
    from manager_gui import BerryManager


class TerminalMixin:
    """The SSH terminal tab, mixed into the main window.

    Every method here runs on the main window; the attributes it uses are set up
    by :class:`manager_gui.BerryManager`, the class that mixes this in.
    """

    def build_ssh_tab(self) -> None:
        ssh_page = QWidget()
        ssh_layout = QVBoxLayout(ssh_page)
        terminal_actions = QHBoxLayout()
        # The SSH connect/disconnect pair lives here rather than in the
        # connection panel: the tab that shows the shell is the place to open
        # and close it.
        self.shell_connect_button = QPushButton("Connect (SSH)")
        self.shell_connect_button.setObjectName("primaryAction")
        self.shell_connect_button.setToolTip(
            "Open the interactive shell with the profile's credentials "
            "(Settings > Connection)"
        )
        self.shell_connect_button.clicked.connect(self.connect_shell)
        self.shell_disconnect_button = QPushButton("Disconnect (SSH)")
        self.shell_disconnect_button.setObjectName("dangerAction")
        self.shell_disconnect_button.setToolTip("Close the SSH terminal session")
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
        # shell's output drives the screen. manager_term does all of it.
        self.terminal = TerminalScreen(ssh_page)
        # The size typed into Settings is the base: View > zoom multiplies it, and
        # manager_gui.apply_zoom() is what puts the two together on the screen.
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
        # assets folder (see manager_widgets.terminal_tab_icon).
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
        if self.exec_worker is not None and self.exec_worker.isRunning():
            QMessageBox.information(self, "Busy", "Another command is still running.")
            return
        self.save_history_entry(command)
        self.exec_worker = RemoteExecWorker(settings, [command], parent=self)
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
        if not self.connections_enabled():
            self.terminal.write_local(
                "Connections are switched off (Settings > Connection).\n")
            return
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
        self.terminal.clear()
        cols, rows = self.terminal.grid_size()
        self.terminal.write_local(
            f"Connecting to {settings['username']}@{settings['host']}:{settings['port']}...\n"
        )
        self.shell_worker = SSHShellWorker(settings, cols=cols, rows=rows, parent=self)
        self.shell_worker.connected.connect(self.shell_connected)
        self.shell_worker.received.connect(self.terminal.feed)
        self.shell_worker.failed.connect(self.shell_failed)
        self.shell_worker.disconnected.connect(self.shell_disconnected)
        self.terminal.send_data.connect(self.shell_worker.send)
        self.terminal.resized.connect(self.shell_worker.resize)
        self.shell_worker.start()
        self.shell_connect_button.setEnabled(False)

    def shell_connected(self) -> None:
        self.terminal.write_local("SSH shell connected.\n")
        self.shell_disconnect_button.setEnabled(True)
        self.terminal.setFocus()
        self.set_status_dot("ssh", "online")
        self.addon_event("ssh_connected", host=self.host_input.text().strip())

    def shell_failed(self, message: str) -> None:
        self.terminal.write_local(f"SSH error: {message}\n")
        QMessageBox.critical(self, "SSH terminal failed", message)

    def shell_disconnected(self) -> None:
        self.terminal.write_local("SSH shell disconnected.\n")
        self.shell_connect_button.setEnabled(True)
        self.shell_disconnect_button.setEnabled(False)
        self.set_status_dot("ssh", "offline")
        self.addon_event("ssh_disconnected", reason="the session closed")
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
        if self.shell_worker is not None:
            self.shell_worker.stop()

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
