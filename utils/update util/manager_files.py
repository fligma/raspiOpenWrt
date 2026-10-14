#!/usr/bin/env python3

"""The app's main connection.

Berry owns the file views now, so the app no longer builds an SFTP tab or moves
files here. What stays is the connection itself: the one main SSH/SFTP session
the Connect button opens, the ``main_connected`` flag, the two status dots in
Settings > Connection, and the ``sftp_connected`` / ``sftp_disconnected`` /
``ssh_connected`` / ``ssh_disconnected`` events - all of which Berry, the other
addons and ``device.is_connected()`` depend on.

One session carries both transports, so there is one Connect, one Disconnect and
one startup switch: opening it lights the SSH and the SFTP dot together, and the
terminal tab is only a PTY channel attached to that session. Addons set to the
main connection work over it and wait for it to be open; an addon on a
connection of its own opens that one per call and needs no main connection at
all (see the guards in ``addon_api`` and ``manager_net.SharedConnection``).
"""


from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from manager_net import SharedSessionWorker

if TYPE_CHECKING:
    from manager_gui import BerryManager


class MainConnectionMixin:
    """Open and close the app's one main connection, mixed into the main window.

    Every method here runs on the main window; the attributes it uses
    (``worker``, ``main_connected``, the connection widgets, ``log``,
    ``set_status_dot``, ``addon_event``) are set up by
    :class:`manager_gui.BerryManager`, the class that mixes this in.
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
