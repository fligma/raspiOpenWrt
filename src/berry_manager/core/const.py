#!/usr/bin/env python3
"""Paths, defaults and the dark stylesheet shared by every Berry Manager module."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from string import Template


# --- where things live (checkout vs frozen build) ---

if getattr(sys, "frozen", False):
    # BASE_DIR is the exe folder (writable); RESOURCE_DIR is the
    # read-only folder PyInstaller unpacks beside it.
    BASE_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(sys._MEIPASS)
    REPOSITORY_ROOT = BASE_DIR
    IS_COMPILED = True
else:
    # This file sits in core/, one down from the program's own folder.
    BASE_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = BASE_DIR
    # Walk up out of ``src/berry_manager`` to the repo root.
    REPOSITORY_ROOT = BASE_DIR.parents[1] if len(BASE_DIR.parents) >= 1 else BASE_DIR
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

#: The program's own version, the one the update addon compares against the
#: tags published on GitHub (``berry`` + this) and the About dialog shows. One
#: line to change per release, everything else reads it from here.
#:
#: The version is byte-packed hex: three numbers (major, minor, patch), each
#: written as its own ``0x`` hex byte and joined with dots - so the release that
#: was ``3.1.0`` in decimal is ``0x3.0x1.0x0`` here. Bumping a release means
#: editing these three bytes (a patch of sixteen reads ``0x10``). Every place
#: that compares or publishes takes this form as it stands: the parser, the
#: floors, the GitHub tag and the setup. Only the surfaces a person reads off
#: the running window - the title bar, the splash and the tray tooltip - show
#: the decimal below.
APP_VERSION = "0x3.0x6.0xa"
UPDATE_TAG_PREFIX = "berry"


def readable_version(value: str = APP_VERSION) -> str:
    """The byte-packed hex version shown back in decimal, for the eye.

    The version is stored as hex (``0x3.0x1.0x0``) because that is what the
    update addon compares and what the GitHub tag is built from; the three
    surfaces a person reads while the program runs take this instead: each
    dotted segment is parsed as a hex byte and re-emitted in decimal, joined
    with dots, so ``0x3.0x1.0x0`` reads ``3.1.0`` and a future ``0x10`` reads
    ``16``. A segment that is not a hex byte is skipped, so an unreadable
    version comes back empty rather than raising.
    """
    parts: list[str] = []
    for raw in str(value or "").split("."):
        part = raw.strip()
        if part[:2].lower() == "0x":
            part = part[2:]
        if part and all(c in "0123456789abcdefABCDEF" for c in part):
            parts.append(str(int(part, 16)))
    return ".".join(parts)


#: The version as the title bar, the splash and the tray tooltip show it.
APP_VERSION_DISPLAY = readable_version()

# --- file names and fixed identifiers ---

# Separate from ~/.ssh/known_hosts so trusting a host never
# touches the system keys. Old brand names stay on purpose:
# renaming would drop every trusted key and stored password.
KNOWN_HOSTS_FILE = Path.home() / ".ssh" / "known_hosts_rasconf"
KEYRING_SERVICE = "rasconf-sftp-manager"

# The manager's own folder, made on first boot. Holds what it
# writes, not ships: config, history, log, addon build record.
# See ``core.ui._prepare_config_dir``.
CONFIG_DIR = BASE_DIR / "config"
APP_CONFIG_FILE = CONFIG_DIR / "config.json"
APP_CONFIG_EXAMPLE_FILE = RESOURCE_DIR / "config.example.json"
HISTORY_FILE = CONFIG_DIR / "command_history.json"
LOG_FILE = CONFIG_DIR / "berry_manager.log"
MAX_COMMAND_HISTORY = 200

# Drop-in addons: an addon.py (or a .py) and its executables.
# Made the first time the addon list is read, beside the exe.
ADDONS_DIR = BASE_DIR / "addons"

# Themes: one ``<name>.theme`` file each, written by the app on first
# boot and edited by the Theme Manager addon. See ``core.ui``.
# The environment may point elsewhere - a test's own folder, or an install
# that keeps its themes with its user - and it is the same variable a
# process addon is handed, so both sides of the pipe read one folder.
THEMES_DIR = Path(str(os.environ.get("RASCONF_THEMES", "")).strip()
                  or BASE_DIR / "themes")
THEME_SUFFIX = ".theme"
DEFAULT_THEME_NAME = "default"

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
SHIPPED_ADDON_IDS = {"berry", "addon_store",
                     "manager_update", "theme_manager"}

#: Addons the source used to ship and no longer does, with the addon that took
#: their place. The id is not simply forgotten: a profile's record for the old
#: name is folded onto the new one, so the switches somebody already set and the
#: values an addon saved keep working when an installer becomes a store addon.
ADDON_ID_TRANSLATIONS = {"web_installer": "berry_web"}

#: Retired names whose leftovers in ``addons`` and ``addons/_data`` are swept on
#: startup, by ``core.addon.prune_retired_addons``. Anything still listed
#: under ``ADDON_ID_TRANSLATIONS`` has its settings folder carried over rather
#: than thrown away; a name only listed here is deleted outright.
RETIRED_SHIPPED_ADDON_IDS = ("web_installer",)

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

#: Where one release of this program is published: the tag is the version with
#: ``UPDATE_TAG_PREFIX`` in front of it, so ``PROJECT_RELEASE_URL + "berry0x1.0x6.0x4"``
#: is the page carrying that build's notes. The About box links it.
PROJECT_RELEASE_URL = PROJECT_GITHUB_URL + "/releases/tag/"

# --- defaults (when a key or profile is missing) ---

# Interface zoom: the density the window is drawn at. View >
# Zoom in/out walks the ladder; every px/pt size is multiplied
# by it (terminal font and hand-sized widgets too).
# Even rungs of five, from half size to a fifth over: one press
# is one small, predictable move, so landing on a size takes no
# thought - the old ladder jumped 10s then 15s and 20s and ran
# to 200%, which read as lag and overshot what was asked for.
UI_ZOOM_STEPS = tuple(range(50, 121, 5))
UI_ZOOM_DEFAULT = 100
UI_ZOOM_MIN = UI_ZOOM_STEPS[0]
UI_ZOOM_MAX = UI_ZOOM_STEPS[-1]

DEFAULT_PROFILE = {
    "name": "Default",
    "host": "192.168.1.1",
    "port": 22,
    "username": "root",
    "key_file": "",
    "remote_root": "/www_berry",
    "source": str(DEFAULT_SOURCE),
    "trust_unknown_host": False,
    "remember_password": False,
}

# All one profile holds: connection defaults, what Settings
# can change. A record is this dict with the user's own
# values; see core.ui for the store.
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
    # The one git token the whole profile has. It buys two things: a private
    # repository can be read, and GitHub's rate limit is the authenticated one
    # rather than the shared anonymous bucket. Addons never hold a token of
    # their own - each one's "use the global git token" switch in the store's
    # Addon manager decides whether api.session.git_token() answers it, and off
    # the addon works anonymously as it always could. See core.window for the
    # field and core.ui for the one-time lift of an old per-addon token.
    "git_token": "",
    "logging_enabled": True,
    "show_hidden_files": False,
    # Master connection switch (Connection panel header). Off
    # closes every session and stops any addon connecting; on
    # lets the Connect button and the addons work again.
    "master_connection": True,
    # One main connection carries SSH and SFTP together; this
    # switch opens it at startup. See core.window.
    "auto_connect": False,
    "use_alt_icon": False,
    # The ``.theme`` file the interface is drawn from, by name (without the
    # suffix). ``core.ui`` reads it; View > Theme and Settings >
    # Appearance both write it, and a missing name falls back to "default".
    "theme": DEFAULT_THEME_NAME,
    # Notices worth seeing without looking at the window: an addon's
    # api.ui.popup, and this program's own news such as "an update is
    # available", come up as a tray toast over the status line. Off,
    # nothing leaves the window. See core.window.notify_popup.
    "system_notifications": True,
    # The boot card that names what the window is loading while it is being
    # built: logo, the step in hand, and a bar with a percentage, painted from
    # the theme in force. Off, the program opens with nothing on screen until
    # the window itself appears. See core.ui.
    "splash_enabled": True,
    # The notes the window writes beside the pointer over its own controls,
    # drawn by core.ui rather than by Qt. Off stops every tool-tip: the
    # cursor-following bubble, a group box's title note and the plain native
    # tip alike. See core.ui._SmartTip.
    "tooltips_enabled": True,
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
    # launches, and that order. The tabs pinned to the right edge
    # are remembered separately, in their own run. See core.window.
    "remember_tab_order": True,
    "tab_order": [],
    "pinned_tab_order": [],
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


# --- the application theme: the colours, the tab chrome, and the sheet ---

#: Every colour the interface is drawn from, in the order the Theme Manager
#: lists them. A ``.theme`` file may name any of them; one that leaves a token
#: out keeps the value written here, so a theme is a handful of lines rather
#: than a copy of the whole set.
THEME_COLORS = {
    # what things are filled with
    "base": "#121212",
    "surface": "#212121",
    "sunken": "#1a1a1a",
    "soft": "#2e2e2e",
    "menu": "#1e1e1e",
    "header": "#2a2a2a",
    "raise": "#333333",
    "hover": "#414141",
    "row_hover": "#2c2c2c",
    "tab": "#262626",
    "tab_hover": "#3a3a3a",
    "disabled_fill": "#292929",
    "toggle_off": "#2b2b2b",
    # the lines drawn round them
    "border": "#3c3c3c",
    "raise_edge": "#4a4a4a",
    "handle": "#555555",
    "faint": "#666666",
    "disabled_edge": "#383838",
    # what is written on them
    "text": "#e0e0e0",
    "bright": "#ffffff",
    "muted": "#aaaaaa",
    "menu_text": "#cfcfcf",
    "note": "#8f8f8f",
    "disabled_text": "#777777",
    # the berry in it
    "accent": "#c51a4a",
    "accent_deep": "#a0153c",
    "accent_edge": "#8f1236",
    "selection": "#6b1730",
    # states a colour means something for
    "ok": "#23d18b",
    "warn": "#ffb020",
    "danger_fill": "#6e1a1a",
    "danger_edge": "#8a2a2a",
    "danger_text": "#ffe4e4",
    "close_ink": "#9a9a9a",
    "close_hot": "#ff6b6b",
    # the pictures this program paints itself
    "icon_alt": "#9db4c8",
    "icon_tint": "#d6d6d6",
    "toggle_handle": "#f5f5f5",
    "toggle_handle_off": "#888888",
}

#: The same tokens in the blocks a person editing a theme reads them in.
THEME_COLOR_GROUPS: dict[str, tuple[str, ...]] = {
    "Fills": ("base", "surface", "sunken", "soft", "menu", "header",
              "raise", "hover", "row_hover", "tab", "tab_hover",
              "disabled_fill", "toggle_off"),
    "Edges": ("border", "raise_edge", "handle", "faint", "disabled_edge"),
    "Text": ("text", "bright", "muted", "menu_text", "note", "disabled_text"),
    "Accent": ("accent", "accent_deep", "accent_edge", "selection"),
    "States": ("ok", "warn", "danger_fill", "danger_edge", "danger_text",
               "close_ink", "close_hot"),
    "Painted marks": ("icon_alt", "icon_tint", "toggle_handle",
                      "toggle_handle_off"),
}

#: What a tab spends on its shape, in pixels at 100% zoom - the interface
#: zoom multiplies these like every other size in the sheet. The two words
#: that are not sizes say whether a tab may wear a caption at all, and
#: whether the transport dots are drawn beside it.
THEME_TABS = {
    "icon_px": 24,          # the smallest a tab's picture is drawn at
    "radius_px": 6,         # the capsule's rounded top, and a group's box
    "pad_y_px": 8,          # tab padding, up and down
    "pad_x_px": 14,         # tab padding, left and right
    "gap_px": 3,            # air between two tabs
    "underline_px": 3,      # the bar under a tab, and under the chosen one
    "min_width_px": 24,     # how narrow a squeezed tab may get
    "nest_pad_y_px": 5,     # the same shape on a tab bar inside a tab
    "nest_pad_x_px": 10,
    "nest_gap_px": 2,
    "nest_min_width_px": 16,
    "captions": True,       # False keeps every tab icon-only
    "dots": True,           # False hides the SSH/SFTP dots
}

#: A colour is a hex, or one plain name: nothing that could carry a ``;`` or a
#: brace into the stylesheet is accepted, so a hand-written theme cannot close
#: the rule it sits in and start another.
_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")
_NAMED_COLOR = re.compile(r"[A-Za-z][A-Za-z0-9 ]*\Z")


def is_theme_color(value: object) -> bool:
    """Whether this could be painted: a #hex of 3, 4, 6 or 8 digits, or a plain colour name."""
    text = str(value or "").strip()
    return bool(_HEX_COLOR.match(text) or _NAMED_COLOR.match(text))


def theme_colors(value: object) -> dict:
    """One theme's colours: every token, with what was given over the shipped value."""
    colors = dict(THEME_COLORS)
    if isinstance(value, dict):
        for token, given in value.items():
            if token in colors and is_theme_color(given):
                colors[str(token)] = str(given).strip()
    return colors


def theme_tabs(value: object) -> dict:
    """One theme's tab chrome: every knob, clamped to something drawable."""
    tabs = dict(THEME_TABS)
    if not isinstance(value, dict):
        return tabs
    for token, given in value.items():
        if token not in tabs:
            continue
        if isinstance(tabs[token], bool):
            tabs[token] = bool(given)
            continue
        try:
            number = int(round(float(given)))
        except (TypeError, ValueError):
            continue
        tabs[token] = max(0, min(200, number))
    return tabs


# --- the stylesheet, as the shape a theme is poured into ---

_SHEET_BODY = Template("""
/* Base colours and font for everything */
QWidget {
    background-color: $base;
    color: $text;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
    /* Selected text everywhere - including the selectable labels - uses the
       accent, not the near-black the palette falls back to on a dark theme. */
    selection-background-color: $accent;
    selection-color: $bright;
}
/* Labels carry no box of their own: without this the base fill above is
   painted behind every QLabel, showing as dark blocks around form text. */
QLabel {
    background-color: transparent;
}
QMainWindow, QTabWidget::pane, QGroupBox, QPlainTextEdit, QTextEdit,
QTreeWidget, QLineEdit, QSpinBox, QComboBox, QListWidget {
    background-color: $surface;
}
QGroupBox {
    border: 1px solid $border;
    border-radius: ${radius_px}px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: $accent;
}
/* Text inputs and list-like widgets */
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QTreeWidget,
QComboBox, QListWidget {
    border: 1px solid $border;
    border-radius: 4px;
    selection-background-color: $accent;
    padding: 5px;
}
QTreeView, QListView {
    background-color: $surface;
    alternate-background-color: $sunken;
    color: $text;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus, QComboBox:focus, QListWidget:focus {
    border: 1px solid $accent;
}
/* Buttons - #primaryAction and #dangerAction are set via setObjectName() */
QPushButton, QToolButton, QComboBox::drop-down {
    background-color: $raise;
    border: 1px solid $raise_edge;
    border-radius: 4px;
    padding: 6px 12px;
}
QPushButton:hover, QToolButton:hover {
    background-color: $hover;
    border-color: $accent;
}
QPushButton:pressed, QToolButton:checked {
    background-color: $accent_deep;
    border-color: $accent;
}
QPushButton:disabled, QToolButton:disabled {
    color: $disabled_text;
    background-color: $disabled_fill;
    border-color: $disabled_edge;
}
QPushButton#primaryAction {
    background-color: $accent;
    border-color: $accent;
    color: $bright;
    font-weight: 600;
}
QPushButton#primaryAction:hover {
    background-color: $accent_deep;
    border-color: $accent_deep;
}
QPushButton#dangerAction {
    background-color: $danger_fill;
    border-color: $danger_edge;
    color: $danger_text;
}
QPushButton#dangerAction:hover {
    background-color: $danger_edge;
}
/* Tab bar: every tab is a bordered capsule with a rounded top, so tabs
   separation reads clearly even when a lot of them are open; the active one
   gets the accent border and underline. min-width stays small on purpose -
   a crowded bar drops captions to icons (see RightPinnedTabBar.refit) and an
   icon tab must be allowed to come out compact. */
QTabBar::tab {
    background-color: $tab;
    color: $muted;
    border: 1px solid $border;
    border-bottom: ${underline_px}px solid $border;
    border-top-left-radius: ${radius_px}px;
    border-top-right-radius: ${radius_px}px;
    margin-right: ${gap_px}px;
    padding: ${pad_y_px}px ${pad_x_px}px;
    min-width: ${min_width_px}px;
}
QTabBar::tab:selected {
    color: $text;
    background-color: $raise;
    border: 1px solid $accent;
    border-bottom: ${underline_px}px solid $accent;
}
QTabBar::tab:hover:!selected {
    color: $bright;
    background-color: $tab_hover;
    border-color: $handle;
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
    padding: ${nest_pad_y_px}px ${nest_pad_x_px}px;
    margin-right: ${nest_gap_px}px;
    min-width: ${nest_min_width_px}px;
}
/* Tree rows: alternating shades, and selected rows keep the accent colour */
QTreeWidget::item, QListWidget::item {
    padding: 4px;
    color: $text;
}
QTreeWidget::item:alternate, QListWidget::item:alternate {
    background-color: $sunken;
}
QTreeWidget::item:selected:!alternate, QListWidget::item:selected:!alternate {
    background-color: $selection;
    color: $bright;
}
QTreeWidget::item:selected:alternate, QListWidget::item:selected:alternate {
    background-color: $selection;
    color: $bright;
}
QTreeWidget::item:hover:!selected, QListWidget::item:hover:!selected {
    background-color: $row_hover;
}
/* Table rows, the same shades as a tree. QTableView is spelled here and it
   covers a QTableWidget too, because a sheet matches base classes - and Qt's
   own AlternateBase is white, which leaves dark text on a white row. */
QTableView {
    background-color: $surface;
    alternate-background-color: $sunken;
    gridline-color: $border;
    border: 1px solid $border;
    border-radius: 4px;
    outline: 0;
}
QTableView::item {
    padding: 4px;
    color: $text;
}
QTableView::item:alternate {
    background-color: $sunken;
}
QTableView::item:selected {
    background-color: $selection;
    color: $bright;
}
QTableView::item:hover:!selected {
    background-color: $row_hover;
}
/* Column headers */
QHeaderView::section {
    background-color: $header;
    color: $muted;
    border: 0;
    border-bottom: 1px solid $border;
    padding: 6px;
}
/* Check boxes */
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}
QCheckBox::indicator:unchecked {
    background-color: $sunken;
    border: 1px solid $faint;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: $accent;
    border: 1px solid $accent;
    border-radius: 3px;
}
/* Progress bar, status bar and tooltips */
QProgressBar {
    background-color: $soft;
    border: 1px solid $border;
    border-radius: 3px;
    text-align: center;
    color: $text;
    min-height: 18px;
}
QProgressBar::chunk {
    background-color: $accent;
}
QStatusBar {
    background-color: $sunken;
    color: $muted;
}
QStatusBar::item { border: 0; }
QToolTip {
    background-color: $soft;
    color: $text;
    border: 1px solid $accent;
    padding: 5px;
}
/* Menus and toolbars */
QMenu {
    background-color: $menu;
    border: 1px solid $border;
}
QMenu::item:selected {
    background-color: $selection;
}
QMenuBar {
    background-color: $sunken;
    color: $menu_text;
}
QMenuBar::item:selected {
    background-color: $selection;
}
QToolBar {
    background-color: $sunken;
    border: 0;
    spacing: 4px;
    padding: 4px;
}
/* Scrollbars, drawn thin with no arrow buttons */
QScrollBar:vertical {
    width: 12px;
    background-color: $sunken;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    background-color: $handle;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background-color: $accent;
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
    color: $warn;
    font-size: 14pt;
}
QLabel#statusDotOnline { color: $ok; }
QLabel#statusDotReady { color: $warn; }
QLabel#statusDotOffline { color: $accent; }
$styles
""")


#: The theme as shipped, and the fallback for every theme that leaves a token
#: out: the dark base with the berry accent, and the tab bar drawn at the sizes
#: the interface was measured with.
DEFAULT_THEME = {
    "name": "Berry Dark",
    "author": "Berry Manager",
    "colors": dict(THEME_COLORS),
    "tabs": dict(THEME_TABS),
    "styles": "",
}


#: Sheets already rendered, kept by the exact tokens they were poured from. A
#: theme is read once and painted from many places - the window, every popout,
#: the theme editor's live preview, every late widget - and substituting a
#: forty-kilobyte shape each time buys nothing: the answer only moves when a
#: colour or a knob does, and that changes the key. The cap is a brake, not a
#: limit worth tuning: switching themes replaces the whole set at once.
_SHEET_CACHE: dict[tuple, str] = {}
_SHEET_CACHE_MAX = 12


def render_theme(theme: object) -> str:
    """The application stylesheet one theme asks for, as Qt text.

    Colours and tab sizes go through the shape above, so a theme cannot say
    anything the interface did not already have a place for; ``styles`` is the
    one part poured in whole, for a rule the shape has no token for. Anything
    missing falls back to :data:`DEFAULT_THEME`, and a value that is not a
    colour is ignored rather than trusted.
    """
    data = theme if isinstance(theme, dict) else {}
    colors = theme_colors(data.get("colors"))
    tabs = theme_tabs(data.get("tabs"))
    values: dict[str, object] = {**colors, **tabs}
    extra = str(data.get("styles") or "")
    values["styles"] = f"\n/* from the theme itself */\n{extra}\n" if extra.strip() else ""
    # Both dicts are rebuilt in the fixed key order of ``THEME_COLORS`` and
    # ``THEME_TABS``, so the same tokens always make the same key.
    key = tuple(values.items())
    held = _SHEET_CACHE.get(key)
    if held is not None:
        return held
    sheet = _SHEET_BODY.safe_substitute(**values)
    if len(_SHEET_CACHE) >= _SHEET_CACHE_MAX:
        _SHEET_CACHE.clear()
    _SHEET_CACHE[key] = sheet
    return sheet


#: The sheet the app boots with before a profile's theme has been read. Every
#: part of the program that needs the look asks ``core.ui.stylesheet()``,
#: which renders this for the theme in force.
APP_STYLESHEET = render_theme(DEFAULT_THEME)


# --- interface zoom: the sizes above, multiplied ---

#: One number followed by a unit is the only thing a zoom may touch: colours,
#: weights, zero lengths and every other declaration value has no px/pt in it.
_QSS_SIZE = re.compile(r"(\d+(?:\.\d+)?)(px|pt)")

#: The zoom in force. ``core.window`` sets it from the profile's ``ui_zoom``;
#: widgets built afterwards read it so they are drawn at the same size as the
#: ones already on screen.
_zoom_state = {"percent": UI_ZOOM_DEFAULT}


def current_zoom() -> int:
    """The percentage the interface is being drawn at right now."""
    return _zoom_state["percent"]


def set_zoom(zoom: int) -> int:
    """Remember the zoom so later widgets match; returns the nearest ladder rung kept."""
    percent = step_zoom(zoom, 0)
    if percent != _zoom_state["percent"]:
        # Every scaled sheet on hand was measured for the old one.
        _SCALED_CACHE.clear()
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


#: Sheets already scaled, kept by the text and the zoom they were scaled at.
#: Re-running the substitution over the whole application sheet is the expensive
#: part of handing a widget its own sheet, and the same sheet goes through here
#: again on every theme refresh and every late-built widget - same words, same
#: zoom, same answer. Held by the text itself rather than by ``id``, because a
#: caller may hand back a freshly built copy of the same sheet.
_SCALED_CACHE: dict[tuple[str, int], str] = {}
_SCALED_CACHE_MAX = 24


def scaled_sheet(sheet: str, zoom: int | None = None) -> str:
    """``sheet`` with every px/pt size multiplied by the zoom, so late widgets match."""
    percent = current_zoom() if zoom is None else int(zoom)
    text = str(sheet or "")
    key = (text, percent)
    held = _SCALED_CACHE.get(key)
    if held is not None:
        return held
    factor = percent / 100.0

    def scale(match: re.Match) -> str:
        return f"{_zoomed(float(match.group(1)), factor)}{match.group(2)}"

    scaled = _QSS_SIZE.sub(scale, text)
    if len(_SCALED_CACHE) >= _SCALED_CACHE_MAX:
        _SCALED_CACHE.clear()
    _SCALED_CACHE[key] = scaled
    return scaled

