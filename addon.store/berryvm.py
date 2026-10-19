#!/usr/bin/env python3
"""BerryVM: a Windows XP-styled virtual desktop in one tab.

The tab is a desktop in the old sense: a wallpaper, a row of icons down the
left, a blue taskbar with a green Start button along the bottom, and free-
floating windows with blue title bars that drag, size and shut like they
should. Every program is one of those windows, and the desktop itself is a
``windows`` canvas from the addon api - the icon layer is just a window with
no title bar, kept at the back of the stack.

The programs that ship with it:

* **My Computer** - a two-paned file manager: this machine's disk at left,
  the device's root folder at right over the app's main connection. Files
  cross between the panes (upload, download), and folders, renames and
  deletes act on the pane they are in. Text files open in the editor,
  pictures in the viewer, and a local file can still be handed to the
  device to run.
* **Notepad** - one editor window per file, local or remote, with Save and
  Save As; a scratch buffer is born when Start > Notepad is pressed with
  nothing picked.
* **Picture Viewer** - local pictures straight off disk, remote ones pulled
  over the main connection; zoom is a fresh window at a new size.
* **Command Prompt** - a real local shell (cmd or sh) kept in a pocket
  terminal: the process stays up, ``cd`` sticks, output trickles in.
* **Terminal** - SSH to the profile's host on its own socket; **Machines**
  keeps bookmarks and opens a terminal window per machine.
* **Display Properties** - the settings window: wallpaper (gradient, solid
  colour or a picture file), the desktop's own scale, terminal text size,
  the clock, and the defaults the programs start with.
* **Calculator** - four keys' worth of arithmetic with a keyboard of buttons.

Extras round it out: right-click the desktop for its menu, right-click any
file row for Open/Edit/View/Transfer/Rename/Delete, and Start offers Change
Background, About and Log Off, which shuts every window on the desktop.

The manifest says ``"connection": "both"`` because the desktop really runs on
two kinds of link: every file pane and the picture viewer ride the app's main
connection (and say so when it is down), while each terminal brings its own
socket - the api insists on that. The addon's data folder is still the VM's
home: ``Desktop``, ``bin``, ``tmp``, ``config``, with ``config/settings.json``
mirroring the addon's own settings, passwords kept out of it as always.
"""

from __future__ import annotations

import json
import os
import posixpath
import subprocess
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from addon_common import AddonError, find_addon_picture

ADDON = {
    "name": "BerryVM",
    "version": "2.2",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh", "sftp"],
    # The desktop's two kinds of link really are two: the file panes and the
    # viewer ride the app's session, and every shell brings its own socket.
    "connection": "both",
    "description": "A Windows XP-styled desktop in one tab: wallpaper, Start "
                   "menu, taskbar and desktop icons, with a two-paned local + "
                   "device file manager, Notepad for local and remote files, "
                   "a picture viewer, a local command prompt, SSH terminals, "
                   "a calculator and Display Properties for the look.",
}

#: The tab's mark when the assets folder carries no picture: a monitor wearing
#: a small floating window, since a desktop of windows is the whole idea.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="1.5" y="3" width="21" height="14" rx="1.5" fill="#1e1e1e"'
    ' stroke="#c51a4a" stroke-width="1.2"/>'
    '<path d="M9 20.5h6M12 17v3.5" stroke="#7a2030" stroke-width="1.2"'
    ' stroke-linecap="round"/>'
    '<rect x="4.5" y="6" width="9" height="6" rx="0.8" fill="#2c2c2c"'
    ' stroke="#ffb020" stroke-width="0.9"/>'
    '<path d="M4.5 7.7h9" stroke="#ffb020" stroke-width="0.7"/>'
    '<path d="M6.2 9.3l1.2 1.1-1.2 1.1" fill="none" stroke="#23d18b"'
    ' stroke-width="0.9" stroke-linecap="round"/>'
    '</svg>'
)

#: The name the store's picture is expected to be filed under: the id and then
#: ``icon.png``, the rule every shipped addon follows.
TAB_ICON_FILE = "berryvmicon.png"

# --- the desktop's own ids ---

TAB = "BerryVM"
DESKTOP = "desktop"           # the ``windows`` canvas the whole tab is
ICONS = "icons"               # the chromeless window holding the icon layer
ICON_GRID = "desk_grid"       # the tile grid inside that window
TASKBAR = "taskbar"           # the blue row under the desktop
TRAY = "tray"                 # the taskbar's right-hand clock box
START_BTN = "start_btn"       # the green Start button
TASK_LIST = "taskbar_list"    # one button per open window
CLOCK = "clock"               # the tray's own time

# --- the programs' window ids (single-instance each) ---

P_EXPLORER = "explorer"       # two-paned file manager; titled My Computer
P_NOTEPAD = "notepad"         # Start entry; editors themselves are ``bp<n>``
P_VIEWER = "viewer"           # Start entry; viewer windows are ``pv<n>``
P_CONSOLE = "console"         # Start entry; console windows are ``cs<n>``
P_MAIN = "main_ssh"           # SSH to the profile's host
P_MACHINES = "machines"       # the SSH bookmark window
P_SETTINGS = "settings"       # Display Properties
P_CALC = "calc"               # Calculator
P_RECYCLE = "recycle"         # the desktop's Recycle Bin window
P_BROWSER = "browser"         # the Web Browser window (single instance)

#: What Start and the desktop know as the program list: an id, a caption, and
#: the mark the tile wears. The caption is the one name the icon layer, the
#: Start menu and the window titles all agree on.
PROGRAMS: list[tuple[str, str]] = [
    (P_EXPLORER, "My Computer"),
    (P_NOTEPAD, "Notepad"),
    (P_VIEWER, "Picture Viewer"),
    (P_CONSOLE, "Command Prompt"),
    (P_BROWSER, "Internet"),
    (P_MAIN, "Terminal"),
    (P_MACHINES, "Machines"),
    (P_SETTINGS, "Display Properties"),
    (P_CALC, "Calculator"),
    (P_RECYCLE, "Recycle Bin"),
]

CAPTIONS: dict[str, str] = dict(PROGRAMS)

#: The captions read back from a tile pick, so the desktop can dispatch on
#: the word under the cursor rather than remembering which row it was.
BY_CAPTION: dict[str, str] = {cap: pid for pid, cap in PROGRAMS}

#: A machine opened from the ``Machines`` window gets its own terminal window;
#: ``msh<seq>`` names it, and the counter never runs backwards.
MACHINE_PREFIX = "msh"

#: Notepad is one window per file; ``bp<seq>`` names it.
BERRY_PREFIX = "bp"

#: Each picture gets a viewer of its own, so ``pv<seq>``; each local shell a
#: console window, so ``cs<seq>``.
VIEWER_PREFIX = "pv"
CONSOLE_PREFIX = "cs"

#: The Explorer's two panes keep short id prefixes of their own - the table,
#: path label and buttons of one pane all start with these.
PANE_LOCAL = "exl"
PANE_REMOTE = "exr"

# --- the marks the desktop draws ---
# Every icon is a small SVG document of its own, kept flat and readable at
# 48 pixels, because that is what a desktop tile is. ``build_icon`` takes the
# text as it stands, so nothing needs a file on disk.

ICON_COMPUTER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect x="4" y="5" width="24" height="16" rx="2" fill="#d8d8c8"'
    ' stroke="#5a5a4a" stroke-width="1.2"/>'
    '<rect x="6.5" y="7.5" width="19" height="11" fill="#3b74d8"'
    ' stroke="#1c3f7d" stroke-width="1"/>'
    '<path d="M9 10.5h6" stroke="#bfe0ff" stroke-width="1.4"/>'
    '<path d="M12 22h8l2 4H10z" fill="#c4c4b4" stroke="#5a5a4a"/>'
    '<rect x="7" y="26.5" width="18" height="2.5" rx="1" fill="#b8b8a8"/>'
    '</svg>'
)

ICON_FOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M3 9h8l3 3h15v15H3z" fill="#e8b34b" stroke="#8a6420"/>'
    '<path d="M3 13.5h26V27H3z" fill="#ffd97a" stroke="#8a6420"'
    ' stroke-width="0.8"/>'
    '</svg>'
)

ICON_FILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M8 4h11l5 5v19H8z" fill="#ffffff" stroke="#6b8fb8"/>'
    '<path d="M19 4v5h5" fill="none" stroke="#6b8fb8"/>'
    '</svg>'
)

ICON_TEXTFILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M8 4h11l5 5v19H8z" fill="#ffffff" stroke="#6b8fb8"/>'
    '<path d="M19 4v5h5" fill="none" stroke="#6b8fb8"/>'
    '<path d="M11 13h10M11 17h10M11 21h7" stroke="#7f9db9"'
    ' stroke-width="1.4"/>'
    '</svg>'
)

ICON_PICTURE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect x="5" y="7" width="22" height="18" fill="#ffffff"'
    ' stroke="#6b8fb8"/>'
    '<rect x="7" y="9" width="18" height="14" fill="#9fd0ff"/>'
    '<path d="M7 20l6-6 4 4 4-4 4 4v5H7z" fill="#4c9b3a"/>'
    '<circle cx="21" cy="12" r="2" fill="#ffd94a"/>'
    '</svg>'
)

ICON_CONSOLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect x="4" y="6" width="24" height="20" rx="1.5" fill="#101418"'
    ' stroke="#5a5a5a"/>'
    '<path d="M8 12l4 3-4 3" fill="none" stroke="#e8e8e8" stroke-width="1.6"'
    ' stroke-linecap="round"/>'
    '<path d="M14 19h8" stroke="#e8e8e8" stroke-width="1.6"'
    ' stroke-linecap="round"/>'
    '</svg>'
)

ICON_SSH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect x="4" y="5" width="24" height="17" rx="2" fill="#1e2c48"'
    ' stroke="#3b74d8" stroke-width="1.4"/>'
    '<path d="M9 10l4 3-4 3" fill="none" stroke="#43e08a" stroke-width="1.6"'
    ' stroke-linecap="round"/>'
    '<path d="M16 17h8" stroke="#43e08a" stroke-width="1.6"'
    ' stroke-linecap="round"/>'
    '<path d="M12 25h8l1.5 3h-11z" fill="#c4c4b4"/>'
    '</svg>'
)

ICON_GEAR = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="6" fill="#cfe0f4" stroke="#3b74d8"'
    ' stroke-width="2"/>'
    '<path d="M16 3v5M16 24v5M3 16h5M24 16h5M7 7l3.5 3.5M21.5 21.5L25 25'
    'M25 7l-3.5 3.5M10.5 21.5L7 25" stroke="#3b74d8" stroke-width="2.4"'
    ' stroke-linecap="round"/>'
    '</svg>'
)

ICON_CALC = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect x="8" y="3" width="16" height="26" rx="2" fill="#e3e3d8"'
    ' stroke="#6a6a5a"/>'
    '<rect x="10.5" y="6" width="11" height="5" fill="#9fd0a8"'
    ' stroke="#3f6b47"/>'
    '<path d="M12.5 16h1.5M15.75 16h1.5M19 16h1.5M12.5 19.5h1.5M15.75 19.5h1.5'
    'M19 19.5h1.5M12.5 23h1.5M15.75 23h1.5M19 23h1.5" stroke="#494940"'
    ' stroke-width="2"/>'
    '</svg>'
)

ICON_EXE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M8 4h11l5 5v19H8z" fill="#ffffff" stroke="#3f6b47"/>'
    '<path d="M19 4v5h5" fill="none" stroke="#3f6b47"/>'
    '<rect x="10" y="13" width="12" height="9" fill="#43e08a"/>'
    '<path d="M13 15.5l3.5 2-3.5 2" fill="none" stroke="#103018"'
    ' stroke-width="1.5" stroke-linecap="round"/>'
    '</svg>'
)

ICON_RECYCLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M8 6h16l-1.5 22H9.5z" fill="#c4dcee" stroke="#3c5e7e"'
    ' stroke-width="1.2"/>'
    '<path d="M6 5h20" stroke="#3c5e7e" stroke-width="1.6"/>'
    '<path d="M13 3h6v2h-6z" fill="#c4dcee" stroke="#3c5e7e"/>'
    '<path d="M12 10h1M15 10h1M18 10h1M12 14h1M15 14h1M18 14h1M12 18h1'
    'M15 18h1M18 18h1M12 22h1M15 22h1M18 22h1" stroke="#3c5e7e"'
    ' stroke-width="1.4" stroke-linecap="round"/>'
    '<path d="M14.5 12.5l3 0-1.5 2z" fill="#3e8522" stroke="none"/>'
    '</svg>'
)

# The Web Browser's mark: a blue globe with a lat/long cross, the shape
# XP's Internet Explorer wore on the desktop and the tray both.
ICON_GLOBE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="12" fill="#4f9ee8" stroke="#1e4a80"'
    ' stroke-width="1.4"/>'
    '<path d="M4 12.5h24M4 19.5h24" stroke="#ffffff" stroke-width="1.1"'
    ' fill="none"/>'
    '<path d="M16 4c-4.5 4.5-4.5 19.5 0 24M16 4c4.5 4.5 4.5 19.5 0 24"'
    ' stroke="#ffffff" stroke-width="1.1" fill="none"/>'
    '<path d="M6.5 8.5c4 3 15 3 19 0M6.5 23.5c4-3 15-3 19 0"'
    ' stroke="#ffffff" stroke-width="1" fill="none"/>'
    '</svg>'
)

#: Program id to the mark its desktop tile wears.
TILE_ICONS = {
    P_EXPLORER: ICON_COMPUTER,
    P_NOTEPAD: ICON_TEXTFILE,
    P_VIEWER: ICON_PICTURE,
    P_CONSOLE: ICON_CONSOLE,
    P_MAIN: ICON_SSH,
    P_MACHINES: ICON_SSH,
    P_SETTINGS: ICON_GEAR,
    P_CALC: ICON_CALC,
    P_RECYCLE: ICON_RECYCLE,
    P_BROWSER: ICON_GLOBE,
}

#: The mark a taskbar button wears, from the ``kind`` its opened record
#: carries. A program's window and the tile that started it wear the same
#: picture; a machine's terminal wears the SSH mark like the Terminal does.
KIND_ICONS = {
    "explorer": ICON_COMPUTER,
    "berrypad": ICON_TEXTFILE,
    "viewer": ICON_PICTURE,
    "console": ICON_CONSOLE,
    "main_ssh": ICON_SSH,
    "machines": ICON_SSH,
    "machine": ICON_SSH,
    "settings": ICON_GEAR,
    "calc": ICON_CALC,
    "recycle": ICON_RECYCLE,
    "browser": ICON_GLOBE,
}

#: The taskbar's own shape, in unscaled pixels: one row of buttons tall, and
#: the mark beside each caption drawn at this edge. Both go through the
#: desktop scale, so a bigger desktop grows a bigger bar.
TASKBAR_H = 30
TASKBAR_ICON = 16

#: What the bar and the clock were last told: the row the refresh wrote and
#: the line the tick wrote. Dragging a window re-runs the refresh through
#: the desktop's ``moved`` report a hundred times a second without changing
#: one button, and the clock's text moves once a minute while its tick runs
#: twice; both answers are written only when they differ. ``fresh`` in the
#: test suite resets these alongside the rest of the module state.
_taskbar_shown: tuple = ()
_clock_shown = None

# --- the look: wallpapers, and the two sheets that paint the desktop ---

#: The backgrounds the desktop knows by name. A gradient is Qt's own
#: ``qlineargradient`` text; ``Solid`` and ``Picture`` are filled in from the
#: settings at :func:`wall_css` time, so the names stay the menu's.
WALLPAPERS = {
    "Bliss": ("gradient",
              "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
              " stop:0 #2f7fdc, stop:0.15 #64a9e8, stop:0.35 #a8d1f2,"
              " stop:0.42 #b5d8f2, stop:0.50 #78a945,"
              " stop:0.65 #4d8f2f, stop:0.85 #3e7a22, stop:1 #2b5b18)"),
    "Night": ("gradient",
              "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
              " stop:0 #060a1c, stop:0.55 #16295c, stop:1 #2c4b8a)"),
    "Olive": ("gradient",
              "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
              " stop:0 #2f3518, stop:0.5 #59642e, stop:1 #8b9a52)"),
    "Crimson": ("gradient",
                "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 #3d0a12, stop:0.5 #8c1f2c, stop:1 #c85a52)"),
    "Aqua": ("gradient",
             "qlineargradient(x1:0,y1:0,x2:0,y2:1,"
             " stop:0 #063a4e, stop:0.5 #0d6f8c, stop:1 #58c4d8)"),
    "Solid": ("solid", ""),
    "Picture": ("picture", ""),
}

#: The fallback floor behind a picture, so a missing file shows a colour
#: rather than the theme's grey.
WALL_FALLBACK = "#1e56a8"


def wall_css() -> str:
    """The canvas's ``background:`` value, from what the settings name."""
    name = str(config("wallpaper", "Bliss") or "Bliss")
    kind, value = WALLPAPERS.get(name, ("gradient", WALLPAPERS["Bliss"][1]))
    if kind == "solid":
        colour = str(config("wallpaper_color", DEFAULTS["wallpaper_color"])
                     or WALL_FALLBACK)
        return colour
    if kind == "picture":
        path = str(config("wallpaper_file", "") or "").strip()
        if path:
            # Qt's url() wants forward slashes whatever the disk said.
            return (f'url("{path.replace(os.sep, "/").replace(chr(92), "/")}");'
                    f" background-repeat: no-repeat;"
                    f" background-position: center;"
                    f" background-color: {WALL_FALLBACK}")
        return WALL_FALLBACK
    return value


def desktop_sheet() -> str:
    """The stylesheet the canvas wears: wallpaper, Luna frames, and every
    control inside them.

    One sheet covers the whole desktop because a stylesheet set on a parent
    cascades to everything under it - which is exactly what is wanted here:
    buttons inside any window are XP buttons, lists are XP lists. The canvas
    itself is only ever reached by its object name, so the wallpaper cannot
    wash over the frames.
    """
    return (
        "QWidget#berryDesktop { background: " + wall_css() + "; }"
        # The frame: a grey client area under a blue 2px border, the way
        # every XP window is drawn.
        "#addonWindow { background: #ece9d8; border: 2px solid #0855dd; }"
        "#addonWindowBar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #2b84f0, stop:0.10 #1260e8, stop:0.55 #0b4fd6,"
        " stop:1 #1866e8); }"
        "#addonWindowBar QLabel { color: #ffffff; font-weight: bold; }"
        "#addonWindowBar QPushButton { border: 1px solid #ffffff;"
        " border-radius: 2px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #f1927c, stop:0.3 #e04e00, stop:1 #b93000);"
        " color: #ffffff; font-weight: bold; }"
        "#addonWindowBar QPushButton:hover { background: #ff7a3c; }"
        "#addonWindowBar QPushButton:pressed { background: #a02800; }"
        # The client-area furniture, so any program looks XP without trying.
        "#addonWindow QPushButton { background: qlineargradient(x1:0,y1:0,"
        " x2:0,y2:1, stop:0 #ffffff, stop:0.45 #f2f4f8, stop:0.55 #dfe4ee,"
        " stop:1 #cdd8ea); border: 1px solid #7f9db9; border-radius: 3px;"
        " padding: 3px 10px; color: #000000; }"
        "#addonWindow QPushButton:hover { border: 1px solid #e9a900;"
        " background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff,"
        " stop:0.5 #fdfbe3, stop:1 #f7e9b0); }"
        "#addonWindow QPushButton:pressed { background: #d4d0c8;"
        " border: 1px solid #4a6f9c; }"
        "#addonWindow QPushButton:disabled { color: #a0a0a0;"
        " background: #f0f0f0; border: 1px solid #c8c8c8; }"
        "#addonWindow QLineEdit, #addonWindow QPlainTextEdit,"
        "#addonWindow QListWidget, #addonWindow QTreeWidget,"
        "#addonWindow QComboBox { background: #ffffff; color: #000000;"
        " border: 1px solid #7f9db9; selection-background-color: #316ac5;"
        " selection-color: #ffffff; }"
        "#addonWindow QGroupBox { border: 1px solid #d4d0c8;"
        " border-radius: 3px; margin-top: 6px; }"
        "#addonWindow QProgressBar { border: 1px solid #7f9db9;"
        " border-radius: 2px; text-align: center; background: #ffffff; }"
        "#addonWindow QProgressBar::chunk { background: #316ac5; }"
    )


def taskbar_sheet() -> str:
    """The blue bar, the green Start button and the tray - the strip that is
    outside the canvas gets its own sheet for the same reason."""
    return (
        "#berryTaskbar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #3a8cf7, stop:0.08 #2574e8, stop:0.5 #1c5ed6,"
        " stop:0.92 #1448b4, stop:1 #1043a8); border-top: 2px solid #5b9bf8; }"
        "#berryStart { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #4aaa4a, stop:0.5 #2f8a2f, stop:1 #1e6b1e);"
        " border: 1px solid #1d5e1d; border-radius: 0px 10px 10px 0px;"
        " color: #ffffff; font-weight: bold; padding: 5px 16px; }"
        "#berryStart:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #5ec05e, stop:1 #2c7c2c); }"
        "#berryStart:pressed { background: #1e5c1e; }"
        "#berryTasks { background: transparent; border: none; }"
        "#berryTasks::item { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #5e9ef5, stop:1 #2a6ada); border: 1px solid #1c4fb4;"
        " border-radius: 3px; color: #ffffff; padding: 3px 8px;"
        " margin-right: 3px; }"
        "#berryTasks::item:selected { background: qlineargradient(x1:0,"
        " y1:0,x2:0,y2:1, stop:0 #1c54b7, stop:1 #0d3a94);"
        " border: 1px solid #0a2c74; }"
        "#berryTray { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #1e68d8, stop:1 #0d47b0); border-left: 1px solid #6ba1e8; }"
        "#berryClock { color: #ffffff; font-weight: bold; background:"
        " transparent; padding: 0px 10px; }"
    )


#: The icon layer's own sheet, put on its frame: everything it would rather
#: not be - the grey client area, the border - goes transparent so the
#: wallpaper is the desktop, and the tiles lose their box.
ICON_FRAME_SHEET = (
    "#addonWindow { background: transparent; border: none; }"
    # The client area under the (absent) bar is a bare widget of its own; the
    # window-wide ``QWidget { background }`` paints it the theme's dark, and
    # since this layer spans the whole desktop, that dark is what a person
    # would see instead of the wallpaper. Made transparent, the wall shows.
    "#addonWindowBody { background: transparent; border: none; }"
    "#berryDeskGrid { background: transparent; border: none;"
    " color: #ffffff; font-weight: bold; outline: none; }"
    "#berryDeskGrid::item { background: transparent; color: #ffffff;"
    " border: 1px solid transparent; border-radius: 2px; padding: 2px; }"
    "#berryDeskGrid::item:selected { background: rgba(49, 106, 197, 0.55);"
    " border: 1px dotted #ffffff; }"
)

# --- what can be configured, and its defaults ---

FIELDS = [
    {"key": "window_width", "label": "Default window width", "kind": "number",
     "minimum": 240, "maximum": 2400,
     "hint": "The size a program window starts at, before the person drags it"},
    {"key": "window_height", "label": "Default window height", "kind": "number",
     "minimum": 180, "maximum": 1600},
    {"key": "ui_scale", "label": "Desktop scale (percent)", "kind": "number",
     "minimum": 80, "maximum": 160,
     "hint": "BerryVM's own scale: window sizes and desktop icons multiply by "
             "it. The app's interface zoom is a different dial"},
    {"key": "term_font", "label": "Terminal text size", "kind": "number",
     "minimum": 8, "maximum": 28,
     "hint": "Points for every shell window on the desktop, the SSH ones "
             "included"},
    {"key": "wallpaper", "label": "Wallpaper", "kind": "text",
     "hint": "Bliss, Night, Olive, Crimson, Aqua, Solid, or Picture - the "
             "last two read wallpaper_color and wallpaper_file"},
    {"key": "wallpaper_color", "label": "Solid wallpaper colour",
     "kind": "text", "hint": "A #rrggbb for the Solid wallpaper"},
    {"key": "wallpaper_file", "label": "Wallpaper picture", "kind": "text",
     "hint": "A file on this machine for the Picture wallpaper"},
    {"key": "clock_on", "label": "Show the clock in the taskbar", "kind": "bool",
     "hint": "Off, the tray loses its time and the timer stops; the desktop "
             "does not care either way"},
    {"key": "default_user", "label": "Username to offer", "kind": "text",
     "hint": "Pre-filled for a machine that names none of its own"},
    {"key": "default_port", "label": "Port to offer", "kind": "number",
     "minimum": 1, "maximum": 65535},
    {"key": "trust_unknown_host", "label": "Accept a host key nobody has seen",
     "kind": "bool",
     "hint": "The same bargain Multi SSH makes: a machine whose key is not in "
             "your known_hosts file is refused when this is off"},
]

#: ``machines`` is in the file but named in no field: the app's form writes
#: only what it shows, so a key it never showed survives the save unchanged -
#: which is what lets ``Machines`` add and drop entries on its own.
DEFAULTS = {
    "window_width": 520,
    "window_height": 360,
    "ui_scale": 100,
    "term_font": 11,
    "wallpaper": "Bliss",
    "wallpaper_color": "#2356b7",
    "wallpaper_file": "",
    "clock_on": True,
    "default_user": "root",
    "default_port": 22,
    "trust_unknown_host": True,
    "machines": [],
}

# --- the tab's spec ---

#: The whole tab: a desktop of floating windows over an XP taskbar. The
#: desktop opts in to every window event so the taskbar can follow it; the
#: object names are what the two sheets paint.
TAB_SPEC = {
    "kind": "column",
    "children": [
        {"kind": "windows", "id": DESKTOP, "object_name": "berryDesktop",
         "expand": True,
         "on": ["activated", "closed", "moved", "resized",
                 "minimized", "restored", "maximized", "shortcut"],
         "shortcuts": [{"keys": "Alt+Tab", "event": "shortcut"}],
         "children": []},
        {"kind": "row", "id": TASKBAR, "object_name": "berryTaskbar",
         "children": [
             {"kind": "button", "id": START_BTN, "object_name": "berryStart",
              "text": "Start", "on": ["clicked"],
              "tooltip": "Open one of BerryVM's programs; a program already "
                         "open is raised rather than started again"},
             {"kind": "list", "id": TASK_LIST, "object_name": "berryTasks",
              "on": ["selected"], "expand": True, "flow": "horizontal",
              "tooltip": "One button per window on the desktop. Select one "
                         "and it comes forward."},
             {"kind": "row", "id": TRAY, "object_name": "berryTray",
              "children": [
                  {"kind": "button", "id": "tray_net", "text": "\u25d1",
                   "object_name": "berryTrayBtn", "on": ["clicked"],
                   "tooltip": "Network: shows whether the app's SSH and "
                              "SFTP sessions are up"},
                  {"kind": "button", "id": "tray_wbr", "text": "\u25ce",
                   "object_name": "berryTrayBtn", "on": ["clicked"],
                   "tooltip": "Open the Web Browser"},
                  {"kind": "label", "id": CLOCK, "object_name": "berryClock",
                   "text": ""},
              ]},
         ]},
    ],
}

# --- module state ---

api = None                     # filled in by init()
machines: list = []            # the Machines quickstart list, normalised
machine_row: int = -1          # the row the Machines list has under the cursor
machine_seq: int = 0           # ``msh<seq>`` ids, so they cannot be reused
berry_seq: int = 0             # ``bp<seq>`` ids, one editor per file
view_seq: int = 0              # ``pv<seq>`` ids, one viewer per picture
console_seq: int = 0           # ``cs<seq>`` ids, one local shell per window
calc_expr: str = ""            # the expression the calculator is building
opened: dict = {}              # win_id -> {"title", "kind", extra}
berrypads: dict = {}           # win_id -> {"path", "origin", "title"}
viewers: dict = {}             # win_id -> {"path", "origin", "size"}
consoles: dict = {}            # win_id -> {"proc", "pending", "cwd", "alive"}
terminals: dict = {}           # terminal id -> win_id it lives inside
pane_pick: dict = {}           # table id -> the name its cursor is on
pane_rows: dict = {}           # table id -> {display name: {"path", ...}}
pane_path: dict = {}           # pane prefix -> the folder that side shows
home_ready: bool = False       # ``_ensure_home()`` has run in this process

#: The desktop's own layout memory. Positions are keyed by a tile's
#: persistent name (the caption it shipped with, before any rename);
#: renames maps that key to what the person typed. Recycled holds one
#: record per file the desktop has stashed in the Recycle Bin folder.
#: Attention is which window ids are currently flashing on the bar, with
#: the second they stop at beside them. Minimized is what the desktop
#: knows is hidden, so a taskbar row can look pressed even without a
#: round trip to the canvas.
_desk_positions: dict = {}
_desk_renames: dict = {}
_desk_recycled: list = []
minimized: dict = {}
attention: dict = {}
_flash_on: bool = False


def init(plugin_api: Any) -> None:
    """Declare the config, paint the desktop, stand up the icon layer.

    ``home_ready`` is ``_ensure_home()``'s own business - this function only
    reaches it through that call, so it is not named in the ``global`` here.
    """
    global api
    api = plugin_api
    # The screen's own size and the app's UI zoom together say what a fresh
    # install should open with, so a small netbook is not asked to drag a
    # 520x360 window around a 768-pixel screen. Anything already stored in
    # the config still wins: this only shapes what has never been set.
    _apply_screen_defaults()
    api.config.declare(fields=FIELDS, defaults=DEFAULTS, title="BerryVM")
    _ensure_home()
    mirror_settings()
    load_desk()
    api.tabs.add_tab(TAB, spec=TAB_SPEC)
    api.tabs.set_icon(TAB, tab_icon())
    apply_theme()
    reload_machines()
    open_icon_layer()
    refresh_taskbar()
    if bool(config("clock_on", True)):
        api.schedule.every(30, tick_clock)
        tick_clock()
    api.schedule.every(0.5, pump_consoles)
    api.schedule.every(0.5, tick_flash)
    api.log("BerryVM ready: an XP-styled desktop in one tab.")
    _welcome()


def shutdown() -> None:
    """Close the tab; the app stops any shell inside on its way out, and any
    local console loses its process here - a child nobody owns is a zombie."""
    global home_ready, machine_seq, berry_seq, view_seq, console_seq, calc_expr
    global _desk_positions, _desk_renames, _desk_recycled
    global minimized, attention, _flash_on
    for record in list(consoles.values()):
        _stop_shell(record)
    opened.clear()
    berrypads.clear()
    viewers.clear()
    consoles.clear()
    terminals.clear()
    pane_pick.clear()
    pane_rows.clear()
    pane_path.clear()
    machines.clear()
    machine_seq = 0
    berry_seq = 0
    view_seq = 0
    console_seq = 0
    calc_expr = ""
    home_ready = False
    _desk_positions = {}
    _desk_renames = {}
    _desk_recycled = []
    minimized = {}
    attention = {}
    _flash_on = False
    if api is not None:
        api.tabs.close(TAB)


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else
    the drawn mark - the same rule every other store addon follows."""
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):
        paths = None
    return find_addon_picture(paths, "berryvm") or TAB_ICON


def _welcome() -> None:
    """The logon bubble: a hello that says what the desktop thinks the main
    connection is, because that is the machine half the programs work on."""
    if api is None:
        return
    try:
        if api.device.is_connected("ssh"):
            profile = api.session.profile() or {}
            host = str(profile.get("host") or "the device")
            api.ui.popup("Welcome to BerryVM",
                         f"Desktop is up, riding the main connection to "
                         f"{host}. Double-click an icon, or press Start.")
        else:
            api.ui.popup("Welcome to BerryVM",
                         "Desktop is up. The main connection is not open - "
                         "file panes will say so until it is.")
    except Exception:                        # a toast is never worth a stack
        api.log("berryvm: welcome popup skipped")


def _screen_geometry() -> tuple[int, int]:
    """The main window's screen, in pixels. Falls back to a modest 1366x768
    when no window is up (an off-screen test run) or the ask itself fails
    on a headless box; the point is a shape to size against, not a truth
    to obey when the machine says nothing."""
    if api is None:
        return 1366, 768
    try:
        win = api.ui.window()
        if win is not None:
            scr = win.screen()
            if scr is not None:
                geo = scr.availableGeometry()
                w, h = int(geo.width()), int(geo.height())
                if w >= 320 and h >= 240:
                    return w, h
    except Exception:
        pass
    return 1366, 768


def _app_zoom() -> int:
    """The interface zoom the app is running at, as a percent. 100 is what
    was shipped: the addon has no handle to change it, only to see it, and
    the desktop's own scale is nudged against a UI that is already grown."""
    if api is None:
        return 100
    try:
        return max(50, min(200, int(api.ui.zoom() or 100)))
    except Exception:
        return 100


def _apply_screen_defaults() -> None:
    """Bake DEFAULTS from the machine's own shape before the app reads it.

    The stored config still wins - these values are what the person gets
    only when a key has never been set - but a first install now opens a
    sensible window on a 1024x600 netbook and on a 4K monitor both, rather
    than one hard-coded pixel size. The desktop's own scale is factored
    against the app's UI zoom too, so a machine already running at 120%
    does not stack a second 20% on top of it."""
    screen_w, screen_h = _screen_geometry()
    zoom = _app_zoom()
    # A window sized for the middle of the screen at 100% zoom: 42% across,
    # 48% down, kept inside the range the settings form offers. Larger
    # screens get larger windows, but a 4K monitor does not want a 3000px
    # Explorer either, so the top end is capped at what reads well.
    width = int(max(240, min(1400, screen_w * 0.42)))
    height = int(max(180, min(1000, screen_h * 0.48)))
    # BerryVM's own scale moves the other way against the app's zoom: an
    # app already at 120% gets a slightly smaller desktop scale so the two
    # together do not push the tiles off the edge on a modest screen.
    scale_pct = 10000 // zoom if zoom else 100
    if screen_w >= 2400:
        scale_pct = max(scale_pct, 130)
    elif screen_w >= 1600:
        scale_pct = max(scale_pct, 115)
    elif screen_w >= 1280:
        scale_pct = max(scale_pct, 100)
    elif screen_w >= 1024:
        scale_pct = min(scale_pct, 90)
    else:
        scale_pct = min(scale_pct, 80)
    scale_pct = max(80, min(160, scale_pct))
    DEFAULTS["window_width"] = width
    DEFAULTS["window_height"] = height
    DEFAULTS["ui_scale"] = scale_pct


# --- small pure helpers, so the shapes above can be read without a window ---

def config(key: str, default: Any = None) -> Any:
    """One key of the addon's own settings file, or ``default`` when nothing
    could be read - so the helpers below stay usable before ``init`` has run."""
    if api is None:
        return default
    try:
        value = api.config.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def port_of(value: Any, default: int = 22) -> int:
    """A port number, or ``default``: a hand-typed file is allowed to hold
    ``"22"``, a blank, or ``0``, and none of those is worth an exception."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return number if 1 <= number <= 65535 else int(default)


def split_address(text: Any) -> dict:
    """``root@192.168.1.1:2222`` cut into the parts a connection needs, the
    same rule Multi SSH applies."""
    body = str(text or "").strip()
    out = {"username": "", "host": "", "port": ""}
    if "@" in body:
        out["username"], _, body = body.partition("@")
        out["username"] = out["username"].strip()
    if body.count(":") == 1:
        out["host"], _, out["port"] = body.rpartition(":")
        out["host"] = out["host"].strip()
    else:
        out["host"] = body
    return out


def normalise_machine(raw: Any) -> dict:
    """One machine entry in the shape ``Machines`` uses: a bare address, or an
    object whose fields may be missing. An entry with no address is dropped."""
    if isinstance(raw, str):
        raw = {"host": raw}
    if not isinstance(raw, dict):
        return {}
    given = split_address(raw.get("host", raw.get("address", "")))
    host = given["host"]
    if not host:
        return {}
    name = " ".join(str(raw.get("name") or "").split())
    key = str(raw.get("key_file", raw.get("key", "")) or "").strip()
    return {
        "name": name or host,
        "host": host,
        "port": port_of(given["port"], 0) or port_of(raw.get("port"), 0)
                or port_of(config("default_port", 22), 22),
        "username": (given["username"] or str(raw.get("username") or "").strip()
                     or str(config("default_user", "root") or "")),
        "key_file": key,
    }


def machines_from(raw: Any) -> list:
    """The config's ``machines`` list as the addon uses it; a broken entry is
    left out rather than hiding the machines beside it."""
    items = raw if isinstance(raw, (list, tuple)) else []
    out: list[dict] = []
    for item in items:
        entry = normalise_machine(item)
        if entry:
            out.append(entry)
    return out


def language_for(path: Any) -> str:
    """The language the editor shows this file in, from its suffix; ``""`` is
    plain text, which is what an unknown ending is."""
    suffix = Path(str(path or "")).suffix.lower()
    return LANG_BY_SUFFIX.get(suffix, "")


def is_text(path: Any) -> bool:
    """Whether this name looks like something the editor could open."""
    suffix = Path(str(path or "")).suffix.lower()
    return suffix in TEXT_SUFFIXES


def is_picture(path: Any) -> bool:
    """Whether this name is something the Picture Viewer could show."""
    suffix = Path(str(path or "")).suffix.lower()
    return suffix in IMAGE_SUFFIXES


def short_name(path: Any) -> str:
    """The last piece of a path, for a title bar that has room for one word."""
    text = str(path or "").rstrip("/\\")
    if not text:
        return ""
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def scale() -> int:
    """BerryVM's own desktop scale in percent. The app's interface zoom is a
    different dial the addon cannot turn; this one moves the windows and
    tiles this desktop draws."""
    try:
        number = int(config("ui_scale", DEFAULTS["ui_scale"]))
    except (TypeError, ValueError):
        return int(DEFAULTS["ui_scale"])
    return max(80, min(160, number))


def scaled(pixels: int) -> int:
    """One unscaled number through the desktop scale, for sizes the addon
    picks itself. The primitive zooms window sizes again at the interface
    zoom; this multiplication is only BerryVM's own layer on top."""
    return max(1, int(round(int(pixels) * scale() / 100.0)))


def window_size() -> tuple[int, int]:
    """The default width and height, from the config, kept inside the range
    the form offers and grown by the desktop scale."""
    width = int(config("window_width", DEFAULTS["window_width"]))
    height = int(config("window_height", DEFAULTS["window_height"]))
    return (scaled(max(240, min(2400, width))),
            scaled(max(180, min(1600, height))))


def term_font() -> int:
    """The point size every shell window on this desktop draws at."""
    try:
        number = int(config("term_font", DEFAULTS["term_font"]))
    except (TypeError, ValueError):
        return int(DEFAULTS["term_font"])
    return max(8, min(28, number))


def describe_size(size: Any) -> str:
    """A byte count as the note beside a refused file likes to say it."""
    try:
        number = int(size)
    except (TypeError, ValueError):
        return "an unknown amount"
    for unit, div in (("KiB", 1024), ("MiB", 1024 * 1024),
                      ("GiB", 1024 * 1024 * 1024)):
        if number < div * 1024:
            return f"{number / div:.1f} {unit}"
    return f"{number / (1024 ** 3):.1f} GiB"


def file_icon(path: Any, directory: bool = False) -> str:
    """The mark a file row wears in Explorer, picked the way a shell picks a
    thumbnail: folder first, then picture, then text, then runnable."""
    if directory:
        return ICON_FOLDER
    name = str(path or "")
    if is_picture(name):
        return ICON_PICTURE
    if is_text(name):
        return ICON_TEXTFILE
    if Path(name).suffix.lower() in EXEC_SUFFIXES:
        return ICON_EXE
    return ICON_FILE


#: Endings that read as "something you could run" to a row of icons.
EXEC_SUFFIXES = frozenset({".sh", ".bash", ".py", ".exe", ".bat", ".cmd",
                           ".com", ".run", ".bin"})

#: Endings Berrypad - the editor - will not read or write above this size.
#: Half a megabyte is far past what any of the shipped views can usefully
#: edit and small enough that a stray ``cat`` of a firmware blob cannot
#: freeze the tab.
MAX_SIZE = 512 * 1024

#: Endings the editor treats as text at all; anything else is a binary as far
#: as it is concerned and gets a saying of what it is.
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".json", ".yaml", ".yml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".ksh", ".py", ".js", ".ts", ".html", ".htm",
    ".xml", ".css", ".svg", ".csv", ".tsv", ".log", ".editorconfig",
    ".service", ".theme", ".list", ".sources", ".repo", ".gitignore",
})

#: Endings the Picture Viewer offers to show. Qt's image plugins cover
#: exactly this table out of the box.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp",
                            ".webp", ".ico", ".xpm"})

#: Suffix to the language the editor highlights in. Unlisted text
#: endings still open, just without colour.
LANG_BY_SUFFIX = {
    ".py": "python", ".json": "json", ".theme": "json",
    ".js": "javascript", ".ts": "javascript",
    ".html": "html", ".htm": "html", ".xml": "html", ".svg": "html",
    ".css": "css",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".editorconfig": "ini",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ksh": "bash",
    ".list": "bash", ".sources": "bash",
}


def calc_eval(expr: Any) -> float:
    """Evaluate a calculator expression: ``+ - * / %`` and parentheses over
    numbers, nothing else.

    A recursive-descent read of the line, rather than ``eval``, because the
    display is a text field a hand can type anything into - and a calculator
    that runs arbitrary code is not a calculator. A bad line raises
    ``ValueError``, which is what the window shows as ``Error``.
    """
    tokens: list[str] = []
    body = str(expr or "").replace(",", ".").strip()
    index = 0
    while index < len(body):
        ch = body[index]
        if ch.isspace():
            index += 1
            continue
        if ch in "+-*/%()":
            tokens.append(ch)
            index += 1
            continue
        if ch.isdigit() or ch in ".eE":
            start = index
            while index < len(body) and (
                    body[index].isdigit() or body[index] in ".eE"
                    or (body[index] in "+-" and body[index - 1:index]
                        in ("e", "E"))):
                index += 1
            tokens.append(body[start:index])
            continue
        raise ValueError(f"unexpected {ch!r}")
    pos = [0]

    def peek() -> str:
        return tokens[pos[0]] if pos[0] < len(tokens) else ""

    def eat(expected: str) -> bool:
        if peek() == expected:
            pos[0] += 1
            return True
        return False

    def primary() -> float:
        if eat("("):
            value = expression()
            if not eat(")"):
                raise ValueError("missing )")
            return value
        if eat("-"):
            return -primary()
        if eat("+"):
            return primary()
        token = peek()
        if not token:
            raise ValueError("ran out")
        pos[0] += 1
        try:
            return float(token)
        except ValueError:
            raise ValueError(f"not a number: {token}") from None

    def term() -> float:
        value = primary()
        while True:
            if eat("*"):
                value *= primary()
            elif eat("/"):
                right = primary()
                if right == 0:
                    raise ValueError("divide by zero")
                value /= right
            elif eat("%"):
                right = primary()
                if right == 0:
                    raise ValueError("divide by zero")
                value %= right
            else:
                return value

    def expression() -> float:
        value = term()
        while True:
            if eat("+"):
                value += term()
            elif eat("-"):
                value -= term()
            else:
                return value

    result = expression()
    if pos[0] != len(tokens):
        raise ValueError("trailing words")
    return result


# --- the VM's home folder ---

#: Folder names created inside ``addons/_data/berryvm`` on first init: this is
#: the shape a home directory is expected to have, kept small and readable.
HOME_DIRS = ("Desktop", "bin", "tmp", "config")

#: The settings file that mirrors the addon's own config into the ``config``
#: folder, so the editor can open it like any other file.
SETTINGS_NAME = "settings.json"

#: Where remote pictures land before the viewer shows them, and where a
#: download the person did not name a place for is offered from.
TMP_DIR_NAME = "tmp"


def home_root() -> Path:
    """``addons/_data/berryvm``, the VM's root."""
    return Path(api.paths.data)


def _ensure_home() -> Path:
    """Create the VM's home layout once, and only once, per running addon.

    ``api.paths.data`` has already made the folder itself; what is added here
    is the shape inside it - ``Desktop``, ``bin``, ``tmp``, ``config`` - so
    the console has somewhere to start and the settings mirror has a place to
    sit. A second call is a no-op rather than a repeat mkdir.
    """
    global home_ready
    root = home_root()
    if home_ready:
        return root
    for name in HOME_DIRS:
        try:
            (root / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    home_ready = True
    return root


def settings_file() -> Path:
    """Where the mirror of the addon's own settings lives inside the VM."""
    return home_root() / "config" / SETTINGS_NAME


def mirror_settings() -> dict:
    """Write the addon's config into ``config/settings.json`` and hand back
    what landed.

    The file is read first so a key neither the form nor ``DEFAULTS`` knows
    survives unchanged, then the named keys are put over it and the whole is
    written. No password goes into this file: ``DEFAULTS`` names none, and
    anything the user has added outside the app's own schema is left exactly
    as it was found.
    """
    stored: dict = {}
    target = settings_file()
    try:
        if target.is_file():
            loaded = json.loads(target.read_text() or "{}")
            if isinstance(loaded, dict):
                stored = loaded
    except (OSError, ValueError):
        stored = {}
    for key, fallback in DEFAULTS.items():
        stored[key] = config(key, fallback)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(stored, indent=2, sort_keys=True))
    except OSError as error:
        if api is not None:
            api.log(f"berryvm: settings.json not written: {error}")
    return stored


def read_settings() -> dict:
    """The mirror file, or an empty dict when it is missing or unreadable."""
    try:
        loaded = json.loads(settings_file().read_text() or "{}")
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


#: Where the desktop remembers the icons' places, names and stashed files.
#: Separate from ``settings.json`` on purpose: the person can wipe this one
#: file to reset the layout without losing their wallpaper or terminal size.
DESK_NAME = "desktop.json"


def desk_file() -> Path:
    """The layout mirror inside the VM's own config folder."""
    return home_root() / "config" / DESK_NAME


def read_desk() -> dict:
    """The saved layout: positions, renames and the recycle list. Missing
    file or bad JSON reads back as an empty dict rather than an exception,
    so a fresh install and a broken file are the same starting point."""
    try:
        loaded = json.loads(desk_file().read_text() or "{}")
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    loaded.setdefault("positions", {})
    loaded.setdefault("renames", {})
    loaded.setdefault("recycled", [])
    return loaded


def mirror_desk() -> dict:
    """Write the in-memory layout back to ``desktop.json`` and hand it over.

    Called after every event that changes one of the three lists. The
    module keeps ``_desk_positions`` and friends as its live view so a
    drag-and-drop on the icon layer costs one write, not one per tile.
    """
    stored = {"positions": _desk_positions,
              "renames": _desk_renames,
              "recycled": _desk_recycled}
    try:
        target = desk_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(stored, indent=2, sort_keys=True))
    except OSError as error:
        if api is not None:
            api.log(f"berryvm: {DESK_NAME} not written: {error}")
    return stored


def load_desk() -> None:
    """Pull the saved layout into the module's state; a fresh start when no
    file has been written yet."""
    global _desk_positions, _desk_renames, _desk_recycled
    loaded = read_desk()
    _desk_positions = dict(loaded.get("positions") or {})
    _desk_renames = dict(loaded.get("renames") or {})
    _desk_recycled = list(loaded.get("recycled") or [])


# --- the taskbar and the clock ---

def tick_clock() -> None:
    """Put the current time on the tray; a clock turned off in the config
    writes an empty string rather than hiding the label. The label only
    hears when the line actually moves - the tick runs every thirty seconds
    and the text changes once a minute. The tray's network mark is refreshed
    here too, so SSH / SFTP changes reach the bar on the same beat."""
    global _clock_shown
    if api is None:
        return
    if not bool(config("clock_on", True)):
        text = ""
    else:
        now = datetime.now()
        text = now.strftime("%H:%M")
        # A long date on hover, the way XP's clock tooltip read: "Monday,
        # October 19, 2026" - the person gets the day without clicking.
        api.tabs.set_widget(TAB, CLOCK, "tooltip",
                             now.strftime("%A, %B %d, %Y"))
    if text != _clock_shown:
        _clock_shown = text
        api.tabs.set_widget(TAB, CLOCK, "text", text)
    _update_net_tray()


def open_window_ids() -> list:
    """The ids the desktop is holding, in the order it opened them. The icon
    layer lives among them - it is a window like any other - and every
    caller that shows the list to a person filters it out."""
    if api is None:
        return []
    try:
        return list(api.tabs.widget_value(TAB, DESKTOP, "windows") or [])
    except Exception:
        return []


def program_window_ids() -> list:
    """The desktop's windows with the icon layer left out: the rows a person
    is offered, and the windows a Log Off shuts."""
    return [win_id for win_id in open_window_ids() if win_id != ICONS]


def caption_for(win_id: str) -> str:
    """What the taskbar button for one window says: the opened record's title,
    or a program caption when the id is one of those that ship."""
    record = opened.get(win_id)
    if record is not None:
        return str(record.get("title") or CAPTIONS.get(win_id, win_id))
    if win_id in CAPTIONS:
        return CAPTIONS[win_id]
    return win_id


def icon_for(win_id: str) -> str:
    """The mark a window's taskbar button wears: the picture its kind is known
    by, or the program's own tile mark when the id is one that ships. A window
    the addon has no record for falls back to the plain file mark."""
    record = opened.get(win_id) or {}
    kind = str(record.get("kind") or "")
    if kind in KIND_ICONS:
        return KIND_ICONS[kind]
    if win_id in TILE_ICONS:
        return TILE_ICONS[win_id]
    return ICON_FILE


def refresh_taskbar() -> None:
    """Redraw the row of window buttons from what the desktop reports.

    The bar is a slim horizontal strip: each button is its window's mark
    beside its caption, and the list is pinned to one button's height so it
    does not fall back on Qt's tall default (192px) and swallow the desktop
    it sits on. The height and mark size ride the desktop scale, so a bigger
    desktop grows a bigger bar - and a scale change, which re-runs this
    through :func:`relayout_desktop`, moves them with it.

    A minimized window wears a pressed-shaped button so its difference
    shows at a glance; the flag joins the memo tuple so the row is
    rewritten the moment a frame hides or comes back.

    Dragging or sizing a window re-runs this on every mouse move through
    the desktop's report, and none of that changes a button, so the whole
    rewrite is skipped when the bar has already been told this exact row.
    """
    global _taskbar_shown
    if api is None:
        return
    row = tuple((caption_for(win_id), icon_for(win_id),
                 bool(minimized.get(win_id)))
                for win_id in program_window_ids())
    state = (scaled(TASKBAR_H), scaled(TASKBAR_ICON), row)
    if state == _taskbar_shown:
        return
    _taskbar_shown = state
    api.tabs.set_widget(TAB, TASK_LIST, "height", state[0])
    api.tabs.set_widget(TAB, TASK_LIST, "icon_size", state[1])
    api.tabs.set_widget(TAB, TASK_LIST, "items",
                        [{"text": cap, "icon": mark, "pressed": is_min}
                         for cap, mark, is_min in row])


def picked_window() -> str:
    """The window the taskbar is on: the picked caption mapped back
    through the desktop's own order, so a rename never points at the wrong
    frame."""
    if api is None:
        return ""
    try:
        found = api.tabs.widget_value(TAB, TASK_LIST, "selected") or []
    except Exception:
        return ""
    win_ids = program_window_ids()
    texts = [caption_for(win_id) for win_id in win_ids]
    for item in found:
        text = str(item or "")
        if text in texts:
            return win_ids[texts.index(text)]
    return ""


def flash(win_id: str, reason: str = "", secs: float = 6.0) -> None:
    """Ask the taskbar button beside a window to blink for a while.

    An entry in :data:`attention` holds the monotonic second the flash
    stops at, so :func:`tick_flash` can drop it once the interest is stale
    and the addon never has to remember to unhook anything. The reason is
    kept for a tooltip a future pass may want to write on the button.
    """
    global _flash_on
    if not win_id or api is None:
        return
    import time as _time
    attention[str(win_id)] = {"until": _time.monotonic() + float(secs),
                              "reason": str(reason or "")}
    _flash_on = True
    _write_attention(force=True)


def clear_flash(win_id: str) -> None:
    """End a window's flash now, because the person has come back to it."""
    if attention.pop(str(win_id), None) is not None:
        _write_attention(force=True)


def tick_flash() -> None:
    """Half-second heartbeat: drop any expired entry, flip the blink on
    and off, and hand the visible row to the host's list ``attention``
    prop. A no-op when nothing is flashing, so a quiet desktop pays
    nothing for the timer running."""
    global _flash_on
    if api is None or not attention:
        if _flash_on:
            _flash_on = False
            _write_attention(force=True)
        return
    import time as _time
    now = _time.monotonic()
    stale = [win_id for win_id, rec in attention.items()
             if float(rec.get("until", 0)) <= now]
    for win_id in stale:
        attention.pop(win_id, None)
    _flash_on = not _flash_on
    _write_attention()


def _write_attention(force: bool = False) -> None:
    """Paint the current attention list onto the taskbar's own row.

    A tick that would repeat the same visible state is skipped unless
    ``force`` says otherwise, so an idle half-second costs no widget
    write. The prop names each flashing window's caption rather than its
    id, because that is what the row uses as its key.
    """
    if api is None:
        return
    if _flash_on or force:
        names = [caption_for(win_id) for win_id in attention
                 if win_id in opened or win_id in CAPTIONS]
    else:
        names = []
    api.tabs.set_widget(TAB, TASK_LIST, "attention", names)


# --- the desktop's own calls ---

def open_window(entry: dict) -> None:
    """Hand the desktop a window descriptor; opening an id that is already
    there is what the primitive already handles - the frame is raised, not
    rebuilt - so a running shell keeps its scrollback."""
    api.tabs.set_widget(TAB, DESKTOP, "open_window", entry)


def close_window(win_id: str) -> None:
    """Take one window off the desktop by its id."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "close_window", str(win_id))


def raise_window(win_id: str) -> None:
    """Bring one window to the front and show it if it had been hidden. The
    icon layer is kept back down afterwards, because raising anything means
    the person wants to look at the other thing."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "raise_window", str(win_id))
        if win_id != ICONS:
            keep_icons_behind()


def lower_window(win_id: str) -> None:
    """Send one window behind every other - the way a desktop's wallpaper
    layer is kept under the frames."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "lower_window", str(win_id))


def keep_icons_behind() -> None:
    """Re-stow the icon layer under the frames. Raising a window is the only
    thing that can push it up by accident, so every raise asks the canvas to
    put it back."""
    if api is not None and ICONS in open_window_ids():
        lower_window(ICONS)


def hide_window(win_id: str) -> None:
    """Put a window out of sight without losing what it holds: the taskbar's
    raise is how it comes back."""
    if api is not None and win_id:
        api.tabs.set_widget(TAB, win_id, "visible", False)


def forget_window(win_id: str) -> None:
    """Drop every bit of addon-side state that a closed window owned."""
    win_id = str(win_id)
    opened.pop(win_id, None)
    berrypads.pop(win_id, None)
    viewers.pop(win_id, None)
    record = consoles.pop(win_id, None)
    if record is not None:
        _stop_shell(record)
    for term_id in [tid for tid, owner in terminals.items()
                    if str(owner) == win_id]:
        terminals.pop(term_id, None)
    if win_id == P_EXPLORER:
        # The Explorer's two panes are the only tables that live straight on
        # the tab id; a machine window's picks die with its own record.
        for pane in (PANE_LOCAL, PANE_REMOTE):
            pane_pick.pop(f"{pane}_table", None)
            pane_rows.pop(f"{pane}_table", None)
            pane_path.pop(pane, None)


# --- painting: the theme and the icon layer ---

def apply_theme() -> None:
    """Put both sheets on: the desktop's (wallpaper and every window inside
    it) and the taskbar's. Called at init and again whenever Display
    Properties says Apply."""
    if api is None:
        return
    api.tabs.set_widget(TAB, DESKTOP, "style", desktop_sheet())
    api.tabs.set_widget(TAB, TASKBAR, "style", taskbar_sheet())
    if ICONS in open_window_ids():
        api.tabs.set_widget(TAB, ICONS, "style", ICON_FRAME_SHEET)


def icon_layer_spec() -> dict:
    """The tile grid the icon layer window holds: an XP run-down the left
    edge, sized from the desktop scale."""
    return {
        "kind": "column",
        "children": [
            {"kind": "list", "id": ICON_GRID,
             "object_name": "berryDeskGrid",
             "grid": True, "icon_size": scaled(48), "tile_width": scaled(92),
             "spacing": scaled(10), "expand": "both",
             "movement": "free", "renamable": True,
             "on": ["selected", "activated", "context", "moved", "renamed"],
             "tooltip": "Double-click a program to open it; right-click for "
                        "the desktop's menu. A tile can be dragged anywhere "
                        "and renamed in place."},
        ],
    }


def open_icon_layer() -> None:
    """Stand the icon layer up: a window with no title bar, no close button
    and no corner grip, opened at the canvas's top-left, sized far past any
    desktop it lands on, and lowered the moment it exists.

    The over-wide frame is deliberate: the canvas clips it, the wallpaper
    shows through its transparent styling, and the tiles get a column's
    worth of room that no drag can shrink away - ``min_size`` pins the floor
    so even a hand on the grip cannot crush the layer into nothing (there is
    no grip to grab either way).
    """
    if api is None:
        return
    open_window({
        "id": ICONS, "title": "icons", "chrome": False, "closable": False,
        "resizable": False, "lower": True, "x": 0, "y": 0,
        "width": 2400, "height": 1500,
        "min_size": [2400, 1500],
        "spec": icon_layer_spec(),
    })
    opened[ICONS] = {"title": "icons", "kind": "icon_layer"}
    draw_desk_icons()
    api.tabs.set_widget(TAB, ICONS, "style", ICON_FRAME_SHEET)


def draw_desk_icons() -> None:
    """Lay the program tiles down, in the order ``PROGRAMS`` names them. A
    scale change re-runs this against the rebuilt layer.

    Each tile carries a persistent ``name`` - the caption it was shipped
    with - beside the cells it shows, so a rename does not lose the position
    the person put on the tile. ``pos`` comes from the layout mirror when
    one was saved; a tile that has never been moved is left for Qt's own
    cascade to seat.
    """
    if api is None:
        return
    tiles = []
    for pid, caption in PROGRAMS:
        shown = str(_desk_renames.get(caption) or caption)
        row = {"cells": [shown], "name": caption,
               "icon": TILE_ICONS.get(pid, ICON_FILE),
               "tip": f"Double-click to open {shown}"}
        saved = _desk_positions.get(caption)
        if isinstance(saved, (list, tuple)) and len(saved) >= 2:
            row["pos"] = [int(saved[0]), int(saved[1])]
        tiles.append(row)
    api.tabs.set_widget(TAB, ICON_GRID, "rows", tiles)
    if _desk_positions:
        api.tabs.set_widget(TAB, ICON_GRID, "positions", _desk_positions)


def _resolve_caption(text: str) -> str:
    """Map a tile's display text back to the program id it names. A rename
    maps the shown word to the original caption first, so a tile that used
    to say ``Notepad`` and now says ``Scratch`` still opens the editor."""
    key = str(text or "")
    if key in BY_CAPTION:
        return BY_CAPTION[key]
    for original, renamed in _desk_renames.items():
        if str(renamed) == key and original in BY_CAPTION:
            return BY_CAPTION[original]
    return ""


def _unique_rename(wanted: str) -> str:
    """A rename that would collide with another program's caption gets a
    numeric tail, so a tile never becomes indistinguishable from another."""
    base = str(wanted or "").strip()
    if not base:
        return base
    taken = {cap for _pid, cap in PROGRAMS} | set(_desk_renames.values())
    if base not in taken:
        return base
    counter = 2
    while f"{base} ({counter})" in taken:
        counter += 1
    return f"{base} ({counter})"


def _apply_rename(key: str, new_name: str, old: str) -> None:
    """Commit one tile's inline rename: update the ``renames`` map keyed by
    the tile's persistent name, and carry any saved position over from the
    old display name so a rename after a move keeps the layout. A blank
    line is a cancel; a name that collides gets a tail.

    The rename is not a reprogram: a tile whose caption used to be
    ``Notepad`` still opens Notepad, only under a different word. That is
    what :func:`_resolve_caption` above undoes on the next double-click."""
    global _desk_renames, _desk_positions
    if not key:
        return
    wanted = str(new_name or "").strip()
    if not wanted:
        # An empty label is Qt's cancel signal; redraw so the old name is
        # put back rather than leaving the tile blank.
        draw_desk_icons()
        return
    if wanted == str(_desk_renames.get(key) or key):
        return
    wanted = _unique_rename(wanted)
    _desk_renames[key] = wanted
    if old and old != key and old in _desk_positions:
        _desk_positions[key] = _desk_positions.pop(old)
    mirror_desk()
    draw_desk_icons()


def switcher_menu() -> None:
    """Alt+Tab: offer the person a pick of every program window on the
    desktop, and raise whichever one they name. A hidden frame is restored
    first, so a switch is also a way out of a minimize a moment ago; any
    flash on the chosen window is cleared because the person has come back
    to it deliberately.

    The shortcut is a Qt ``WindowShortcut`` and fires only while Berry
    Manager itself has keyboard focus; no OS-level grab is offered to
    addons."""
    if api is None:
        return
    win_ids = program_window_ids()
    if not win_ids:
        return
    labels = [caption_for(win_id) for win_id in win_ids]
    picked = api.ui.choose(labels)
    if picked is None:
        return
    try:
        index = labels.index(str(picked))
    except ValueError:
        return
    win_id = win_ids[index]
    if minimized.pop(win_id, None):
        api.tabs.set_widget(TAB, DESKTOP, "restore_window",
                            {"id": win_id})
        refresh_taskbar()
    raise_window(win_id)
    clear_flash(win_id)


# --- the Recycle Bin: a folder under the desktop's own tree ---

#: The stash folder inside ``Desktop/`` - the leading dot hides it from the
#: Explorer's local pane listing, and the record list keeps track of where
#: each file came from so a restore can put it back.
RECYCLE_DIR = ".Recycle"


def recycle_root() -> Path:
    """Where the stashed files live. The home folder is created on first
    use so a Recycle Bin opened before any Explorer run still finds room."""
    return _ensure_home() / "Desktop" / RECYCLE_DIR


def recycle_file(path: Any, name: str = "") -> bool:
    """Move one local file into ``.Recycle/`` and remember where it came
    from. A clash on the target name grows a numeric tail, the same way a
    download into the Desktop folder does. Returns True on success."""
    import shutil as _shutil
    source = Path(str(path or ""))
    if not source.exists():
        return False
    target_dir = recycle_root()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        landed = target_dir / source.name
        counter = 2
        while landed.exists():
            landed = target_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        _shutil.move(str(source), str(landed))
    except (OSError, _shutil.Error) as error:
        if api is not None:
            api.ui.notify(f"berryvm: could not recycle {source.name}: {error}")
        return False
    _desk_recycled.append({
        "name": landed.name,
        "from": str(source),
        "deleted": datetime.now().isoformat(timespec="seconds"),
    })
    mirror_desk()
    if api is not None:
        api.ui.popup("Recycle Bin",
                     f"Moved {name or source.name} to Recycle Bin")
    draw_recycle()
    return True


def restore_recycled(name: str) -> bool:
    """Move one stashed file back to the folder it came from, or to
    ``Desktop/`` when that folder is gone. The record is dropped either
    way - a vanished file is not something to keep chasing."""
    import shutil as _shutil
    record = next((r for r in _desk_recycled
                   if str(r.get("name") or "") == str(name or "")), None)
    if not record:
        return False
    source = recycle_root() / str(record.get("name") or "")
    if not source.is_file():
        _desk_recycled.remove(record)
        mirror_desk()
        draw_recycle()
        return False
    original = Path(str(record.get("from") or ""))
    target_dir = original.parent if original.parent.is_dir() else (
        _ensure_home() / "Desktop")
    target = target_dir / original.name
    counter = 2
    while target.exists():
        target = target_dir / (f"{original.stem}_{counter}{original.suffix}")
        counter += 1
    try:
        _shutil.move(str(source), str(target))
    except (OSError, _shutil.Error) as error:
        if api is not None:
            api.ui.notify(f"berryvm: could not restore: {error}")
        return False
    _desk_recycled.remove(record)
    mirror_desk()
    if api is not None:
        api.ui.popup("Recycle Bin",
                     f"Restored {original.name} to {target_dir}")
    draw_recycle()
    return True


def empty_recycle(force: bool = False) -> int:
    """Delete every file ``.Recycle/`` holds; the person is asked first
    unless ``force`` says otherwise. A folder inside the stash (from a
    moved directory) is walked the same way. Returns how many entries
    were cleared."""
    import shutil as _shutil
    if api is None:
        return 0
    if not force and not api.ui.question(
            "Recycle Bin",
            "Permanently delete everything in the Recycle Bin?"):
        return 0
    global _desk_recycled
    root = recycle_root()
    count = 0
    if root.is_dir():
        for entry in root.iterdir():
            try:
                if entry.is_dir():
                    _shutil.rmtree(entry)
                else:
                    entry.unlink()
                count += 1
            except OSError:
                pass
    _desk_recycled = []
    mirror_desk()
    if api is not None and count:
        api.ui.popup("Recycle Bin", f"{count} item(s) deleted")
    draw_recycle()
    return count


def recycle_spec(win_id: str) -> dict:
    """The Recycle Bin window: a table of stashed files with the buttons
    that act on the picked one. A double-click on a row is the shortcut
    a person expects: restore."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": f"{win_id}_hint",
         "text": "Files you deleted from the desktop. Double-click one to "
                 "put it back.", "word_wrap": True},
        {"kind": "table", "id": f"{win_id}_table",
         "columns": ["Name", "From", "Deleted"],
         "on": ["activated", "selected"], "expand": "both"},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{win_id}_restore", "text": "Restore",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": f"{win_id}_delete", "text": "Delete",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": f"{win_id}_empty", "text": "Empty all",
             "on": ["clicked"], "expand": True},
        ]},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def draw_recycle() -> None:
    """Refresh the Recycle Bin's table with what the record list holds.
    Skipped silently when the window is not on the desktop."""
    if api is None or P_RECYCLE not in opened:
        return
    rows = [{"cells": [str(r.get("name") or ""),
                       str(r.get("from") or ""),
                       str(r.get("deleted") or "")]} for r in _desk_recycled]
    api.tabs.set_widget(TAB, f"{P_RECYCLE}_table", "rows", rows)


def open_recycle() -> str:
    """Bring the Recycle Bin window up, and fill its table from the record
    list. A second click just re-reads what is already there."""
    if api is None:
        return ""
    title = CAPTIONS[P_RECYCLE]
    open_window(_program_entry(P_RECYCLE, title, recycle_spec(P_RECYCLE),
                               scaled(520), scaled(320)))
    opened[P_RECYCLE] = {"title": title, "kind": "recycle"}
    refresh_taskbar()
    draw_recycle()
    return P_RECYCLE


def relayout_desktop() -> None:
    """Rebuild the icon layer from the current scale: the tile sizes are
    build-time numbers, so the window shuts and stands up again."""
    if api is None:
        return
    if ICONS in open_window_ids():
        close_window(ICONS)
    opened.pop(ICONS, None)
    open_icon_layer()
    refresh_taskbar()


def desktop_menu(text: str) -> None:
    """The right-click on the icon layer. A tile's name opens its menu -
    just ``Open``, which is what a tile's menu ever offered - and bare
    wallpaper gets the desktop's own: the New family, the two resets that
    put the icon layer back to rights, the Recycle Bin, and the plain
    refresh."""
    if api is None:
        return
    if text:
        picked = api.ui.choose(["Open", "Rename", "Delete"])
        if picked == "Open":
            pid = _resolve_caption(text)
            if pid:
                start_program(pid)
        elif picked == "Rename":
            fresh = api.ui.input("Rename", "New name", text)
            fresh = str(fresh or "").strip()
            if fresh and fresh != text:
                key = next((orig for orig, renamed in _desk_renames.items()
                            if str(renamed) == text), text)
                if key in BY_CAPTION or text in BY_CAPTION:
                    _apply_rename(key if key in BY_CAPTION else text,
                                  fresh, text)
        elif picked == "Delete":
            # A tile is only a shortcut; deleting it drops a note rather
            # than the Recycle Bin, so a stray right-click is undoable by
            # the desktop's own Reset Icon Names.
            _note(ICONS, f"{text} is a program tile; leave it on the "
                         f"desktop or Reset Icon Names to bring back a "
                         f"renamed one.")
        return
    picked = api.ui.choose([
        "Open My Computer", "Notepad", "Change Desktop Background...",
        "New Text Document", "New Folder",
        "Auto Arrange Icons", "Reset Icon Names",
        "Open Recycle Bin", "Empty Recycle Bin",
        "Refresh",
    ])
    if picked == "Open My Computer":
        start_program(P_EXPLORER)
    elif picked == "Notepad":
        start_program(P_NOTEPAD)
    elif picked == "Change Desktop Background...":
        start_program(P_SETTINGS)
    elif picked == "New Text Document":
        new_desk_note()
    elif picked == "New Folder":
        new_desk_folder()
    elif picked == "Auto Arrange Icons":
        _desk_positions.clear()
        mirror_desk()
        draw_desk_icons()
    elif picked == "Reset Icon Names":
        _desk_renames.clear()
        mirror_desk()
        draw_desk_icons()
    elif picked == "Open Recycle Bin":
        start_program(P_RECYCLE)
    elif picked == "Empty Recycle Bin":
        empty_recycle()
    elif picked == "Refresh":
        relayout_desktop()
        apply_theme()


def _tray_stub(name: str) -> None:
    """A tray icon was pressed. BerryVM has no OS handle for a real network
    centre, so the mark for the network button reads what the app itself
    can see - the SSH and SFTP sessions - and the Web Browser button opens
    the browser window; anything else falls back to a plain "not wired"
    note."""
    if api is None:
        return
    if name == "tray_wbr":
        start_program(P_BROWSER)
        return
    if name == "tray_net":
        api.ui.popup("Network", _net_summary())
        return
    api.ui.popup("Not wired",
                 f"BerryVM shows a {name} mark for shape but cannot read "
                 f"the real state - no OS handle is exposed to addons.")


def _net_summary() -> str:
    """The three lines the tray's network popup says: SSH, SFTP, and what
    the desktop thinks WiFi is. The addon has no handle for the machine's
    wireless radio - Berry Manager itself is a network client, so a WiFi
    mark reflects whether the app reached its device at all."""
    ssh = sftp = False
    host = ""
    if api is not None:
        try:
            ssh = bool(api.device.is_connected("ssh"))
        except Exception:                       # a headless run has no device
            ssh = False
        try:
            sftp = bool(api.device.is_connected("sftp"))
        except Exception:
            sftp = False
        try:
            profile = api.session.profile() or {}
            host = str(profile.get("host") or "")
        except Exception:
            host = ""
    # WiFi is not readable from an addon, but a device that answers SSH
    # is on a network the app can reach: same link, said twice.
    wifi = ssh or sftp
    lines = [
        f"WiFi: {'connected' if wifi else 'offline'}",
        f"SSH:  {'connected' if ssh else 'offline'}"
        + (f" ({host})" if ssh and host else ""),
        f"SFTP: {'connected' if sftp else 'offline'}",
    ]
    return "\n".join(lines)


def _update_net_tray() -> None:
    """Put the current SSH / SFTP / WiFi answer on the network mark. The
    glyph changes when the link does, so a glance at the bar says whether
    the device is reachable; the tooltip spells the three lines out."""
    if api is None:
        return
    text = _net_summary()
    wifi = "WiFi: connected" in text
    ssh = "SSH:  connected" in text
    # A filled circle for anything live, a hollow one for none; SSH on top
    # of WiFi gets the quarter-split disk so the two are told apart.
    glyph = "\u25d0" if (ssh and wifi) else "\u25cf" if wifi else "\u25cb"
    api.tabs.set_widget(TAB, "tray_net", "text", glyph)
    api.tabs.set_widget(TAB, "tray_net", "tooltip", text)


def new_desk_folder() -> str:
    """A fresh ``New Folder`` (numbered when one exists) in the VM's
    Desktop folder. Cheap and XP-shaped: it does not need a round trip
    through the Explorer to sit next to the tiles."""
    if api is None:
        return ""
    root = _ensure_home() / "Desktop"
    target = root / "New Folder"
    counter = 2
    while target.exists():
        target = root / f"New Folder {counter}"
        counter += 1
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        api.ui.notify(f"berryvm: could not create folder: {error}")
        return ""
    _note(ICONS, f"created {target.name}")
    return str(target)


def new_desk_note() -> str:
    """A fresh ``Note.txt`` (numbered when one exists) in the VM's Desktop
    folder, opened straight into the editor - the desktop's New context."""
    if api is None:
        return ""
    root = _ensure_home() / "Desktop"
    target = root / "Note.txt"
    counter = 2
    while target.exists():
        target = root / f"Note{counter}.txt"
        counter += 1
    try:
        target.write_text("")
    except OSError as error:
        api.ui.notify(f"berryvm: could not create {target.name}: {error}")
        return ""
    return open_berrypad(str(target), "local", target.name)


# --- the Start button ---

#: The lines under the programs: a divider, then the desktop's own errands.
START_EXTRA = ["", "Change Desktop Background...", "About BerryVM", "Log Off"]


def start_menu() -> None:
    """The Start button's own menu: every program it can bring up, then the
    desktop errands. A real XP menu painted at the mouse - which is exactly
    what ``api.ui.choose`` is."""
    if api is None:
        return
    labels = [CAPTIONS[pid] for pid, _cap in PROGRAMS]
    picked = api.ui.choose(labels + START_EXTRA)
    start_choice(picked)


def start_choice(picked: Any) -> None:
    """Act on one Start-menu answer by its caption; ``None`` means the person
    pressed Escape and nothing happens at all."""
    if api is None or picked is None:
        return
    text = str(picked)
    if text in BY_CAPTION:
        start_program(BY_CAPTION[text])
    elif text == "Change Desktop Background...":
        start_program(P_SETTINGS)
    elif text == "About BerryVM":
        about_box()
    elif text == "Log Off":
        log_off()


def start_program(win_id: str) -> None:
    """Dispatch on the id a Start-menu row or a desktop tile named; each
    program is the same single call, so a raised window and a fresh one look
    the same to the taskbar."""
    if api is None:
        return
    if win_id == P_EXPLORER:
        open_explorer()
    elif win_id == P_NOTEPAD:
        open_berrypad("", "scratch", "Untitled")
    elif win_id == P_VIEWER:
        open_viewer()
    elif win_id == P_CONSOLE:
        open_console()
    elif win_id == P_MAIN:
        open_main_ssh()
    elif win_id == P_MACHINES:
        open_machines()
    elif win_id == P_SETTINGS:
        open_settings()
    elif win_id == P_CALC:
        open_calc()
    elif win_id == P_RECYCLE:
        open_recycle()
    elif win_id == P_BROWSER:
        open_browser()
    else:
        api.ui.notify(f"BerryVM has no program {win_id!r}.")


def about_box() -> None:
    """Start > About: what this desktop is and what is on it."""
    if api is None:
        return
    names = ", ".join(cap for _pid, cap in PROGRAMS)
    api.ui.information(
        "About BerryVM",
        f"BerryVM {ADDON.get('version')} - a Windows XP-styled desktop in "
        f"one tab of Berry Manager. Programs: {names}. File panes and "
        "pictures ride the app's main connection; every terminal brings "
        "its own socket.")


def log_off() -> None:
    """Shut every program window; the icon layer and the taskbar stay, the
    way a desktop does when its person signs out of the windows and not the
    machine."""
    if api is None:
        return
    for win_id in program_window_ids():
        close_window(win_id)
        forget_window(win_id)
    refresh_taskbar()
    api.ui.notify("BerryVM: windows closed. The desktop stays until you "
                  "close the tab.")


# --- program windows: shared bits ---

def _program_entry(win_id: str, title: str, spec: dict,
                   width: int = 0, height: int = 0) -> dict:
    """One program, wrapped as a window descriptor the primitive opens:
    default size from the config and the desktop scale, no explicit position
    so the desktop cascades them."""
    default_w, default_h = window_size()
    return {"id": win_id, "title": title,
            "width": width or default_w, "height": height or default_h,
            "spec": spec}


def _note(identifier: str, text: str = "") -> None:
    """Set one window's note line: what the last action achieved or refused,
    always kept short enough to sit under a table."""
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_note", "text", str(text or ""))


def _set_path(identifier: str, text: str) -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{identifier}_path", "text", str(text or ""))


def _selected_text(identifier: str) -> str:
    """The picked row's text, or ``""`` when nothing is picked."""
    if api is None:
        return ""
    try:
        found = api.tabs.widget_value(TAB, identifier, "selected") or []
    except Exception:
        return ""
    if isinstance(found, dict):
        return str(found.get("text") or "")
    return str(found[0]) if found else ""


# --- 1. My Computer: the two-paned file manager ---

#: What the hint line above the panes says, so nobody has to guess which
#: side is which machine.
EXPLORER_HINT = ("Left: this computer. Right: the device, over the app's "
                 "main connection. Double-click to open; right-click a row "
                 "for its menu.")

#: Pane prefix -> (origin word, display name). Everything below reads a pane
#: through this table, so the two sides differ in data source only.
PANE_KINDS = {
    PANE_LOCAL: ("local", "Local disk"),
    PANE_REMOTE: ("remote", "Device"),
}


def explorer_spec() -> dict:
    """One window, two panes, a fold line between them: the columns of the
    panes are built by the same function with a different prefix, so an
    action means the same thing on both sides."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": f"{P_EXPLORER}_hint", "text": EXPLORER_HINT,
         "word_wrap": True},
        {"kind": "splitter", "sizes": [1, 1], "children": [
            _pane_column(PANE_LOCAL, "This computer"),
            _pane_column(PANE_REMOTE, "Device (main connection)"),
        ]},
        {"kind": "label", "id": f"{P_EXPLORER}_note", "text": "",
         "word_wrap": True},
    ]}


def _pane_column(pane: str, heading: str) -> dict:
    """One side of the Explorer: where it is, the rows it holds, and the
    buttons that act on the picked one."""
    transfer = ("Upload \u2192" if pane == PANE_LOCAL
                else "\u2190 Download")
    extra = ([{"kind": "button", "id": f"{pane}_run",
               "text": "Run on device", "on": ["clicked"],
               "tooltip": "Upload the picked file to the device's tmp and "
                          "run it there"}]
             if pane == PANE_LOCAL else [])
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{pane}_head", "text": heading,
             "word_wrap": True},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{pane}_path", "text": "",
             "expand": True, "word_wrap": True},
            {"kind": "button", "id": f"{pane}_up", "text": "Up",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_home", "text": "Home",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_refresh", "text": "Refresh",
             "on": ["clicked"]},
        ]},
        {"kind": "table", "id": f"{pane}_table",
         "columns": ["Name", "Size"], "on": ["selected", "activated",
                                             "context"],
         "expand": "both",
         "tooltip": "Double-click a folder to walk in or a file to open it; "
                    "right-click for the item's menu."},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{pane}_open", "text": "Open",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_new", "text": "New Folder",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_rename", "text": "Rename",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_delete", "text": "Delete",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_send", "text": transfer,
             "on": ["clicked"],
             "tooltip": ("Copy the picked file into the folder the other "
                         "pane is showing")},
            *extra,
        ]},
    ]}


def open_explorer() -> str:
    """Bring My Computer up, or raise the window that is already open, and
    read both panes from wherever they last stood."""
    if api is None:
        return ""
    title = CAPTIONS[P_EXPLORER]
    width, height = window_size()
    open_window(_program_entry(P_EXPLORER, title, explorer_spec(),
                               width * 2, height))
    opened[P_EXPLORER] = {"title": title, "kind": "explorer"}
    refresh_taskbar()
    # Where each side stands between openings: home here, the device's root
    # there, unless a listing earlier in this session said otherwise.
    pane_path.setdefault(PANE_LOCAL, str(Path.home()))
    pane_path.setdefault(PANE_REMOTE, "")
    pane_list(PANE_LOCAL, pane_path[PANE_LOCAL])
    pane_list(PANE_REMOTE, pane_path[PANE_REMOTE])
    return P_EXPLORER


def pane_meta(pane: str, name: str) -> dict:
    """What a row of one pane really is: the path and kind the listing kept
    beside the words. Names are unique inside a folder, which is the only
    promise this map needs."""
    return (pane_rows.get(f"{pane}_table") or {}).get(str(name)) or {}


def pane_picked(pane: str) -> str:
    return str(pane_pick.get(f"{pane}_table") or "")


def pane_list(pane: str, where: str) -> None:
    """Read one folder into one pane; the local side walks this machine and
    the remote side the main connection, and the two disagree loudly rather
    than quietly when a listing cannot be had."""
    if api is None:
        return
    if pane == PANE_LOCAL:
        _pane_list_local(where or str(Path.home()))
    else:
        _pane_list_remote(where)


def _pane_list_local(where: str) -> None:
    target = Path(str(where or ".")).expanduser()
    pane_path[PANE_LOCAL] = str(target)
    _set_path(PANE_LOCAL, str(target))
    try:
        names = sorted((p.name for p in target.iterdir()), key=str.lower)
    except (OSError, ValueError) as error:
        pane_rows[f"{PANE_LOCAL}_table"] = {}
        api.tabs.set_widget(TAB, f"{PANE_LOCAL}_table", "rows", [])
        _note(P_EXPLORER, f"local pane cannot read this folder: {error}")
        return
    rows: dict = {}
    laid: list[dict] = []
    for name in names:
        child = target / name
        try:
            is_dir = child.is_dir()
            size = child.stat().st_size if child.is_file() else 0
            mode = child.stat().st_mode if child.exists() else 0
        except OSError:
            is_dir, size, mode = False, 0, 0
        rows[name] = {"path": str(child), "directory": bool(is_dir),
                      "size": size, "mode": mode}
        laid.append({"cells": [name, "" if is_dir else describe_size(size)],
                     "icon": file_icon(name, is_dir)})
    pane_rows[f"{PANE_LOCAL}_table"] = rows
    api.tabs.set_widget(TAB, f"{PANE_LOCAL}_table", "rows", laid)
    _note(P_EXPLORER, f"local: {len(laid)} item(s)")


def _pane_list_remote(where: str) -> None:
    rel = str(where or "").strip("/")
    pane_path[PANE_REMOTE] = rel
    _set_path(PANE_REMOTE, f"device: /{rel}")
    if not api.device.is_connected("sftp"):
        pane_rows[f"{PANE_REMOTE}_table"] = {}
        api.tabs.set_widget(TAB, f"{PANE_REMOTE}_table", "rows", [])
        _note(P_EXPLORER, "device pane: the main connection is not open - "
                          "connect it on the Connection panel and press "
                          "Refresh.")
        return
    try:
        entries = api.device.list(rel) or []
    except Exception as error:
        _note(P_EXPLORER, f"device pane could not read that folder: {error}")
        return
    rows: dict = {}
    laid: list[dict] = []
    for entry in sorted(entries, key=lambda e: (not e.get("directory"),
                                                str(e.get("name") or ""))):
        name = str(entry.get("name") or "")
        if not name:
            continue
        is_dir = bool(entry.get("directory"))
        rows[name] = {"path": str(entry.get("path") or name),
                      "directory": is_dir, "size": entry.get("size", 0)}
        laid.append({"cells": [name,
                               "" if is_dir else describe_size(
                                   entry.get("size", 0))],
                     "icon": file_icon(name, is_dir)})
    pane_rows[f"{PANE_REMOTE}_table"] = rows
    api.tabs.set_widget(TAB, f"{PANE_REMOTE}_table", "rows", laid)
    _note(P_EXPLORER, f"device: {len(laid)} item(s)")


def pane_enter(pane: str, name: str) -> None:
    """Walk one pane into the folder its row names - or open the file, which
    is what the Open button and a double-click both come down to."""
    meta = pane_meta(pane, name)
    if not meta:
        _note(P_EXPLORER, "Select an item first.")
        return
    if meta.get("directory"):
        pane_list(pane, str(meta.get("path") or ""))
        return
    open_item(pane, name, meta)


def pane_up(pane: str) -> None:
    here = str(pane_path[pane] or "")
    if pane == PANE_LOCAL:
        parent = str(Path(here).parent)
        pane_list(pane, parent if parent and parent != here else here)
    else:
        pane_list(pane, posixpath.dirname(here.rstrip("/")))


def pane_home(pane: str) -> None:
    pane_list(pane, str(Path.home()) if pane == PANE_LOCAL else "")


def pane_open_picked(pane: str) -> None:
    name = pane_picked(pane)
    if not name:
        name = _selected_text(f"{pane}_table")
    if not name:
        _note(P_EXPLORER, "Select an item first.")
        return
    pane_enter(pane, name)


def open_item(pane: str, name: str, meta: dict) -> None:
    """Send a picked file to the program that can do something with it:
    pictures to the viewer, text to the editor, and everything else gets a
    saying rather than a crash or a blank window."""
    origin = PANE_KINDS[pane][0]
    path = str(meta.get("path") or name)
    if meta.get("directory"):
        pane_list(pane, path)
        return
    if is_picture(name):
        open_viewer(path, origin)
    elif is_text(name):
        open_berrypad(path, origin, name)
    else:
        _note(P_EXPLORER, f"{name} is neither a picture nor text BerryVM "
                          "can open - use Upload/Download or Run instead.")


def pane_new_folder(pane: str) -> None:
    name = api.ui.input("New folder", "Folder name", "New Folder")
    name = str(name or "").strip().strip("/\\")
    if not name:
        return
    if pane == PANE_LOCAL:
        try:
            (Path(pane_path[pane]) / name).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _note(P_EXPLORER, f"could not create the folder: {error}")
            return
        _note(P_EXPLORER, f"{name} created")
        pane_list(pane, pane_path[pane])
        return
    rel = posixpath.join(pane_path[pane] or "", name)
    try:
        api.device.mkdir(rel)
    except Exception as error:
        _note(P_EXPLORER, f"could not create the folder on the device: "
                          f"{error}")
        return
    _note(P_EXPLORER, f"{name} created on the device")
    pane_list(pane, pane_path[pane])


def pane_rename(pane: str) -> None:
    name = pane_picked(pane)
    meta = pane_meta(pane, name)
    if not meta:
        _note(P_EXPLORER, "Select an item to rename.")
        return
    fresh = api.ui.input("Rename", "New name", name)
    fresh = str(fresh or "").strip()
    if not fresh or fresh == name:
        return
    if pane == PANE_LOCAL:
        try:
            Path(meta["path"]).rename(Path(pane_path[pane]) / fresh)
        except OSError as error:
            _note(P_EXPLORER, f"could not rename: {error}")
            return
    else:
        old = str(meta["path"])
        new = posixpath.join(posixpath.dirname(old.rstrip("/")), fresh)
        try:
            api.device.rename(old, new)
        except Exception as error:
            _note(P_EXPLORER, f"could not rename on the device: {error}")
            return
    pane_list(pane, pane_path[pane])


def pane_delete(pane: str) -> None:
    name = pane_picked(pane)
    meta = pane_meta(pane, name)
    if not meta:
        _note(P_EXPLORER, "Select an item to delete.")
        return
    kind = "folder" if meta.get("directory") else "file"
    if pane == PANE_LOCAL and not meta.get("directory"):
        # A local file is stashed in the Recycle Bin rather than shredded,
        # so the person can fish it out from the desktop's own icon.
        if recycle_file(str(meta["path"]), name):
            pane_list(pane, pane_path[pane])
        return
    if not api.ui.question("Delete",
                           f"Delete the {kind} {name!r} for good?"):
        return
    if pane == PANE_LOCAL:
        try:
            target = Path(meta["path"])
            if meta.get("directory"):
                import shutil as _shutil
                _shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as error:
            _note(P_EXPLORER, f"could not delete: {error}")
            return
    else:
        try:
            api.device.remove(str(meta["path"]), True)
        except Exception as error:
            _note(P_EXPLORER, f"could not delete on the device: {error}")
            return
    _note(P_EXPLORER, f"{name} deleted")
    pane_list(pane, pane_path[pane])


def pane_send(pane: str) -> None:
    """Cross the picked item to the other pane: an upload puts a local file
    into the device folder the right pane shows, a download pulls a remote
    one into the folder the left pane shows."""
    name = pane_picked(pane)
    meta = pane_meta(pane, name)
    if not meta or meta.get("directory"):
        _note(P_EXPLORER, "Select a file to send across.")
        return
    if pane == PANE_LOCAL:
        target = posixpath.join(pane_path[PANE_REMOTE] or "", name)
        try:
            landed = api.device.upload(str(meta["path"]), target)
        except Exception as error:
            _note(P_EXPLORER, f"upload failed: {error}")
            return
        _note(P_EXPLORER, f"uploaded to {landed}")
        pane_list(PANE_REMOTE, pane_path[PANE_REMOTE])
        return
    into = Path(pane_path[PANE_LOCAL]) / name
    try:
        landed = api.device.download(str(meta["path"]), str(into))
    except Exception as error:
        _note(P_EXPLORER, f"download failed: {error}")
        return
    _note(P_EXPLORER, f"downloaded to {landed or into}")
    pane_list(PANE_LOCAL, pane_path[PANE_LOCAL])


def run_on_main(local: str) -> None:
    """Upload a local file to the device and run it there.

    The upload lands under the remote root's ``tmp/`` and the run asks the
    shell to invoke it with ``sh``; either failing is reported through the
    Explorer's own note, so the person sees which half went wrong rather
    than a general message on the app's bar.
    """
    if api is None:
        return
    source = Path(str(local))
    if not source.is_file():
        _note(P_EXPLORER, f"{source.name} is not a file.")
        return
    if not api.device.is_connected("ssh"):
        _note(P_EXPLORER, "The main connection is not open, so nothing can "
                          "run there.")
        return
    try:
        target = api.device.upload(str(source),
                                   posixpath.join("tmp", source.name))
    except Exception as error:
        _note(P_EXPLORER, f"upload failed: {error}")
        return
    _note(P_EXPLORER, f"uploaded to {target}, running...")

    def done(result: Any) -> None:
        code = int((result or {}).get("code", -1))
        out = str((result or {}).get("out") or "")
        err = str((result or {}).get("err") or "")
        if code == 0:
            _note(P_EXPLORER, f"{target}: ok")
        else:
            _note(P_EXPLORER,
                   f"{target} exited {code}: {err.strip() or out.strip()}")

    api.device.run_async(f"sh {target}", on_done=done, timeout=30)


def pane_run(pane: str) -> None:
    name = pane_picked(pane)
    meta = pane_meta(pane, name)
    if not meta or meta.get("directory"):
        _note(P_EXPLORER, "Select a file to run.")
        return
    run_on_main(str(meta["path"]))


def pane_menu(pane: str, name: str) -> None:
    """The right-click on one row: what the buttons can do to it, trimmed to
    what makes sense for the side it is on."""
    if not name:
        pane_menu_desktop(pane)
        return
    meta = pane_meta(pane, name)
    if not meta:
        return
    lines = ["Open"]
    if not meta.get("directory"):
        lines += ["Edit in Notepad", "View as picture"]
    lines += ["Rename", "Delete"]
    if pane == PANE_LOCAL:
        lines += ["Upload to device", "Run on device"]
    else:
        lines += ["Download to local pane"]
    picked = api.ui.choose(lines)
    if picked == "Open":
        pane_enter(pane, name)
    elif picked == "Edit in Notepad":
        open_berrypad(str(meta.get("path") or name), PANE_KINDS[pane][0],
                      name)
    elif picked == "View as picture":
        open_viewer(str(meta.get("path") or name), PANE_KINDS[pane][0])
    elif picked == "Rename":
        pane_pick[f"{pane}_table"] = name
        pane_rename(pane)
    elif picked == "Delete":
        pane_pick[f"{pane}_table"] = name
        pane_delete(pane)
    elif picked in ("Upload to device", "Download to local pane"):
        pane_pick[f"{pane}_table"] = name
        pane_send(pane)
    elif picked == "Run on device":
        pane_pick[f"{pane}_table"] = name
        pane_run(pane)


def pane_menu_desktop(pane: str) -> None:
    """A right-click on the empty part of a pane's table: the pane's own
    refresh and new folder, which need no selection."""
    picked = api.ui.choose(["Refresh", "New Folder", "Up"])
    if picked == "Refresh":
        pane_list(pane, pane_path[pane])
    elif picked == "New Folder":
        pane_new_folder(pane)
    elif picked == "Up":
        pane_up(pane)


# --- 2. Notepad: one editor window per file ---

def berry_spec(win_id: str, path: str, origin: str) -> dict:
    """The editor: a path line, Save and Save As, and a text area wearing
    the language the suffix suggests. ``origin`` is ``local``, ``remote`` or
    ``scratch`` - a buffer with no file yet."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_path",
             "text": path or "unsaved buffer", "expand": True,
             "word_wrap": True},
            {"kind": "button", "id": f"{win_id}_save", "text": "Save",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_saveas", "text": "Save As",
             "on": ["clicked"]},
        ]},
        {"kind": "multi", "id": f"{win_id}_edit",
         "highlight": language_for(path), "expand": "both"},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_berrypad(path: str, origin: str, show_title: str = "") -> str:
    """One editor window per file. Reopening a path that is already up raises
    that window rather than losing unsaved words to a new copy.

    Returns the window's id, or ``""`` when the file was refused - too big,
    or a binary as far as this editor is concerned - which the caller does
    not have to check because the note is written where the person is
    looking: the Explorer's own line when the file came from there.
    """
    global berry_seq
    if api is None:
        return ""
    wanted = str(path or "")
    if origin == "scratch":
        berry_seq += 1
        win_id = f"{BERRY_PREFIX}{berry_seq}"
        title = show_title or "Untitled"
        open_window(_program_entry(win_id, f"Notepad - {title}",
                                   berry_spec(win_id, "", origin)))
        berrypads[win_id] = {"path": "", "origin": "scratch", "title": title}
        opened[win_id] = {"title": f"Notepad - {title}", "kind": "berrypad",
                          "path": "", "origin": "scratch"}
        refresh_taskbar()
        return win_id
    for win_id, record in berrypads.items():
        if record.get("path") == wanted and record.get("origin") == origin:
            raise_window(win_id)
            return win_id
    title = show_title or short_name(wanted) or wanted
    if not is_text(wanted):
        api.ui.notify(f"{title} is not a text file the editor can open.")
        return ""
    if origin == "local":
        try:
            size = Path(wanted).stat().st_size
        except OSError:
            api.ui.notify(f"{title} could not be read.")
            return ""
        if size > MAX_SIZE:
            api.ui.notify(f"{title} is {describe_size(size)}, over the "
                          f"editor's cap of {describe_size(MAX_SIZE)}.")
            return ""
    berry_seq += 1
    win_id = f"{BERRY_PREFIX}{berry_seq}"
    open_window(_program_entry(win_id, f"Notepad - {title}",
                               berry_spec(win_id, wanted, origin)))
    berrypads[win_id] = {"path": wanted, "origin": origin, "title": title}
    opened[win_id] = {"title": f"Notepad - {title}", "kind": "berrypad",
                      "path": wanted, "origin": origin}
    refresh_taskbar()
    _berry_load(win_id, wanted, origin)
    return win_id


def _berry_load(win_id: str, path: str, origin: str) -> None:
    """Read the file into the editor. Refusals say what they are on the
    window's own note, so the person sees the reason next to the empty box."""
    if api is None:
        return
    if origin == "local":
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            _note(win_id, f"could not read: {error}")
            return
    else:
        if not api.device.is_connected("sftp"):
            _note(win_id, "The main connection is not open.")
            return
        try:
            stat = api.device.stat(path) or {}
            size = int(stat.get("size") or 0)
            if size > MAX_SIZE:
                _note(win_id, f"{describe_size(size)}, over the cap of "
                              f"{describe_size(MAX_SIZE)}")
                return
            text = api.device.read_text(path)
        except Exception as error:
            _note(win_id, f"could not read: {error}")
            return
    api.tabs.set_widget(TAB, f"{win_id}_edit", "value", str(text or ""))


def _berry_text(win_id: str) -> str:
    """What the editor's box holds right now."""
    try:
        return str(api.tabs.widget_value(TAB, f"{win_id}_edit", "value")
                   or "")
    except Exception:
        return ""


def _berry_save(win_id: str) -> None:
    """Write the editor's contents back where they came from. A local file
    keeps the same path; a remote one goes over the main connection; a
    scratch buffer has nowhere yet, so Save As is what it gets."""
    record = berrypads.get(win_id)
    if record is None or api is None:
        return
    if record.get("origin") == "scratch":
        _berry_save_as(win_id)
        return
    path = str(record.get("path") or "")
    origin = str(record.get("origin") or "local")
    text = _berry_text(win_id)
    if len(text.encode("utf-8", "replace")) > MAX_SIZE:
        _note(win_id, f"refusing to save {describe_size(len(text))} - over "
                      f"the cap of {describe_size(MAX_SIZE)}")
        return
    if origin == "local":
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            _note(win_id, f"could not write: {error}")
            return
    else:
        try:
            api.device.write_text(path, text)
        except Exception as error:
            _note(win_id, f"could not write: {error}")
            return
    stamp = datetime.now().strftime("%H:%M:%S")
    _note(win_id, f"saved {stamp}")


def _berry_save_as(win_id: str) -> None:
    """Copy the buffer to a file on this machine, picked in a Save box - the
    one way a remote file or an untitled note becomes a local one."""
    record = berrypads.get(win_id)
    if record is None or api is None:
        return
    started = str(record.get("title") or short_name(record.get("path")
                                                    or "text.txt")
                  or "text.txt")
    target = api.ui.pick_file("Save As", kind="save", initial=started)
    target = str(target or "").strip()
    if not target:
        _note(win_id, "Nothing was saved.")
        return
    text = _berry_text(win_id)
    try:
        Path(target).write_text(text, encoding="utf-8")
    except OSError as error:
        _note(win_id, f"could not write: {error}")
        return
    if record.get("origin") == "scratch":
        # The buffer has found its file: from here Save writes to it like
        # any other local document, and the title says what it is.
        name = short_name(target)
        record.update({"path": target, "origin": "local", "title": name})
        opener = opened.get(win_id)
        if opener is not None:
            opener["title"] = f"Notepad - {name}"
            opener["path"] = target
            opener["origin"] = "local"
        _set_path(win_id, target)
        refresh_taskbar()
    _note(win_id, f"saved a copy to {target}")


# --- 3. Picture Viewer ---

def viewer_spec(win_id: str, size: int) -> dict:
    """The viewer: a row of errands over the picture itself. The leaf was
    built with its maximum size, which is why zoom shuts the window and
    stands a bigger one up in its place."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_path", "text": "no picture",
             "expand": True, "word_wrap": True},
            {"kind": "button", "id": f"{win_id}_local", "text": "Open Local",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_remote", "text": "Open Remote",
             "on": ["clicked"],
             "tooltip": "Pull a picture off the device over the main "
                        "connection and show it"},
            {"kind": "button", "id": f"{win_id}_zin", "text": "Zoom +",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_zout", "text": "Zoom -",
             "on": ["clicked"]},
        ]},
        {"kind": "image", "id": f"{win_id}_pic", "size": size,
         "expand": "both"},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_viewer(path: str = "", origin: str = "local", size: int = 0) -> str:
    """Show a picture: this machine's file straight in, a remote one pulled
    down to the VM's ``tmp`` first. An empty call opens a waiting viewer with
    its Open buttons, which is what Start > Picture Viewer does.

    Reopening a path that is already up raises its viewer rather than
    stacking a second copy of the same file.
    """
    global view_seq
    if api is None:
        return ""
    wanted = str(path or "")
    for win_id, record in viewers.items():
        if wanted and record.get("path") == wanted \
                and record.get("origin") == origin:
            raise_window(win_id)
            return win_id
    if wanted and origin == "remote":
        landed = _viewer_pull(wanted)
        if not landed:
            return ""
        local_show = landed
    elif wanted:
        local_show = wanted
    else:
        local_show = ""
    view_seq += 1
    win_id = f"{VIEWER_PREFIX}{view_seq}"
    shown = size or scaled(512)
    title = f"Picture - {short_name(wanted)}" if wanted else "Picture Viewer"
    open_window(_program_entry(win_id, title, viewer_spec(win_id, shown)))
    viewers[win_id] = {"path": wanted, "origin": origin, "size": shown}
    opened[win_id] = {"title": title, "kind": "viewer", "path": wanted}
    refresh_taskbar()
    if local_show:
        api.tabs.set_widget(TAB, f"{win_id}_pic", "value", local_show)
        _set_path(win_id, wanted)
    else:
        _note(win_id, "Open a picture from this machine or from the device.")
    return win_id


def _viewer_pull(remote: str) -> str:
    """Download one remote picture into the VM's ``tmp`` and hand back the
    local path, or ``""`` with a saying when it could not be had."""
    if not api.device.is_connected("sftp"):
        api.ui.notify("The main connection is not open, so the device has "
                      "no pictures to show.")
        return ""
    into = home_root() / TMP_DIR_NAME / short_name(remote)
    try:
        landed = api.device.download(remote, str(into))
    except Exception as error:
        api.ui.notify(f"could not pull {short_name(remote)} down: {error}")
        return ""
    return str(landed or into)


def viewer_open_local(win_id: str) -> None:
    picked = api.ui.pick_file(
        "Open picture",
        filter="Pictures (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.ico);;"
               "All files (*)")
    picked = str(picked or "").strip()
    if not picked:
        return
    record = viewers.get(win_id) or {}
    _show_in(win_id, picked, int(record.get("size") or scaled(512)),
             "local", picked)


def viewer_open_remote(win_id: str) -> None:
    record = viewers.get(win_id) or {}
    shown = int(record.get("size") or scaled(512))
    wanted = api.ui.input("Open picture from the device",
                          "Path inside the remote root",
                          pane_path.get(PANE_REMOTE) or "")
    wanted = str(wanted or "").strip().lstrip("/")
    if not wanted:
        return
    landed = _viewer_pull(wanted)
    if landed:
        _show_in(win_id, landed, shown, "remote", wanted)


def _show_in(win_id: str, local: str, size: int, origin: str,
             label: str) -> None:
    """Put a picture in this viewer, or - when the size asks for more than
    the leaf was built to hold - stand a fresh window up at the new size.
    That is how Zoom + and Zoom - work: an ``image`` leaf fixes its maximum
    at build time, so the viewer is replaced, not resized."""
    record = viewers.get(win_id)
    if record is None:
        return
    if size != int(record.get("size") or 0):
        close_window(win_id)
        forget_window(win_id)
        open_viewer(record.get("path") or label, record.get("origin")
                    or origin, size)
        return
    api.tabs.set_widget(TAB, f"{win_id}_pic", "value", local)
    # The window now shows this picture: remember it, so a zoom rebuilds the
    # right file and opening the same path again raises this window.
    record["path"] = str(label or local)
    record["origin"] = str(origin or record.get("origin") or "local")
    _set_path(win_id, label)


def viewer_zoom(win_id: str, factor: float) -> None:
    record = viewers.get(win_id)
    if record is None:
        return
    wanted = str(record.get("path") or "")
    base = int(record.get("size") or scaled(512))
    new_size = max(scaled(96), int(round(base * factor)))
    if not wanted:
        _note(win_id, "Nothing is shown yet, so there is nothing to zoom.")
        return
    # Re-show at the new size: _show_in sees the difference and replaces the
    # window with one built for the bigger (or smaller) leaf.
    local = wanted
    if record.get("origin") == "remote":
        local = _viewer_pull(wanted) or wanted
    _show_in(win_id, local, new_size, str(record.get("origin") or "local"),
             wanted)


# --- 4. Command Prompt: a local shell in a window ---

#: The first words on every console screen, so the person knows whose keys
#: they are giving.
CONSOLE_BANNER = ("BerryVM local shell - this machine, this user. "
                  "Closing the window ends it.\n")


def console_spec(win_id: str, cwd: str) -> dict:
    """A read-only screen, a prompt line, and a note under them. The screen
    is a ``multi`` rather than a ``terminal`` because a terminal's screen
    belongs to an SSH socket; this one belongs to a child process."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": f"{win_id}_path", "text": cwd,
         "expand": True, "word_wrap": True},
        {"kind": "multi", "id": f"{win_id}_out", "readonly": True,
         "expand": "both", "word_wrap": False},
        {"kind": "line", "id": f"{win_id}_in",
         "placeholder": "Type a command and press Enter",
         "on": ["returned"], "expand": True},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_console(start: str = "") -> str:
    """Stand a command prompt up and hand its window to a live shell. Each
    window gets a process of its own, so two prompts can sit in two
    directories at once - exactly the desktop habit this copies."""
    global console_seq
    if api is None:
        return ""
    console_seq += 1
    win_id = f"{CONSOLE_PREFIX}{console_seq}"
    cwd = str(start or Path.home())
    open_window(_program_entry(win_id, "Command Prompt",
                               console_spec(win_id, cwd)))
    opened[win_id] = {"title": "Command Prompt", "kind": "console",
                      "cwd": cwd}
    consoles[win_id] = {"proc": None, "pending": deque([CONSOLE_BANNER]),
                        "cwd": cwd, "alive": False, "ended": False}
    refresh_taskbar()
    _start_console(win_id)
    return win_id


def _start_shell(cwd: str) -> Any:
    """Spawn the machine's own interactive shell with its keys and words
    piped through. Kept whole in one call so a test can hand back a fake."""
    if os.name == "nt":
        exe = os.environ.get("COMSPEC", "cmd.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen([exe], cwd=cwd,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", bufsize=1,
                                creationflags=flags)
    exe = os.environ.get("SHELL", "/bin/sh")
    return subprocess.Popen([exe], cwd=cwd,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            errors="replace", bufsize=1)


def _start_console(win_id: str) -> None:
    con = consoles.get(win_id)
    if con is None:
        return
    try:
        proc = _start_shell(str(con["cwd"]))
    except OSError as error:
        con["pending"].append(f"could not start a shell here: {error}\n")
        return
    con["proc"] = proc
    con["alive"] = True
    worker = threading.Thread(target=_console_reader, args=(con,),
                              daemon=True)
    worker.start()


def _console_reader(con: dict) -> None:
    """Carry every line the shell says into the window's pending tray; the
    tab's own poll drains it onto the screen. Reading happens here, on a
    thread of its own, because a command that never returns - a ``ping`` to
    a dead host, an editor that waits for keys - must be allowed to sit
    without freezing the desktop next to it."""
    proc = con.get("proc")
    try:
        for line in proc.stdout:
            con["pending"].append(str(line))
    except (OSError, ValueError):
        con["pending"].append("(the shell's words stopped arriving)\n")
    con["alive"] = False
    con["ended"] = True
    con["pending"].append("[process ended]\n")


def pump_consoles() -> None:
    """Move whatever the shells have said since last time onto their
    screens. Runs on the tab's own timer; with no console window open there
    is nothing to pump, and an empty tray writes nothing."""
    if api is None or not consoles:
        return
    for win_id, con in list(consoles.items()):
        lines = []
        while con["pending"]:
            try:
                lines.append(con["pending"].popleft())
            except IndexError:
                break
        if lines:
            api.tabs.set_widget(TAB, f"{win_id}_out", "append", "".join(lines))


def _console_submit(win_id: str) -> None:
    """Type the prompt line into the shell, echo it on the screen first so
    the window reads like one."""
    con = consoles.get(win_id)
    if con is None or api is None:
        return
    text = str(api.tabs.widget_value(TAB, f"{win_id}_in", "value") or "")
    api.tabs.set_widget(TAB, f"{win_id}_in", "value", "")
    proc = con.get("proc")
    if proc is None or not con.get("alive"):
        _note(win_id, "this shell has ended - close the window and open "
                      "another")
        return
    con["pending"].append(f"> {text}\n")
    try:
        proc.stdin.write(text + "\n")
        proc.stdin.flush()
    except (OSError, ValueError) as error:
        con["pending"].append(f"(could not reach the shell: {error})\n")


def _stop_shell(record: dict) -> None:
    """Let go of a console's process: ask politely first, and do not wait on
    a shell that means to finish its last command anyway."""
    proc = record.get("proc")
    record["proc"] = None
    record["alive"] = False
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
    except OSError:
        pass


# --- 5. Terminal: SSH to the profile's host ---

def main_ssh_spec() -> dict:
    """The Terminal window: one line saying where it is going, and a shell
    underneath it that brings its own socket up."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{P_MAIN}_path", "text": "main ssh",
             "expand": True, "word_wrap": True},
            {"kind": "label", "id": f"{P_MAIN}_state", "text": "not connected"},
            {"kind": "button", "id": f"{P_MAIN}_conn", "text": "Connect",
             "on": ["clicked"], "enabled": False,
             "tooltip": "Ask for the password again and try once more"},
            {"kind": "button", "id": f"{P_MAIN}_disc", "text": "Disconnect",
             "on": ["clicked"], "enabled": False},
        ]},
        {"kind": "terminal", "id": f"{P_MAIN}_term",
         "font_size": term_font(), "on": ["ready", "closed", "failed"]},
    ]}


def open_main_ssh() -> str:
    """Open the Terminal, or raise it if it is already up.

    Returns the window's id, or ``""`` when the profile has no host to point
    at - the terminal's socket is opened once the window is there, so an
    empty host is refused before any frame is drawn rather than as a shell
    that fails on screen.
    """
    if api is None:
        return ""
    profile = api.session.profile() or {}
    host = str(profile.get("host") or "").strip()
    if not host:
        api.ui.warning(TAB, "Terminal needs a host in the profile. Fill in "
                            "the address on the Connection panel first.")
        return ""
    user = str(profile.get("username") or "") or str(config("default_user", "root"))
    port = port_of(profile.get("port"), config("default_port", 22))
    key_file = str(profile.get("key_file") or "")
    title = f"Terminal - {host}"
    open_window(_program_entry(P_MAIN, title, main_ssh_spec()))
    opened[P_MAIN] = {"title": title, "kind": "main_ssh", "host": host,
                      "port": port, "username": user, "key_file": key_file}
    terminals[f"{P_MAIN}_term"] = P_MAIN
    refresh_taskbar()
    _connect_main_ssh()
    return P_MAIN


def _connect_main_ssh() -> None:
    """Ask for the password and hand the terminal its connection. A cancelled
    box leaves the window standing with a Connect button and an empty shell,
    which is exactly the state a "try again" is meant to answer."""
    record = opened.get(P_MAIN) or {}
    host = str(record.get("host") or "")
    user = str(record.get("username") or "")
    port = port_of(record.get("port"), 22)
    password = api.ui.input("Connect", f"Password for {user}@{host}:{port}",
                            "", secret=True)
    if password is None:
        _set_main_state("cancelled")
        return
    api.tabs.set_widget(TAB, f"{P_MAIN}_term", "connect", {
        "host": host, "port": port, "username": user,
        "password": str(password),
        "key_file": str(record.get("key_file") or ""),
        "trust_unknown_host": bool(config("trust_unknown_host", True)),
    })
    _set_main_state("opening...")


def _set_main_state(text: str) -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{P_MAIN}_state", "text", str(text or ""))


def main_ssh_news(event: str, payload: dict) -> None:
    """The three sayings the shell gives us, reflected on the window's own
    state line and on its two buttons."""
    record = opened.get(P_MAIN) or {}
    if event == "ready":
        _set_main_state(f"connected: {record.get('host', '?')}")
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", False)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", True)
    elif event == "failed":
        _set_main_state(str(payload.get("error") or "connection failed"))
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", True)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", False)
    elif event == "closed":
        _set_main_state("disconnected")
        api.tabs.set_widget(TAB, f"{P_MAIN}_conn", "enabled", True)
        api.tabs.set_widget(TAB, f"{P_MAIN}_disc", "enabled", False)


# --- 6. Machines: SSH bookmarks, one window per connection ---

def machines_spec() -> dict:
    """The Machines launcher: the list from the config, and the buttons that
    reach or edit one entry. Every connected machine is a window of its own,
    made the same way Multi SSH makes its sessions."""
    return {"kind": "column", "children": [
        {"kind": "list", "id": f"{P_MACHINES}_list", "on": ["selected"],
         "expand": "both",
         "tooltip": "Machines kept in addons/_data/berryvm/config.json under "
                    "'machines'. Select one and press Connect."},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{P_MACHINES}_connect",
             "text": "Connect", "on": ["clicked"], "expand": True},
            {"kind": "button", "id": f"{P_MACHINES}_new", "text": "Reach once",
             "on": ["clicked"],
             "tooltip": "Connect to a machine that is not on the list, once"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{P_MACHINES}_add", "text": "Add",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_MACHINES}_edit", "text": "Edit",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_MACHINES}_remove", "text": "Remove",
             "on": ["clicked"]},
        ]},
        {"kind": "button", "id": f"{P_MACHINES}_reload",
         "text": "Reload from config", "on": ["clicked"]},
        {"kind": "label", "id": f"{P_MACHINES}_note", "text": "",
         "word_wrap": True},
    ]}


def open_machines() -> str:
    """Bring up the launcher window; the entries come from the addon's own
    config, so a file edited elsewhere is picked up on Reload."""
    if api is None:
        return ""
    title = CAPTIONS[P_MACHINES]
    open_window(_program_entry(P_MACHINES, title, machines_spec()))
    opened[P_MACHINES] = {"title": title, "kind": "machines"}
    refresh_taskbar()
    draw_machines()
    return P_MACHINES


def reload_machines() -> list:
    """Read ``machines`` from the config and keep the addon's copy in step."""
    global machines
    machines = machines_from(config("machines", []))
    return machines


def draw_machines() -> None:
    if api is None:
        return
    api.tabs.set_widget(
        TAB, f"{P_MACHINES}_list", "items",
        [{"text": f"{m.get('name')} - {m.get('username')}@{m.get('host')}"
                  f":{m.get('port')}"} for m in machines])


def machine_row_of(text: str) -> int:
    """The row a caption names, so a selection maps back into the list."""
    for index, entry in enumerate(machines):
        caption = (f"{entry.get('name')} - {entry.get('username')}"
                   f"@{entry.get('host')}:{entry.get('port')}")
        if caption == text:
            return index
    return -1


def machine_form_spec(entry: dict | None) -> dict:
    """The form an entry is added or changed with, filled from ``entry``."""
    record = entry or {}
    return {
        "kind": "form",
        "children": [
            {"label": "Name", "kind": "line", "id": "name",
             "value": str(record.get("name") or ""), "placeholder": "Router"},
            {"label": "Address", "kind": "line", "id": "host",
             "value": str(record.get("host") or ""),
             "placeholder": "192.168.1.1, or root@192.168.1.1:2222"},
            {"label": "Port", "kind": "spin", "id": "port",
             "value": port_of(record.get("port"), config("default_port", 22)),
             "minimum": 1, "maximum": 65535},
            {"label": "Username", "kind": "line", "id": "username",
             "value": str(record.get("username") or ""),
             "placeholder": str(config("default_user", "root") or "")},
            {"label": "Key file", "kind": "line", "id": "key_file",
             "value": str(record.get("key_file") or ""),
             "placeholder": "optional; blank asks for a password"},
        ],
    }


def write_machines(items: list) -> None:
    """Save the ``machines`` list; a key the form never showed survives the
    save unchanged, which is what lets the file be edited outside the app."""
    global machines
    machines = [m for m in (items or []) if m]
    if api is not None:
        api.config.set("machines", [dict(m) for m in machines])
    draw_machines()
    mirror_settings()


def ask_machine(entry: dict | None) -> dict:
    """One form's answers as an entry; empty when the person walked out."""
    if api is None:
        return {}
    values = api.ui.dialog(machine_form_spec(entry),
                           title="Edit machine" if entry else "New machine")
    if values is None:
        return {}
    record = dict(entry or {})
    record.update({
        "name": " ".join(str(values.get("name") or "").split()),
        "host": str(values.get("host") or "").strip(),
        "port": port_of(values.get("port"), config("default_port", 22)),
        "username": str(values.get("username") or "").strip(),
        "key_file": str(values.get("key_file") or "").strip(),
    })
    return normalise_machine(record)


def machine_add() -> None:
    entry = ask_machine(None)
    if not entry:
        _note(P_MACHINES, "An entry needs an address.")
        return
    write_machines(list(machines) + [entry])
    _note(P_MACHINES, f"{entry.get('name')} added.")


def machine_edit() -> None:
    if not (0 <= machine_row < len(machines)):
        _note(P_MACHINES, "Select an entry to change.")
        return
    entry = ask_machine(machines[machine_row])
    if not entry:
        _note(P_MACHINES, "Nothing was changed.")
        return
    updated = list(machines)
    updated[machine_row] = entry
    write_machines(updated)


def machine_remove() -> None:
    """Drop one machine from the list. Windows already opened from it are left
    alone - they were launched from a copy of the details, and closing a
    router's window by deleting its bookmark is not what anyone means."""
    global machine_row
    if not (0 <= machine_row < len(machines)):
        _note(P_MACHINES, "Select an entry to remove.")
        return
    entry = machines[machine_row]
    if not api.ui.question("Remove machine",
                           f"Take {entry.get('name')} out of the list?"):
        return
    updated = list(machines)
    updated.pop(machine_row)
    write_machines(updated)
    machine_row = -1


def machine_connect(entry: dict) -> str:
    """Open a machine in its own window and its own socket. Each machine gets
    a fresh id, so visiting the same host twice is allowed - and a second
    window beside the first is the desktop's whole point."""
    global machine_seq
    if api is None:
        return ""
    host = str(entry.get("host") or "").strip()
    if not host:
        _note(P_MACHINES, "That entry has no address.")
        return ""
    user = str(entry.get("username") or config("default_user", "root"))
    port = port_of(entry.get("port"), config("default_port", 22))
    password = api.ui.input("Connect", f"Password for {user}@{host}:{port}",
                            "", secret=True)
    if password is None:
        _note(P_MACHINES, "No password, no session.")
        return ""
    machine_seq += 1
    win_id = f"{MACHINE_PREFIX}{machine_seq}"
    term_id = f"{win_id}_term"
    title = f"{entry.get('name') or host}"
    spec = {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_path",
             "text": f"{user}@{host}:{port}", "expand": True},
            {"kind": "label", "id": f"{win_id}_state", "text": "opening..."},
        ]},
        {"kind": "terminal", "id": term_id, "font_size": term_font(),
         "on": ["ready", "closed", "failed"]},
    ]}
    open_window(_program_entry(win_id, title, spec))
    opened[win_id] = {"title": title, "kind": "machine", "host": host,
                      "port": port, "username": user}
    terminals[term_id] = win_id
    api.tabs.set_widget(TAB, term_id, "connect", {
        "host": host, "port": port, "username": user,
        "password": str(password),
        "key_file": str(entry.get("key_file") or ""),
        "trust_unknown_host": bool(config("trust_unknown_host", True)),
    })
    refresh_taskbar()
    return win_id


def machine_news(win_id: str, event: str, payload: dict) -> None:
    """One machine's shell reporting itself onto its window's state line."""
    if api is None:
        return
    if event == "ready":
        api.tabs.set_widget(TAB, f"{win_id}_state", "text", "connected")
    elif event == "failed":
        api.tabs.set_widget(TAB, f"{win_id}_state", "text",
                            str(payload.get("error") or "failed"))
    elif event == "closed":
        api.tabs.set_widget(TAB, f"{win_id}_state", "text", "disconnected")


# --- 7. Display Properties: the settings window ---

def settings_spec() -> dict:
    """The wallpaper, the sizes and the habits - the same keys the app's own
    form writes, offered here in the XP control-panel shape."""
    return {"kind": "column", "children": [
        {"kind": "group", "text": "Wallpaper (Desktop tab)", "children": [
            {"kind": "form", "children": [
                {"label": "Background", "kind": "combo", "id": "set_wall",
                 "items": list(WALLPAPERS),
                 "value": str(config("wallpaper", DEFAULTS["wallpaper"])),
                 "on": ["changed"]},
                {"label": "Picture file", "kind": "line", "id": "set_file",
                 "value": str(config("wallpaper_file", "")),
                 "placeholder": "a file on this machine"},
            ]},
            {"kind": "row", "children": [
                {"kind": "button", "id": "set_picture",
                 "text": "Browse for a Picture...", "on": ["clicked"],
                 "tooltip": "Picks the Picture wallpaper and its file"},
                {"kind": "button", "id": "set_colour",
                 "text": "Pick a Solid Colour...", "on": ["clicked"],
                 "tooltip": "Picks the Solid wallpaper and its colour"},
            ]},
        ]},
        {"kind": "group", "text": "Sizes (Settings tab)", "children": [
            {"kind": "form", "children": [
                {"label": "Desktop scale %", "kind": "spin", "id": "set_scale",
                 "value": scale(), "minimum": 80, "maximum": 160,
                 "tooltip": "BerryVM's own scale for window sizes and "
                            "desktop icons"},
                {"label": "Terminal text", "kind": "spin", "id": "set_font",
                 "value": term_font(), "minimum": 8, "maximum": 28},
                {"label": "Window width", "kind": "spin", "id": "set_width",
                 "value": int(config("window_width",
                                     DEFAULTS["window_width"])),
                 "minimum": 240, "maximum": 2400},
                {"label": "Window height", "kind": "spin", "id": "set_height",
                 "value": int(config("window_height",
                                     DEFAULTS["window_height"])),
                 "minimum": 180, "maximum": 1600},
            ]},
        ]},
        {"kind": "group", "text": "Taskbar and shells", "children": [
            {"kind": "check", "id": "set_clock", "text": "Show the clock",
             "checked": bool(config("clock_on", True))},
            {"kind": "form", "children": [
                {"label": "Username to offer", "kind": "line",
                 "id": "set_user",
                 "value": str(config("default_user", "root"))},
                {"label": "Port to offer", "kind": "spin", "id": "set_port",
                 "value": port_of(config("default_port", 22), 22),
                 "minimum": 1, "maximum": 65535},
            ]},
            {"kind": "check", "id": "set_trust",
             "text": "Accept a host key nobody has seen",
             "checked": bool(config("trust_unknown_host", True))},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": "set_apply", "text": "Apply",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": "set_use", "text": "OK",
             "on": ["clicked"], "expand": True,
             "tooltip": "Apply and shut this window"},
            {"kind": "button", "id": "set_defaults", "text": "Defaults",
             "on": ["clicked"],
             "tooltip": "Put every key back the way the addon shipped it"},
        ]},
        {"kind": "label", "id": f"{P_SETTINGS}_note", "text": "",
         "word_wrap": True},
    ]}


def open_settings() -> str:
    """Bring Display Properties up; it reads the config fresh every time, so
    a hand-edit elsewhere shows up here."""
    if api is None:
        return ""
    title = CAPTIONS[P_SETTINGS]
    open_window(_program_entry(P_SETTINGS, title, settings_spec()))
    opened[P_SETTINGS] = {"title": title, "kind": "settings"}
    refresh_taskbar()
    return P_SETTINGS


def _read_one(identifier: str, default: Any) -> Any:
    """One Display Properties control's current answer, or ``default`` when
    the window is not on the desktop to be asked."""
    try:
        value = api.tabs.widget_value(TAB, identifier, "value")
    except Exception:
        return default
    return default if value is None or value == "" else value


def settings_apply(close_after: bool = False) -> None:
    """Write what the window says, then repaint the desktop from it. A scale
    that moved rebuilds the icon layer, because tile sizes are chosen when
    the grid is built; a text size that moved goes to every shell standing
    on the desktop right now."""
    if api is None:
        return
    was = scale()
    api.config.set("wallpaper", str(_read_one("set_wall", "Bliss")))
    api.config.set("wallpaper_file",
                   str(_read_one("set_file", "")).strip())
    api.config.set("ui_scale",
                   int(_read_one("set_scale", DEFAULTS["ui_scale"])))
    api.config.set("term_font",
                   int(_read_one("set_font", DEFAULTS["term_font"])))
    api.config.set("window_width",
                   int(_read_one("set_width", DEFAULTS["window_width"])))
    api.config.set("window_height",
                   int(_read_one("set_height", DEFAULTS["window_height"])))
    try:
        clock = bool(api.tabs.widget_value(TAB, "set_clock", "value"))
    except Exception:
        clock = bool(config("clock_on", True))
    api.config.set("clock_on", clock)
    api.config.set("default_user",
                   str(_read_one("set_user", "root")))
    api.config.set("default_port",
                   int(_read_one("set_port", 22)))
    try:
        trust = bool(api.tabs.widget_value(TAB, "set_trust", "value"))
    except Exception:
        trust = True
    api.config.set("trust_unknown_host", trust)
    mirror_settings()
    apply_theme()
    if scale() != was:
        relayout_desktop()
    apply_term_font()
    tick_clock()
    _note(P_SETTINGS, "Applied.")
    if close_after:
        close_window(P_SETTINGS)
        forget_window(P_SETTINGS)
        refresh_taskbar()


def apply_term_font() -> None:
    """Hand the configured text size to every terminal on the desktop - the
    Main one and each machine window's - as the api's own ``font_size``."""
    if api is None:
        return
    size = term_font()
    for term_id in list(terminals):
        api.tabs.set_widget(TAB, term_id, "font_size", size)


def settings_pick_picture() -> None:
    """A wallpaper off this machine: the pick sets the Picture wallpaper and
    repaints straight away, XP's Apply-preview way."""
    if api is None:
        return
    picked = api.ui.pick_file(
        "Wallpaper picture",
        filter="Pictures (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*)")
    picked = str(picked or "").strip()
    if not picked:
        return
    api.config.set("wallpaper_file", picked)
    api.config.set("wallpaper", "Picture")
    api.tabs.set_widget(TAB, "set_file", "value", picked)
    api.tabs.set_widget(TAB, "set_wall", "value", "Picture")
    mirror_settings()
    apply_theme()
    _note(P_SETTINGS, f"wallpaper: {short_name(picked)}")


def settings_pick_colour() -> None:
    picked = api.ui.pick_color("Wallpaper colour",
                               str(config("wallpaper_color",
                                          DEFAULTS["wallpaper_color"])))
    picked = str(picked or "").strip()
    if not picked:
        return
    api.config.set("wallpaper_color", picked)
    api.config.set("wallpaper", "Solid")
    api.tabs.set_widget(TAB, "set_wall", "value", "Solid")
    mirror_settings()
    apply_theme()
    _note(P_SETTINGS, f"wallpaper: {picked}")


def settings_defaults() -> None:
    """Every key back to the shipped value, and the desktop repainted to
    match - the Defaults button means it."""
    if api is None:
        return
    was = scale()
    for key, value in DEFAULTS.items():
        if key == "machines":
            continue           # a person's bookmarks are not a default
        api.config.set(key, value)
    mirror_settings()
    apply_theme()
    if scale() != was:
        relayout_desktop()
    apply_term_font()
    tick_clock()
    _note(P_SETTINGS, "The desktop is back to its shipped look.")


# --- 8. Calculator ---

#: The keys, left to right, top to bottom; the grid they are laid in is four
#: across, so the row of digits falls where a calculator's does.
CALC_KEYS = ["C", "\u2190", "%", "/",
             "7", "8", "9", "*",
             "4", "5", "6", "-",
             "1", "2", "3", "+",
             "0", ".", "(", ")",
             "=", "", "", ""]

#: One id per key, so the dispatcher reads the button's name rather than the
#: glyph on it.
CALC_IDS = {f"calc_b{index}": key
            for index, key in enumerate(CALC_KEYS) if key}


def calc_spec() -> dict:
    """The display line and a grid of keys under it. The line is read-only
    words; the keys do all the typing."""
    buttons = []
    for index, key in enumerate(CALC_KEYS):
        if not key:
            continue
        buttons.append({"kind": "button", "id": f"calc_b{index}",
                        "text": key, "row": index // 4, "col": index % 4,
                        "on": ["clicked"]})
    return {"kind": "column", "children": [
        {"kind": "line", "id": "calc_disp", "text": "0", "readonly": True},
        {"kind": "grid", "children": buttons},
        {"kind": "label", "id": "calc_note", "text": "", "word_wrap": True},
    ]}


def open_calc() -> str:
    if api is None:
        return ""
    title = CAPTIONS[P_CALC]
    open_window(_program_entry(P_CALC, title, calc_spec(), 260, 300))
    opened[P_CALC] = {"title": title, "kind": "calc"}
    refresh_taskbar()
    _calc_show()
    return P_CALC


# --- 9. Internet: the Web Browser window ---

#: The address a fresh browser starts on, and the label the URL bar carries
#: so the person sees what "no page loaded yet" looks like in the field.
BROWSER_HOME = "https://www.wikipedia.org"


def browser_spec(start_url: str = BROWSER_HOME) -> dict:
    """One row of furniture over a web leaf: an address field, a Go button
    beside it, and the QtWebEngine view underneath. The leaf is named
    ``browser_view`` so a later ``set_widget`` can push a new address into
    it without rebuilding the whole window."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "line", "id": f"{P_BROWSER}_url",
             "text": start_url,
             "placeholder": "Address - press Enter or Go",
             "on": ["activated"], "expand": True},
            {"kind": "button", "id": f"{P_BROWSER}_go", "text": "Go",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{P_BROWSER}_home", "text": "Home",
             "on": ["clicked"]},
        ]},
        {"kind": "web", "id": f"{P_BROWSER}_view", "url": start_url,
         "expand": "both"},
        {"kind": "label", "id": f"{P_BROWSER}_note", "text": "",
         "word_wrap": True},
    ]}


def open_browser(url: str = "") -> str:
    """Bring the Web Browser up on the app's main screen. The window is
    single-instance: a second call raises what is already there and, if a
    fresh address came with it, points the leaf at the new page."""
    if api is None:
        return ""
    title = CAPTIONS[P_BROWSER]
    target = str(url or "").strip() or BROWSER_HOME
    if P_BROWSER in open_window_ids():
        if url:
            api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "url", target)
            api.tabs.set_widget(TAB, f"{P_BROWSER}_url", "text", target)
        raise_window(P_BROWSER)
        return P_BROWSER
    # A browser wants the whole middle of the screen: two default widths
    # across and one and a half down is roughly a web page's aspect.
    default_w, default_h = window_size()
    open_window(_program_entry(P_BROWSER, title, browser_spec(target),
                                default_w * 2, int(default_h * 1.5)))
    opened[P_BROWSER] = {"title": title, "kind": "browser"}
    refresh_taskbar()
    return P_BROWSER


def browser_go() -> None:
    """Read the URL bar and hand it to the leaf. A bare host without a
    scheme gets ``https://`` prepended, the same bargain every browser
    strikes with a hand that forgets to type one."""
    if api is None:
        return
    try:
        text = str(api.tabs.widget_value(TAB, f"{P_BROWSER}_url",
                                          "value") or "").strip()
    except Exception:
        text = ""
    if not text:
        return
    low = text.lower()
    if not (low.startswith("http://") or low.startswith("https://")
            or low.startswith("file://") or low.startswith("about:")):
        text = "https://" + text
    api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "url", text)


def calc_press(key: str) -> None:
    """One key's arithmetic. ``calc_expr`` is the line as it stands; ``=``
    asks :func:`calc_eval`, which knows only numbers and the four marks, and
    a bad line shows ``Error`` rather than a traceback."""
    global calc_expr
    if key == "C":
        calc_expr = ""
    elif key == "\u2190":
        calc_expr = calc_expr[:-1]
    elif key == "=":
        try:
            calc_expr = _calc_show_value(calc_eval(calc_expr))
        except (ValueError, ZeroDivisionError):
            calc_expr = ""
            _note(P_CALC, "Error - check the line")
            api.tabs.set_widget(TAB, "calc_disp", "value", "Error")
            return
    else:
        calc_expr = (calc_expr + key) if calc_expr != "0" else key
    _calc_show()


def _calc_show_value(value: float) -> str:
    """A number as the little window above the keys writes it: no trailing
    ``.0`` on a whole, a trimmed tail on a fraction."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _calc_show() -> None:
    if api is None:
        return
    api.tabs.set_widget(TAB, "calc_disp", "value", calc_expr or "0")


# --- what the tab says back ---

def is_terminal_id(identifier: str) -> bool:
    """Whether one event came from a terminal inside a program window."""
    return str(identifier or "") in terminals


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Every click, selection, double-click and window report this tab can
    produce.

    The dispatcher is a wide ``if`` chain rather than a table, because the
    ids a window's controls carry are made up on the spot (``<win_id>_path``,
    ``bp3_saveas``) and a table of them would have to be rebuilt every time
    the desktop changes.
    """
    global machine_row
    if api is None or str(tab) != TAB:
        return
    payload = payload or {}
    body = str(identifier or "")
    # 1. The desktop itself: window events. ``body`` is the window's id when
    # the primitive reported a change inside a frame, or ``DESKTOP`` when the
    # canvas has something of its own to say.
    if body == DESKTOP:
        refresh_taskbar()
        return
    if body in opened and event in ("activated", "moved", "resized", "closed"):
        if event == "closed":
            forget_window(body)
        refresh_taskbar()
        if event in ("activated", "closed"):
            # Only a change of who is on top can push the icon layer up;
            # a drag or a sizing move never re-stacks the frames, so the
            # bar's busiest events skip the extra desktop read entirely.
            keep_icons_behind()
        return
    # 1b. A frame's own minimize / maximize / restore report. The taskbar
    # row follows the flag; a raised or restored window also clears any
    # flash it was wearing, since the person has come back to it.
    if body in opened and event in ("minimized", "maximized", "restored"):
        if event == "minimized":
            minimized[body] = True
        elif event == "maximized":
            minimized.pop(body, None)
        else:                                # restored
            minimized.pop(body, None)
            clear_flash(body)
        refresh_taskbar()
        return
    # 1c. The desktop's own shortcut: Alt+Tab cycles the windows through
    # a choose prompt. A global grab is not offered to addons, so the
    # chord fires only while the Berry Manager window has focus.
    if body == DESKTOP and event == "shortcut":
        if str(payload.get("keys") or "").lower() == "alt+tab":
            switcher_menu()
        return
    # 2. Start and the taskbar.
    if body == START_BTN and event == "clicked":
        start_menu()
        return
    if body == TASK_LIST and event == "selected":
        win_id = picked_window()
        if win_id:
            raise_window(win_id)
        return
    # 3. The icon layer: double-click opens, right-click is the menu, a lone
    # selection just remembers which tile the cursor is on.
    if body == ICON_GRID:
        if event == "activated":
            text = str(payload.get("text") or "")
            pid = _resolve_caption(text)
            if pid:
                start_program(pid)
        elif event == "context":
            desktop_menu(str(payload.get("text") or ""))
        elif event == "moved":
            # Every tile's position arrives on each drop; the whole map is
            # written to the layout file in one pass, so a fast drag of
            # several tiles costs only the writes it really needs.
            for one in (payload.get("all") or []):
                key = str(one.get("key") or "")
                if not key:
                    continue
                _desk_positions[key] = [int(one.get("x", 0)),
                                        int(one.get("y", 0))]
            mirror_desk()
        elif event == "renamed":
            _apply_rename(str(payload.get("key") or ""),
                          str(payload.get("name") or ""),
                          str(payload.get("old") or ""))
        return
    # 4. A terminal's news, whether the Terminal's or a machine window's.
    if is_terminal_id(body):
        owner = terminals[body]
        if owner == P_MAIN:
            main_ssh_news(event, payload)
        else:
            machine_news(owner, event, payload)
        return
    # 5. The Explorer: its two tables and their buttons.
    if body.endswith("_table") and body[:3] in PANE_KINDS:
        pane = body[:3]
        name = str(payload.get("text") or "")
        if event == "selected":
            pane_pick[body] = name
        elif event == "activated":
            pane_pick[body] = name
            if name:
                pane_enter(pane, name)
        elif event == "context":
            pane_menu(pane, name)
        return
    if event == "clicked" and body[:3] in PANE_KINDS \
            and body.rsplit("_", 1)[-1] in PANE_ACTIONS:
        pane, action = body[:3], body.rsplit("_", 1)[-1]
        if action == "up":
            pane_up(pane)
        elif action == "home":
            pane_home(pane)
        elif action == "refresh":
            pane_list(pane, pane_path.get(pane) or "")
        elif action == "open":
            pane_open_picked(pane)
        elif action == "new":
            pane_new_folder(pane)
        elif action == "rename":
            pane_rename(pane)
        elif action == "delete":
            pane_delete(pane)
        elif action == "send":
            pane_send(pane)
        elif action == "run":
            pane_run(pane)
        return
    # 6. A viewer's buttons.
    if event == "clicked" and body.endswith(("_local", "_remote", "_zin",
                                             "_zout")):
        win_id = body.rsplit("_", 1)[0]
        if win_id not in viewers:
            return
        tail = body.rsplit("_", 1)[-1]
        if tail == "local":
            viewer_open_local(win_id)
        elif tail == "remote":
            viewer_open_remote(win_id)
        elif tail == "zin":
            viewer_zoom(win_id, 1.5)
        elif tail == "zout":
            viewer_zoom(win_id, 2.0 / 3.0)
        return
    # 7. A command prompt's typed line: read it, clear the box, feed it.
    if body.endswith("_in") and event == "returned":
        win_id = body[:-3]
        if win_id in consoles:
            _console_submit(win_id)
        return
    # 8. Notepad's Save and Save As. The rest of that window is the editor
    # and two labels, none of which have an event that means anything here.
    # ``_saveas`` is tried first: it does not end in ``_save``, and a plain
    # suffix test would hand the copy button to the wrong door.
    if event == "clicked" and body.endswith(("_save", "_saveas")):
        if body.endswith("_saveas"):
            win_id = body[: -len("_saveas")]
            if win_id in berrypads:
                _berry_save_as(win_id)
            return
        win_id = body[: -len("_save")]
        if win_id in berrypads:
            _berry_save(win_id)
        return
    # 9. The calculator's keys.
    if event == "clicked" and body in CALC_IDS:
        calc_press(CALC_IDS[body])
        return
    if event == "clicked" and body in ("tray_net", "tray_wbr"):
        _tray_stub(body)
        return
    # 9a. The Web Browser: Go and Home buttons, and Enter on the URL bar.
    if body == f"{P_BROWSER}_go" and event == "clicked":
        browser_go()
        return
    if body == f"{P_BROWSER}_home" and event == "clicked":
        api.tabs.set_widget(TAB, f"{P_BROWSER}_url", "value", BROWSER_HOME)
        api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "url", BROWSER_HOME)
        return
    if body == f"{P_BROWSER}_url" and event == "activated":
        browser_go()
        return
    # 9b. The Recycle Bin window: a row's double-click restores it, and
    # the three buttons below the table do the same thing plus a wipe.
    if body == f"{P_RECYCLE}_table":
        row = str(payload.get("text") or "")
        if event == "activated" and row:
            restore_recycled(row)
        elif event == "selected":
            pane_pick[body] = row
        return
    if event == "clicked" and body == f"{P_RECYCLE}_restore":
        target = pane_pick.get(f"{P_RECYCLE}_table") or ""
        if target:
            restore_recycled(target)
        else:
            _note(P_RECYCLE, "Select a file to restore.")
        return
    if event == "clicked" and body == f"{P_RECYCLE}_delete":
        target = pane_pick.get(f"{P_RECYCLE}_table") or ""
        if not target:
            _note(P_RECYCLE, "Select a file to delete.")
            return
        if not api.ui.question("Recycle Bin",
                               f"Delete {target!r} for good?"):
            return
        try:
            (recycle_root() / target).unlink()
        except OSError as error:
            _note(P_RECYCLE, f"could not delete: {error}")
            return
        _desk_recycled[:] = [r for r in _desk_recycled
                             if str(r.get("name") or "") != target]
        mirror_desk()
        draw_recycle()
        return
    if event == "clicked" and body == f"{P_RECYCLE}_empty":
        gone = empty_recycle()
        _note(P_RECYCLE, f"{gone} item(s) deleted" if gone
                          else "Nothing was in the bin.")
        return
    # 10. Display Properties.
    if event == "clicked" and body == "set_apply":
        settings_apply()
        return
    if event == "clicked" and body == "set_use":
        settings_apply(close_after=True)
        return
    if event == "clicked" and body == "set_defaults":
        settings_defaults()
        return
    if event == "clicked" and body == "set_picture":
        settings_pick_picture()
        return
    if event == "clicked" and body == "set_colour":
        settings_pick_colour()
        return
    if event == "changed" and body == "set_wall":
        # Choosing by name still needs the write: the combo is the fastest
        # way to the gradient wallpapers, which need no file or colour.
        api.config.set("wallpaper", str(payload.get("text") or "Bliss"))
        mirror_settings()
        apply_theme()
        return
    # 11. The Terminal's own buttons.
    if event == "clicked" and body == f"{P_MAIN}_conn":
        _connect_main_ssh()
        return
    if event == "clicked" and body == f"{P_MAIN}_disc":
        api.tabs.set_widget(TAB, f"{P_MAIN}_term", "disconnect", None)
        return
    # 12. Machines.
    if body == f"{P_MACHINES}_list" and event == "selected":
        picked = _selected_text(f"{P_MACHINES}_list")
        machine_row = machine_row_of(picked)
        return
    if event == "clicked" and body == f"{P_MACHINES}_connect":
        if 0 <= machine_row < len(machines):
            machine_connect(machines[machine_row])
        else:
            _note(P_MACHINES, "Select a machine first.")
        return
    if event == "clicked" and body == f"{P_MACHINES}_new":
        entry = ask_machine(None)
        if entry:
            machine_connect(entry)
        return
    if event == "clicked" and body == f"{P_MACHINES}_add":
        machine_add()
        return
    if event == "clicked" and body == f"{P_MACHINES}_edit":
        machine_edit()
        return
    if event == "clicked" and body == f"{P_MACHINES}_remove":
        machine_remove()
        return
    if event == "clicked" and body == f"{P_MACHINES}_reload":
        reload_machines()
        draw_machines()
        _note(P_MACHINES, "Reloaded from the config.")
        return


#: The Explorer buttons that share one dispatch: everything a pane prefix +
#: an underscore + one of these words means the same action on either side.
PANE_ACTIONS = ("up", "home", "refresh", "open", "new", "rename", "delete",
                "send", "run")
