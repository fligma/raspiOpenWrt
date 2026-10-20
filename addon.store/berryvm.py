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
  deletes act on the pane they are in. Text files open in the editor and
  pictures in the viewer.
* **Notepad** - one editor window per file, local or remote, with Save and
  Save As; a scratch buffer is born when Start > Notepad is pressed with
  nothing picked.
* **Picture Viewer** - local pictures straight off disk, remote ones pulled
  over the main connection; zoom is a fresh window at a new size.
* **Command Prompt** - a real local shell (cmd or sh) kept in a pocket
  terminal: the process stays up, ``cd`` sticks, output trickles in.
* **Display Properties** - the settings window: wallpaper (gradient, solid
  colour or a picture file), the desktop's own scale, terminal text size,
  the clock, and the defaults the programs start with.
* **Internet** - a Web Browser over the app's web leaf, with a bookmark shelf.
* **Paint** - a free-drawing surface (pen, line, rectangle, ellipse, eraser,
  fill) with a colour, a width, undo, and Open/Save As to a PNG off this
  machine; a saved picture can be handed straight to the Picture Viewer.
* **Media Player** - QtMultimedia audio with a playlist: open a local file or
  pull one off the device over the file session, then play, pause, stop, seek
  and set volume.

Extras round it out: right-click the desktop for its menu, right-click any
file row for Open/Edit/View/Transfer/Rename/Delete, drag a window's title bar
against a desktop edge to snap it to a half, quarter or full with a ghost
preview, right-click the taskbar to cascade, tile or Show desktop, and Start
offers Change Background, About and Log Off, which shuts every window on the
desktop.

The manifest says ``"connection": "app"`` because everything that reaches off
this machine rides the app's one file session: the device pane, the picture
viewer and the media player pull over SFTP (and say so when it is down), while
the shells - Command Prompt chief among them - are the machine's own local
processes. The addon's data folder is still the VM's home: ``Desktop``,
``bin``, ``tmp``, ``config``, with ``config/settings.json`` mirroring the
addon's own settings, passwords kept out of it as always.
"""

from __future__ import annotations

import json
import os
import posixpath
from datetime import datetime
from pathlib import Path
from typing import Any

from addon_common import AddonError, find_addon_picture

ADDON = {
    "name": "BerryVM",
    "version": "2.6",
    "author": "fligma",
    "api": 1,
    "requires": ["sftp"],
    # The only thing that reaches off this machine is the file session: the
    # device pane, viewer and media player ride the app's SFTP, and the shells
    # are the machine's own local processes - no addon-owned socket is opened.
    "connection": "app",
    "description": "A Windows XP-styled desktop in one tab: wallpaper, Start "
                   "menu, taskbar and desktop icons, with a two-paned local + "
                   "device file manager, Notepad, a picture viewer, a real "
                   "local Command Prompt wired to the machine's own shell, a "
                   "Linux-shaped home with alias and profile support, a Web "
                   "Browser, Paint, a Media Player and multi-tab Display "
                   "Properties with selectable themes, sliders and toggles. "
                   "Windows snap to half, quarter or full by their title bar, "
                   "and the taskbar right-clicks into cascade, tile and Show "
                   "desktop - all dressed up with a boot splash, a logon "
                   "picker and a few XP easter eggs.",
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
P_SETTINGS = "settings"       # Display Properties
P_BROWSER = "browser"         # the Web Browser window (single instance)
P_PAINT = "paint"             # Paint: the canvas drawing program
P_MEDIA = "media"             # Media Player: QtMultimedia audio

#: The Start flyout itself. It is a launcher, not a launch-able program, so
#: it rides in none of ``PROGRAMS``/``CAPTIONS``/``BY_CAPTION`` - it never
#: gets a desktop tile or a catalog row. The taskbar's Start button raises
#: this two-pane window; ``start_menu`` stays as the flat ``ui.choose`` menu
#: it falls back to when a flyout cannot be stood up.
P_START = "start_flyout"

#: The boot splash and the logon picker. Like the Start flyout they are part
#: of the shell's own cosplay, not launch-able programs, so they ride in no
#: ``PROGRAMS``/``CAPTIONS``/``BY_CAPTION`` and get no tile or catalog row.
SPLASH = "boot_splash"
LOGON = "logon"

#: The Konami sequence, matched against the desktop's lone-key shortcuts.
KONAMI = ("up", "up", "down", "down", "left", "right",
          "left", "right", "b", "a")

#: What Start and the desktop know as the program list: an id, a caption, and
#: the mark the tile wears. The caption is the one name the icon layer, the
#: Start menu and the window titles all agree on.
PROGRAMS: list[tuple[str, str]] = [
    (P_EXPLORER, "My Computer"),
    (P_NOTEPAD, "Notepad"),
    (P_VIEWER, "Picture Viewer"),
    (P_CONSOLE, "Command Prompt"),
    (P_BROWSER, "Internet"),
    (P_SETTINGS, "Display Properties"),
    (P_PAINT, "Paint"),
    (P_MEDIA, "Media Player"),
]

CAPTIONS: dict[str, str] = dict(PROGRAMS)

#: The captions read back from a tile pick, so the desktop can dispatch on
#: the word under the cursor rather than remembering which row it was.
BY_CAPTION: dict[str, str] = {cap: pid for pid, cap in PROGRAMS}

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

ICON_GEAR = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="6" fill="#cfe0f4" stroke="#3b74d8"'
    ' stroke-width="2"/>'
    '<path d="M16 3v5M16 24v5M3 16h5M24 16h5M7 7l3.5 3.5M21.5 21.5L25 25'
    'M25 7l-3.5 3.5M10.5 21.5L7 25" stroke="#3b74d8" stroke-width="2.4"'
    ' stroke-linecap="round"/>'
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

#: Paint's mark: a palette with a brush laid over it.
ICON_PAINT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<path d="M16 4c7 0 12 4 12 10 0 4-3 5-5 5h-3c-2 0-3 1-3 3 0 1 .5 2 .5 '
    '3 0 2-1.5 3-3.5 3C9 28 4 23 4 16 4 9 9 4 16 4z" fill="#e8d8b0"'
    ' stroke="#7a5a2a" stroke-width="1.2"/>'
    '<circle cx="11" cy="12" r="1.7" fill="#c51a4a"/>'
    '<circle cx="16" cy="9.5" r="1.7" fill="#2f7fdc"/>'
    '<circle cx="21" cy="12" r="1.7" fill="#3e8522"/>'
    '<circle cx="10.5" cy="18" r="1.7" fill="#e8b34b"/>'
    '<path d="M20 20l7-9 2 1.5-7 9z" fill="#b98a4a" stroke="#5a3f1a"'
    ' stroke-width="0.8"/>'
    '<path d="M25 13.5l2 1.5-1.5 2.5-2.5-1.5z" fill="#c51a4a"/>'
    '</svg>'
)

#: Media Player's mark: a blue disc with a play triangle, the WMP echo.
ICON_MEDIA = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="12" fill="#2a5ea8" stroke="#123060"'
    ' stroke-width="1.2"/>'
    '<circle cx="16" cy="16" r="4" fill="#bfe0ff"/>'
    '<path d="M14 12l8 4-8 4z" fill="#ffb020" stroke="#a06020"'
    ' stroke-width="0.6"/>'
    '</svg>'
)

#: Program id to the mark its desktop tile wears.
TILE_ICONS = {
    P_EXPLORER: ICON_COMPUTER,
    P_NOTEPAD: ICON_TEXTFILE,
    P_VIEWER: ICON_PICTURE,
    P_CONSOLE: ICON_CONSOLE,
    P_SETTINGS: ICON_GEAR,
    P_PAINT: ICON_PAINT,
    P_MEDIA: ICON_MEDIA,
    P_BROWSER: ICON_GLOBE,
}

#: The mark a taskbar button wears, from the ``kind`` its opened record
#: carries. A program's window and the tile that started it wear the same
#: picture.
KIND_ICONS = {
    "explorer": ICON_COMPUTER,
    "berrypad": ICON_TEXTFILE,
    "viewer": ICON_PICTURE,
    "console": ICON_CONSOLE,
    "settings": ICON_GEAR,
    "paint": ICON_PAINT,
    "media": ICON_MEDIA,
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

# --- the themes: the accent palette every frame and the bar are painted from ---

#: The XP look is three colour schemes wearing the same shape - the blue
#: Luna, an olive parody and a steel Silver. Each theme is one flat palette,
#: so :func:`desktop_sheet` and :func:`taskbar_sheet` read their colours from
#: here and a theme swap recolours the whole desktop with no second sheet.
#: The literal gradients keep the shapes they were drawn with; only the tints
#: move.
THEMES = {
    "Luna Blue": {
        "frame": "#0855dd",
        "bar": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2b84f0,"
               " stop:0.10 #1260e8, stop:0.55 #0b4fd6, stop:1 #1866e8)",
        "task": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3a8cf7,"
                " stop:0.08 #2574e8, stop:0.5 #1c5ed6, stop:0.92 #1448b4,"
                " stop:1 #1043a8)",
        "task_edge": "#5b9bf8",
        "start": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4aaa4a,"
                 " stop:0.5 #2f8a2f, stop:1 #1e6b1e)",
        "start_border": "#1d5e1d",
        "start_hover": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #5ec05e,"
                       " stop:1 #2c7c2c)",
        "start_press": "#1e5c1e",
        "item": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #5e9ef5,"
                " stop:1 #2a6ada)",
        "item_border": "#1c4fb4",
        "item_sel": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1c54b7,"
                    " stop:1 #0d3a94)",
        "item_sel_border": "#0a2c74",
        "tray": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1e68d8,"
                " stop:1 #0d47b0)",
        "tray_edge": "#6ba1e8",
        "select": "#316ac5",
        "edge": "#7f9db9",
    },
    "Olive Gold": {
        "frame": "#4a5a1e",
        "bar": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8a9a3e,"
               " stop:0.10 #6b7c2c, stop:0.55 #556322, stop:1 #6f8230)",
        "task": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #7a8a3e,"
                " stop:0.08 #5f6f2c, stop:0.5 #4d5a22, stop:0.92 #3e4a1c,"
                " stop:1 #35401a)",
        "task_edge": "#9aa85a",
        "start": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #d8b24a,"
                 " stop:0.5 #b8912f, stop:1 #96751e)",
        "start_border": "#6b5614",
        "start_hover": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #e8c25a,"
                       " stop:1 #a8862c)",
        "start_press": "#8a6b18",
        "item": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #9aa85a,"
                " stop:1 #6b7c2c)",
        "item_border": "#4d5a22",
        "item_sel": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #556322,"
                    " stop:1 #3e4a1c)",
        "item_sel_border": "#2f3814",
        "tray": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #6b7c2c,"
                " stop:1 #4d5a22)",
        "tray_edge": "#9aa85a",
        "select": "#6b7c2c",
        "edge": "#8a9a6a",
    },
    "Silver": {
        "frame": "#6a6a72",
        "bar": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #c0c0c8,"
               " stop:0.10 #a8a8b2, stop:0.55 #8f8f9a, stop:1 #a0a0aa)",
        "task": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #b8b8c0,"
                " stop:0.08 #9a9aa4, stop:0.5 #82828c, stop:0.92 #6e6e78,"
                " stop:1 #64646e)",
        "task_edge": "#d0d0d8",
        "start": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #9aa0ac,"
                 " stop:0.5 #7a828f, stop:1 #5f6672)",
        "start_border": "#4a505a",
        "start_hover": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #aab0bc,"
                       " stop:1 #7f8794)",
        "start_press": "#565c66",
        "item": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #c4c4cc,"
                " stop:1 #9a9aa4)",
        "item_border": "#7a7a84",
        "item_sel": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #82828c,"
                    " stop:1 #6e6e78)",
        "item_sel_border": "#56565e",
        "tray": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #9a9aa4,"
                " stop:1 #7a7a84)",
        "tray_edge": "#c4c4cc",
        "select": "#6a6a72",
        "edge": "#a0a0a8",
    },
}

#: The scheme a fresh install and an unknown name both fall back to.
DEFAULT_THEME = "Luna Blue"


def theme() -> str:
    """The theme's name as configured, always one that exists in
    :data:`THEMES` - an unknown value is Luna Blue, the way a blank wallpaper
    is Bliss."""
    name = str(config("theme", DEFAULT_THEME) or DEFAULT_THEME)
    return name if name in THEMES else DEFAULT_THEME


def palette() -> dict:
    """The colour set the two sheets are painted from, keyed as in
    :data:`THEMES`. A theme that names no entry still paints, because the
    default palette answers for it."""
    return THEMES.get(theme(), THEMES[DEFAULT_THEME])


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
    p = palette()
    return (
        "QWidget#berryDesktop { background: " + wall_css() + "; }"
        # The frame: a grey client area under a themed 2px border, the way
        # every XP window is drawn. ``round_corners`` squared or rounds it.
        "#addonWindow { background: #ece9d8; border: 2px solid "
        + p["frame"] + "; border-radius: "
        + ("3px" if round_corners() else "0px") + "; }"
        "#addonWindowBar { background: " + _bar_css(p) + "; min-height: "
        + str(scaled(caption_height())) + "px; }"
        "#addonWindowBar QLabel { color: " + caption_text()
        + "; font-weight: bold; font-size: "
        + str(scaled(caption_font())) + "px; }"
        "#addonWindowBar QPushButton { border: 1px solid #ffffff;"
        " border-radius: 2px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        " stop:0 #f1927c, stop:0.3 #e04e00, stop:1 #b93000);"
        " color: #ffffff; font-weight: bold; }"
        "#addonWindowBar QPushButton:hover { background: #ff7a3c; }"
        "#addonWindowBar QPushButton:pressed { background: #a02800; }"
        # The client-area furniture, so any program looks XP without trying.
        "#addonWindow QPushButton { background: qlineargradient(x1:0,y1:0,"
        " x2:0,y2:1, stop:0 #ffffff, stop:0.45 #f2f4f8, stop:0.55 #dfe4ee,"
        " stop:1 #cdd8ea); border: 1px solid " + p["edge"] + ";"
        " border-radius: 3px;"
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
        " border: 1px solid " + p["edge"] + ";"
        " selection-background-color: " + p["select"] + ";"
        " selection-color: #ffffff; }"
        "#addonWindow QGroupBox { border: 1px solid #d4d0c8;"
        " border-radius: 3px; margin-top: 6px; }"
        "#addonWindow QProgressBar { border: 1px solid " + p["edge"] + ";"
        " border-radius: 2px; text-align: center; background: #ffffff; }"
        "#addonWindow QProgressBar::chunk { background: " + p["select"]
        + "; }"
        # The Start flyout, painted like an XP menu: a themed header banner,
        # a white left column for the programs, a coloured right column for
        # the folders and a themed green footer holding the two ways out.
        "#berryStartMenu { background: #ffffff; }"
        "#berryStartHead { background: " + p["bar"] + "; color: #ffffff;"
        " font-weight: bold; padding: 8px 12px; }"
        "#berryStartSub { color: " + p["select"] + "; font-weight: bold;"
        " padding: 2px 6px; background: #ffffff; }"
        "#berryStartLeft { background: #ffffff; }"
        "#berryStartLeft QListWidget { background: #ffffff; border: none; }"
        "#berryStartLeft QListWidget::item:selected { background: "
        + p["select"] + "; color: #ffffff; border-radius: 3px; }"
        "#berryStartRight { background: " + p["frame"] + "; }"
        "#berryStartRight QListWidget { background: " + p["frame"]
        + "; border: none; color: #ffffff; }"
        "#berryStartRight QListWidget::item { padding: 4px 6px; }"
        "#berryStartRight QListWidget::item:selected"
        " { background: rgba(255, 255, 255, 0.25); border-radius: 3px; }"
        "#berryStartFoot { background: " + p["start"] + "; }"
        "#berryStartFoot QPushButton { background: transparent;"
        " border: none; color: #ffffff; font-weight: bold; padding: 5px; }"
        "#berryStartFoot QPushButton:hover {"
        " background: rgba(255, 255, 255, 0.2); }"
    )


def taskbar_sheet() -> str:
    """The themed bar, the Start button and the tray - the strip that is
    outside the canvas gets its own sheet for the same reason. The bar's
    height comes from ``task_height`` and the button padding from
    ``task_small``, so the row can be grown or shrunk like a real one."""
    p = palette()
    small = task_small()
    start_pad = "2px 8px" if small else "5px 16px"
    item_pad = "1px 4px" if small else "3px 8px"
    item_font = scaled(9 if small else 11)
    return (
        "#berryTaskbar { background: " + p["task"]
        + "; border-top: 2px solid " + p["task_edge"]
        + "; min-height: " + str(scaled(task_height())) + "px; }"
        "#berryStart { background: " + p["start"]
        + "; border: 1px solid " + p["start_border"]
        + "; border-radius: 0px 10px 10px 0px;"
        " color: #ffffff; font-weight: bold; padding: " + start_pad + "; }"
        "#berryStart:hover { background: " + p["start_hover"] + "; }"
        "#berryStart:pressed { background: " + p["start_press"] + "; }"
        "#berryTasks { background: transparent; border: none; }"
        "#berryTasks::item { background: " + p["item"]
        + "; border: 1px solid " + p["item_border"]
        + "; border-radius: 3px; color: #ffffff; padding: " + item_pad
        + "; font-size: " + str(item_font) + "px; margin-right: 3px; }"
        "#berryTasks::item:selected { background: " + p["item_sel"]
        + "; border: 1px solid " + p["item_sel_border"] + "; }"
        "#berryTray { background: " + p["tray"]
        + "; border-left: 1px solid " + p["tray_edge"] + "; }"
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
    # The frame's content is not one widget but a small tree - the body, the
    # column the spec wraps the grid in, and the grid's own scroll viewport -
    # and every one of them is an unnamed ``QWidget`` that the window-wide
    # dark rule pours straight through. So the rule reaches them all: the
    # whole subtree goes transparent and only the tiles, painted by their own
    # ``::item`` rule below, stay visible over the wallpaper.
    "#addonWindowBody { background: transparent; border: none; }"
    "#addonWindow QWidget { background: transparent; border: none; }"
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
     "hint": "Points for every shell window on the desktop"},
    {"key": "icon_px", "label": "Desktop icon size", "kind": "number",
     "minimum": 24, "maximum": 96,
     "hint": "The edge of a desktop tile's mark, before the desktop scale"},
    {"key": "caption_height", "label": "Title bar height", "kind": "number",
     "minimum": 16, "maximum": 48,
     "hint": "Pixels tall every window's caption is drawn"},
    {"key": "caption_font", "label": "Title bar text size", "kind": "number",
     "minimum": 7, "maximum": 24},
    {"key": "task_height", "label": "Taskbar height", "kind": "number",
     "minimum": 22, "maximum": 64},
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
     "hint": "Pre-filled for a machine that names none of its own, and the "
             "name the logon screen shows"},
]

#: ``profile`` is in the file but named in no field: the app's form writes
#: only what it shows, so a key it never showed survives the save unchanged -
#: which is what lets the shell keep its aliases and prompt on its own.
DEFAULTS = {
    "window_width": 520,
    "window_height": 360,
    "ui_scale": 100,
    "term_font": 11,
    # The title bar's own shape and colour, all painted from by
    # :func:`desktop_sheet`. The two caption colours default to blank - "" -
    # meaning "follow the theme", so a fresh install keeps the Luna gradient.
    "caption_height": 24,
    "caption_font": 11,
    "caption_text": "#ffffff",
    "active_caption": "",
    "inactive_caption": "",
    "bar_gradient": True,
    "round_corners": True,
    # The taskbar row and its buttons, read by :func:`taskbar_sheet` and
    # :func:`refresh_taskbar`. The last three are remembered preferences the
    # canvas cannot fully honour (one bar, always on show), kept faithful.
    "task_height": 30,
    "task_small": False,
    "task_lock": False,
    "task_autohide": False,
    # A desktop tile's mark edge, before the desktop scale - the density dial
    # :func:`icon_layer_spec` sizes the whole icon layer from.
    "icon_px": 48,
    "wallpaper": "Bliss",
    "wallpaper_color": "#2356b7",
    "wallpaper_file": "",
    # Named in no field, so the app's form leaves it be and Display
    # Properties writes it whole: the colour scheme both sheets are painted
    # from, one of the names in :data:`THEMES`.
    "theme": DEFAULT_THEME,
    "clock_on": True,
    "default_user": "root",
    # Named in no field, so the app's form leaves it be and the shell owns
    # it whole: ``profile`` is the alias table, its PATH additions and its
    # prompt - what Command Prompt reads to expand a command.
    "profile": {
        "aliases": {},
        "path_extra": [],
        "prompt": "user@berryvm:\\w$ ",
    },
    # The Web Browser's shelf of favourite pages: name over address, picked
    # from a combo. Named in no field, so the app's form never shows it and
    # the browser owns the whole key.
    "bookmarks": [
        {"name": "Wikipedia", "url": "https://www.wikipedia.org"},
        {"name": "Example", "url": "https://example.com"},
    ],
    # Notepad's shelf of recently opened files: full paths, newest first.
    # Named in no field, so the app's form never shows it and the editor
    # owns the whole key.
    "recent": [],
    # The XP cosplay: a boot splash, a logon pick and a startup chime, each
    # shown only when a real window is up (they degrade to nothing on a
    # headless run). Named in no field, so the app's form leaves them be and
    # a person edits them straight in the config file.
    "boot_splash": True,
    "logon": True,
    "startup_sound": True,
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
         "shortcuts": [{"keys": "Alt+Tab", "event": "shortcut"},
                       {"keys": "Up", "event": "shortcut"},
                       {"keys": "Down", "event": "shortcut"},
                       {"keys": "Left", "event": "shortcut"},
                       {"keys": "Right", "event": "shortcut"},
                       {"keys": "B", "event": "shortcut"},
                       {"keys": "A", "event": "shortcut"}],
         "children": []},
        {"kind": "row", "id": TASKBAR, "object_name": "berryTaskbar",
         "children": [
             {"kind": "button", "id": START_BTN, "object_name": "berryStart",
              "text": "Start", "on": ["clicked"],
              "tooltip": "Open one of BerryVM's programs; a program already "
                         "open is raised rather than started again"},
             {"kind": "list", "id": TASK_LIST, "object_name": "berryTasks",
              "on": ["selected", "context"], "expand": True,
              "flow": "horizontal",
              "tooltip": "One button per window on the desktop. Select one "
                         "and it comes forward; right-click for the "
                         "taskbar's window menu."},
             {"kind": "row", "id": TRAY, "object_name": "berryTray",
              "children": [
                  {"kind": "button", "id": "tray_net", "text": "\u25d1",
                   "object_name": "berryTrayBtn", "on": ["clicked"],
                   "tooltip": "Network: shows whether the app's file "
                              "session and this machine's link are up"},
                  {"kind": "button", "id": "tray_wbr", "text": "\u25ce",
                   "object_name": "berryTrayBtn", "on": ["clicked"],
                   "tooltip": "Open the Web Browser"},
                  {"kind": "button", "id": "tray_show", "text": "\u25a1",
                   "object_name": "berryTrayBtn", "on": ["clicked"],
                   "tooltip": "Show desktop: minimise every window at once"},
                  {"kind": "label", "id": CLOCK, "object_name": "berryClock",
                   "text": ""},
              ]},
         ]},
    ],
}

# --- module state ---

api = None                     # filled in by init()
always_on_top: set = set()     # window ids kept above the rest
berry_seq: int = 0             # ``bp<seq>`` ids, one editor per file
view_seq: int = 0              # ``pv<seq>`` ids, one viewer per picture
console_seq: int = 0           # ``cs<seq>`` ids, one local shell per window
opened: dict = {}              # win_id -> {"title", "kind", extra}
berrypads: dict = {}           # win_id -> {"path", "origin", "title"}
viewers: dict = {}             # win_id -> {"path", "origin", "size"}
terminals: dict = {}           # terminal id -> win_id it lives inside
paint_state: dict = {}         # win_id -> {"color", "width", "tool"} for Paint
media_state: dict = {}         # win_id -> {"tracks", "index"} for Media Player
pane_pick: dict = {}           # table id -> the name its cursor is on
pane_rows: dict = {}           # table id -> {display name: {"path", ...}}
pane_path: dict = {}           # pane prefix -> the folder that side shows
home_ready: bool = False       # ``_ensure_home()`` has run in this process

#: The desktop's own layout memory. Positions are keyed by a tile's
#: persistent name (the caption it shipped with, before any rename);
#: renames maps that key to what the person typed.
#: Attention is which window ids are currently flashing on the bar, with
#: the second they stop at beside them. Minimized is what the desktop
#: knows is hidden, so a taskbar row can look pressed even without a
#: round trip to the canvas.
_desk_positions: dict = {}
_desk_renames: dict = {}
minimized: dict = {}
maximized: set = set()          # window ids the desktop reports grown full
attention: dict = {}
_flash_on: bool = False
_splash_step: int = 0           # how far the boot splash's bar has come
_konami: list = []              # the lone keys seen, waiting for the sequence
_SCHED: dict = {}               # cosplay timers by name, so they can be stopped


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
    open_icon_layer()
    refresh_taskbar()
    # The network mark is refreshed on its own beat, apart from the clock:
    # a person who switches the clock off still expects the tray to say
    # whether the machine is online, and a link can come and go between the
    # clock's thirty-second ticks.
    _update_net_tray()
    api.schedule.every(15, _update_net_tray)
    if bool(config("clock_on", True)):
        api.schedule.every(30, tick_clock)
        tick_clock()
    api.schedule.every(0.5, tick_flash)
    api.log("BerryVM ready: an XP-styled desktop in one tab.")
    _welcome()
    _xp_cosplay()


def _xp_cosplay() -> None:
    """Run the boot splash, the startup chime and the logon picker, each of
    which makes itself scarce unless a real window is up. Wrapped whole so a
    hiccup in one never stops the desktop from coming up."""
    for step in (boot_splash, startup_sound, logon_screen):
        try:
            step()
        except Exception as error:
            if api is not None:
                api.log(f"berryvm: {step.__name__} skipped: {error}")


def shutdown() -> None:
    """Close the tab; the app stops any shell inside on its way out, and any
    local console loses its process here - a child nobody owns is a zombie."""
    global home_ready, berry_seq, view_seq, console_seq
    global _desk_positions, _desk_renames
    global minimized, attention, _flash_on
    global _splash_step, _konami
    # A running local shell is owned by the terminal widget's worker thread;
    # closing the tab below has the app stop every terminal inside it, so the
    # addon only drops its own bookkeeping here.
    opened.clear()
    berrypads.clear()
    viewers.clear()
    terminals.clear()
    paint_state.clear()
    media_state.clear()
    pane_pick.clear()
    pane_rows.clear()
    pane_path.clear()
    berry_seq = 0
    view_seq = 0
    console_seq = 0
    always_on_top.clear()
    home_ready = False
    _desk_positions = {}
    _desk_renames = {}
    minimized = {}
    maximized.clear()
    attention = {}
    _flash_on = False
    # The cosplay: stop its timers and drop its bookkeeping, so a restart
    # shows a fresh splash rather than resuming a bar that was left mid-fill.
    for _name in list(_SCHED):
        _stop_sched(_name)
    _splash_step = 0
    _konami = []
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
    """The logon bubble: a hello that says whether the app's file session is
    up, because that is the half the file panes and viewer work on."""
    if api is None:
        return
    try:
        if api.device.is_connected("sftp"):
            profile = api.session.profile() or {}
            host = str(profile.get("host") or "the device")
            api.ui.popup("Welcome to BerryVM",
                         f"Desktop is up, riding the file session to "
                         f"{host}. Double-click an icon, or press Start.")
        else:
            api.ui.popup("Welcome to BerryVM",
                         "Desktop is up. The file session is not open - "
                         "the device pane will say so until it is.")
    except Exception:                        # a toast is never worth a stack
        api.log("berryvm: welcome popup skipped")


# --- the XP cosplay: boot splash, logon, startup chime, easter eggs ---

#: Gag tips that cycle under the boot bar, and the parody version the About
#: box hangs on each running program.
SPLASH_TIPS = [
    "Tip: double-click an icon, or press Start.",
    "Tip: your files live in a home that looks like Linux's.",
    "Tip: ll is an alias. So is dir. Neither is better.",
    "Tip: this blue screen is the friendly kind.",
    "Almost there...",
]
PARODY_VERSIONS = {
    P_EXPLORER: "6.0.2900 (My Computer, SP3)",
    P_NOTEPAD: "5.1 (notepad.exe, uncompromised)",
    P_CONSOLE: "5.1 (cmd.exe, wearing a penguin)",
    P_BROWSER: "6.0 (definitely not Internet Explorer)",
    P_MEDIA: "9.0 (Windows Media Rip-off)",
    P_PAINT: "5.1 (bucket included)",
}


def _gui_live() -> bool:
    """Whether a real window is up to host the cosplay.

    The splash, the logon picker and the chime are pure presentation: on a
    headless run - an off-screen test, or a build with no window - there is
    nothing to show them on, so they make themselves scarce rather than leave
    a frame stranded on the desktop. ``ui.window()`` is the tell: the app
    answers with its main window, a test answers with nothing."""
    if api is None:
        return False
    try:
        return api.ui.window() is not None
    except Exception:
        return False


def _splash_spec() -> dict:
    """The boot bar: the brand line, a progress meter, a rotating tip, held
    apart by springs so it sits in the middle of a bare black frame."""
    return {"kind": "column", "children": [
        {"kind": "spacer"},
        {"kind": "label", "id": f"{SPLASH}_brand",
         "text": f"BerryVM {ADDON.get('version')}",
         "align": "center"},
        {"kind": "label", "id": f"{SPLASH}_load",
         "text": "Loading...", "align": "center"},
        {"kind": "progress", "id": f"{SPLASH}_bar", "maximum": 100,
         "value": 0},
        {"kind": "label", "id": f"{SPLASH}_tip", "text": SPLASH_TIPS[0],
         "align": "center", "word_wrap": True},
        {"kind": "spacer"},
    ]}


def boot_splash() -> None:
    """Stand the loading frame up and let a timer fill its bar, then fold it
    away to reveal the desktop. Non-blocking: it is a window, not a modal, so
    nothing waits on it, and it only ever shows when a real window can hold
    it."""
    global _splash_step
    if api is None or not _gui_live() or not config("boot_splash", True):
        return
    _splash_step = 0
    wide, tall = _screen_geometry()
    try:
        open_window({
            "id": SPLASH, "title": "BerryVM", "chrome": False,
            "closable": False, "resizable": False,
            "x": int(wide * 0.3), "y": int(tall * 0.34),
            "width": scaled(360), "height": scaled(170),
            "spec": _splash_spec(),
        })
        raise_window(SPLASH)
        _SCHED["splash"] = api.schedule.every(0.15, _splash_tick)
    except Exception as error:
        api.log(f"berryvm: boot splash skipped: {error}")


def _splash_tick() -> None:
    """Advance one notch of the bar and change the tip; fold the splash away
    once the bar fills. A no-op the moment the frame is gone, so a lingering
    timer never reaches a window that has closed."""
    global _splash_step
    if api is None:
        return
    if SPLASH not in open_window_ids():
        _stop_sched("splash")
        return
    _splash_step += 1
    shown = min(100, _splash_step * 7)
    try:
        api.tabs.set_widget(TAB, f"{SPLASH}_bar", "value", shown)
        api.tabs.set_widget(TAB, f"{SPLASH}_tip", "text",
                            SPLASH_TIPS[_splash_step % len(SPLASH_TIPS)])
    except Exception:
        pass
    if shown >= 100:
        close_window(SPLASH)
        _stop_sched("splash")


def _logon_users() -> list:
    """The names the logon picker offers: the default user first, then
    whoever is running the app - the accounts a person might mean, without
    asking them to type one."""
    users: list = []
    default = str(config("default_user", "root") or "").strip()
    if default and default not in users:
        users.insert(0, default)
    try:
        import getpass
        os_user = str(getpass.getuser() or "").strip()
        if os_user and os_user not in users:
            users.append(os_user)
    except Exception:
        pass
    return users or [default or "user"]


def logon_spec() -> dict:
    """The XP-style welcome screen: a list of faces and the two ways in."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": f"{LOGON}_head",
         "text": "To begin, choose your name"},
        {"kind": "list", "id": f"{LOGON}_who", "items": _logon_users(),
         "on": ["selected", "activated"], "expand": True,
         "tooltip": "Double-click a name to log on, or pick one and press "
                    "Log On."},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{LOGON}_ok", "text": "Log On",
             "on": ["clicked"], "expand": True},
            {"kind": "button", "id": f"{LOGON}_skip", "text": "Skip",
             "on": ["clicked"], "expand": True,
             "tooltip": "Leave the desktop's user exactly as it is"},
        ]},
    ]}


def logon_screen() -> None:
    """Show the welcome picker once per session, skippable. It is a plain
    frame so its person can also close it; picking a name feeds the config's
    ``default_user`` and folds it away."""
    if api is None or not _gui_live() or not config("logon", True):
        return
    try:
        wide, tall = _screen_geometry()
        open_window({
            "id": LOGON, "title": "Welcome to BerryVM",
            "closable": True,
            "x": int(wide * 0.32), "y": int(tall * 0.2),
            "width": scaled(320), "height": scaled(360),
            "spec": logon_spec(),
        })
        raise_window(LOGON)
    except Exception as error:
        api.log(f"berryvm: logon screen skipped: {error}")


def logon_picked(name: str) -> None:
    """One name chosen on the welcome screen: make it the user the desktop
    offers, then put the picker away."""
    if api is None:
        return
    name = str(name or "").strip()
    if not name:
        return
    try:
        api.config.set("default_user", name)
    except Exception:
        pass
    close_window(LOGON)
    api.ui.notify(f"BerryVM: welcome, {name}.")


def startup_sound() -> None:
    """Play the startup chime through the same QtMultimedia leaf Media Player
    uses, off a hidden one-pixel frame that folds itself when the sound ends.
    Silent no-op when there is no window, no file or no multimedia."""
    if api is None or not _gui_live() or not config("startup_sound", True):
        return
    wav = home_root() / "etc" / "welcome.wav"
    if not wav.is_file():
        return
    try:
        open_window({
            "id": "sfx", "title": "", "chrome": False, "closable": False,
            "resizable": False, "lower": True, "x": 0, "y": 0,
            "width": 1, "height": 1,
            "spec": {"kind": "player", "id": "sfx_player", "volume": 60,
                     "on": ["done", "error"]},
        })
        api.tabs.set_widget(TAB, "sfx_player", "open", str(wav))
        api.tabs.set_widget(TAB, "sfx_player", "play", True)
    except Exception as error:
        api.log(f"berryvm: startup chime skipped: {error}")


def blue_screen_of_fun() -> None:
    """The friendly blue screen: a parody of the crash box, all joke and no
    crash, reached from About. An information dialog, so it can only ever be
    read and dismissed."""
    if api is None:
        return
    api.ui.information(
        "BerryVM",
        "A problem has been detected and BerryVM has been shut down to "
        "prevent damage to your desk plant.\n\n"
        "CRAPPY_DESKTOP_ERROR\n\n"
        "If this is the first time you have seen this screen, try turning "
        "the tab off and on again. BerryVM will continue to pretend nothing "
        "happened.\n\n"
        "Press any key to continue (there is no any key).")


def konami_seen(key: str) -> None:
    """Collect one lone-key shortcut toward the Konami code; when the six
    match, flip the wallpaper to Night (and back). Keys outside the sequence
    drop the buffer, so an errant arrow never stacks a false hit."""
    global _konami
    key = str(key or "").lower()
    if key not in KONAMI:
        _konami = []
        return
    _konami.append(key)
    _konami = _konami[-len(KONAMI):]
    if tuple(_konami) == KONAMI:
        _konami = []
        toggle_night()


def toggle_night() -> None:
    """The Konami payoff: swap the wallpaper between Bliss and Night."""
    if api is None:
        return
    current = str(config("wallpaper", "Bliss") or "Bliss")
    new = "Bliss" if current == "Night" else "Night"
    try:
        api.config.set("wallpaper", new)
    except Exception:
        return
    apply_theme()
    api.ui.notify(f"BerryVM: up, up, down, down... wallpaper is now {new}.")


def _running_programs() -> list:
    """The programs on the desktop right now, as ``caption (parody version)``
    lines, for the About box to read out."""
    lines = []
    for win_id in program_window_ids():
        caption = CAPTIONS.get(win_id) or (opened.get(win_id) or {}).get(
            "title") or win_id
        version = PARODY_VERSIONS.get(win_id)
        lines.append(f"{caption}" + (f" ({version})" if version else ""))
    return lines


def _stop_sched(name: str) -> None:
    """Stop a saved cosplay timer, if one is still standing."""
    timer = _SCHED.pop(name, None)
    if timer is None:
        return
    try:
        timer.stop()
    except Exception:
        pass



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


def _num(key: str, low: int, high: int) -> int:
    """One integer config key, clamped to the range the slider offers, so a
    hand-edit outside the dial still paints something sensible."""
    try:
        number = int(config(key, DEFAULTS[key]))
    except (TypeError, ValueError, KeyError):
        return int(DEFAULTS.get(key, low))
    return max(low, min(high, number))


def caption_height() -> int:
    """A title bar's height in pixels, before the desktop scale."""
    return _num("caption_height", 16, 48)


def caption_font() -> int:
    """A title bar's caption text size, in points."""
    return _num("caption_font", 7, 24)


def caption_text() -> str:
    """The colour the caption's words are painted in."""
    return str(config("caption_text", DEFAULTS["caption_text"])
               or DEFAULTS["caption_text"])


def active_caption() -> str:
    """The active title bar's own colour, or ``""`` to follow the theme."""
    return str(config("active_caption", "") or "").strip()


def inactive_caption() -> str:
    """The inactive title bar's colour, kept as a preference; the canvas paints
    every bar alike, so it rounds the panel out rather than showing."""
    return str(config("inactive_caption", "") or "").strip()


def task_height() -> int:
    """The taskbar row's height in pixels, before the desktop scale."""
    return _num("task_height", 22, 64)


def icon_px() -> int:
    """A desktop tile's mark edge in pixels, before the desktop scale."""
    return _num("icon_px", 24, 96)


def bar_gradient() -> bool:
    """Whether the title bar is a vertical gradient or a flat colour."""
    return bool(config("bar_gradient", True))


def round_corners() -> bool:
    """Whether a window frame's corners are rounded XP-style or squared."""
    return bool(config("round_corners", True))


def task_small() -> bool:
    """Whether the taskbar wears classic small buttons."""
    return bool(config("task_small", False))


def task_lock() -> bool:
    """Whether the taskbar is locked - a remembered preference."""
    return bool(config("task_lock", False))


def task_autohide() -> bool:
    """Whether the taskbar auto-hides - a remembered preference."""
    return bool(config("task_autohide", False))


def _shade(colour: str, amount: float) -> str:
    """One ``#rrggbb`` nudged lighter (a positive amount) or darker (a
    negative one) by a fraction, for the two ends of a solid caption's
    gradient. Anything that is not a plain six-digit hex is handed back."""
    text = str(colour or "").strip()
    if not text.startswith("#") or len(text) != 7:
        return text
    try:
        r = int(text[1:3], 16)
        g = int(text[3:5], 16)
        b = int(text[5:7], 16)
    except ValueError:
        return text

    def one(x: int) -> int:
        moved = x + (255 - x) * amount if amount >= 0 else x * (1.0 + amount)
        return max(0, min(255, int(moved)))

    return "#{:02x}{:02x}{:02x}".format(one(r), one(g), one(b))


def _bar_css(p: dict) -> str:
    """The active title bar's background, from the caption colour and the
    gradient switch: a chosen colour becomes a flat fill or a shaded gradient,
    and with no colour chosen the theme's own bar (or its flat frame, when the
    gradient is off) answers."""
    custom = active_caption()
    if bar_gradient():
        if custom:
            hi = _shade(custom, 0.22)
            lo = _shade(custom, -0.28)
            return (f"qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {hi},"
                    f" stop:0.5 {custom}, stop:1 {lo})")
        return p["bar"]
    return custom or p["frame"]


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


# --- the VM's home folder ---

#: Folder names created inside ``addons/_data/berryvm`` on first init: this is
#: the shape a home directory is expected to have, kept small and readable.
HOME_DIRS = ("Desktop", "bin", "tmp", "config")

#: The top-level folders of the Linux-shaped tree the VM models, made under
#: the root beside :data:`HOME_DIRS` so a shell started here reads like it is
#: on a Linux box: ``bin etc home tmp usr var``.
LINUX_DIRS = ("bin", "etc", "home", "tmp", "usr", "var")

#: Folders made inside the VM user's own home, the desktop-per-user shape.
USER_SUBDIRS = ("Desktop", "Documents", "Downloads", "Pictures", "Music")

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
    is the shape inside it - a Linux tree (``bin etc home tmp usr var`` with a
    ``home/<user>`` full of the usual per-user folders) beside the ``Desktop``
    and ``config`` the desktop itself keeps. So the console has somewhere to
    start that looks like a real home, and the settings mirror has a place to
    sit. A second call is a no-op rather than a repeat mkdir.
    """
    global home_ready
    root = home_root()
    if home_ready:
        return root
    targets = [root / name for name in HOME_DIRS]
    targets += [root / name for name in LINUX_DIRS]
    user_home = vm_home()
    targets += [user_home / name for name in USER_SUBDIRS]
    for folder in targets:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    _seed_fs_files(root, user_home)
    home_ready = True
    return root


def vm_user() -> str:
    """The name the VM's Linux home is filed under: the user the profile
    offers, or this machine's own login, cleaned to a single safe word."""
    raw = (str(config("default_user", "") or "").strip()
           or os.environ.get("USERNAME") or os.environ.get("USER") or "user")
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))
    return cleaned or "user"


def vm_home() -> Path:
    """``addons/_data/berryvm/home/<user>`` - the shell's real starting point
    and the ``~`` the prompt shows."""
    return home_root() / "home" / vm_user()


def shell_env() -> dict:
    """The environment handed to a local shell: a set ``HOME`` in the Linux
    tree, ``bin`` on the ``PATH``, a friendly prompt and the OS's temp pointed
    at the VM's own ``tmp``, so the machine's shell behaves like it lives here.

    Everything is merged over the real process environment by the worker (the
    ``env`` value on ``spawn``), so only these few keys are overridden and the
    shell still finds its own system tools."""
    root = home_root()
    home = vm_home()
    sep = ";" if os.name == "nt" else ":"
    entries = [str(root / "bin"), str(home / "bin"), os.environ.get("PATH", "")]
    for extra in (read_profile().get("path_extra") or []):
        entries.insert(1, str(extra))
    who = vm_user()
    prompt = read_profile().get("prompt") or f"{who}@berryvm:\\w$ "
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": sep.join(p for p in entries if p),
        "USER": who,
        "USERNAME": who,
        "LOGNAME": who,
        "TERM": "xterm-256color",
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
        "PS1": prompt,
    }


def _seed_fs_files(root: Path, home: Path) -> None:
    """Write the couple of plain files that make the tree read like Linux: an
    ``/etc`` password and login-banner gag, and the user's ``.bashrc`` from
    the profile. Every one is best-effort - a folder this OS will not let the
    addon write is skipped, not fatal."""
    who = vm_user()
    try:
        (root / "etc" / "passwd").write_text(
            f"{who}:x:1000:1000:BerryVM user:{home}:/bin/sh\n")
    except OSError:
        pass
    try:
        (root / "etc" / "issue").write_text(
            "BerryVM - a Windows XP desktop with a Linux-shaped home.\n"
            "Type 'help' for the commands this shell knows.\n")
    except OSError:
        pass
    mirror_bashrc(home)


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


# --- the shell profile: aliases, extra PATH and the prompt ---

#: The mirror of the profile into the VM's ``config`` folder, so the editor
#: can open it like any other file and a hand-edit survives a reload.
PROFILE_NAME = "profile.json"


def profile_file() -> Path:
    """Where the profile mirror lives inside the VM's own config folder."""
    return home_root() / "config" / PROFILE_NAME


def _clean_profile(raw: Any) -> dict:
    """One profile dict in the shape the addon uses: a string-keyed alias
    table with string values, a list of PATH additions, and one prompt line.
    Anything malformed in a saved file is dropped rather than raised, so a
    hand-edit cannot strand the shell."""
    raw = raw if isinstance(raw, dict) else {}
    aliases: dict[str, str] = {}
    given = raw.get("aliases")
    if isinstance(given, dict):
        for name, target in given.items():
            key = str(name).strip()
            value = " ".join(str(target).split())
            if key and value:
                aliases[key] = value
    extra = raw.get("path_extra")
    path_extra = [str(one).strip() for one in (extra or [])
                  if str(one or "").strip()]
    prompt = str(raw.get("prompt") or DEFAULTS["profile"]["prompt"])
    return {"aliases": aliases, "path_extra": path_extra, "prompt": prompt}


def read_profile() -> dict:
    """The addon's shell profile: the alias table, the PATH additions and the
    prompt the console is handed on spawn.

    The config store is the source of truth (``profile`` rides in ``DEFAULTS``
    but names no form field, so a form that shows only its own keys leaves it
    be); the ``profile.json``
    mirror is read only when nothing is stored yet, so a file written by hand
    on a fresh install is picked up. Either way the dict is cleaned on the
    way out, so a caller never has to second-guess its shape."""
    stored = config("profile", None)
    if isinstance(stored, dict) and stored:
        return _clean_profile(stored)
    try:
        loaded = json.loads(profile_file().read_text() or "{}")
    except (OSError, ValueError):
        loaded = {}
    if isinstance(loaded, dict) and loaded:
        return _clean_profile(loaded)
    return _clean_profile(DEFAULTS["profile"])


def alias_map() -> dict:
    """The profile's alias table, ready to hand a spawned shell: every name
    mapped to the command it stands for, blank pairs already dropped."""
    return dict(read_profile().get("aliases") or {})


def mirror_bashrc(home: Path) -> None:
    """Write the profile into the home as files a real shell would source.

    ``.bashrc`` carries the POSIX ``alias`` lines, an ``export PS1`` and the
    PATH additions, so a ``sh``/``bash`` started here honours the same table
    the terminal worker expands. Alongside it a ``bin/aliases.doskey`` macro
    file spells the aliases out in ``doskey`` form for ``cmd.exe``, which has
    no rc file of its own. Both are best-effort and neither is fatal: the
    generic worker expands aliases whatever the shell underneath does."""
    prof = read_profile()
    aliases = prof.get("aliases") or {}
    lines = ["# written by BerryVM's Shell Profile; edit it there."]
    for name, target in aliases.items():
        lines.append(f"alias {name}={target!r}")
    lines.append(f"export PS1={prof.get('prompt')!r}")
    for extra in prof.get("path_extra") or []:
        lines.append(f'export PATH="{extra}:$PATH"')
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / ".bashrc").write_text("\n".join(lines) + "\n")
    except OSError:
        pass
    macro = [f"doskey {name}={target} $*"
             for name, target in aliases.items()]
    try:
        (home / "bin").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "aliases.doskey").write_text("\n".join(macro) + "\n")
    except OSError:
        pass


def mirror_profile(prof: dict | None = None) -> None:
    """Write the profile to its ``config`` mirror and refresh the sourced
    shell files, so the folder and the config always say the same thing."""
    prof = _clean_profile(prof if isinstance(prof, dict) else read_profile())
    target = profile_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(prof, indent=2, sort_keys=True))
    except OSError as error:
        if api is not None:
            api.log(f"berryvm: profile.json not written: {error}")
    mirror_bashrc(vm_home())


def write_profile(prof: dict) -> dict:
    """Commit a profile: clean it, store it as the addon's config (so a form
    that never shows the key leaves it be), then re-mirror the file and the
    rc files. Hands back the cleaned dict that landed."""
    clean = _clean_profile(prof)
    if api is not None:
        api.config.set("profile", clean)
    mirror_profile(clean)
    return clean


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
    return loaded


def mirror_desk() -> dict:
    """Write the in-memory layout back to ``desktop.json`` and hand it over.

    Called after every event that changes one of the three lists. The
    module keeps ``_desk_positions`` and friends as its live view so a
    drag-and-drop on the icon layer costs one write, not one per tile.
    """
    stored = {"positions": _desk_positions,
              "renames": _desk_renames}
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
    global _desk_positions, _desk_renames
    loaded = read_desk()
    _desk_positions = dict(loaded.get("positions") or {})
    _desk_renames = dict(loaded.get("renames") or {})


# --- the taskbar and the clock ---

def tick_clock() -> None:
    """Put the current time on the tray; a clock turned off in the config
    writes an empty string rather than hiding the label. The label only
    hears when the line actually moves - the tick runs every thirty seconds
    and the text changes once a minute. The tray's network mark is refreshed
    here too, so file-session changes reach the bar on the same beat."""
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
    """The desktop's windows with the icon layer and the Start flyout left
    out: the rows a person is offered, and the windows a Log Off shuts. The
    flyout is a menu, not a program, so it never takes a taskbar button or a
    place in the Alt+Tab ring."""
    return [win_id for win_id in open_window_ids()
            if win_id not in (ICONS, P_START)]


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
                 bool(minimized.get(win_id)) or win_id in maximized)
                for win_id in program_window_ids())
    state = (scaled(task_height()),
             scaled(12 if task_small() else TASKBAR_ICON), row)
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
    the person wants to look at the other thing. Any window marked always-on-
    top is raised again behind it, so a pin survives being under a raise."""
    if win_id:
        api.tabs.set_widget(TAB, DESKTOP, "raise_window", str(win_id))
        if win_id != ICONS:
            keep_icons_behind()
        _restack_top(skip=str(win_id))


def _restack_top(skip: str = "") -> None:
    """Re-raise every always-on-top window still on the desktop, so a pin
    stays above the frames after any other raise. ``skip`` is the window just
    raised - if it is itself pinned, it is already on top and needs no second
    call."""
    if api is None or not always_on_top:
        return
    live = open_window_ids()
    for top_id in list(always_on_top):
        if top_id in live and top_id != skip and top_id != ICONS:
            api.tabs.set_widget(TAB, DESKTOP, "raise_window", str(top_id))


def is_pinned(win_id: str) -> bool:
    """Whether one window is currently kept above the rest."""
    return str(win_id) in always_on_top


def toggle_top(win_id: str) -> bool:
    """Pin a window above the others, or let it go. Hands back the pin's new
    state. A pinned window is raised the moment it is pinned and re-raised
    after every future raise (:func:`_restack_top`), the only way a drawn
    frame can be kept forward without native window-level support."""
    if api is None or not win_id:
        return False
    win_id = str(win_id)
    if win_id in always_on_top:
        always_on_top.discard(win_id)
        return False
    always_on_top.add(win_id)
    raise_window(win_id)
    return True


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
    """Drop every bit of addon-side state that a closed window owned. A local
    shell is stopped by the app when the frame is taken down, so the console's
    only trace here is the terminal id it registered."""
    win_id = str(win_id)
    opened.pop(win_id, None)
    berrypads.pop(win_id, None)
    viewers.pop(win_id, None)
    paint_state.pop(win_id, None)
    media_state.pop(win_id, None)
    always_on_top.discard(win_id)
    maximized.discard(win_id)
    minimized.pop(win_id, None)
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
             "grid": True, "icon_size": scaled(icon_px()),
             "tile_width": scaled(icon_px() + 44),
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
    put the icon layer back to rights, and the plain refresh."""
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
            # A tile is only a shortcut; deleting one is not offered here,
            # so a stray right-click is undoable by the desktop's own Reset
            # Icon Names.
            _note(ICONS, f"{text} is a program tile; leave it on the "
                         f"desktop or Reset Icon Names to bring back a "
                         f"renamed one.")
        return
    picked = api.ui.choose([
        "Open My Computer", "Notepad", "Change Desktop Background...",
        "New Text Document", "New Folder",
        "Auto Arrange Icons", "Reset Icon Names",
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
    elif picked == "Refresh":
        relayout_desktop()
        apply_theme()


def _tray_stub(name: str) -> None:
    """A tray icon was pressed. BerryVM has no OS handle for a real network
    centre, so the mark for the network button reads what the app itself
    can see - the file session and this machine's link - and the Web Browser
    button opens the browser window; anything else falls back to a plain
    "not wired" note."""
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


def _link_up() -> bool:
    """Whether this machine has a live route to the network right now.

    An addon is handed no OS handle for the wireless radio, so the desktop
    cannot read a WiFi card directly. What it *can* do is ask the operating
    system which interface it would use to reach the wider net: an unconnected
    ``SOCK_DGRAM`` socket's ``connect`` chooses a route without ever sending a
    packet, so it answers instantly and fails only when there is no way out.
    That is the honest signal a person means by "the browser works" - if a
    page loads, this succeeds too. Any hiccup is read as offline rather than
    raised, because the tray mark must never be the reason the desktop trips.
    """
    import socket
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass


def _net_summary() -> str:
    """The two lines the tray's network popup says: this machine's link and
    the app's file (SFTP) session. The link is read with :func:`_link_up` - a
    desktop that can load a web page is online whether or not the app has
    opened a device session, so the mark no longer goes dark just because the
    file session is down."""
    sftp = False
    host = ""
    if api is not None:
        try:
            sftp = bool(api.device.is_connected("sftp"))
        except Exception:                       # a headless run has no device
            sftp = False
        try:
            profile = api.session.profile() or {}
            host = str(profile.get("host") or "")
        except Exception:
            host = ""
    # WiFi is the machine's link; a device session that is up is proof of a
    # network too, so the mark reads online if either the probe or the file
    # session says so.
    wifi = _link_up() or sftp
    lines = [
        f"WiFi: {'connected' if wifi else 'offline'}",
        f"Files: {'connected' if sftp else 'offline'}"
        + (f" ({host})" if sftp and host else ""),
    ]
    return "\n".join(lines)


def _update_net_tray() -> None:
    """Put the current file-session / WiFi answer on the network mark. The
    glyph changes when the link does, so a glance at the bar says whether
    the device is reachable; the tooltip spells the two lines out."""
    if api is None:
        return
    text = _net_summary()
    wifi = "WiFi: connected" in text
    sftp = "Files: connected" in text
    # A filled circle for anything live, a hollow one for none; the file
    # session on top of WiFi gets the quarter-split disk so the two are told
    # apart.
    glyph = "\u25d0" if (sftp and wifi) else "\u25cf" if wifi else "\u25cb"
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


# --- the Start flyout: the XP two-pane menu ---

#: The right-hand column: the folders a person reaches for, My Computer, and
#: the way to the Control Panel. Program launches live on the left; the two
#: ways out are the footer's own buttons.
START_SIDE = ["My Documents", "My Pictures", "My Music", "My Computer", "",
              "Control Panel"]


def _start_icon(pid: str) -> str:
    """The mark one flyout row wears: the program's own tile picture, so the
    menu and the desktop agree on what each program looks like."""
    return TILE_ICONS.get(pid, ICON_FILE)


def _flyout_left() -> tuple[list, list]:
    """The two left-hand blocks of an XP Start menu: a short list of the
    programs a person is most likely to want, then everything. Each row is a
    caption wearing its tile mark, so a list ``activated`` hands back a word
    that maps straight through :data:`BY_CAPTION`."""
    frequent_ids = (P_EXPLORER, P_NOTEPAD, P_CONSOLE, P_BROWSER)
    frequent = [{"text": CAPTIONS[pid], "icon": _start_icon(pid)}
                for pid in frequent_ids if pid in CAPTIONS]
    every = [{"text": CAPTIONS[pid], "icon": _start_icon(pid)}
             for pid, _cap in PROGRAMS]
    return frequent, every


def _flyout_right() -> list:
    """The right-hand column, each row wearing a mark of its own: the folders,
    My Computer, a blank that reads as a separator, then Control Panel."""
    marks = {"My Documents": ICON_FOLDER, "My Pictures": ICON_PICTURE,
             "My Music": ICON_MEDIA, "My Computer": ICON_COMPUTER,
             "Control Panel": _start_icon(P_SETTINGS)}
    return [{"text": line, "icon": marks.get(line)} for line in START_SIDE]


def start_flyout_spec() -> dict:
    """The flyout's own spec, laid out the way an XP Start menu is: a blue
    header banner over two columns - the programs on the left, the folders and
    the Control Panel on the right - and a green footer holding the two ways
    out. Rebuilt each time it is raised, so it always reads the live config."""
    user = str(config("default_user", "root") or "root").strip() or "user"
    frequent, every = _flyout_left()
    return {"kind": "column", "object_name": "berryStartMenu", "children": [
        {"kind": "label", "id": f"{P_START}_head",
         "object_name": "berryStartHead",
         "text": f"BerryVM  \u2014  {user}"},
        {"kind": "splitter", "sizes": [3, 2], "children": [
            {"kind": "column", "object_name": "berryStartLeft", "children": [
                {"kind": "label", "object_name": "berryStartSub",
                 "text": "Programs"},
                {"kind": "list", "id": f"{P_START}_apps", "items": frequent,
                 "on": ["activated"], "expand": True, "icon_size": scaled(24),
                 "tooltip": "Double-click a program to start it"},
                {"kind": "label", "object_name": "berryStartSub",
                 "text": "All Programs"},
                {"kind": "list", "id": f"{P_START}_all", "items": every,
                 "on": ["activated"], "icon_size": scaled(24),
                 "tooltip": "Everything BerryVM ships"},
            ]},
            {"kind": "column", "object_name": "berryStartRight", "children": [
                {"kind": "list", "id": f"{P_START}_side",
                 "items": _flyout_right(), "on": ["activated"],
                 "expand": True, "icon_size": scaled(24),
                 "tooltip": "A folder to browse, or the Control Panel"},
            ]},
        ]},
        {"kind": "row", "object_name": "berryStartFoot", "children": [
            {"kind": "button", "id": f"{P_START}_logoff", "text": "Log Off",
             "on": ["clicked"], "expand": True,
             "tooltip": "Close every window on the desktop"},
            {"kind": "button", "id": f"{P_START}_shutdown",
             "text": "Turn Off Computer", "on": ["clicked"], "expand": True,
             "tooltip": "The XP sign-out joke; the tab stays put"},
        ]},
        {"kind": "label", "id": f"{P_START}_note", "text": "",
         "word_wrap": True},
    ]}


def close_start() -> None:
    """Take the flyout back down, if it is up. Every launch from it ends
    here, the way an XP menu collapses once a pick lands."""
    if api is not None and P_START in open_window_ids():
        close_window(P_START)
        forget_window(P_START)


def open_start() -> str:
    """Stand the flyout up in the desktop's lower-left, over the Start
    button. Already open, it is simply raised again; the window is a plain
    frame so a person can also shut it with its close box."""
    if api is None:
        return ""
    if P_START in open_window_ids():
        raise_window(P_START)
        return P_START
    _ensure_home()
    wide = scaled(380)
    tall = scaled(440)
    open_window({
        "id": P_START, "title": "Start", "closable": True,
        "width": wide, "height": tall,
        "spec": start_flyout_spec(),
    })
    opened[P_START] = {"title": "Start", "kind": "start"}
    raise_window(P_START)
    return P_START


def start_pressed() -> None:
    """The taskbar's Start button: toggle the flyout. If a flyout cannot be
    stood up for any reason, fall back to the flat :func:`start_menu` so a
    person always has a way to launch something."""
    if api is None:
        return
    if P_START in open_window_ids():
        close_start()
        return
    try:
        if not open_start():
            start_menu()
    except Exception as error:
        if api is not None:
            api.log(f"berryvm: Start flyout fell back to the menu: {error}")
        start_menu()


def start_flyout_picked(list_id: str, text: str) -> None:
    """One flyout row doubled. A program caption - from either left column -
    launches through the shared :func:`start_choice`; the right column's
    ``My *`` rows walk Explorer to that folder, My Computer opens the file
    manager (it is a program caption too), and Control Panel brings Display
    Properties up."""
    text = str(text or "")
    if not text:
        return
    if text in BY_CAPTION:
        close_start()
        start_choice(text)
        return
    if text == "Control Panel":
        close_start()
        open_settings()
        return
    if text.startswith("My "):
        close_start()
        start_folder(text[3:].strip())
        return
    close_start()
    start_choice(text)               # any other errand the columns know


def start_folder(which: str) -> None:
    """Open Explorer on one of the home's folders (Documents, Pictures,
    Music), walking the local pane there."""
    if api is None or not which:
        return
    target = vm_home() / which
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    pane_path[PANE_LOCAL] = str(target)
    open_explorer()
    pane_list(PANE_LOCAL, str(target))


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
    elif win_id == P_SETTINGS:
        open_settings()
    elif win_id == P_PAINT:
        open_paint()
    elif win_id == P_MEDIA:
        open_media()
    elif win_id == P_BROWSER:
        open_browser()
    else:
        api.ui.notify(f"BerryVM has no program {win_id!r}.")


def about_box() -> None:
    """Start > About: what this desktop is, what is on it, and - for the
    patient - the way to the Blue Screen of Fun."""
    if api is None:
        return
    names = ", ".join(cap for _pid, cap in PROGRAMS)
    running = _running_programs()
    text = (f"BerryVM {ADDON.get('version')} - a Windows XP-styled desktop "
            f"in one tab of Berry Manager. Programs: {names}. The file "
            "panes, pictures and media ride the app's SFTP session; the "
            "shells are this machine's own local processes.")
    if running:
        text += "\n\nRunning now:\n  " + "\n  ".join(running)
    api.ui.information("About BerryVM", text)
    # The easter egg, reachable from About: a joke blue screen.
    picked = api.ui.choose(["Blue Screen of Fun", "Close"])
    if picked == "Blue Screen of Fun":
        blue_screen_of_fun()


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


# --- window management: show desktop, taskbar menu, cascade and tile ---

def _ui_zoom() -> float:
    """The app's interface zoom as a factor, or ``1.0`` when it cannot be
    read. ``area`` and ``geometry`` come back in device pixels, while
    ``move_window`` and ``resize_window`` multiply their numbers by this
    zoom, so a layout worked out from a read is divided back through it."""
    if api is None:
        return 1.0
    try:
        return max(0.25, float(api.session.zoom()) / 100.0)
    except Exception:
        return 1.0


def _frame_geometry(win_id: str) -> dict:
    """One window's live place and size in device pixels, as the canvas
    reports it; an empty dict when the frame cannot be read."""
    if api is None:
        return {}
    try:
        found = api.tabs.widget_value(TAB, win_id, "geometry")
    except Exception:
        return {}
    return found if isinstance(found, dict) else {}


def _desktop_area() -> tuple[int, int]:
    """The desktop canvas' own room in device pixels, the box a cascade or a
    tile fills; the config's default window size is the floor when the canvas
    cannot be read."""
    if api is not None:
        try:
            found = api.tabs.widget_value(TAB, DESKTOP, "area")
            if isinstance(found, dict):
                wide = int(found.get("width") or 0)
                tall = int(found.get("height") or 0)
                if wide > 0 and tall > 0:
                    return (wide, tall)
        except Exception:
            pass
    return window_size()


def show_desktop() -> None:
    """Minimise every program window at once - the tray's Show desktop
    button and the taskbar menu both reach it. The icon layer is not a
    program window, so it stays; a window already hidden is left alone."""
    if api is None:
        return
    for win_id in program_window_ids():
        if minimized.get(win_id):
            continue
        api.tabs.set_widget(TAB, DESKTOP, "minimize_window", win_id)
        minimized[win_id] = True
    refresh_taskbar()


def _restore_first(win_id: str) -> None:
    """Bring a window down from minimize or maximize before it is laid into
    a cascade or a tile, so the target rect actually takes it."""
    if minimized.pop(win_id, None):
        api.tabs.set_widget(TAB, DESKTOP, "restore_window", {"id": win_id})
    if win_id in maximized:
        # A window being laid out fresh is un-maximized first, or a tile's
        # rect would only reach the part of it the grown frame leaves free.
        maximized.discard(win_id)
        api.tabs.set_widget(TAB, DESKTOP, "restore_window", {"id": win_id})


def _place_window(win_id: str, rect: dict) -> None:
    """Move and size one window into a device-pixel rect, undoing the
    interface zoom on the way to the primitive's unscaled units."""
    zoom = _ui_zoom()
    api.tabs.set_widget(TAB, DESKTOP, "move_window", {
        "id": win_id, "x": int(rect["x"] / zoom), "y": int(rect["y"] / zoom)})
    api.tabs.set_widget(TAB, DESKTOP, "resize_window", {
        "id": win_id,
        "width": int(rect["width"] / zoom),
        "height": int(rect["height"] / zoom)})


def cascade_windows() -> None:
    """Stand every program window up in a falling stack from the top-left,
    each nudged down and right of the last, all the same readable size."""
    if api is None:
        return
    win_ids = program_window_ids()
    if not win_ids:
        return
    area_w, area_h = _desktop_area()
    wide = max(scaled(280), int(area_w * 0.55))
    tall = max(scaled(220), int(area_h * 0.6))
    step = scaled(28)
    for index, win_id in enumerate(win_ids):
        _restore_first(win_id)
        x = step * index
        y = step * index
        x = min(x, max(0, area_w - wide))
        y = min(y, max(0, area_h - tall))
        _place_window(win_id, {"x": x, "y": y, "width": wide, "height": tall})
        raise_window(win_id)
    keep_icons_behind()
    refresh_taskbar()


def tile_windows(vertical: bool = True) -> None:
    """Spread every program window across the desktop in a grid: side by
    side down the columns when ``vertical``, stacked in rows when not."""
    if api is None:
        return
    win_ids = program_window_ids()
    if not win_ids:
        return
    area_w, area_h = _desktop_area()
    count = len(win_ids)
    cols = count if vertical else 1
    rows = 1 if vertical else count
    cols = max(1, cols)
    rows = max(1, rows)
    cell_w = int(area_w / cols)
    cell_h = int(area_h / rows)
    for index, win_id in enumerate(win_ids):
        _restore_first(win_id)
        col = index % cols
        row = index // cols
        _place_window(win_id, {
            "x": col * cell_w, "y": row * cell_h,
            "width": cell_w, "height": cell_h})
    keep_icons_behind()
    refresh_taskbar()


def _window_row_for(text: str) -> str:
    """The window id whose taskbar caption is ``text``, or ``""`` when the
    right-click landed on empty bar rather than a button."""
    if not text:
        return ""
    win_ids = program_window_ids()
    captions = [caption_for(win_id) for win_id in win_ids]
    if text in captions:
        return win_ids[captions.index(text)]
    return ""


def taskbar_menu(row_text: str) -> None:
    """The taskbar's right-click. Over a window's button the menu is that
    window's own - Restore, Minimize, Close; over empty bar it lays the whole
    desktop out - Cascade, tile side by side, tile top and bottom, Show
    desktop."""
    if api is None:
        return
    win_id = _window_row_for(row_text)
    if win_id:
        pinned = is_pinned(win_id)
        picked = api.ui.choose(
            ["Restore", "Maximize", "Minimize",
             ("Unpin from top" if pinned else "Always on top"), "Close"])
        if picked == "Restore":
            _restore_first(win_id)
            raise_window(win_id)
        elif picked == "Maximize":
            if minimized.pop(win_id, None):
                api.tabs.set_widget(TAB, DESKTOP, "restore_window",
                                    {"id": win_id})
            api.tabs.set_widget(TAB, DESKTOP, "maximize_window", win_id)
        elif picked == "Minimize":
            if not minimized.get(win_id):
                api.tabs.set_widget(TAB, DESKTOP, "minimize_window", win_id)
                minimized[win_id] = True
            refresh_taskbar()
        elif picked in ("Always on top", "Unpin from top"):
            now_pinned = toggle_top(win_id)
            api.ui.notify(f"{caption_for(win_id)}: "
                          + ("kept on top" if now_pinned
                             else "no longer kept on top"))
        elif picked == "Close":
            close_window(win_id)
            forget_window(win_id)
            refresh_taskbar()
        return
    picked = api.ui.choose(["Cascade windows", "Tile side by side",
                           "Tile top bottom", "Show desktop"])
    if picked == "Cascade windows":
        cascade_windows()
    elif picked == "Tile side by side":
        tile_windows(vertical=True)
    elif picked == "Tile top bottom":
        tile_windows(vertical=False)
    elif picked == "Show desktop":
        show_desktop()


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
            _pane_column(PANE_REMOTE, "Device (file session)"),
        ]},
        {"kind": "label", "id": f"{P_EXPLORER}_note", "text": "",
         "word_wrap": True},
    ]}


def _pane_column(pane: str, heading: str) -> dict:
    """One side of the Explorer: where it is, the rows it holds, and the
    buttons that act on the picked one."""
    transfer = ("Upload \u2192" if pane == PANE_LOCAL
                else "\u2190 Download")
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{pane}_head", "text": heading,
             "word_wrap": True},
        ]},
        {"kind": "row", "children": [
            {"kind": "line", "id": f"{pane}_path", "text": "",
             "placeholder": "Type a path (~ or the Linux tree) and Enter",
             "on": ["returned"], "expand": True},
            {"kind": "button", "id": f"{pane}_up", "text": "Up",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_root", "text": "Root",
             "on": ["clicked"], "tooltip": "Jump to the top of this side"},
            {"kind": "button", "id": f"{pane}_home", "text": "Home",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{pane}_refresh", "text": "Refresh",
             "on": ["clicked"]},
        ]},
        {"kind": "table", "id": f"{pane}_table",
         "columns": ["Name", "Size", "Mode", "Modified"],
         "on": ["selected", "activated", "context"],
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
    pane_path.setdefault(PANE_LOCAL, str(vm_home()))
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


def _mode_text(mode: int, is_dir: bool) -> str:
    """A stat mode drawn the way ``ls -l`` draws it: ``d`` or ``-`` for a
    directory, then the nine rwx marks. A zero mode (a file that could not be
    read) is a row of dashes rather than a lie about its permissions."""
    try:
        mode = int(mode or 0)
    except (TypeError, ValueError):
        mode = 0
    lead = "d" if is_dir else "-"
    if not mode:
        return lead + "---------"
    marks = ""
    for shift in (6, 3, 0):
        bits = (mode >> shift) & 0o7
        marks += ("r" if bits & 4 else "-")
        marks += ("w" if bits & 2 else "-")
        marks += ("x" if bits & 1 else "-")
    return lead + marks


def _file_stamp(seconds: Any) -> str:
    """A modification time as a short date, or ``""`` when there is nothing
    sensible to show - a folder the machine would not stat, a zero mtime."""
    try:
        stamp = float(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return ""


def pane_list(pane: str, where: str) -> None:
    """Read one folder into one pane; the local side walks this machine and
    the remote side the file session, and the two disagree loudly rather
    than quietly when a listing cannot be had."""
    if api is None:
        return
    if pane == PANE_LOCAL:
        _pane_list_local(where or str(vm_home()))
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
            stat = child.stat()
            size = stat.st_size if child.is_file() else 0
            mode = stat.st_mode
            mtime = stat.st_mtime
        except OSError:
            is_dir, size, mode, mtime = False, 0, 0, 0
        rows[name] = {"path": str(child), "directory": bool(is_dir),
                      "size": size, "mode": mode, "mtime": mtime}
        laid.append({"cells": [name, "" if is_dir else describe_size(size),
                               _mode_text(mode, is_dir), _file_stamp(mtime)],
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
        _note(P_EXPLORER, "device pane: the file session is not open - "
                          "connect it on the app's Connection panel and "
                          "press Refresh.")
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
                      "directory": is_dir, "size": entry.get("size", 0),
                      "mode": entry.get("mode", 0),
                      "mtime": entry.get("mtime", 0)}
        laid.append({"cells": [name,
                               "" if is_dir else describe_size(
                                   entry.get("size", 0)),
                               _mode_text(entry.get("mode", 0), is_dir),
                               _file_stamp(entry.get("mtime", 0))],
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
    pane_list(pane, str(vm_home()) if pane == PANE_LOCAL else "")


def pane_root(pane: str) -> None:
    """Up to the very top of one side: the Linux tree's root here, the
    device's own root there - the way XP's My Computer sat above every
    drive."""
    pane_list(pane, str(home_root()) if pane == PANE_LOCAL else "")


def pane_goto(pane: str, text: str) -> None:
    """Type an address into the bar and Enter walks the pane there. ``~`` and
    the Linux tree both reach the local side; a device path is read inside
    the remote root."""
    wanted = str(text or "").strip()
    if not wanted:
        return
    pane_list(pane, wanted)


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
        lines += ["Upload to device"]
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
            {"kind": "button", "id": f"{win_id}_find", "text": "Find",
             "on": ["clicked"], "tooltip": "Search the text below"},
            {"kind": "button", "id": f"{win_id}_recent", "text": "Recent",
             "on": ["clicked"], "tooltip": "Open a file from this list"},
            {"kind": "button", "id": f"{win_id}_save", "text": "Save",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_saveas", "text": "Save As",
             "on": ["clicked"]},
        ]},
        {"kind": "multi", "id": f"{win_id}_edit",
         "highlight": language_for(path), "expand": "both",
         "on": ["changed"]},
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
        berrypads[win_id]["saved_text"] = ""
        berrypads[win_id]["dirty"] = False
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
    berrypads[win_id]["saved_text"] = _berry_text(win_id)
    berrypads[win_id]["dirty"] = False
    if origin == "local":
        _recent_remember(wanted)
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


def _berry_title(win_id: str) -> None:
    """Re-caption the window: the file's name, wearing a trailing ``*`` while
    there are words the file has not been given yet. Opening the id that is
    already on the desktop only moves the caption, so the editor keeps running.
    """
    record = berrypads.get(win_id)
    if record is None or api is None:
        return
    title = str(record.get("title") or "Untitled")
    star = " *" if record.get("dirty") else ""
    caption = f"Notepad - {title}{star}"
    opener = opened.get(win_id)
    if opener is not None:
        opener["title"] = caption
    open_window({"id": win_id, "title": caption, "current": False})


def _berry_changed(win_id: str, text: str) -> None:
    """A keystroke: the buffer is dirty the moment its words differ from what
    was last read or written, and the caption says so."""
    record = berrypads.get(win_id)
    if record is None:
        return
    dirty = str(text or "") != str(record.get("saved_text") or "")
    if dirty != bool(record.get("dirty")):
        record["dirty"] = dirty
        _berry_title(win_id)


def _berry_settled(win_id: str) -> None:
    """After a save: the file and the buffer agree, so the ``*`` comes off."""
    record = berrypads.get(win_id)
    if record is None:
        return
    record["saved_text"] = _berry_text(win_id)
    if record.get("dirty"):
        record["dirty"] = False
        _berry_title(win_id)


def _recent_list() -> list:
    """The editor's shelf of recently opened files, newest first."""
    raw = config("recent", DEFAULTS["recent"]) or []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw if str(item or "").strip()]


def _recent_remember(path: str) -> None:
    """Push a freshly opened file to the front of the shelf, dropping any
    older copy, and keep it to a sensible length."""
    path = str(path or "").strip()
    if not path:
        return
    shelf = [path] + [one for one in _recent_list() if one != path]
    api.config.set("recent", shelf[:10])


def berry_recent(win_id: str) -> None:
    """The Recent button: choose a file the editor has opened before and show
    it here - a scratch buffer takes it on, a named file opens afresh."""
    if api is None:
        return
    shelf = _recent_list()
    if not shelf:
        _note(win_id, "No files have been opened yet.")
        return
    picked = api.ui.choose([short_name(one) for one in shelf])
    if not picked:
        return
    for one in shelf:
        if short_name(one) == picked:
            open_berrypad(one, "local", short_name(one))
            return


def berry_find(win_id: str) -> None:
    """Find: ask for a word and say how often the text carries it. The editor
    leaf has no cursor to move, so the count and the first line are reported
    on the window's own note rather than jumped to."""
    if api is None:
        return
    wanted = api.ui.input("Find", "Search for")
    wanted = str(wanted or "")
    if not wanted:
        return
    text = _berry_text(win_id)
    lowered = text.lower()
    count = lowered.count(wanted.lower())
    if not count:
        _note(win_id, f"{wanted!r} is not in this text.")
        return
    line = 1
    for index, row in enumerate(text.splitlines()):
        if wanted.lower() in row.lower():
            line = index + 1
            break
    _note(win_id, f"{wanted!r}: {count} match(es), first on line {line}.")


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
    record["saved_text"] = text
    if record.get("dirty"):
        record["dirty"] = False
        _berry_title(win_id)
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
    record["saved_text"] = text
    if record.get("dirty"):
        record["dirty"] = False
    _berry_title(win_id)
    _recent_remember(target)
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
            {"kind": "button", "id": f"{win_id}_fit", "text": "Fit",
             "on": ["clicked"], "tooltip": "Back to the default size"},
            {"kind": "button", "id": f"{win_id}_prev", "text": "\u25c0 Prev",
             "on": ["clicked"],
             "tooltip": "The picture before this one in its folder"},
            {"kind": "button", "id": f"{win_id}_next", "text": "Next \u25b6",
             "on": ["clicked"],
             "tooltip": "The picture after this one in its folder"},
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


def viewer_fit(win_id: str) -> None:
    """Snap the leaf back to the size a fresh viewer is built at - the
    "whole picture, no zoom" default, whatever zooming has since done."""
    record = viewers.get(win_id)
    if record is None:
        return
    wanted = str(record.get("path") or "")
    if not wanted:
        _note(win_id, "Nothing is shown yet, so there is nothing to fit.")
        return
    base = scaled(512)
    local = wanted
    if record.get("origin") == "remote":
        local = _viewer_pull(wanted) or wanted
    _show_in(win_id, local, base, str(record.get("origin") or "local"),
             wanted)


def _viewer_siblings(path: str) -> list:
    """Every picture in ``path``'s folder, in name order - the reel Prev and
    Next step along. An unreadable folder is simply an empty reel."""
    try:
        folder = Path(path).parent
        found = [str(item) for item in folder.iterdir()
                 if item.suffix.lower() in IMAGE_SUFFIXES]
    except OSError:
        return []
    return sorted(found, key=lambda name: name.lower())


def viewer_step(win_id: str, delta: int) -> None:
    """Move to the next or previous picture in this one's folder, keeping the
    current size. At either end of the reel it says so rather than looping -
    the way the XP viewer stopped rather than wrapped."""
    record = viewers.get(win_id)
    if record is None:
        return
    wanted = str(record.get("path") or "")
    if not wanted:
        _note(win_id, "Open a picture first, then step through its folder.")
        return
    reel = _viewer_siblings(wanted)
    if not reel:
        _note(win_id, "This folder holds no other pictures.")
        return
    try:
        here = reel.index(wanted)
    except ValueError:
        here = 0
    there = here + delta
    if there < 0 or there >= len(reel):
        _note(win_id, "No picture that way.")
        return
    _show_in(win_id, reel[there],
             int(record.get("size") or scaled(512)), "local", reel[there])


# --- 4. Command Prompt: a local shell in a window ---

#: The first words on every console screen, so the person knows whose keys
#: they are giving.
CONSOLE_BANNER = ("BerryVM local shell - this machine, this user. "
                  "Closing the window ends it.\n")


def console_spec(win_id: str, cwd: str) -> dict:
    """A path line over a real terminal screen. The screen is a ``terminal``
    leaf that ``spawn`` points at the machine's own shell: the process' bytes
    are fed into the widget's VT parser and its keystrokes come back out, so
    the window is a terminal emulator rather than a read-only log with a box
    typed into it."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": f"{win_id}_path", "text": cwd,
         "expand": True, "word_wrap": True},
        {"kind": "terminal", "id": f"{win_id}_term",
         "font_size": term_font(), "on": ["ready", "closed", "failed"]},
    ]}


def _local_shell() -> str:
    """The machine's own interactive shell, by path: ``COMSPEC``/``cmd.exe``
    on Windows, ``SHELL``/``sh`` elsewhere. The worker actually runs it; this
    only names what to run."""
    if os.name == "nt":
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/sh")


def open_console(start: str = "") -> str:
    """Stand a command prompt up and point its terminal at a live shell.
    Each window spawns a process of its own - in the VM's Linux-shaped home,
    wearing the shell environment and the profile's aliases - so two prompts
    can sit in two directories at once, exactly the desktop habit this copies.
    The worker owns the child process and its line editing; closing the window
    has the app stop it."""
    global console_seq
    if api is None:
        return ""
    _ensure_home()
    console_seq += 1
    win_id = f"{CONSOLE_PREFIX}{console_seq}"
    term_id = f"{win_id}_term"
    cwd = str(start or vm_home())
    open_window(_program_entry(win_id, "Command Prompt",
                               console_spec(win_id, cwd)))
    opened[win_id] = {"title": "Command Prompt", "kind": "console",
                      "cwd": cwd}
    terminals[term_id] = win_id
    refresh_taskbar()
    api.tabs.set_widget(TAB, term_id, "spawn", {
        "cmd": _local_shell(), "cwd": cwd, "env": shell_env(),
        "aliases": alias_map(),
    })
    api.tabs.set_widget(TAB, term_id, "write", CONSOLE_BANNER)
    return win_id


def console_news(win_id: str, event: str, payload: dict) -> None:
    """One console's shell reporting itself onto its window's path line. The
    real shell prints its own prompt, so the addon only marks the moments the
    process itself ends - and never has to keep a working directory."""
    if api is None:
        return
    if event == "ready":
        api.tabs.set_widget(TAB, f"{win_id}_path", "text",
                            str((opened.get(win_id) or {}).get("cwd") or ""))
    elif event == "failed":
        api.tabs.set_widget(TAB, f"{win_id}_path", "text",
                            f"shell error: {payload.get('error') or 'failed'}")
    elif event == "closed":
        api.tabs.set_widget(TAB, f"{win_id}_path", "text",
                            "shell ended - close the window, open another")


# --- 7. Display Properties: the settings window ---

#: Each Display Properties dial: control id -> (config key, low, high, what
#: a move repaints). ``scale`` and ``icon`` rebuild the tile grid, ``font``
#: retypes every shell, ``bar`` re-pours the desktop sheet, ``task`` re-pours
#: the bar's sheet and its row, and ``size`` only moves the defaults a not-yet-
#: opened window takes. :func:`settings_preview` reads this table on each
#: slider ``changed``.
_SLIDERS = {
    "set_scale": ("ui_scale", 80, 160, "scale"),
    "set_font": ("term_font", 8, 28, "font"),
    "set_width": ("window_width", 240, 2400, "size"),
    "set_height": ("window_height", 180, 1600, "size"),
    "set_icon": ("icon_px", 24, 96, "icon"),
    "set_cap_h": ("caption_height", 16, 48, "bar"),
    "set_cap_font": ("caption_font", 7, 24, "bar"),
    "set_task_h": ("task_height", 22, 64, "task"),
}

#: Each toggle: control id -> (config key, what a flip repaints).
_TOGGLES = {
    "set_grad": ("bar_gradient", "bar"),
    "set_round": ("round_corners", "bar"),
    "set_small": ("task_small", "task"),
    "set_lock": ("task_lock", "task"),
    "set_autohide": ("task_autohide", "task"),
    "set_clock": ("clock_on", "task"),
}

#: Each caption-colour field: control id -> config key. A blank reads as
#: "follow the theme", the way :func:`_bar_css` treats an empty colour.
_COLORS = {
    "set_active": "active_caption",
    "set_inactive": "inactive_caption",
    "set_capt": "caption_text",
}

#: The Control Panel home's category buttons, each naming the ``set_tabs``
#: page it jumps to. Kept as a table so the labels and their targets cannot
#: drift apart when the cards are reordered.
_SETTINGS_NAV = {
    "set_nav_theme": 1,
    "set_nav_wall": 2,
    "set_nav_appear": 4,
    "set_nav_taskbar": 5,
}


def _set_slider(identifier: str, text: str, key: str,
                low: int, high: int) -> dict:
    """One density dial: a caption, the slider, and a read-out of the number
    it holds. The read-out wears ``<id>_val`` so :func:`settings_preview` can
    keep it in step as the knob is dragged."""
    value = _num(key, low, high)
    return {"kind": "row", "children": [
        {"kind": "label", "text": text},
        {"kind": "slider", "id": identifier, "minimum": low,
         "maximum": high, "value": value, "on": ["changed"], "expand": True},
        {"kind": "label", "id": f"{identifier}_val", "text": str(value)},
    ]}


def _set_toggle(identifier: str, text: str, key: str,
                tip: str = "") -> dict:
    """One checkbox whose flip is previewed live; ``checked`` starts from the
    stored preference so the window reads the desktop back to the person."""
    return {"kind": "check", "id": identifier, "text": text,
            "checked": bool(config(key, DEFAULTS.get(key, False))),
            "on": ["changed"], "tooltip": tip}


def _set_colour(identifier: str, label: str, key: str,
                placeholder: str) -> dict:
    """One caption-colour field, seeded from the stored value (a blank means
    "follow the theme") and labelled for the form it sits in."""
    return {"label": label, "kind": "line", "id": identifier,
            "value": str(config(key, DEFAULTS.get(key, "")) or ""),
            "placeholder": placeholder, "on": ["changed"]}


def settings_spec() -> dict:
    """The wallpaper, the sizes and the habits - the same keys the app's own
    form writes, offered here in the XP control-panel shape: a ``tabs``
    container wearing a Control-Panel home and the classic Display Properties
    cards (Themes, Desktop, Screen Saver, Appearance, Settings). The size and
    density dials are sliders with a live read-out beside each, and every
    control keeps a stable id so :func:`settings_apply` and
    :func:`settings_preview` read them the same way however the pages are
    arranged - a tab only moves a widget on screen, never out of the
    registry."""
    return {"kind": "column", "children": [
        {"kind": "tabs", "id": "set_tabs", "expand": "both", "children": [
            # 0. The Control Panel home: a small grid of categories, each a
            # button that turns to the card it names. XP led a person here
            # first; the cards below are what the buttons reach.
            {"label": "Control Panel", "kind": "column", "children": [
                {"kind": "label",
                 "text": "Pick a category to change how BerryVM's desktop "
                         "looks. Everything here is a card of the same "
                         "Display Properties window.",
                 "word_wrap": True},
                {"kind": "grid", "children": [
                    {"kind": "button", "id": "set_nav_theme",
                     "text": "Themes\nPick a Luna colour scheme",
                     "row": 0, "col": 0, "on": ["clicked"],
                     "tooltip": "Open the Themes card"},
                    {"kind": "button", "id": "set_nav_wall",
                     "text": "Display\nWallpaper, window size and icon "
                            "density",
                     "row": 0, "col": 1, "on": ["clicked"],
                     "tooltip": "Open the Desktop and Settings cards"},
                    {"kind": "button", "id": "set_nav_appear",
                     "text": "Appearance\nTitle bars, fonts and effects",
                     "row": 1, "col": 0, "on": ["clicked"],
                     "tooltip": "Open the Appearance card"},
                    {"kind": "button", "id": "set_nav_taskbar",
                     "text": "Taskbar\nHeight, buttons and the clock",
                     "row": 1, "col": 1, "on": ["clicked"],
                     "tooltip": "Open the Settings card"},
                ]},
                {"kind": "spacer", "expand": True},
            ]},
            # 1. Themes: the colour scheme both sheets are painted from.
            {"label": "Themes", "kind": "column", "children": [
                {"kind": "group", "text": "Color scheme", "children": [
                    {"kind": "form", "children": [
                        {"label": "Theme", "kind": "combo", "id": "set_theme",
                         "items": list(THEMES), "value": theme(),
                         "on": ["changed"],
                         "tooltip": "Repaints the window frames, the Start "
                                    "button and the selection colour"},
                    ]},
                    {"kind": "label", "id": "set_theme_note",
                     "text": "Luna Blue is the shipped look; Olive Gold and "
                             "Silver are the other two XP themes.",
                     "word_wrap": True},
                ]},
            ]},
            # 2. Desktop: the wallpaper behind every window.
            {"label": "Desktop", "kind": "column", "children": [
                {"kind": "group", "text": "Background", "children": [
                    {"kind": "form", "children": [
                        {"label": "Wallpaper", "kind": "combo",
                         "id": "set_wall", "items": list(WALLPAPERS),
                         "value": str(config("wallpaper",
                                             DEFAULTS["wallpaper"])),
                         "on": ["changed"]},
                        {"label": "Picture file", "kind": "line",
                         "id": "set_file",
                         "value": str(config("wallpaper_file", "")),
                         "placeholder": "a file on this machine"},
                    ]},
                    {"kind": "row", "children": [
                        {"kind": "button", "id": "set_picture",
                         "text": "Browse for a Picture...",
                         "on": ["clicked"],
                         "tooltip": "Picks the Picture wallpaper and its "
                                    "file"},
                        {"kind": "button", "id": "set_colour",
                         "text": "Pick a Solid Colour...", "on": ["clicked"],
                         "tooltip": "Picks the Solid wallpaper and its "
                                    "colour"},
                    ]},
                ]},
            ]},
            # 3. Screen Saver: a nod to the shelf; BerryVM runs none.
            {"label": "Screen Saver", "kind": "column", "children": [
                {"kind": "group", "text": "Screen saver", "children": [
                    {"kind": "form", "children": [
                        {"label": "Saver", "kind": "combo", "id": "set_saver",
                         "items": ["(None)", "Beastro", "Mystify",
                                   "Starfield", "3D Pipes"],
                         "value": str(config("saver", "(None)") or "(None)"),
                         "tooltip": "A nod to the shelf of screensavers; "
                                    "BerryVM does not actually run one"},
                    ]},
                ]},
            ]},
            # 4. Appearance: the title bars' colour and shape, their caption
            # metrics, and the two cosmetic effects XP parked on this card.
            {"label": "Appearance", "kind": "column", "children": [
                {"kind": "group", "text": "Title bars", "children": [
                    {"kind": "form", "children": [
                        _set_colour("set_active", "Active colour",
                                    "active_caption",
                                    "#rrggbb - blank follows the theme"),
                        _set_colour("set_inactive", "Inactive colour",
                                    "inactive_caption",
                                    "#rrggbb - remembered; the canvas paints "
                                    "every bar alike"),
                        _set_colour("set_capt", "Caption text",
                                    "caption_text", "#rrggbb of the words"),
                    ]},
                    _set_toggle("set_grad", "Use a gradient caption",
                                "bar_gradient",
                                "Off paints the title bar a flat colour"),
                    _set_toggle("set_round", "Rounded window corners",
                                "round_corners",
                                "Off squares every frame's corners"),
                    _set_slider("set_cap_h", "Title bar height",
                                "caption_height", 16, 48),
                    _set_slider("set_cap_font", "Title bar text",
                                "caption_font", 7, 24),
                ]},
                {"kind": "group", "text": "Effects", "children": [
                    {"kind": "check", "id": "set_bigicons",
                     "text": "Use classic large icons",
                     "checked": bool(config("big_icons", False)),
                     "tooltip": "A flavour toggle; the icon-size dial on the "
                                "Settings card is what sizes the tiles"},
                    {"kind": "form", "children": [
                        {"label": "Font smoothing", "kind": "combo",
                         "id": "set_clear", "items": ["Standard", "ClearType"],
                         "value": str(config("clear_type", "ClearType")
                                      or "ClearType"),
                         "tooltip": "Cosmetic; the app draws text its own "
                                    "way"},
                    ]},
                ]},
            ]},
            # 5. Settings: the display size and density, the terminal text and
            # the whole taskbar. This is the card XP's "Settings" tab grew.
            {"label": "Settings", "kind": "column", "children": [
                {"kind": "group", "text": "Window and screen", "children": [
                    _set_slider("set_width", "Window width",
                                "window_width", 240, 2400),
                    _set_slider("set_height", "Window height",
                                "window_height", 180, 1600),
                    {"kind": "row", "children": [
                        {"kind": "button", "id": "set_preset_800",
                         "text": "800x600", "on": ["clicked"], "expand": True},
                        {"kind": "button", "id": "set_preset_1024",
                         "text": "1024x768", "on": ["clicked"], "expand": True},
                        {"kind": "button", "id": "set_preset_1280",
                         "text": "1280x1024", "on": ["clicked"],
                         "expand": True},
                        {"kind": "button", "id": "set_fit",
                         "text": "Fit to screen", "on": ["clicked"],
                         "expand": True,
                         "tooltip": "Size a window against this machine's "
                                    "own screen"},
                    ]},
                ]},
                {"kind": "group", "text": "Density", "children": [
                    _set_slider("set_scale", "Desktop scale %",
                                "ui_scale", 80, 160),
                    _set_slider("set_icon", "Icon size", "icon_px", 24, 96),
                    _set_slider("set_font", "Terminal text",
                                "term_font", 8, 28),
                ]},
                {"kind": "group", "text": "Taskbar and shells", "children": [
                    _set_slider("set_task_h", "Taskbar height",
                                "task_height", 22, 64),
                    _set_toggle("set_small", "Use small buttons",
                                "task_small",
                                "Slimmer taskbar buttons and Start padding"),
                    _set_toggle("set_lock", "Lock the taskbar", "task_lock",
                                "A remembered preference; the bar is a fixed "
                                "row in the tab"),
                    _set_toggle("set_autohide", "Auto-hide the taskbar",
                                "task_autohide",
                                "A remembered preference; the bar stays on "
                                "show in a single tab"),
                    _set_toggle("set_clock", "Show the clock", "clock_on"),
                    {"kind": "form", "children": [
                        {"label": "Username to offer", "kind": "line",
                         "id": "set_user",
                         "value": str(config("default_user", "root"))},
                    ]},
                ]},
            ]},
        ]},
        # The shared footer, XP's three buttons: Apply leaves the window up,
        # OK commits and closes, Defaults wipes back to the shipped look.
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
    api.config.set("theme", str(_read_one("set_theme", DEFAULT_THEME)))
    api.config.set("wallpaper", str(_read_one("set_wall", "Bliss")))
    api.config.set("wallpaper_file",
                   str(_read_one("set_file", "")).strip())
    # Every dial writes through its own ``_SLIDERS`` entry, so a commit reads
    # the same id, key and clamp the live-preview path uses.
    for identifier, (key, low, high, _effect) in _SLIDERS.items():
        number = int(_read_one(identifier, DEFAULTS.get(key, low)))
        api.config.set(key, max(low, min(high, number)))
    # Each toggle is read as its bool and written whole; a control the window
    # never built (a missing id) keeps whatever was already stored.
    for identifier, (key, _effect) in _TOGGLES.items():
        try:
            on = bool(api.tabs.widget_value(TAB, identifier, "value"))
        except Exception:
            on = bool(config(key, DEFAULTS.get(key, False)))
        api.config.set(key, on)
    # The caption colours: a blank is left blank, the way :func:`_bar_css`
    # reads "follow the theme".
    for identifier, key in _COLORS.items():
        api.config.set(key,
                       str(_read_one(identifier,
                                     DEFAULTS.get(key, ""))).strip())
    api.config.set("default_user", str(_read_one("set_user", "root")))
    # The cosmetic cards: a screensaver name, a large-icons flavour and a
    # font-smoothing pick. None of them changes a pixel BerryVM draws - the
    # sheets and the app own that - but they round the control panel out and
    # are remembered like any other preference.
    api.config.set("saver", str(_read_one("set_saver", "(None)")))
    api.config.set("clear_type",
                   str(_read_one("set_clear", "ClearType")))
    try:
        big = bool(api.tabs.widget_value(TAB, "set_bigicons", "value"))
    except Exception:
        big = bool(config("big_icons", False))
    api.config.set("big_icons", big)
    mirror_settings()
    apply_theme()
    if scale() != was:
        relayout_desktop()
    apply_term_font()
    refresh_taskbar()
    tick_clock()
    _note(P_SETTINGS, "Applied.")
    if close_after:
        close_window(P_SETTINGS)
        forget_window(P_SETTINGS)
        refresh_taskbar()


def apply_term_font() -> None:
    """Hand the configured text size to every shell standing on the desktop -
    each Command Prompt window's terminal - as the api's own ``font_size``."""
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


def settings_preview(identifier: str, payload: dict) -> None:
    """Live-preview one Display Properties control the moment it moves: write
    the single key it owns, keep its read-out in step, and repaint just
    enough to show the change. A scale or icon size rebuilds the tile grid, a
    caption or taskbar control re-pours the sheets (and the bar's row), a text
    size retypes the shells, and a window-size dial only moves the defaults a
    not-yet-opened window takes. Apply and OK still commit the whole set
    through :func:`settings_apply`; this is the XP habit of a change showing
    as the knob turns."""
    if api is None:
        return
    body = str(identifier or "")
    payload = payload or {}
    if body in _SLIDERS:
        key, low, high, effect = _SLIDERS[body]
        try:
            value = int(payload.get("value"))
        except (TypeError, ValueError):
            return
        value = max(low, min(high, value))
        api.config.set(key, value)
        api.tabs.set_widget(TAB, f"{body}_val", "text", str(value))
        mirror_settings()
        if effect in ("scale", "icon"):
            apply_theme()
            relayout_desktop()
        elif effect == "font":
            apply_term_font()
        elif effect == "task":
            apply_theme()
            refresh_taskbar()
        elif effect == "bar":
            apply_theme()
        # "size" moves only the defaults; nothing on screen changes yet.
        return
    if body in _TOGGLES:
        key, effect = _TOGGLES[body]
        api.config.set(key, bool(payload.get("checked")))
        mirror_settings()
        apply_theme()
        if effect == "task":
            refresh_taskbar()
        if body == "set_clock":
            tick_clock()
        return
    if body in _COLORS:
        api.config.set(_COLORS[body], str(payload.get("text") or "").strip())
        mirror_settings()
        apply_theme()
        return


def settings_size_preset(width: int, height: int) -> None:
    """Seat the two window-size dials at a named resolution and write them: the
    presets and Fit to screen both land here, so the sliders, their read-outs
    and the stored keys always agree."""
    if api is None:
        return
    width = max(240, min(2400, int(width)))
    height = max(180, min(1600, int(height)))
    api.config.set("window_width", width)
    api.config.set("window_height", height)
    api.tabs.set_widget(TAB, "set_width", "value", width)
    api.tabs.set_widget(TAB, "set_height", "value", height)
    api.tabs.set_widget(TAB, "set_width_val", "text", str(width))
    api.tabs.set_widget(TAB, "set_height_val", "text", str(height))
    mirror_settings()
    _note(P_SETTINGS, f"window size: {width}x{height}")


def settings_fit_screen() -> None:
    """Size a default window against this machine's own screen: sixty percent
    across, seventy down, clamped to the range the dials offer - the same
    shape :func:`_apply_screen_defaults` bakes on a first run."""
    wide, tall = _screen_geometry()
    settings_size_preset(int(wide * 0.6), int(tall * 0.7))


def settings_defaults() -> None:
    """Every key back to the shipped value, and the desktop repainted to
    match - the Defaults button means it."""
    if api is None:
        return
    was = scale()
    for key, value in DEFAULTS.items():
        api.config.set(key, value)
    mirror_settings()
    # The shell profile is reset and re-mirrored so a fresh ``.bashrc``
    # reflects it too.
    draw_desk_icons()
    mirror_profile(DEFAULTS["profile"])
    apply_theme()
    if scale() != was:
        relayout_desktop()
    apply_term_font()
    tick_clock()
    _note(P_SETTINGS, "The desktop is back to its shipped look.")


# --- 9. Internet: the Web Browser window ---

#: The address a fresh browser starts on, and the label the URL bar carries
#: so the person sees what "no page loaded yet" looks like in the field.
BROWSER_HOME = "https://www.wikipedia.org"


def browser_spec(start_url: str = BROWSER_HOME) -> dict:
    """One row of address furniture and one row of navigation over a web
    leaf: a field, Go and Home, then Back / Forward / Reload and a bookmark
    shelf. The leaf is named ``browser_view`` so a later ``set_widget`` can
    push a new address into it without rebuilding the whole window."""
    marks = [str(one.get("name") or "")
             for one in bookmarks_list() if one.get("name")]
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
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{P_BROWSER}_back", "text": "\u2190",
             "on": ["clicked"], "tooltip": "Back one page"},
            {"kind": "button", "id": f"{P_BROWSER}_forward", "text": "\u2192",
             "on": ["clicked"], "tooltip": "Forward one page"},
            {"kind": "button", "id": f"{P_BROWSER}_reload", "text": "Reload",
             "on": ["clicked"]},
            {"kind": "combo", "id": f"{P_BROWSER}_mark", "items": marks,
             "value": "", "placeholder": "Bookmarks",
             "on": ["activated"], "expand": True,
             "tooltip": "A saved page; pick it to go there"},
        ]},
        {"kind": "web", "id": f"{P_BROWSER}_view", "url": start_url,
         "expand": "both"},
        {"kind": "label", "id": f"{P_BROWSER}_note", "text": "",
         "word_wrap": True},
    ]}


def bookmarks_list() -> list:
    """The browser's saved pages, each a ``{name, url}`` pair, cleaned of
    anything malformed so a hand-edited config cannot strand the shelf."""
    raw = config("bookmarks", DEFAULTS["bookmarks"]) or []
    out: list[dict] = []
    if isinstance(raw, list):
        for one in raw:
            if isinstance(one, dict) and one.get("url"):
                out.append({"name": str(one.get("name") or one["url"]),
                            "url": str(one["url"])})
    return out


def browser_bookmark(name: str) -> None:
    """Follow one bookmark by its shelf name, filling the address bar and
    pointing the leaf at it. An unknown name is a no-op."""
    for one in bookmarks_list():
        if one["name"] == str(name or ""):
            api.tabs.set_widget(TAB, f"{P_BROWSER}_url", "value", one["url"])
            api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "url", one["url"])
            return


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


# --- 10. Paint: a canvas drawing program ---

#: The tools, in the order they stand on the window's tool row, each paired
#: with the ``canvas`` leaf's own ``tool`` word. The button's label is what a
#: person reads; the word is what the surface is told to draw with.
PAINT_TOOLS = (
    ("Pen", "pen"),
    ("Line", "line"),
    ("Rect", "rect"),
    ("Ellipse", "ellipse"),
    ("Eraser", "eraser"),
    ("Fill", "fill"),
)

#: The colour and width a fresh canvas starts in.
PAINT_DEFAULT_COLOR = "#000000"
PAINT_DEFAULT_WIDTH = 3


def paint_spec(win_id: str) -> dict:
    """A row of tools and a row of errands over the drawing surface. The
    surface is one ``canvas`` leaf that paints the hand's strokes itself, so
    a line lands with no round trip; the buttons and the slider only ever
    name the tool, the colour and the width, or ask for a file."""
    tools = [{"kind": "button", "id": f"{win_id}_pt_{word}", "text": label,
              "on": ["clicked"],
              "tooltip": f"Draw with the {label.lower()} tool"}
             for label, word in PAINT_TOOLS]
    return {"kind": "column", "children": [
        {"kind": "row", "children": tools + [
            {"kind": "button", "id": f"{win_id}_undo", "text": "Undo",
             "on": ["clicked"], "tooltip": "Take the last stroke back"},
            {"kind": "button", "id": f"{win_id}_clear", "text": "Clear",
             "on": ["clicked"], "tooltip": "Blank the whole picture"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{win_id}_color", "text": "Colour",
             "on": ["clicked"], "tooltip": "Pick the pen colour"},
            {"kind": "label", "id": f"{win_id}_colorname",
             "text": PAINT_DEFAULT_COLOR, "word_wrap": True},
            {"kind": "label", "id": f"{win_id}_wlabel", "text": "Width"},
            {"kind": "slider", "id": f"{win_id}_width", "minimum": 1,
             "maximum": 32, "value": PAINT_DEFAULT_WIDTH,
             "on": ["changed"], "expand": True},
            {"kind": "button", "id": f"{win_id}_open", "text": "Open",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_save", "text": "Save As",
             "on": ["clicked"]},
        ]},
        {"kind": "canvas", "id": f"{win_id}_surface", "expand": "both",
         "tool": "pen", "color": PAINT_DEFAULT_COLOR,
         "width": PAINT_DEFAULT_WIDTH},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_paint() -> str:
    """Bring Paint up: a single window, raised rather than rebuilt if a
    canvas is already on the desktop. The surface keeps whatever the person
    drew, so reopening never wipes the picture."""
    if api is None:
        return ""
    if P_PAINT in open_window_ids():
        raise_window(P_PAINT)
        return P_PAINT
    title = CAPTIONS[P_PAINT]
    width, height = window_size()
    open_window(_program_entry(P_PAINT, title, paint_spec(P_PAINT),
                               int(width * 1.4), int(height * 1.3)))
    opened[P_PAINT] = {"title": title, "kind": "paint"}
    paint_state[P_PAINT] = {"color": PAINT_DEFAULT_COLOR,
                            "width": PAINT_DEFAULT_WIDTH, "tool": "pen"}
    refresh_taskbar()
    return P_PAINT


def paint_tool(tool: str) -> None:
    """Name the surface's tool. Only the six the leaf knows are accepted, so
    a stray id on the row cannot put the canvas into an unknown state."""
    if api is None:
        return
    api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "tool", tool)
    paint_state.setdefault(P_PAINT, {})["tool"] = tool


def paint_colour() -> None:
    """The colour wheel, then hand the choice to the surface and show the
    hex on the row so the person can see what the pen is wearing now."""
    if api is None:
        return
    state = paint_state.get(P_PAINT) or {}
    picked = api.ui.pick_color("Paint colour",
                               str(state.get("color") or PAINT_DEFAULT_COLOR))
    picked = str(picked or "").strip()
    if not picked:
        return
    api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "color", picked)
    api.tabs.set_widget(TAB, f"{P_PAINT}_colorname", "text", picked)
    state["color"] = picked
    paint_state[P_PAINT] = state


def paint_width(value: Any) -> None:
    """The slider's stroke width, in pixels, straight to the surface."""
    if api is None:
        return
    try:
        width = int(value)
    except (TypeError, ValueError):
        return
    api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "width", width)
    paint_state.setdefault(P_PAINT, {})["width"] = width


def paint_undo() -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "undo", True)


def paint_clear() -> None:
    if api is not None:
        api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "clear", "")


def paint_open() -> None:
    """A picture off this machine, laid under the pen as the working
    surface - the canvas draws its own blank, so opening is only a load."""
    if api is None:
        return
    picked = api.ui.pick_file(
        "Open picture",
        filter="Pictures (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*)")
    picked = str(picked or "").strip()
    if not picked:
        _note(P_PAINT, "Nothing was opened.")
        return
    api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "open", picked)
    _note(P_PAINT, f"opened {short_name(picked)}")


def paint_save() -> None:
    """Write the picture out as a PNG and, the way XP's Paint did, offer to
    look at what was just drawn in the Picture Viewer."""
    if api is None:
        return
    target = api.ui.pick_file("Save picture", kind="save",
                              initial="drawing.png")
    target = str(target or "").strip()
    if not target:
        _note(P_PAINT, "Nothing was saved.")
        return
    if Path(target).suffix.lower() not in IMAGE_SUFFIXES:
        target = target + ".png"
    api.tabs.set_widget(TAB, f"{P_PAINT}_surface", "save", target)
    if api.ui.question("Paint", f"Saved {short_name(target)}. Open it in "
                                f"the Picture Viewer?"):
        open_viewer(target, "local")
    _note(P_PAINT, f"saved {short_name(target)}")


# --- 12. Media Player ---

#: The endings Open offers. What QtMultimedia can actually decode is its
#: own business; the filter only keeps the picker from hiding a file the
#: person meant to play.
AUDIO_FILTER = ("Audio (*.mp3 *.ogg *.wav *.flac *.m4a *.aac *.opus);;"
                "All files (*)")


def media_spec(win_id: str) -> dict:
    """A transport row and a volume slider over a playlist, with the audio
    leaf itself below - invisible, since it plays rather than shows. The
    playlist and the leaf report back: double-click a row to play it, and the
    leaf says when a track ends or fails."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "button", "id": f"{win_id}_open", "text": "Open",
             "on": ["clicked"], "tooltip": "Queue a track off this machine"},
            {"kind": "button", "id": f"{win_id}_add", "text": "From device",
             "on": ["clicked"],
             "tooltip": "Pull a track off the device over the main "
                        "connection and queue it"},
            {"kind": "button", "id": f"{win_id}_play", "text": "Play",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_pause", "text": "Pause",
             "on": ["clicked"]},
            {"kind": "button", "id": f"{win_id}_stop", "text": "Stop",
             "on": ["clicked"]},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_vollabel", "text": "Volume"},
            {"kind": "slider", "id": f"{win_id}_volume", "minimum": 0,
             "maximum": 100, "value": 80, "on": ["changed"], "expand": True},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_seeklabel", "text": "0:00"},
            {"kind": "slider", "id": f"{win_id}_seek", "minimum": 0,
             "maximum": 0, "value": 0, "on": ["changed"], "expand": True,
             "tooltip": "Drag to seek through the loaded track"},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "id": f"{win_id}_movelabel",
             "text": "Reorder"},
            {"kind": "button", "id": f"{win_id}_up", "text": "\u25b2 Up",
             "on": ["clicked"], "tooltip": "Move the selected track up"},
            {"kind": "button", "id": f"{win_id}_down", "text": "\u25bc Down",
             "on": ["clicked"], "tooltip": "Move the selected track down"},
        ]},
        {"kind": "list", "id": f"{win_id}_list",
         "on": ["activated", "selected"],
         "expand": "both", "tooltip": "Double-click a track to play it"},
        {"kind": "player", "id": f"{win_id}_player", "volume": 80,
         "on": ["state", "done", "error"]},
        {"kind": "label", "id": f"{win_id}_now", "text": "No track loaded",
         "word_wrap": True},
        {"kind": "label", "id": f"{win_id}_note", "text": "",
         "word_wrap": True},
    ]}


def open_media() -> str:
    """Bring Media Player up: a single window, raised if one already stands.
    The playlist it holds is kept between openings in this session."""
    if api is None:
        return ""
    if P_MEDIA in open_window_ids():
        raise_window(P_MEDIA)
        return P_MEDIA
    title = CAPTIONS[P_MEDIA]
    open_window(_program_entry(P_MEDIA, title, media_spec(P_MEDIA)))
    opened[P_MEDIA] = {"title": title, "kind": "media"}
    media_state[P_MEDIA] = {"tracks": [], "index": -1, "selected": -1}
    refresh_taskbar()
    return P_MEDIA


def _media_state() -> dict:
    return media_state.setdefault(
        P_MEDIA, {"tracks": [], "index": -1, "selected": -1})


def _media_draw_list() -> None:
    """Lay the playlist out, with a mark on whichever row is loaded."""
    if api is None:
        return
    state = _media_state()
    rows = []
    for index, path in enumerate(state.get("tracks") or []):
        mark = "\u25b6 " if index == state.get("index") else ""
        rows.append(mark + short_name(path))
    api.tabs.set_widget(TAB, f"{P_MEDIA}_list", "items", rows)


def media_add(path: str) -> None:
    if not path:
        return
    state = _media_state()
    if path not in state["tracks"]:
        state["tracks"].append(path)
    _media_draw_list()


def media_play_index(index: int) -> None:
    """Load the row and start it. The leaf's ``open`` sets the source and
    ``play`` begins; the state events it sends back keep the caption honest."""
    state = _media_state()
    tracks = state.get("tracks") or []
    if index < 0 or index >= len(tracks):
        return
    state["index"] = index
    path = tracks[index]
    api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "open", path)
    api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "play", True)
    api.tabs.set_widget(TAB, f"{P_MEDIA}_now", "text",
                        f"Playing: {short_name(path)}")
    _media_draw_list()


def media_open_local() -> None:
    if api is None:
        return
    picked = api.ui.pick_file("Open audio", filter=AUDIO_FILTER)
    picked = str(picked or "").strip()
    if not picked:
        _note(P_MEDIA, "Nothing was opened.")
        return
    state = _media_state()
    media_add(picked)
    media_play_index(len(state["tracks"]) - 1)


def media_add_device() -> None:
    """A track off the device: pulled into the VM's ``tmp`` over the main
    connection, then queued - the same path a picture takes to the viewer."""
    if api is None:
        return
    if not api.device.is_connected("sftp"):
        api.ui.notify("The main connection is not open, so the device has "
                      "no music to add.")
        return
    wanted = api.ui.input("Add track from the device",
                          "Path inside the remote root",
                          pane_path.get(PANE_REMOTE) or "")
    wanted = str(wanted or "").strip().lstrip("/")
    if not wanted:
        return
    into = home_root() / TMP_DIR_NAME / short_name(wanted)
    try:
        landed = api.device.download(wanted, str(into))
    except Exception as error:
        _note(P_MEDIA, f"could not pull {short_name(wanted)} down: {error}")
        return
    path = str(landed or into)
    state = _media_state()
    media_add(path)
    if state.get("index", -1) < 0:
        media_play_index(len(state["tracks"]) - 1)
    _note(P_MEDIA, f"queued {short_name(wanted)}")


def media_button(tail: str) -> None:
    """One transport button by the id's tail: the two file doors, and the
    three that steer the leaf."""
    if api is None:
        return
    if tail == "open":
        media_open_local()
    elif tail == "add":
        media_add_device()
    elif tail == "play":
        state = _media_state()
        if state.get("index", -1) < 0 and state.get("tracks"):
            media_play_index(0)
        else:
            api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "play", True)
    elif tail == "pause":
        api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "pause", True)
    elif tail == "stop":
        api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "stop", True)
    elif tail == "up":
        media_move(-1)
    elif tail == "down":
        media_move(1)


def media_pick(payload: dict) -> None:
    """A double-click on the playlist: the row's index plays that track."""
    try:
        index = int((payload or {}).get("row"))
    except (TypeError, ValueError):
        index = -1
    if index >= 0:
        state = _media_state()
        state["selected"] = index
        media_play_index(index)


def media_select(payload: dict) -> None:
    """A row is highlighted but not played: remember it so the Reorder
    buttons know which track to move."""
    try:
        index = int((payload or {}).get("row"))
    except (TypeError, ValueError):
        return
    _media_state()["selected"] = index


def media_move(delta: int) -> None:
    """Slide the highlighted (or, failing that, the loaded) row up or down
    the playlist, carrying the play-mark with it so the track still sounds
    like the one it was."""
    if api is None:
        return
    state = _media_state()
    tracks = state.get("tracks") or []
    src = state.get("selected", -1)
    if src < 0:
        src = state.get("index", -1)
    dst = src + delta
    if src < 0 or dst < 0 or dst >= len(tracks):
        return
    tracks[src], tracks[dst] = tracks[dst], tracks[src]
    if state.get("index") == src:
        state["index"] = dst
    elif state.get("index") == dst:
        state["index"] = src
    state["selected"] = dst
    _media_draw_list()


def media_volume(value: Any) -> None:
    if api is None:
        return
    try:
        level = int(value or 0)
    except (TypeError, ValueError):
        return
    api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "volume", level)


def media_seek(value: Any) -> None:
    if api is None:
        return
    try:
        point = int(value or 0)
    except (TypeError, ValueError):
        return
    api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "seek", point)


def _media_clock() -> None:
    """Seat the seek slider at the loaded track's length and print the clock.
    The leaf holds its own position and duration; a state change is when they
    are worth reading, so the slider's reach and the mm:ss label follow it.
    Without QtMultimedia the reads come back blank and the row stays at zero,
    which is the honest thing to show when nothing can play."""
    if api is None:
        return
    try:
        duration = int(api.tabs.widget_value(TAB, f"{P_MEDIA}_player",
                                             "duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    try:
        position = int(api.tabs.widget_value(TAB, f"{P_MEDIA}_player",
                                             "position") or 0)
    except (TypeError, ValueError):
        position = 0
    if duration > 0:
        api.tabs.set_widget(TAB, f"{P_MEDIA}_seek", "maximum", duration)
    api.tabs.set_widget(TAB, f"{P_MEDIA}_seek", "value",
                        min(position, duration) if duration else 0)
    api.tabs.set_widget(TAB, f"{P_MEDIA}_seeklabel", "text",
                        _media_time(position))


def _media_time(ms: int) -> str:
    """Milliseconds as ``m:ss`` - the shape a transport's clock wears."""
    try:
        total = max(0, int(ms or 0)) // 1000
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60}:{total % 60:02d}"


def _media_next() -> None:
    """Step to the track after the one just loaded, or stop when the list is
    at its end. This is what a ``done`` event reaches."""
    state = _media_state()
    tracks = state.get("tracks") or []
    nxt = state.get("index", -1) + 1
    if 0 <= nxt < len(tracks):
        media_play_index(nxt)
    else:
        api.tabs.set_widget(TAB, f"{P_MEDIA}_player", "stop", True)
        api.tabs.set_widget(TAB, f"{P_MEDIA}_now", "text",
                            "Playlist finished")


def media_news(event: str, payload: dict) -> None:
    """What the leaf says about the track it holds: a new transport state
    relabels the now-playing line, an end advances the playlist, and a
    failure is said on the note rather than swallowed."""
    if api is None:
        return
    payload = payload or {}
    if event == "state":
        word = str(payload.get("state") or "")
        state = _media_state()
        tracks = state.get("tracks") or []
        index = state.get("index", -1)
        if 0 <= index < len(tracks):
            label = {"playing": "Playing", "paused": "Paused",
                     "stopped": "Stopped"}.get(word, word or "Idle")
            api.tabs.set_widget(TAB, f"{P_MEDIA}_now", "text",
                                f"{label}: {short_name(tracks[index])}")
        _media_clock()
    elif event == "done":
        _media_next()
    elif event == "error":
        _note(P_MEDIA, f"cannot play: {payload.get('text') or 'an error'}")


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
    if api is None or str(tab) != TAB:
        return
    payload = payload or {}
    body = str(identifier or "")
    # 1. The desktop itself: window events. ``body`` is the window's id when
    # the primitive reported a change inside a frame, or ``DESKTOP`` when the
    # canvas has something of its own to say. A ``shortcut`` event is left for
    # the 1c branch below, so Alt+Tab and the Konami keys are not swallowed
    # with the plain window news.
    if body == DESKTOP and event != "shortcut":
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
            maximized.discard(body)
        elif event == "maximized":
            minimized.pop(body, None)
            maximized.add(body)
        else:                                # restored
            minimized.pop(body, None)
            maximized.discard(body)
            clear_flash(body)
        refresh_taskbar()
        return
    # 1c. The desktop's own shortcut: Alt+Tab cycles the windows through
    # a choose prompt, and the lone arrow/B/A keys feed the Konami buffer.
    # A global grab is not offered to addons, so a chord fires only while
    # the Berry Manager window has focus.
    if body == DESKTOP and event == "shortcut":
        keys = str(payload.get("keys") or "").lower()
        if keys == "alt+tab":
            switcher_menu()
        elif keys in KONAMI:
            konami_seen(keys)
        return
    # 2. Start and the taskbar.
    if body == START_BTN and event == "clicked":
        start_pressed()
        return
    if body in (f"{P_START}_apps", f"{P_START}_all",
                f"{P_START}_side") and event == "activated":
        start_flyout_picked(body, str(payload.get("text") or ""))
        return
    if event == "clicked" and body == f"{P_START}_logoff":
        close_start()
        log_off()
        return
    if event == "clicked" and body == f"{P_START}_shutdown":
        close_start()
        log_off()
        api.ui.information("Turn Off Computer",
                           "It's a joke, un mate. Berry Manager stays put - "
                           "this is a desktop in a tab, not the machine.")
        return
    if body == TASK_LIST and event == "selected":
        win_id = picked_window()
        if win_id:
            raise_window(win_id)
        return
    if body == TASK_LIST and event == "context":
        taskbar_menu(str(payload.get("text") or ""))
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
    # 4. A local console's news. The Command Prompt is the only shell left,
    # so its terminal reports straight to the console window that owns it.
    if is_terminal_id(body):
        owner = terminals[body]
        record = opened.get(owner) or {}
        if record.get("kind") == "console":
            console_news(owner, event, payload)
        return
    # 4b. Paint and Media Player. Their controls are handled ahead of the
    # generic button-suffix cases below, so a ``_save`` on the Paint window
    # is read as the picture's Save As and not a Notepad save, and a
    # playlist row reaches the right program.
    if event == "changed" and body == f"{P_PAINT}_width":
        paint_width(payload.get("value"))
        return
    if event == "clicked" and body.startswith(f"{P_PAINT}_"):
        tail = body[len(P_PAINT) + 1:]
        if tail.startswith("pt_"):
            paint_tool(tail[3:])
        elif tail == "color":
            paint_colour()
        elif tail == "undo":
            paint_undo()
        elif tail == "clear":
            paint_clear()
        elif tail == "open":
            paint_open()
        elif tail == "save":
            paint_save()
        return
    if event == "changed" and body == f"{P_MEDIA}_volume":
        media_volume(payload.get("value"))
        return
    if event == "changed" and body == f"{P_MEDIA}_seek":
        media_seek(payload.get("value"))
        return
    if event == "activated" and body == f"{P_MEDIA}_list":
        media_pick(payload)
        return
    if event == "selected" and body == f"{P_MEDIA}_list":
        media_select(payload)
        return
    if body == f"{P_MEDIA}_player" and event in ("state", "done", "error"):
        media_news(event, payload)
        return
    if event == "clicked" and body.startswith(f"{P_MEDIA}_"):
        media_button(body[len(P_MEDIA) + 1:])
        return
    # 4c. The cosplay: the logon picker's faces and the startup chime's
    # player. Both only ever exist when a real window hosted them, so these
    # rows are dead weight on a headless run and never reached in a test.
    if body == f"{LOGON}_who":
        name = str(payload.get("text") or "")
        if event == "selected":
            pane_pick[LOGON] = name
        elif event == "activated" and name:
            logon_picked(name)
        return
    if event == "clicked" and body == f"{LOGON}_ok":
        logon_picked(pane_pick.get(LOGON) or "")
        return
    if event == "clicked" and body == f"{LOGON}_skip":
        close_window(LOGON)
        return
    if body == "sfx_player" and event in ("done", "error"):
        close_window("sfx")
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
    if event == "returned" and body.endswith("_path") \
            and body[:3] in PANE_KINDS:
        # Enter on the address bar walks that pane to the typed path.
        wanted = str(payload.get("text") or "")
        if not wanted:
            try:
                wanted = str(api.tabs.widget_value(TAB, body, "value") or "")
            except Exception:
                wanted = ""
        pane_goto(body[:3], wanted)
        return
    if event == "clicked" and body[:3] in PANE_KINDS \
            and body.rsplit("_", 1)[-1] in PANE_ACTIONS:
        pane, action = body[:3], body.rsplit("_", 1)[-1]
        if action == "up":
            pane_up(pane)
        elif action == "home":
            pane_home(pane)
        elif action == "root":
            pane_root(pane)
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
        return
    # 6. A viewer's buttons.
    if event == "clicked" and body.endswith(("_local", "_remote", "_zin",
                                             "_zout", "_fit", "_prev",
                                             "_next")):
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
        elif tail == "fit":
            viewer_fit(win_id)
        elif tail == "prev":
            viewer_step(win_id, -1)
        elif tail == "next":
            viewer_step(win_id, 1)
        return
    # 7. Notepad's Save and Save As. The rest of that window is the editor
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
    # Notepad's Find and Recent, and the dirty ``*`` that rides every
    # keystroke the editor reports back.
    if event == "clicked" and body.endswith("_find"):
        win_id = body[: -len("_find")]
        if win_id in berrypads:
            berry_find(win_id)
        return
    if event == "clicked" and body.endswith("_recent"):
        win_id = body[: -len("_recent")]
        if win_id in berrypads:
            berry_recent(win_id)
        return
    if event == "changed" and body.endswith("_edit"):
        win_id = body[: -len("_edit")]
        if win_id in berrypads:
            _berry_changed(win_id, str(payload.get("text") or ""))
        return
    if event == "clicked" and body == "tray_show":
        show_desktop()
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
    if body == f"{P_BROWSER}_back" and event == "clicked":
        api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "back", None)
        return
    if body == f"{P_BROWSER}_forward" and event == "clicked":
        api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "forward", None)
        return
    if body == f"{P_BROWSER}_reload" and event == "clicked":
        api.tabs.set_widget(TAB, f"{P_BROWSER}_view", "reload", None)
        return
    if body == f"{P_BROWSER}_mark" and event == "activated":
        browser_bookmark(str(payload.get("text") or ""))
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
    if event == "changed" and body == "set_theme":
        # A named scheme repaints both sheets the moment it is picked, the
        # same live-preview bargain the wallpaper combo strikes.
        api.config.set("theme", str(payload.get("text") or DEFAULT_THEME))
        mirror_settings()
        apply_theme()
        return
    if event == "clicked" and body in _SETTINGS_NAV:
        # A Control Panel category turns to the card it names; the tabs
        # container answers to its ``current`` page index.
        api.tabs.set_widget(TAB, "set_tabs", "current",
                            _SETTINGS_NAV[body])
        return
    if event == "clicked" and body == "set_preset_800":
        settings_size_preset(800, 600)
        return
    if event == "clicked" and body == "set_preset_1024":
        settings_size_preset(1024, 768)
        return
    if event == "clicked" and body == "set_preset_1280":
        settings_size_preset(1280, 1024)
        return
    if event == "clicked" and body == "set_fit":
        settings_fit_screen()
        return
    if event == "changed" and (body in _SLIDERS or body in _TOGGLES
                               or body in _COLORS):
        # Every density dial, title-bar toggle and caption colour previews
        # itself as it moves; see :func:`settings_preview`.
        settings_preview(body, payload)
        return


#: The Explorer buttons that share one dispatch: everything a pane prefix +
#: an underscore + one of these words means the same action on either side.
PANE_ACTIONS = ("up", "home", "root", "refresh", "open", "new", "rename",
                "delete", "send")
