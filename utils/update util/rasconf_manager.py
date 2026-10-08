#!/usr/bin/env python3
"""
Manage Router & Web interfaces.
Copyright 2026 fligma. Licensed under the MIT License.
"""

from __future__ import annotations

import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from manager_const import BASE_DIR, RESOURCE_DIR
from manager_gui import RasconfManager

def main() -> int:
    """Build the main window and hand control to the Qt event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("Rasconf Manager")
    for candidate in (RESOURCE_DIR / "icon.png", BASE_DIR / "icon.png"):
        if candidate.is_file():
            app.setWindowIcon(QIcon(str(candidate)))
            break
    window = RasconfManager()
    window.show()
    window.fit_window_to_screen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
