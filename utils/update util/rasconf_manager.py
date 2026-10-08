#!/usr/bin/env python3
"""Manage, deploy, and monitor the rasconf web interface on a remote OpenWrt host."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from manager_const import BASE_DIR, RESOURCE_DIR
from manager_gui import RasconfManager


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Build the main window and hand control to the Qt event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("Rasconf Manager")
    # From a checkout the icon sits next to the sources, in a PyInstaller build
    # it is unpacked into the temporary resource directory.
    for candidate in (RESOURCE_DIR / "icon.png", BASE_DIR / "icon.png"):
        if candidate.is_file():
            app.setWindowIcon(QIcon(str(candidate)))
            break
    window = RasconfManager()
    window.show()
    # Only shrinks the window when the default size exceeds the screen.
    window.fit_window_to_screen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
