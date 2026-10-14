#!/usr/bin/env python3
"""Paths, defaults and the dark stylesheet shared by every Berry Manager module."""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath


# --- where things live (checkout vs frozen build) ---

if getattr(sys, "frozen", False):
    # BASE_DIR is the exe folder (writable); RESOURCE_DIR is the
    # read-only folder PyInstaller unpacks beside it.
    BASE_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(sys._MEIPASS)
    REPOSITORY_ROOT = BASE_DIR
    IS_COMPILED = True
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR
    # Walk up out of the util folder to the repo root.
    REPOSITORY_ROOT = BASE_DIR.parents[2] if len(BASE_DIR.parents) >= 2 else BASE_DIR
    IS_COMPILED = False

DEFAULT_SOURCE = REPOSITORY_ROOT / "src" / "www_rasconf"

# Files that ship inside the program. One path serves both a
# frozen build and a source checkout.
ASSETS_DIR = RESOURCE_DIR / "assets"

#: The two logos: View > Alternate icon picks the second one, and the toolbar,
#: the window icon and the About dialog all follow that choice.
ICON_FILES = ("icon.png", "icon2.png")


def find_asset(filename: str) -> Path | None:
    """Locate a file that ships with the program, or ``None``; older spots still searched."""
    for candidate in (ASSETS_DIR / filename, RESOURCE_DIR / filename,
                      BASE_DIR / "assets" / filename, BASE_DIR / filename):
        if candidate.is_file():
            return candidate
    return None


#: The program's own version, the one the About dialog shows and the update
#: addon compares against the tags published on GitHub (``berry`` + this).
#: One line to change per release, everything else reads it from here.
APP_VERSION = "1.5.9"
UPDATE_TAG_PREFIX = "berry"

# --- file names and fixed identifiers ---

# Separate from ~/.ssh/known_hosts so trusting a host never
# touches the system keys. Old brand names stay on purpose:
# renaming would drop every trusted key and stored password.
KNOWN_HOSTS_FILE = Path.home() / ".ssh" / "known_hosts_rasconf"
KEYRING_SERVICE = "rasconf-sftp-manager"

# The manager's own folder, made on first boot. Holds what it
# writes, not ships: config, history, log, addon build record.
# See ``manager_config._prepare_config_dir``.
CONFIG_DIR = BASE_DIR / "config"
APP_CONFIG_FILE = CONFIG_DIR / "config.json"
APP_CONFIG_EXAMPLE_FILE = RESOURCE_DIR / "config.example.json"
HISTORY_FILE = CONFIG_DIR / "command_history.json"
LOG_FILE = CONFIG_DIR / "berry_manager.log"
MAX_COMMAND_HISTORY = 200

# Drop-in addons: an addon.py (or a .py) and its executables.
# Made the first time the addon list is read, beside the exe.
ADDONS_DIR = BASE_DIR / "addons"

# Addons that ship inside the program: the build copies them
# into the archive so a packaged program carries its own. A
# checkout keeps the two folders as one.
BUILTIN_ADDONS_DIR = RESOURCE_DIR / "addons"
HAS_BUILTIN_ADDONS = IS_COMPILED and BUILTIN_ADDONS_DIR != ADDONS_DIR

# The two modules an addon imports to reach the app. Compiling and testing an
# addon run in a Python of their own, outside this program, and can only read
# what lies on disk: a checkout has them in its own folder, and a build carries
# a copy of them beside the addons it packs.
HARNESS_FILES = ("addon_common.py", "addon_client.py")
HARNESS_DIR = RESOURCE_DIR / "harness" if IS_COMPILED else BASE_DIR

# Addons the source ships with, named so they stay known even
# when run from outside the folder. In a ``.py`` run this is
# only a fallback: everything in ``addons`` is bundled anyway
# (it is what the build stages; see ``Addon.is_bundled``).
# An addon the store installs marks its own manifest instead,
# so nothing here may claim it: these ids name the files in
# ``addons`` and nothing published in ``addon.store``.
SHIPPED_ADDON_IDS = {"berry", "web_installer", "addon_store", "manager_update"}

# Per-addon folder addons/_data/<id>/ holds its config.json
# and fields.json. The underscore makes the scanner skip it,
# so data is never mistaken for an addon. These settings
# belong to the install, not a profile; editable by hand.
ADDON_DATA_DIR = ADDONS_DIR / "_data"

# The Addon Dev tab edits an addon as a ``.py``, runs a test
# script and builds a program. Its working folders sit inside
# the addons folder, skipped (leading underscore): the build
# staging area and the addon's test scripts.
ADDON_BUILD_DIR = ADDONS_DIR / "_build"
ADDON_TEST_DIR = ADDONS_DIR / "_tests"

#: Where the compiled copy of an addon is put, beside its source, so the same
#: id and the same sidecar manifest keep working. ``<id>.exe`` on Windows.
ADDON_EXECUTABLE_SUFFIX = ".exe" if sys.platform == "win32" else ""

PROJECT_GITHUB_URL = "https://github.com/fligma/raspiOpenWrt"
PROJECT_LICENSE_URL = PROJECT_GITHUB_URL + "/blob/main/LICENSE"

# --- defaults (when a key or profile is missing) ---

# Interface zoom: the density the window is drawn at. View >
# Zoom in/out walks the ladder; every px/pt size is multiplied
# by it (terminal font and hand-sized widgets too).
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

# All one profile holds: connection defaults, what Settings
# can change. A record is this dict with the user's own
# values; see manager_config for the store.
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
    # Master connection switch (Connection panel header). Off
    # closes every session and stops any addon connecting; on
    # lets the Connect button and the addons work again.
    "master_connection": True,
    # One main connection carries SSH and SFTP together; this
    # switch opens it at startup. See manager_gui.
    "auto_connect": False,
    "use_alt_icon": False,
    # Per-transport gates, each with its own switch in Settings >
    # Connection. Off, the transport's controls go away and
    # api.device refuses that transport to every addon (see the
    # guards in addon_api); the header's master switch still stops
    # both at once. ssh_enabled additionally shows or hides the
    # SSH terminal tab. An addon may only work over a connection
    # while the main connection is open and its own switch is on.
    "sftp_enabled": True,
    "ssh_enabled": True,
    # The Addon Dev tab: the addon index, logs, processes, tools.
    # Shown in a checkout and a build alike; off hides the tab
    # and its Tools menu entry.
    "addon_dev_enabled": True,
    # The browser trusts only certs Chromium accepts, which a
    # self-signed one is not until it's in the Windows store. When
    # on, cert errors are ignored in every web tab (at startup).
    "ignore_cert_errors": False,
    # Reboot handling: downtime before the first reconnect, the
    # seconds between later tries, and whether a reboot command
    # typed in the terminal offers to start that watch.
    "reboot_wait_seconds": 30,
    "reboot_retry_interval": 10,
    "reboot_auto_watch": True,
    "default_tab": "Berry",
    # Tab arrangement: whether the dragged order is kept between
    # launches, and that order. See manager_gui.
    "remember_tab_order": True,
    "tab_order": [],
    "terminal_font_size": 10,
    # Interface size, as a percent of the shipped sizes. View >
    # Zoom in/out owns it; terminal_font_size is the terminal's.
    "ui_zoom": UI_ZOOM_DEFAULT,
    "max_log_lines": 500,
    # Whether the log pane under the tabs shows. View > Show log
    # console (Ctrl+Shift+L) toggles it; the log runs either way.
    "log_visible": True,
    "terminal_history_limit": 500,
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
    "luci_url": "http://192.168.1.1/",
    # Browser tabs: name, optional ip (blank = active host),
    # a port and a path. Seeded from the legacy keys at migration.
    "web_tabs": [
        {"name": "Web Interface", "ip": "", "port": 8989, "path": "/cgi-bin/index.py", "scheme": "http"},
        {"name": "LuCI", "ip": "192.168.1.1", "port": 80, "path": "/", "scheme": "http"},
    ],
    # Buttons over the SSH terminal; 'Label||command' in editor.
    "quick_commands": [
        {"label": "Reload uhttpd", "command": "killall -HUP uhttpd"},
        {"label": "Restart cgi wrapper", "command": "/etc/init.d/cgiwrapper restart"},
        {"label": "Show openwrt version", "command": "cat /etc/openwrt_release"},
        {"label": "Reboot", "command": "reboot"},
    ],
    # One record per addon id: whether the profile runs it, and
    # what the addon asked api.settings to keep. See addon_api.
    "addons": {},
}


# --- application stylesheet (dark theme, crimson accent) ---

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
/* Tab bar: every tab is a bordered capsule with a rounded top, so tabs
   separation reads clearly even when a lot of them are open; the active one
   gets the accent border and underline. min-width stays small on purpose -
   a crowded bar drops captions to icons (see RightPinnedTabBar.refit) and an
   icon tab must be allowed to come out compact. */
QTabBar::tab {
    background-color: #262626;
    color: #aaaaaa;
    border: 1px solid #3c3c3c;
    border-bottom: 3px solid #3c3c3c;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 3px;
    padding: 8px 14px;
    min-width: 24px;
}
QTabBar::tab:selected {
    color: #e0e0e0;
    background-color: #333333;
    border: 1px solid #c51a4a;
    border-bottom: 3px solid #c51a4a;
}
QTabBar::tab:hover:!selected {
    color: #ffffff;
    background-color: #3a3a3a;
    border-color: #555555;
}
/* The elastic gap tab that pins Settings right draws nothing at all: it is
   the only disabled tab, so it sheds the capsule, the border and the margin. */
QTabBar::tab:disabled {
    background: transparent;
    border: 0;
    margin: 0;
    padding: 0;
    min-width: 0;
}
/* The Settings page has a tab bar of its own, inside a tab of the window's, so
   its capsules are drawn smaller: the same shapes and colours, less padding,
   and the two bars still read as one above the other. */
QTabWidget#settingsTabs > QTabBar::tab {
    padding: 5px 10px;
    margin-right: 2px;
    min-width: 16px;
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
/* Connection status dot, recoloured by set_status_dot(). Three states carry the
   whole connection story: green while a transport is usable now, amber while
   every switch is on but nothing stands connected, red while a switch refuses. */
QLabel#statusDot {
    color: #ffb020;
    font-size: 14pt;
}
QLabel#statusDotOnline { color: #23d18b; }
QLabel#statusDotReady { color: #ffb020; }
QLabel#statusDotOffline { color: #c51a4a; }
"""


# --- interface zoom: the sizes above, multiplied ---

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
    """Remember the zoom so later widgets match; returns the nearest ladder rung kept."""
    percent = step_zoom(zoom, 0)
    _zoom_state["percent"] = percent
    return percent


def step_zoom(zoom: int, direction: int) -> int:
    """The next rung of ``UI_ZOOM_STEPS`` in ``direction``; 0 clamps to the nearest."""
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
    """A size times ``factor``, rounded half up, never below 1 (so a border can't vanish)."""
    return max(1, int(number * factor + 0.5))


def zoom_size(number: int, zoom: int | None = None) -> int:
    """One hand-written px/pt size, drawn at the zoom in force (for sizes set in code)."""
    percent = current_zoom() if zoom is None else int(zoom)
    return _zoomed(int(number), percent / 100.0)


def scaled_sheet(sheet: str, zoom: int | None = None) -> str:
    """``sheet`` with every px/pt size multiplied by the zoom, so late widgets match."""
    percent = current_zoom() if zoom is None else int(zoom)
    factor = percent / 100.0

    def scale(match: re.Match) -> str:
        return f"{_zoomed(float(match.group(1)), factor)}{match.group(2)}"

    return _QSS_SIZE.sub(scale, sheet)

