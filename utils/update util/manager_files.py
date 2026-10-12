#!/usr/bin/env python3

"""The lightweight SFTP connection.

Berry owns the file views now, so the app no longer builds an SFTP tab or moves
files here. What stays is the connection itself: a short probe that opens an
SFTP session, the ``sftp_connected`` flag, the status dot in Settings >
Advanced connection settings and the
``sftp_connected`` / ``sftp_disconnected`` events - all of which Berry and
``device.is_connected("sftp")`` depend on."""


from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from manager_net import SftpWorker

if TYPE_CHECKING:
    from manager_gui import RasconfManager


class FileViewsMixin:
    """SFTP connect / disconnect, mixed into the main window.

    Every method here runs on the main window; the attributes it uses
    (``worker``, ``sftp_connected``, the connection widgets, ``log``,
    ``set_status_dot``, ``addon_event``) are set up by
    :class:`manager_gui.RasconfManager`, the class that mixes this in.
    """

    def connect_remote(self, _checked: bool = False) -> None:
        """Open an SFTP session as a connection test (no file tree)."""
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

        self.worker = SftpWorker("test", settings, {}, parent=self)
        self.worker.progress.connect(self.log)
        self.worker.succeeded.connect(self.operation_succeeded)
        self.worker.failed.connect(self.operation_failed)
        self.worker.finished.connect(self.operation_finished)
        self.set_busy(True)
        self.log("Connecting (SFTP)...")
        self.worker.start()

    def disconnect_remote(self, _checked: bool = False) -> None:
        """Close the session and mark the transport offline."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.sftp_connected = False
        self.set_status_dot("sftp", "offline")
        self.set_busy(False)
        self.log("SFTP session closed")
        self.statusBar().showMessage("Disconnected (SFTP)", 4000)
        self.addon_event("sftp_disconnected", reason="closed by the user")

    def set_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(not busy and self.sftp_connected)

    def operation_succeeded(self, _result) -> None:
        first_connection = not self.sftp_connected
        self.sftp_connected = True
        self.set_status_dot("sftp", "online")
        self.statusBar().showMessage("Connected (SFTP)", 4000)
        self.log("SFTP connection established")
        if first_connection:
            self.addon_event("sftp_connected", host=self.host_input.text().strip())

    def operation_failed(self, message: str) -> None:
        self.statusBar().showMessage("Connection failed (SFTP)", 5000)
        self.log("Error: " + message)
        was_connected = self.sftp_connected
        self.sftp_connected = False
        self.set_status_dot("sftp", "offline")
        QMessageBox.critical(self, "SFTP connection failed", message)
        if was_connected:
            self.addon_event("sftp_disconnected", reason=str(message))

    def operation_finished(self) -> None:
        self.set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
