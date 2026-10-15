#!/usr/bin/env python3
"""Entry point: start the Qt application and show the Berry Manager window.

This is the one module that runs as a program: here the event loop is started
and the window is shown. Everything else is imported - ``core/*.py`` holds the
window's own parts, the addon runtime and the look of the app, while
``addon_api``, ``addon_client`` and ``addon_common`` stay beside this file as
the contract an addon reads. Here too are the two things that must happen
before the window exists: the WebEngine OpenGL attribute, which Qt only
accepts before the application object, and the saved certificate switch, which
Chromium reads when its process starts on the first embedded tab.
"""

from __future__ import annotations

import atexit
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from addon_common import kill_all_trees
from core.views import (clear_replaced_program, relaunch_process,
                        set_cert_bypass, take_restart_request)
import core.ui
from core.ui import install_smart_tooltips, load_app_config
from core.const import ICON_FILES, find_asset
from core.addon import prune_retired_addons
from core.window import BerryManager


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

    # An addon this version no longer ships can still be sitting in ``addons``
    # from an older install, with its settings beside it. That is cleared before
    # the window looks for anything to run, so an upgrade never starts a retired
    # addon or lists one in Settings, and what it saved moves to the addon that
    # took its place. The words are kept for the window's own log.
    cleared = prune_retired_addons()

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
    core.ui.ensure_installed()
    core.ui.use(boot_config.get("theme", "default"))
    # The cursor-following, fading tool-tip is the whole window's business, so
    # it goes on the application object once, before any window is built; the
    # bubble then wears the theme that was just resolved.
    install_smart_tooltips(app)
    core.ui.show(boot_config)

    window = BerryManager()
    for line in cleared:
        window.log(line)
    window.fit_window_to_screen()
    window.show()
    # The window is on screen: the card has said everything it had to say.
    core.ui.finish()
    return app.exec()


if __name__ == "__main__":
    _code = main()
    if take_restart_request() and not relaunch_process():
        print("could not start a new copy; reopen the app manually",
              file=sys.stderr)
    raise SystemExit(_code)
