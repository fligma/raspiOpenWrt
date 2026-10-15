#!/usr/bin/env python3
"""Entry point: start the Qt application and show the Berry Manager window.

This is the one module that runs as a program. Every other file holds a piece
of the app - the window, its tabs, the addon runtime - and is imported; here
the event loop is started, the window is shown and the two things that must
happen before it exists are done: the WebEngine OpenGL attribute, which Qt
only accepts before the application object, and the saved certificate switch,
which Chromium reads when its process starts on the first embedded tab.
"""

from __future__ import annotations

import atexit
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from addon_common import kill_all_trees
from manager_browser import (clear_replaced_program, relaunch_process,
                             set_cert_bypass, take_restart_request)
from manager_config import load_app_config
from manager_const import ICON_FILES, find_asset
import manager_splash
import manager_theme
from manager_gui import BerryManager


def main() -> int:
    """Build the application, open the window and run it. Returns the exit code."""
    # An update swaps the new program in while the old one still runs, and
    # only this fresh copy can take the moved-aside file away.
    clear_replaced_program()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    boot_config = load_app_config()
    set_cert_bypass(bool(boot_config.get("ignore_cert_errors")))
    # Whatever way this process goes down, no addon child outlives it;
    # the window's own closing path releases them long before this runs.
    atexit.register(kill_all_trees)

    app = QApplication(sys.argv)
    app.setApplicationName("Berry Manager")
    app.setOrganizationName("fligma")
    icon_path = find_asset(ICON_FILES[0])
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    # The splash goes up before the window is built, so the wait has a face,
    # and paints itself on the way in: the first thing a launch shows is the
    # card, not a blank screen. The theme is resolved here for it to be
    # painted from; the window runs the same two calls again on its way in,
    # which changes nothing but the revision count.
    manager_theme.ensure_installed()
    manager_theme.use(boot_config.get("theme", "default"))
    manager_splash.show(boot_config)

    window = BerryManager()
    window.fit_window_to_screen()
    window.show()
    # The window is on screen: the card has said everything it had to say.
    manager_splash.finish()
    return app.exec()


if __name__ == "__main__":
    _code = main()
    if take_restart_request() and not relaunch_process():
        print("could not start a new copy; reopen the app manually",
              file=sys.stderr)
    raise SystemExit(_code)
