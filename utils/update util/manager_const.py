#!/usr/bin/env python3
"""Paths, defaults and the dark stylesheet shared by every rasconf Manager module."""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath


# ---------------------------------------------------------------------------
# Where things live - a source checkout and a frozen build resolve differently
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    # BASE_DIR is the exe folder (writable, user data), RESOURCE_DIR is the
    # read-only directory PyInstaller unpacks next to it.
    BASE_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(sys._MEIPASS)
    REPOSITORY_ROOT = BASE_DIR
    IS_COMPILED = True
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR
    # Walk up out of utils/update util to find the repository root.
    REPOSITORY_ROOT = BASE_DIR.parents[2] if len(BASE_DIR.parents) >= 2 else BASE_DIR
    IS_COMPILED = False

DEFAULT_SOURCE = REPOSITORY_ROOT / "src" / "www_rasconf"

# Images and anything else that ships inside the program. A PyInstaller build
# unpacks them into RESOURCE_DIR (a temporary folder next to the exe), while a
# source checkout keeps them beside the scripts - which is also RESOURCE_DIR -
# so one path serves both layouts.
ASSETS_DIR = RESOURCE_DIR / "assets"

#: The two logos: View > Alternate icon picks the second one, and the toolbar,
#: the window icon and the About dialog all follow that choice.
ICON_FILES = ("icon.png", "icon2.png")


def find_asset(filename: str) -> Path | None:
    """Locate a file that ships with the program, or ``None``.

    ``assets`` beside the resources is the normal home. The older places are
    still searched so nothing breaks: a build that kept the icons at the top
    level of the archive, and a user who dropped their own ``icon.png`` next to
    the executable.
    """
    for candidate in (ASSETS_DIR / filename, RESOURCE_DIR / filename,
                      BASE_DIR / "assets" / filename, BASE_DIR / filename):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# File names and other fixed identifiers
# ---------------------------------------------------------------------------

# Kept separate from ~/.ssh/known_hosts so "trust unknown host" never pollutes
# the keys the system ssh client uses.
KNOWN_HOSTS_FILE = Path.home() / ".ssh" / "known_hosts_rasconf"
KEYRING_SERVICE = "rasconf-sftp-manager"

# The manager's own working folder, made on first boot and holding everything
# it writes rather than ships: the profile config, the terminal command history
# and the activity log, plus the shared addon build record (``_build.json``).
# Keeping it in one place keeps the program root tidy and the machine-local
# files easy to ignore in Git. See ``manager_config._prepare_config_dir``.
CONFIG_DIR = BASE_DIR / "config"
APP_CONFIG_FILE = CONFIG_DIR / "config.json"
APP_CONFIG_EXAMPLE_FILE = RESOURCE_DIR / "config.example.json"
HISTORY_FILE = CONFIG_DIR / "command_history.json"
LOG_FILE = CONFIG_DIR / "rasconf_manager.log"
MAX_COMMAND_HISTORY = 200

# Drop-in addons: an `addon.py` (or a plain .py file) and any executable that
# belongs to it. Created the first time the addon list is read, so there is
# always somewhere to put a file - next to the exe in a packaged build.
ADDONS_DIR = BASE_DIR / "addons"

# The addons that ship inside the program. The build copies every addon file
# out of ``addons`` into the archive under this name, so a packaged program
# brings its own addons with it and a user cannot delete them by accident.
# In a source checkout the two folders are the same directory, and there is
# nothing separate to ship - ``HAS_BUILTIN_ADDONS`` says whether there is.
BUILTIN_ADDONS_DIR = RESOURCE_DIR / "addons"
HAS_BUILTIN_ADDONS = IS_COMPILED and BUILTIN_ADDONS_DIR != ADDONS_DIR

# The addons the source ships with, named here so a source checkout (where the
# drop-in folder and the build folder are the same, so ``builtin`` is never set)
# still knows which addons came with the program rather than being added later.
# A packaged build marks the same files ``builtin`` as it copies them in.
SHIPPED_ADDON_IDS = {"berry", "cert_manager", "web_installer"}

# One JSON file per addon, kept in the addon's own folder under ``addons/_data``
# (see ADDON_DATA_DIR below), holding the settings that belong to the
# installation rather than to a profile: where the certificate lives, which
# command restarts the web server. Editable by hand, and shown under the addon's
# name in Settings > Addons. See addon_api.ConfigAPI.
#
# Every addon's own working folder, and where its config lives too:
# ``addons/_data/<id>/config.json`` (plus ``fields.json`` beside it). The folder
# is a sibling the scanner skips (a leading underscore), so an addon's data is
# never mistaken for an addon. The shared ``_build.json`` the Addon Dev tab
# writes lives in the manager's own ``config`` folder, not here.
ADDON_DATA_DIR = ADDONS_DIR / "_data"

# The Addon Dev tab reads and writes an addon as a ``.py`` file: it shows the
# source, runs a test script and, where a Python is available, builds a program
# from it. Its two working folders live inside the addons folder and are never
# scanned as addons, because a name starting with an underscore is skipped:
# PyInstaller's staging area, and the test scripts written for an addon.
ADDON_BUILD_DIR = ADDONS_DIR / "_build"
ADDON_TEST_DIR = ADDONS_DIR / "_tests"

#: Where the compiled copy of an addon is put, beside its source, so the same
#: id and the same sidecar manifest keep working. ``<id>.exe`` on Windows.
ADDON_EXECUTABLE_SUFFIX = ".exe" if sys.platform == "win32" else ""

PROJECT_GITHUB_URL = "https://github.com/fligma/raspiOpenWrt"
PROJECT_LICENSE_URL = PROJECT_GITHUB_URL + "/blob/main/LICENSE"

# ---------------------------------------------------------------------------
# Defaults - used when config.json is missing a key or a profile
# ---------------------------------------------------------------------------

# Interface zoom: the density the whole window is drawn at. View > Zoom in/out
# walks the ladder below, and every px and pt size written in the stylesheet
# (plus the terminal's own font and the hand-sized widgets) is multiplied by it.
# 100 is exactly what the stylesheet spells out.
UI_ZOOM_STEPS = (60, 70, 80, 90, 100, 110, 125, 140, 160, 180, 200)
UI_ZOOM_DEFAULT = 100
UI_ZOOM_MIN = UI_ZOOM_STEPS[0]
UI_ZOOM_MAX = UI_ZOOM_STEPS[-1]

DEFAULT_PROFILE = {
    "name": "Default",
    "host": "192.168.1.1",
    "port": 22,
    "username": "root",
    "key_file": "",
    "remote_root": "/www_rasconf",
    "source": str(DEFAULT_SOURCE),
    "trust_unknown_host": False,
    "remember_password": False,
}

# Everything one profile can hold: the connection defaults plus everything
# the Settings tab can change. A profile record in config.json is this dict
# with the user's own values; see manager_config for the profile store.
DEFAULT_APP_CONFIG = {
    "host": DEFAULT_PROFILE["host"],
    "port": DEFAULT_PROFILE["port"],
    "username": DEFAULT_PROFILE["username"],
    "key_file": DEFAULT_PROFILE["key_file"],
    "remote_root": DEFAULT_PROFILE["remote_root"],
    "source": DEFAULT_PROFILE["source"],
    "trust_unknown_host": DEFAULT_PROFILE["trust_unknown_host"],
    "remember_password": DEFAULT_PROFILE["remember_password"],
    "ssh_timeout": 12,
    "logging_enabled": True,
    "show_hidden_files": False,
    # Auto-connect is per protocol now that SSH and SFTP have their own
    # connection indicators; each fires at startup when its tab is enabled.
    "auto_connect_sftp": False,
    "auto_connect_ssh": False,
    "use_alt_icon": False,
    # Which transports the connection panel offers. The Berry and SSH tabs
    # themselves are switched on and off in Settings; these flags only decide
    # whether the SFTP / SSH connect buttons and their status dots in Settings >
    # Advanced connection settings are shown.
    "sftp_enabled": True,
    "ssh_enabled": True,
    # The Addon Dev tab: the index of every addon, their logs and processes, and
    # the tools that turn a ``.py`` into a program. Shown in a source checkout
    # and in a built one alike - switched off here takes the tab and its Tools
    # menu entry away.
    "addon_dev_enabled": True,
    # The embedded browser only trusts certificates its Chromium network stack
    # accepts, which a self-signed HTTPS certificate is not until it lives in
    # the Windows certificate store. When on, certificate errors are ignored in
    # every web tab (takes effect at startup). See manager_browser.
    "ignore_cert_errors": False,
    # Reboot handling: downtime to wait before the first reconnect attempt,
    # the seconds between attempts after that, and whether a reboot command
    # typed in the terminal offers to start that watch.
    "reboot_wait_seconds": 30,
    "reboot_retry_interval": 10,
    "reboot_auto_watch": True,
    "default_tab": "Berry",
    # Tab arrangement: whether the order you drag the tabs into is kept between
    # launches, and that order (list of tab titles). See manager_gui.
    "remember_tab_order": True,
    "tab_order": [],
    "terminal_font_size": 10,
    # How big the whole interface is drawn, in percent of the shipped sizes.
    # View > Zoom in/out owns it; terminal_font_size stays the terminal's own.
    "ui_zoom": UI_ZOOM_DEFAULT,
    "max_log_lines": 500,
    # Whether the log pane under the tabs is showing. View > Show log console
    # (Ctrl+Shift+L) switches it; the log itself keeps running either way.
    "log_visible": True,
    "terminal_history_limit": 500,
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
    "luci_url": "http://192.168.1.1/",
    # Embeddable browser tabs. Each entry has a display name, an optional ip
    # (blank = follow the active profile host), a port and a path. Seeded from
    # the legacy web_port/web_path/luci_url keys during migration.
    "web_tabs": [
        {"name": "Web Interface", "ip": "", "port": 8989, "path": "/cgi-bin/index.py", "scheme": "http"},
        {"name": "LuCI", "ip": "192.168.1.1", "port": 80, "path": "/", "scheme": "http"},
    ],
    # Shown as buttons above the SSH terminal; "Label||command" in the editor.
    "quick_commands": [
        {"label": "Reload uhttpd", "command": "killall -HUP uhttpd"},
        {"label": "Restart cgi wrapper", "command": "/etc/init.d/cgiwrapper restart"},
        {"label": "Show openwrt version", "command": "cat /etc/openwrt_release"},
        {"label": "Reboot", "command": "reboot"},
    ],
    # One record per addon id: whether the profile runs it, and whatever the
    # addon asked api.settings to keep. See manager_addons and addon_api.
    "addons": {},
}


# ---------------------------------------------------------------------------
# Application stylesheet - dark theme with the crimson accent colour
# ---------------------------------------------------------------------------

APP_STYLESHEET = """
/* Base colours and font for everything */
QWidget {
    background-color: #121212;
    color: #e0e0e0;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
    /* Selected text everywhere - including the selectable labels - uses the
       accent, not the near-black the palette falls back to on a dark theme. */
    selection-background-color: #c51a4a;
    selection-color: #ffffff;
}
/* Labels carry no box of their own: without this the base #121212 above is
   painted behind every QLabel, showing as black blocks around form text. */
QLabel {
    background-color: transparent;
}
QMainWindow, QTabWidget::pane, QGroupBox, QPlainTextEdit, QTextEdit,
QTreeWidget, QLineEdit, QSpinBox, QComboBox, QListWidget {
    background-color: #212121;
}
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #c51a4a;
}
/* Text inputs and list-like widgets */
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QTreeWidget,
QComboBox, QListWidget {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    selection-background-color: #c51a4a;
    padding: 5px;
}
QTreeView, QListView {
    background-color: #212121;
    alternate-background-color: #1a1a1a;
    color: #e0e0e0;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus, QComboBox:focus, QListWidget:focus {
    border: 1px solid #c51a4a;
}
/* Buttons - #primaryAction and #dangerAction are set via setObjectName() */
QPushButton, QToolButton, QComboBox::drop-down {
    background-color: #333333;
    border: 1px solid #4a4a4a;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton:hover, QToolButton:hover {
    background-color: #414141;
    border-color: #c51a4a;
}
QPushButton:pressed, QToolButton:checked {
    background-color: #a0153c;
    border-color: #c51a4a;
}
QPushButton:disabled, QToolButton:disabled {
    color: #777777;
    background-color: #292929;
    border-color: #383838;
}
QPushButton#primaryAction {
    background-color: #c51a4a;
    border-color: #c51a4a;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryAction:hover {
    background-color: #a0153c;
    border-color: #a0153c;
}
QPushButton#dangerAction {
    background-color: #6e1a1a;
    border-color: #8a2a2a;
    color: #ffe4e4;
}
QPushButton#dangerAction:hover {
    background-color: #8a2a2a;
}
/* Tab bar, the active tab gets the accent underline */
QTabBar::tab {
    background-color: #2a2a2a;
    color: #aaaaaa;
    border: 0;
    border-bottom: 3px solid transparent;
    padding: 10px 18px;
    min-width: 80px;
}
QTabBar::tab:selected {
    color: #e0e0e0;
    background-color: #333333;
    border-bottom-color: #c51a4a;
}
QTabBar::tab:hover:!selected {
    color: #ffffff;
    background-color: #3a3a3a;
}
/* Tree rows: alternating shades, and selected rows keep the accent colour */
QTreeWidget::item, QListWidget::item {
    padding: 4px;
    color: #e0e0e0;
}
QTreeWidget::item:alternate, QListWidget::item:alternate {
    background-color: #1a1a1a;
}
QTreeWidget::item:selected:!alternate, QListWidget::item:selected:!alternate {
    background-color: #6b1730;
    color: #ffffff;
}
QTreeWidget::item:selected:alternate, QListWidget::item:selected:alternate {
    background-color: #6b1730;
    color: #ffffff;
}
QTreeWidget::item:hover:!selected, QListWidget::item:hover:!selected {
    background-color: #2c2c2c;
}
/* Table rows, the same shades as a tree. QTableView is spelled here and it
   covers a QTableWidget too, because a sheet matches base classes - and Qt's
   own AlternateBase is white, which leaves dark text on a white row. */
QTableView {
    background-color: #212121;
    alternate-background-color: #1a1a1a;
    gridline-color: #3c3c3c;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    outline: 0;
}
QTableView::item {
    padding: 4px;
    color: #e0e0e0;
}
QTableView::item:alternate {
    background-color: #1a1a1a;
}
QTableView::item:selected {
    background-color: #6b1730;
    color: #ffffff;
}
QTableView::item:hover:!selected {
    background-color: #2c2c2c;
}
/* Column headers */
QHeaderView::section {
    background-color: #2a2a2a;
    color: #aaaaaa;
    border: 0;
    border-bottom: 1px solid #3c3c3c;
    padding: 6px;
}
/* Check boxes */
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}
QCheckBox::indicator:unchecked {
    background-color: #1a1a1a;
    border: 1px solid #666666;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #c51a4a;
    border: 1px solid #c51a4a;
    border-radius: 3px;
}
/* Progress bar, status bar and tooltips */
QProgressBar {
    background-color: #2e2e2e;
    border: 1px solid #3c3c3c;
    border-radius: 3px;
    text-align: center;
    color: #e0e0e0;
    min-height: 18px;
}
QProgressBar::chunk {
    background-color: #c51a4a;
}
QStatusBar {
    background-color: #1a1a1a;
    color: #aaaaaa;
}
QStatusBar::item { border: 0; }
QToolTip {
    background-color: #2e2e2e;
    color: #e0e0e0;
    border: 1px solid #c51a4a;
    padding: 5px;
}
/* Menus and toolbars */
QMenu {
    background-color: #1e1e1e;
    border: 1px solid #3c3c3c;
}
QMenu::item:selected {
    background-color: #6b1730;
}
QMenuBar {
    background-color: #1a1a1a;
    color: #cfcfcf;
}
QMenuBar::item:selected {
    background-color: #6b1730;
}
QToolBar {
    background-color: #1a1a1a;
    border: 0;
    spacing: 4px;
    padding: 4px;
}
/* Scrollbars, drawn thin with no arrow buttons */
QScrollBar:vertical {
    width: 12px;
    background-color: #1a1a1a;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    background-color: #555555;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background-color: #c51a4a;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: none;
}
/* Connection status dot, recoloured by set_status_dot() */
QLabel#statusDot {
    color: #ffb020;
    font-size: 14pt;
}
QLabel#statusDotOnline { color: #23d18b; }
QLabel#statusDotOffline { color: #c51a4a; }
"""


# ---------------------------------------------------------------------------
# Interface zoom - the sizes above, multiplied
# ---------------------------------------------------------------------------

#: One number followed by a unit is the only thing a zoom may touch: colours,
#: weights, zero lengths and every other declaration value has no px/pt in it.
_QSS_SIZE = re.compile(r"(\d+(?:\.\d+)?)(px|pt)")

#: The zoom in force. ``manager_gui`` sets it from the profile's ``ui_zoom``;
#: widgets built afterwards read it so they are drawn at the same size as the
#: ones already on screen.
_zoom_state = {"percent": UI_ZOOM_DEFAULT}


def current_zoom() -> int:
    """The percentage the interface is being drawn at right now."""
    return _zoom_state["percent"]


def set_zoom(zoom: int) -> int:
    """Remember the zoom, so anything built later is drawn at it too.

    Returns the value actually kept: a percentage from a hand-edited config file
    lands on the nearest rung of the ladder, so zooming from there has somewhere
    to go.
    """
    percent = step_zoom(zoom, 0)
    _zoom_state["percent"] = percent
    return percent


def step_zoom(zoom: int, direction: int) -> int:
    """The next rung of ``UI_ZOOM_STEPS`` in the direction asked for.

    ``direction`` 0 only clamps: a percentage from a hand-edited config lands on
    the nearest rung, so ``Zoom out`` from there has somewhere to go.
    """
    rungs = UI_ZOOM_STEPS
    try:
        value = int(zoom)
    except (TypeError, ValueError):
        return UI_ZOOM_DEFAULT
    if direction > 0:
        higher = [rung for rung in rungs if rung > value]
        return higher[0] if higher else rungs[-1]
    if direction < 0:
        lower = [rung for rung in rungs if rung < value]
        return lower[-1] if lower else rungs[0]
    return min(rungs, key=lambda rung: abs(rung - value))


def _zoomed(number: float, factor: float) -> int:
    """A size multiplied by ``factor``, rounded half up and never below 1.

    Plain ``round()`` would send 12.5 to 12 and 22.5 to 22, losing half a pixel
    on every odd rung, so the halfway case goes up the way it does on paper. A
    size that would round away to nothing stays at 1, so a border never vanishes
    at the small end.
    """
    return max(1, int(number * factor + 0.5))


def zoom_size(number: int, zoom: int | None = None) -> int:
    """One hand-written px or pt size, drawn at the zoom in force.

    For the sizes that live in code rather than in the stylesheet: the toggle
    switch's track, the header logo, the height the log pane stops at.
    """
    percent = current_zoom() if zoom is None else int(zoom)
    return _zoomed(int(number), percent / 100.0)


def scaled_sheet(sheet: str, zoom: int | None = None) -> str:
    """``sheet`` with every px and pt size multiplied by the zoom.

    Used for the application stylesheet and for the small sheets individual
    widgets carry, so a widget made after a zoom change matches the ones
    already drawn.
    """
    percent = current_zoom() if zoom is None else int(zoom)
    factor = percent / 100.0

    def scale(match):
        return f"{_zoomed(float(match.group(1)), factor)}{match.group(2)}"

    return _QSS_SIZE.sub(scale, sheet)

