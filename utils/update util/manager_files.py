#!/usr/bin/env python3

"""The app's main (shared) connection.

Berry owns the file views now, so the app no longer builds an SFTP tab or moves
files here. What stays is the connection itself: the shared SSH/SFTP session
the Connect button opens, the ``sftp_connected`` flag, the status dot in
Settings > Connection, and the ``sftp_connected`` /
``sftp_disconnected`` events - all of which Berry and
``device.is_connected("sftp")`` depend on. Addons set to "main" connection mode
work over this same session; see manager_net.SharedConnection."""


from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from manager_net import SharedSessionWorker

if TYPE_CHECKING:
    from manager_gui import BerryManager


class FileViewsMixin:
    """SFTP connect / disconnect, mixed into the main window.

    Every method here runs on the main window; the attributes it uses
    (``worker``, ``sftp_connected``, the connection widgets, ``log``,
    ``set_status_dot``, ``addon_event``) are set up by
    :class:`manager_gui.BerryManager`, the class that mixes this in.
    """

    def connect_remote(self, _checked: bool = False) -> None:
        """Open the app's shared SSH/SFTP session (the main connection)."""
        if not self.connections_enabled():
            self.log("Connections are switched off (Settings > Connection)")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        if self.shared_conn.active():
            # Already open (an addon in main mode): just agree.
            self.mark_shared_connected()
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
        self.log("Connecting (SFTP)...")
        self.worker.start()

    def disconnect_remote(self, _checked: bool = False) -> None:
        """Close the shared session and mark the transport offline."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.shared_conn.close()
        self.mark_shared_closed("closed by the user")

    def mark_shared_connected(self) -> None:
        """Agree the shared session is open: flag, dot, words, event (GUI thread only)."""
        first_connection = not self.sftp_connected
        self.sftp_connected = True
        self.set_status_dot("sftp", "online")
        self.statusBar().showMessage("Connected (SFTP)", 4000)
        if first_connection:
            self.log("SFTP connection established")
            self.addon_event("sftp_connected", host=self.host_input.text().strip())

    def mark_shared_closed(self, reason: str = "the session closed") -> None:
        """Agree that the shared session is gone, and tell the addons once."""
        was_connected = self.sftp_connected
        self.sftp_connected = False
        self.set_status_dot("sftp", "offline")
        if was_connected:
            self.log("SFTP session closed")
            self.statusBar().showMessage("Disconnected (SFTP)", 4000)
            self.addon_event("sftp_disconnected", reason=reason)

    def set_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(not busy and self.sftp_connected)

    def operation_succeeded(self) -> None:
        if getattr(self, "_closing", False):
            return                     # the app is going down; no news needed
        if not self.connections_enabled():
            # The master switch went off while the connect was in flight.
            self.shared_conn.close()
            return
        self.mark_shared_connected()

    def operation_failed(self, message: str) -> None:
        if getattr(self, "_closing", False):
            return                     # a cancel at close is not worth a dialog
        self.statusBar().showMessage("Connection failed (SFTP)", 5000)
        self.log("Error: " + message)
        self.sftp_connected = False
        self.set_status_dot("sftp", "offline")
        QMessageBox.critical(self, "SFTP connection failed", message)

    def operation_finished(self) -> None:
        if getattr(self, "_closing", False):
            return                     # the window keeps no buttons to free
        self.set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
