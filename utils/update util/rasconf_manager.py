#!/usr/bin/env python3
"""Entry point: start the Qt application and show the rasconf Manager window.

This is the one module that runs as a program. Every other file holds a piece
of the app - the window, its tabs, the addon runtime - and is imported; here
the event loop is started, the window is shown and the two things that must
happen before it exists are done: the WebEngine OpenGL attribute, which Qt
only accepts before the application object, and the saved certificate switch,
which Chromium reads when its process starts on the first embedded tab.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from manager_browser import set_cert_bypass
from manager_config import load_app_config
from manager_const import ICON_FILES, find_asset
from manager_gui import RasconfManager


def main() -> int:
    """Build the application, open the window and run it. Returns the exit code."""
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    set_cert_bypass(bool(load_app_config().get("ignore_cert_errors")))

    app = QApplication(sys.argv)
    app.setApplicationName("rasconf Manager")
    app.setApplicationDisplayName("rasconf Manager")
    app.setOrganizationName("fligma")
    icon_path = find_asset(ICON_FILES[0])
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    window = RasconfManager()
    window.fit_window_to_screen()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
