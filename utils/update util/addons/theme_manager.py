#!/usr/bin/env python3
"""Theme Manager: make, edit and try the looks this program is drawn from.

Adds a ``Theme Manager`` tab with three sub-tabs - **Themes**, **Edit** and
**Docs** - and is the program's own way of doing what a ``.theme`` file is for:
a look is one small JSON file in the ``themes`` folder next to the program, and
this addon reads, writes and applies it through ``api.theme`` rather than
touching the disk or the window itself, so the same file works whether the addon
runs inside the app or as its own process.

``themes/default.theme`` is written by the app when it starts, so there is
always a file to copy from and always something to fall back on. A theme holds
three things: ``colors``, the named colours the interface is painted with;
``tabs``, the shape of the tab bar - how big a tab's picture is, how round its
corners, whether it shows its name at all; and ``styles``, a few lines of
stylesheet for anything the first two do not reach. Everything a theme does not
say is taken from the shipped look, so a file with two colours in it is a whole,
usable theme.

Editing happens on a *draft*: the theme in the editor's hand, held complete in
memory, changed a token or a knob at a time and shown as JSON that can be typed
into directly. Nothing reaches the disk until **Save**, and nothing reaches the
window until **Apply**, so a look can be ruined without anyone noticing.

The **Docs** sub-tab is laid out like the Addon Dev panel's: a tree of the
``api.theme`` calls under headings, beside the page each one opens. It is read
off the API object itself, so an addon author can learn the namespace here and
it cannot disagree with the program it describes.
"""

from __future__ import annotations

import json
from typing import Any

from addon_common import AddonError, config_get, find_addon_picture, merge_config, one_line

ADDON = {
    "name": "Theme Manager",
    "version": "1.0",
    "author": "fligma",
    "description": "Makes and edits the .theme files the program is drawn from: "
                   "a list of every look on disk with its author and what it is, "
                   "an editor that works on one theme at a time - colours by "
                   "their groups, the tab bar by its knobs, or the JSON direct - "
                   "and a reference of every api.theme call, read off the running "
                   "app. Saving writes themes/<name>.theme; applying draws the "
                   "window from it.",
    "requires": [],
    # A look is a file beside this program; no device is involved.
    "connection": "none",
    "api": 1,
}

#: The caption of the tab. The app looks for these words to jump from
#: Settings > Appearance and View > Theme to the editor, so a change here has
#: to be made in ``manager_gui.THEME_MANAGER_TAB`` as well.
TAB = "Theme Manager"

#: This addon's own id: what the icon rule and its data folder are named for.
OWN_ID = "theme_manager"

#: The picture the tab wears when the assets folder holds one: this addon's id
#: and then ``icon.png``, the one name every addon's picture is looked up under.
TAB_ICON_FILE = "theme_managericon.png"

TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M4.2 8.6h15.6v8.2a1.6 1.6 0 0 1-1.6 1.6H5.8a1.6 1.6 0 0 1-1.6-1.6z"'
    ' fill="none" stroke="#c51a4a" stroke-width="1.5" stroke-linejoin="round"/>'
    '<path d="M4.6 8.6 7.6 4.2h8.8l3 4.4" fill="none" stroke="#7fb3d5" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="9.2" cy="12.6" r="1.5" fill="#23d18b"/>'
    '<circle cx="12.6" cy="14.4" r="1.5" fill="#f0b429"/>'
    '<circle cx="15.6" cy="12.2" r="1.5" fill="#c51a4a"/>'
    '</svg>'
)

# --- what can be configured, and what it starts out as ---

DEFAULTS = {
    "confirm_delete": True,
    "save_applies": True,
    "expand_groups": True,
}

FIELDS = [
    {"key": "confirm_delete", "label": "Ask before a theme file is deleted",
     "kind": "bool",
     "hint": "Off, Delete goes straight through. The shipped default theme is "
             "never deleted either way."},
    {"key": "save_applies", "label": "Redraw the window when the theme in use is saved",
     "kind": "bool",
     "hint": "The switch under the editor's name. On, saving a theme the window "
             "is already drawn from shows the change at once; off, the change "
             "waits for Apply."},
    {"key": "expand_groups", "label": "Keep the colour and knob groups open",
     "kind": "bool",
     "hint": "The editor's trees list every token, grouped by what it is used "
             "for. Closed, only the first group of each is opened for you."},
]

GROUPS = (("The editor", ["save_applies", "expand_groups"]),
          ("Files", ["confirm_delete"]))

# --- the tab's own look ---

def page_style() -> str:
    """The sheet this tab is drawn with, built from the theme in force.

    An addon owns the look of its own page and nothing else, so the colours
    come from ``api.theme`` rather than being written down: the editor shows
    the program's current theme, including while it is being changed. Only the
    shape - spacing, the group boxes, the monospace family - is the addon's
    own choice.
    """
    colors = theme_colors()
    if not colors:
        # Nothing to read the look from - a build with no app beside it. The
        # window's own sheet stays in charge of this page.
        return ""
    return (
        f"QWidget {{ background-color: {colors['base']}; }}"
        f" QGroupBox {{ border: 1px solid {colors['border']};"
        " border-radius: 6px; margin-top: 10px; padding: 10px 8px 8px 8px;"
        " font-weight: 600; }"
        f" QGroupBox::title {{ subcontrol-origin: margin;"
        " subcontrol-position: top left; left: 12px; padding: 0 6px;"
        f" color: {colors['accent']}; }}"
        " QPlainTextEdit { font-family: Consolas, monospace; }"
        f" QPushButton {{ padding: 6px 12px; background-color: {colors['raise']};"
        f" border: 1px solid {colors['raise_edge']}; border-radius: 5px;"
        f" color: {colors['text']}; }}"
        f" QPushButton:hover {{ background-color: {colors['hover']}; }}"
        f" QPushButton:pressed {{ background-color: {colors['accent_deep']}; }}"
    )


_HEAD_STYLE = "color: #8f8f8f; font-weight: 600; padding: 4px 2px 0 2px;"

_BANNER_STYLE = "color: #e8e8e8; font-size: 13pt; font-weight: 600;"

_STATUS_STYLE = "color: #23d18b; padding: 2px 4px;"

_STATUS_BAD = "color: #ffb020; padding: 2px 4px;"

#: The sub-tabs, in order. The app owns the outer tab bar; these belong to the
#: addon's page and are what the editor switches between.
SUB = "subtabs"
SUB_PAGES = ("Themes", "Edit", "Docs")

#: The buttons under the theme list and under the editor's JSON box, each with
#: the words on it and the words that explain it.
THEME_BUTTONS = (
    ("use", "Apply", "Draw the whole window from the picked theme, and "
                     "remember the choice for this profile"),
    ("edit", "Edit it", "Open the picked theme in the editor"),
    ("dup", "Duplicate", "Copy the picked theme to a new name"),
    ("rename", "Rename", "The picked theme under a new name, the old file gone"),
    ("del", "Delete", "Take the picked theme's file away; the shipped default "
                      "cannot be deleted"),
    ("folder", "Themes folder", "Open the folder the .theme files live in, to "
                                "copy one in or out"),
)

EDIT_BUTTONS = (
    ("getsource", "Show the JSON", "Put the draft back into the box below, the "
                                   "way its file would hold it"),
    ("putsource", "Use the JSON", "Read the box below back into the draft, "
                                  "checking it first"),
    ("save", "Save", "Write themes/<name>.theme"),
    ("saveapply", "Save and apply", "Write it and draw the window from it"),
    ("discard", "Re-read from disk", "Throw the draft away and open the file again"),
)


def buttons(entries: tuple) -> list:
    """A row of command buttons out of ``(id, caption, tooltip)`` triples.

    The id is what :func:`on_widget` looks a handler up by, so the same words
    that name a button are the words the click is answered under.
    """
    return [{"kind": "button", "id": identifier, "text": text,
             "tooltip": tip, "on": ["clicked"]}
            for identifier, text, tip in entries]

# --- the state the tab works from ---

api = None                  # filled in by init()

#: The theme in the editor's hand: the dict its file stands for, complete, and
#: the name of the file it came from (or would be written to).
draft: dict | None = None
draft_name = ""

#: Which colour token and which tab knob the editor is pointed at, and what
#: the tree of ``api.theme`` calls maps a heading onto.
picked_token = ""
picked_knob = ""
doc_branches: dict[str, list[str]] = {}

#: The theme a right-click named, until the next command reads it. A menu shown
#: at the pointer is about the row under the pointer, which is not necessarily
#: the row the list is still pointing at.
context_name = ""

# --- small helpers over the api ---

def theme_colors(name: str = "") -> dict:
    """Every colour token of a theme - the one in force by default."""
    if api is None:
        return {}
    try:
        return dict(api.theme.colors(name) or {})
    except (AddonError, TypeError):
        return {}


def accent() -> str:
    """The colour a heading in one of this tab's trees is painted in."""
    return theme_colors().get("accent") or "#c51a4a"


def say(text: str, bad: bool = False) -> None:
    """One line on the tab's status, and nothing once this copy is gone."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style",
                            _STATUS_BAD if bad else _STATUS_STYLE)
    except AddonError:
        pass


def note(text: str) -> None:
    """One line said out loud on the status and written to the program's log."""
    say(text)
    if api is not None:
        api.log(text)


def put(identifier: str, value: Any, prop: str = "value") -> None:
    """Write one widget on this tab, ignoring a pane that is not built."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, identifier, prop, value)
    except (AddonError, TypeError):
        pass


def get(identifier: str, what: str = "value", fallback: Any = "") -> Any:
    """Read one widget back; the fallback when the pane or the widget is gone."""
    if api is None:
        return fallback
    try:
        found = api.tabs.widget_value(TAB, identifier, what)
    except (AddonError, AttributeError, TypeError):
        return fallback
    return fallback if found is None else found


def words(identifier: str) -> str:
    """One line as the whole text of a text widget, newlines and all."""
    return str(get(identifier, "value", "") or "")


def ask_name(suggest: str = "") -> str:
    """The name in the 'named' line, or a box asking for it when it is empty."""
    typed = one_line(get("newname", "value", ""))
    if typed:
        return typed
    if api is None:
        return one_line(suggest)
    try:
        answer = api.ui.input("Name the theme", "Theme name",
                              one_line(suggest) or "my look")
    except AddonError:
        return ""
    return one_line(answer or "")


def guarded(action: Any, doing: str) -> Any:
    """Run one api call and say what went wrong instead of raising into a click.

    A theme manager that refuses is doing its job - a name that cannot be a
    file, a colour that is not a colour, a token nobody heard of - so the
    refusal belongs on the status line, in the words the app used.
    """
    try:
        return action()
    except (AddonError, ValueError, OSError) as error:
        say(f"cannot {doing}: {error}", bad=True)
        if api is not None:
            api.error(f"{doing} failed: {error}")
        return None


# --- the tab itself ---

def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark.

    One name is tried - ``theme_managericon.png`` - in the addon's own assets
    folder and then the program's, so putting the file down is the whole of it.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    if paths is None:
        return TAB_ICON
    try:
        return find_addon_picture(paths, OWN_ID) or TAB_ICON
    except (OSError, AddonError):
        return TAB_ICON


def themes_page() -> dict:
    """Every look on disk, what each one is, and the ways to make a new one."""
    return {"kind": "column", "children": [
        {"kind": "table", "id": "themes",
         "columns": ["Theme", "In use", "Author", "What it is"],
         "stretch": 3, "col_widths": [140, 60, 110],
         "on": ["selected", "context"],
         "tooltip": "Every themes/<name>.theme on disk. Pick one to read its "
                    "file; right-click it for what can be done to it."},
        {"kind": "row", "children": buttons(THEME_BUTTONS)},
        {"kind": "group", "text": "A look that does not exist yet", "children": [
            {"kind": "row", "children": [
                {"kind": "label", "text": "from"},
                {"kind": "combo", "id": "tpl", "expand": True,
                 "on": ["changed"],
                 "tooltip": "The starting point: each is a whole theme, ready "
                            "to edit, and none of them is on disk until you "
                            "create it"},
                {"kind": "label", "text": "named"},
                {"kind": "line", "id": "newname", "expand": True,
                 "placeholder": "my look",
                 "tooltip": "A file name, so no / \\ : * ? or quotes; spaces are "
                            "allowed and become part of the theme's name"},
                {"kind": "button", "id": "create", "text": "Create",
                 "on": ["clicked"], "tooltip": "Write the new theme and open it "
                                               "in the editor"},
            ]},
            {"kind": "label", "id": "tplnote", "text": "", "word_wrap": True,
             "style": _HEAD_STYLE},
        ]},
        {"kind": "multi", "id": "tinfo", "readonly": True, "expand": True,
         "highlight": "json",
         "placeholder": "pick a theme to read its file as it stands on disk"},
    ]}


def edit_page() -> dict:
    """One theme in the editor's hand: colours, tab knobs, and the JSON itself."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Editing", "style": _HEAD_STYLE},
            {"kind": "combo", "id": "draft", "expand": True, "on": ["changed"],
             "tooltip": "The theme in the editor. Picking another one drops any "
                        "change that has not been saved."},
            {"kind": "line", "id": "draftas", "placeholder": "a new name for it",
             "tooltip": "Leave blank to save over the theme opened. Filled in, "
                        "Save writes a new file instead of touching that one."},
            {"kind": "check", "id": "applylive", "text": "redraw on save",
             "checked": True,
             "tooltip": "Save the theme the window is drawn from and the change "
                        "shows at once"},
            {"kind": "button", "id": "newdraft", "text": "Blank from default",
             "on": ["clicked"], "tooltip": "A draft of the shipped look, not on "
                                           "disk until it is saved"},
        ]},
        {"kind": "splitter", "sizes": [5, 4], "children": [
            {"kind": "tree", "id": "tokens", "columns": ["Colour", "Hex"],
             "on": ["selected"],
             "tooltip": "Every colour the interface is painted with, in the "
                        "blocks it is used for. Pick one and set it on the right."},
            {"kind": "column", "children": [
                {"kind": "label", "id": "tokennote", "text": "", "word_wrap": True,
                 "style": _HEAD_STYLE},
                {"kind": "row", "children": [
                    {"kind": "label", "text": "hex"},
                    {"kind": "color", "id": "hex", "expand": True,
                     "placeholder": "#rrggbb",
                     "tooltip": "Type a colour, or press the chip for the wheel"},
                    {"kind": "button", "id": "wheel", "text": "Wheel...",
                     "on": ["clicked"], "tooltip": "The app's colour wheel"},
                    {"kind": "button", "id": "setcolor", "text": "Set colour",
                     "on": ["clicked"], "tooltip": "Put that colour on the "
                                                   "picked token in the draft"},
                ]},
                {"kind": "label", "text": "The tab bar", "style": _HEAD_STYLE},
                {"kind": "tree", "id": "knobs", "columns": ["Knob", "Now"],
                 "on": ["selected"],
                 "tooltip": "What the app owns about a tab: its picture, its "
                            "air, its corners, whether it shows its name at all."},
                {"kind": "row", "children": [
                    {"kind": "label", "text": "to"},
                    {"kind": "spin", "id": "knobnum", "minimum": 0, "maximum": 200,
                     "value": 0,
                     "tooltip": "For a knob measured in pixels"},
                    {"kind": "check", "id": "knobbool", "text": "on",
                     "tooltip": "For a knob that is a switch"},
                    {"kind": "button", "id": "setknob", "text": "Set knob",
                     "on": ["clicked"], "tooltip": "Put that value on the picked "
                                                   "knob in the draft"},
                ]},
            ]},
        ]},
        {"kind": "multi", "id": "source", "highlight": "json",
         "placeholder": "the draft as its file holds it - edit it here and "
                        "press Use the JSON"},
        {"kind": "row", "children": buttons(EDIT_BUTTONS) + [
            {"kind": "label", "id": "draftstate", "text": "", "expand": True,
             "style": _HEAD_STYLE},
        ]},
    ]}


def docs_page() -> dict:
    """The reference, laid out like the Addon Dev panel's own docs pane."""
    return {"kind": "splitter", "sizes": [1, 2], "children": [
        {"kind": "tree", "id": "docs", "columns": ["api.theme"],
         "on": ["selected"],
         "tooltip": "The guides first, then every call on api.theme under the "
                    "heading it belongs to; the page opens beside it"},
        {"kind": "column", "children": [
            {"kind": "label", "id": "dochead", "text": "", "word_wrap": True,
             "style": _HEAD_STYLE},
            {"kind": "multi", "id": "docpage", "readonly": True, "expand": True,
             "placeholder": "pick a heading or a call"},
        ]},
    ]}


def tab_spec() -> dict:
    """The whole tab: a banner over the three sub-tabs.

    Declarative, so the same file runs in the app's process or outside it, and
    every widget is reached by name through ``set_widget`` afterwards.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": TAB, "style": _BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": _STATUS_STYLE},
            {"kind": "label", "id": "where", "text": "", "style": _HEAD_STYLE},
        ]},
        {"kind": "tabs", "id": SUB, "children": [
            dict(themes_page(), label=SUB_PAGES[0]),
            dict(edit_page(), label=SUB_PAGES[1]),
            dict(docs_page(), label=SUB_PAGES[2]),
        ]},
    ]}


def build_tab() -> None:
    """One tab, pinned, wearing its caption - the theme decides whether it shows."""
    api.tabs.add_tab(TAB, spec=tab_spec(), style=page_style(), pinned=True)
    api.tabs.set_icon(TAB, tab_icon())
    api.tabs.set_widget(TAB, SUB, "current", 0)


def show_page(page: int) -> None:
    """Move to one of the sub-tabs; the menu and the app's links use this."""
    put(SUB, int(page), "current")


# --- the Themes sub-tab ---

def in_force() -> str:
    """The name the window is drawn from."""
    if api is None:
        return "default"
    try:
        return str(api.theme.name() or "default")
    except AddonError:
        return "default"


def theme_blurb(name: str) -> tuple[str, str]:
    """What a theme says about itself - author and one-line story - from its file."""
    written = api.theme.raw(name) if api is not None else {}
    author = one_line((written or {}).get("author"))
    blurb = one_line((written or {}).get("blurb"))
    if not (author and blurb):
        whole = api.theme.read(name) if api is not None else {}
        author = author or one_line((whole or {}).get("author"))
        blurb = blurb or one_line((whole or {}).get("blurb"))
    return author, blurb


def refresh_themes() -> None:
    """The list, read off the folder: every file, the default at the top."""
    if api is None:
        return
    live = in_force()
    rows = []
    for name in api.theme.names():
        author, blurb = theme_blurb(name)
        rows.append({"cells": [name, "*" if name == live else "", author, blurb]})
    put("themes", rows, "rows")
    put("where", "folder: " + str(api.theme.folder()), "text")
    put("draft", api.theme.names(), "items")
    put("draft", live if api.theme.exists(live) else "default", "value")
    templates = api.theme.templates()
    put("tpl", [str(one.get("name") or "") for one in templates], "items")
    chosen = str(get("tpl", "value", ""))
    put("tplnote", next((one_line(one.get("blurb")) for one in templates
                         if str(one.get("name")) == chosen), ""))


def picked_theme() -> str:
    """The theme a command is about: the row a right-click named, then the one
    the list is pointed at, then the one in force when nothing is picked."""
    global context_name
    if context_name:
        named, context_name = context_name, ""
        return named
    rows = get("themes", "selected", [])
    if isinstance(rows, (list, tuple)) and rows:
        return one_line(rows[0])
    return in_force()


def show_info(name: str) -> None:
    """The picked theme's own file, as it stands on disk."""
    if api is None or not name:
        return
    written = api.theme.raw(name)
    text = json.dumps(written, indent=2, ensure_ascii=False) if written \
        else "(nothing readable in that file)"
    put("tinfo", text)
    put("tplnote", "", "text")


def note_template(_changed: Any = None) -> None:
    """Say what the template in the combo is, when the pick moves."""
    chosen = one_line(get("tpl", "value", ""))
    for one in (api.theme.templates() if api is not None else []):
        if str(one.get("name")) == chosen:
            say(f"{chosen}: {one_line(one.get('blurb'))}")
            put("tplnote", one_line(one.get("blurb")))
            return


# --- the Edit sub-tab ---

def open_draft(name: str) -> bool:
    """Put one theme on disk into the editor's hand; complete, every token."""
    global draft, draft_name, picked_token, picked_knob
    if api is None or not name:
        return False
    # A name with no file behind it is not a theme: the app reads it as the
    # shipped look, and an editor that went along with that would be holding
    # ``default`` under a name nothing on disk answers to.
    there = guarded(lambda: api.theme.exists(name), f"look for {name}")
    if there is None:
        return False
    if not there:
        say(f"there is no themes/{name}.theme to open", bad=True)
        return False
    try:
        whole = api.theme.read(name)
    except AddonError as error:
        say(f"cannot read {name}: {error}", bad=True)
        return False
    draft = dict(whole or {})
    draft["name"] = name
    draft_name = name
    picked_token = ""
    picked_knob = ""
    put("draftas", "", "value")
    show_draft()
    say(f"editing {name}")
    return True


def show_draft() -> None:
    """Every part of the tab that reads the draft: the trees, the name, the note."""
    if api is None or draft is None:
        return
    put("draft", draft_name, "value")
    tokens = str(draft.get("name") or draft_name)
    put("draftstate",
        f"{tokens} \u00b7 {len(draft.get('colors') or {})} colours, "
        f"{len(draft.get('tabs') or {})} tab knobs"
        + (", not saved yet" if not api.theme.exists(draft_name) else ""))
    show_tokens()
    show_knobs()
    # The box under the trees is a mirror of the draft rather than a second
    # copy of it: a colour changed here would otherwise leave, in the box, a
    # file that nothing agrees with - and 'Use the JSON' would undo the change.
    show_source()
    if picked_token:
        show_token(picked_token)
    elif picked_knob:
        show_knob(picked_knob)


def token_value(token: str) -> str:
    """What the draft says a colour token is."""
    hand = draft or {}
    return str((hand.get("colors") or {}).get(token, ""))


def show_tokens() -> None:
    """The colour tree: one branch per group, one line per token, in the order
    the app lists them."""
    if api is None or draft is None:
        return
    expand = bool(config_get(values(), "expand_groups", True))
    rows = []
    for index, group in enumerate(api.theme.groups()):
        kids = []
        for one in group.get("tokens") or []:
            name = str(one.get("name") or "")
            kids.append({"cells": [name, token_value(name) or str(one.get("color"))]})
        rows.append({"cells": [f"{group.get('title')} ({len(kids)})", ""],
                    "color": accent(),
                    "expanded": expand or index == 0,
                    "children": kids})
    put("tokens", rows, "rows")


def show_knobs() -> None:
    """The tab-knob tree: pixels in one branch, switches in the other."""
    if api is None or draft is None:
        return
    hand = dict(draft.get("tabs") or {})
    sizes, switches = [], []
    for one in api.theme.token_knobs():
        name = str(one.get("name") or "")
        now = hand.get(name, one.get("value"))
        shown = ("on" if now else "off") if one.get("kind") == "switch" else str(now)
        (switches if one.get("kind") == "switch" else sizes).append(
            {"cells": [name, shown]})
    rows = [{"cells": [f"Sizes, in pixels ({len(sizes)})", ""], "color": accent(),
             "expanded": True, "children": sizes},
            {"cells": [f"Switches ({len(switches)})", ""], "color": accent(),
             "expanded": True, "children": switches}]
    put("knobs", rows, "rows")


def knob_kind(name: str) -> str:
    """Whether a knob is measured in pixels or is a switch, '' when unknown."""
    if api is None:
        return ""
    for one in api.theme.token_knobs():
        if str(one.get("name")) == str(name):
            return str(one.get("kind") or "")
    return ""


def show_token(name: str) -> None:
    """Point the colour half of the editor at one token."""
    global picked_token
    picked_token = str(name)
    hint = ""
    for group in (api.theme.groups() if api is not None else []):
        for one in group.get("tokens") or []:
            if str(one.get("name")) == picked_token:
                hint = str(one.get("hint") or "")
    put("tokennote", f"{picked_token} \u00b7 {hint or 'a colour the interface uses'}")
    put("hex", token_value(picked_token))


def show_knob(name: str) -> None:
    """Point the knob half of the editor at one knob, and its control at its kind."""
    global picked_knob
    picked_knob = str(name)
    kind = knob_kind(picked_knob)
    now = dict((draft or {}).get("tabs") or {}).get(picked_knob)
    hint = ""
    for one in (api.theme.token_knobs() if api is not None else []):
        if str(one.get("name")) == picked_knob:
            hint = str(one.get("hint") or "")
    put("tokennote", f"{picked_knob} \u00b7 {hint or 'a knob of the tab bar'}"
                     f" \u2014 {'a switch' if kind == 'switch' else 'pixels'}")
    if kind == "switch":
        put("knobbool", bool(now), "checked")
        put("knobnum", 0)
        put("knobnum", False, "visible")
        put("knobbool", True, "visible")
    else:
        try:
            put("knobnum", int(float(now or 0)))
        except (TypeError, ValueError):
            put("knobnum", 0)
        put("knobbool", False, "visible")
        put("knobnum", True, "visible")


def draft_json() -> str:
    """The draft the way its file would hold it."""
    return json.dumps(draft or {}, indent=2, ensure_ascii=False) + "\n"


def values() -> dict:
    """This addon's saved config, or the defaults when there is no api yet."""
    if api is None:
        return dict(DEFAULTS)
    try:
        return merge_config(DEFAULTS, api.config.stored())
    except (AddonError, TypeError):
        return dict(DEFAULTS)


def apply_change(group: str, name: str, value: Any) -> bool:
    """One change to the draft, checked the way the app would check the file.

    The candidate is the whole draft with the change in it, so a colour that
    clashes with nothing and a knob left out of the file both come out as the
    complete theme they are meant to be; a refusal is said in the app's words.
    """
    global draft
    if draft is None:
        say("nothing is in the editor's hand", bad=True)
        return False
    candidate = json.loads(json.dumps(draft))
    candidate.setdefault(group, {})[name] = value
    if api is not None:
        problems = api.theme.validate(candidate)
        if problems:
            say("; ".join(str(one) for one in problems), bad=True)
            return False
    draft = candidate
    return True


def set_colour(_checked: bool = False) -> None:
    """Put the hex line's colour on the picked token."""
    if not picked_token:
        say("pick a colour from the list first", bad=True)
        return
    wanted = one_line(get("hex", "value", ""))
    if not wanted:
        say("there is no colour in that line", bad=True)
        return
    if apply_change("colors", picked_token, wanted):
        show_draft()
        say(f"{picked_token} is {wanted}")


def pick_colour(_checked: bool = False) -> None:
    """The app's colour wheel, straight into the hex line."""
    if api is None:
        return
    start = token_value(picked_token) if picked_token else ""
    try:
        found = api.ui.pick_color(f"Set {picked_token or 'a colour'}", start)
    except AddonError:
        return
    if found:
        put("hex", found)


def set_knob(_checked: bool = False) -> None:
    """Put the spin's pixels, or the switch, on the picked knob."""
    if not picked_knob:
        say("pick a knob from the list first", bad=True)
        return
    if knob_kind(picked_knob) == "switch":
        wanted: Any = bool(get("knobbool", "checked", False))
    else:
        wanted = int(get("knobnum", "value", 0) or 0)
    if apply_change("tabs", picked_knob, wanted):
        show_draft()
        show_knob(picked_knob)
        say(f"{picked_knob} is {wanted}")


def show_source(_checked: bool = False) -> None:
    """The draft into the JSON box, the way its file would hold it."""
    put("source", draft_json() if draft is not None else "")


def use_source(_checked: bool = False) -> None:
    """The JSON box back into the draft, checked first; the direct way in."""
    global draft
    text = words("source").strip()
    if not text:
        say("the JSON box is empty", bad=True)
        return
    try:
        read = json.loads(text)
    except ValueError as error:
        say(f"that is not JSON: {error}", bad=True)
        return
    if not isinstance(read, dict):
        say("a theme file is one JSON object", bad=True)
        return
    if api is not None:
        problems = api.theme.validate(read)
        if problems:
            say("; ".join(str(one) for one in problems), bad=True)
            return
    draft = read
    if api is not None:
        put("draftas", "", "value")
    show_draft()
    say("the draft is what the box says", bad=False)


def target_name() -> str:
    """The file Save writes to: the name in the editor, or the one typed over it."""
    typed = one_line(get("draftas", "value", ""))
    return typed or str(draft_name or "")


def save_draft(apply: bool = False) -> bool:
    """Write the draft to its file, and draw the window from it when asked."""
    global draft, draft_name
    if api is None or draft is None:
        say("there is nothing to save", bad=True)
        return False
    wanted = target_name()
    if not wanted:
        say("a theme needs a name", bad=True)
        return False
    cutting = json.loads(json.dumps(draft))
    cutting["name"] = wanted
    saved = guarded(lambda: api.theme.save(wanted, cutting), f"save {wanted}")
    if saved is None:
        return False
    draft = cutting
    draft_name = str(saved)
    put("draftas", "", "value")
    refresh_themes()
    show_draft()
    if apply or (bool(get("applylive", "checked", True)) and saved == in_force()):
        used = guarded(lambda: api.theme.use(str(saved)), f"apply {saved}")
        say(f"{saved} saved and the window is drawn from {used or saved}")
    else:
        say(f"{saved} saved")
    return True


def discard_draft(_checked: bool = False) -> None:
    """Throw the draft away and open the file again; a new name starts the default."""
    if api is None:
        return
    if not api.theme.exists(draft_name):
        open_draft(in_force())
        say("started again from the theme in use")
        return
    open_draft(draft_name)
    say(f"{draft_name} re-read from disk")


def blank_draft(_checked: bool = False) -> None:
    """A draft of the shipped look under no file at all."""
    global draft, draft_name, picked_token, picked_knob
    if api is None:
        return
    for one in api.theme.templates():
        if str(one.get("name")) == "default":
            draft = json.loads(json.dumps(one.get("theme") or {}))
            break
    else:
        draft = api.theme.read(in_force())
    draft_name = str(draft.get("name") or "my look")
    if api.theme.exists(draft_name):
        # The shipped template is called ``default``, and ``default`` is on disk:
        # a draft that is not on disk yet needs a name nothing answers to, or the
        # editor would claim to be holding the shipped look.
        draft_name += " copy"
    draft["name"] = draft_name
    picked_token = picked_knob = ""
    put("draftas", "")
    show_draft()
    say("a draft of the shipped look; it is not on disk until you save it")


# --- the commands on the Themes list ---

def apply_picked(_checked: bool = False) -> None:
    """Draw the window from the picked theme and remember the choice."""
    if api is None:
        return
    name = picked_theme()
    used = guarded(lambda: api.theme.use(name), f"apply {name}")
    if used is None:
        return
    refresh_themes()
    if str(used) != str(name):
        say(f"{name} has no file; the window is drawn from {used}", bad=True)
    else:
        say(f"the window is drawn from {used}")


def edit_picked(_checked: bool = False) -> None:
    """Open the picked theme in the editor, saying so if there is work not saved."""
    name = picked_theme()
    if not name:
        say("no theme is picked", bad=True)
        return
    edit_picked_by_name(name)


def same_draft() -> bool:
    """Whether the draft in hand is still exactly the file on disk."""
    if api is None or draft is None:
        return True
    try:
        return api.theme.read(draft_name) == draft
    except AddonError:
        return False


def create_from_template(_checked: bool = False) -> None:
    """A new theme out of a template, opened in the editor straight after."""
    if api is None:
        return
    wanted = ask_name()
    if not wanted:
        say("a theme needs a name", bad=True)
        return
    if api.theme.exists(wanted):
        say(f"a theme called {wanted} is already there", bad=True)
        return
    chosen = one_line(get("tpl", "value", ""))
    cutting = next((one.get("theme") for one in api.theme.templates()
                    if str(one.get("name")) == chosen), None)
    if cutting is None:
        cutting = api.theme.read(in_force())
    cutting = json.loads(json.dumps(cutting))
    cutting["name"] = wanted
    saved = guarded(lambda: api.theme.save(wanted, cutting), f"create {wanted}")
    if saved is None:
        return
    put("newname", "")
    refresh_themes()
    show_page(1)
    open_draft(str(saved))
    say(f"{saved} made from {chosen or 'the theme in use'}")


def duplicate_picked(_checked: bool = False) -> None:
    """A copy of the picked theme under a new name."""
    if api is None:
        return
    name = picked_theme()
    wanted = ask_name(f"{name} copy")
    if not wanted:
        return
    if guarded(lambda: api.theme.copy(name, wanted), f"copy {name}"):
        refresh_themes()
        say(f"{name} copied to {wanted}")


def rename_picked(_checked: bool = False) -> None:
    """The picked theme under a new name, its old file gone."""
    if api is None:
        return
    name = picked_theme()
    wanted = ask_name(name)
    if not wanted or wanted == name:
        return
    fresh = guarded(lambda: api.theme.rename(name, wanted), f"rename {name}")
    if fresh is None:
        return
    global draft, draft_name
    if draft is not None and draft_name == name:
        draft_name = str(fresh)
        draft["name"] = str(fresh)
    refresh_themes()
    show_draft()
    say(f"{name} is now {fresh}")


def delete_picked(_checked: bool = False) -> None:
    """Take one theme file away, once; the shipped default refuses."""
    global draft, draft_name
    if api is None:
        return
    name = picked_theme()
    if not name:
        say("no theme is picked", bad=True)
        return
    if config_get(values(), "confirm_delete", True):
        if not api.ui.question("Delete a theme",
                               f"Delete themes/{name}.theme? The look it gives is "
                               "gone from this program's folder.", False):
            say("nothing deleted")
            return
    gone = guarded(lambda: api.theme.remove(name), f"delete {name}")
    if gone is None:
        return
    if not gone:
        say(f"{name} is the shipped default and stays", bad=True)
        return
    if draft is not None and draft_name == name:
        draft, draft_name = None, ""
        put("draftstate", "nothing in the editor")
    refresh_themes()
    say(f"{name} deleted")


def open_folder(_checked: bool = False) -> None:
    """The themes folder, in the system's own file viewer, through the window."""
    if api is None:
        return
    opener = getattr(get_window(), "open_themes_folder", None)
    if callable(opener):
        try:
            opener()
            say("the themes folder is open")
            return
        except Exception:                        # an app that cannot open it still says where
            pass
    api.ui.information("The themes folder",
                       f"Your file viewer cannot be opened from here.\n\n"
                       f"{api.theme.folder()}")


def get_window() -> Any:
    """The app window, when this addon is running inside one."""
    try:
        return api.ui.window()
    except (AddonError, AttributeError, TypeError):
        return None


# --- the Docs sub-tab ---

#: The guide pages the Docs tree starts with, each a short read in plain text.
#: They are the addon's own; the api.theme reference beside them is generated.
GUIDES: tuple[tuple[str, str], ...] = (
    ("What a theme is", """
A theme is one file: ``themes/<name>.theme``, holding one JSON object. The
program reads it, paints itself from it, and never needs more than that.

    {
      "name": "deep sea",
      "author": "you",
      "blurb": "Blue-black surfaces, cyan accent.",
      "colors": { "base": "#0b1220", "accent": "#2f9bd6" },
      "tabs": { "icon_px": 22, "radius_px": 8 },
      "styles": "QToolTip { color: #d6e2f0; }"
    }

Three keys do the work and two are only for people: ``name``, ``author`` and
``blurb`` say what the theme is called and who made it and why, and are shown
in the list. ``colors``, ``tabs`` and ``styles`` are what the window is built
from.

A key that is missing is not a problem. Everything a theme does not say is
taken from the shipped look, so the file above - two colours and two knobs out
of forty-odd and thirteen - is a complete theme. That is what makes a theme a
cheap thing to make: you change the two or three colours that bother you and
leave the rest alone.

The file is read when it is applied, not watched, so a program that is already
running does not notice a change until Apply. The folder is beside the program,
and the program makes it, with ``default.theme`` inside it, the first time it
runs.
"""),
    ("Colour tokens", """
``colors`` names the colours the interface is painted with. Every token is one
of a fixed list - there is no use in inventing a name, because the sheet that
paints the window asks for these words and no others.

The tokens fall into blocks, and the editor lists them in those blocks:

* surfaces: ``base`` the window, ``surface`` the panels and inputs in it,
  ``sunken`` the menu bar and alternate rows, ``soft`` the progress bars,
  ``menu`` a menu when it drops, ``header`` a column header, ``raise`` a button,
  ``hover`` a button the pointer is over, ``row_hover`` a row the pointer is
  over, ``tab`` a tab and ``tab_hover`` the one under the pointer,
  ``disabled_fill`` a greyed button, ``toggle_off`` a switch pushed the wrong
  way;
* lines: ``border``, ``raise_edge``, ``handle``, ``faint``, ``disabled_edge``;
* words: ``text``, ``bright`` for the selected and pressed, ``muted``,
  ``menu_text``, ``note``, ``disabled_text``;
* meanings: ``accent`` the berry every highlight wears, ``accent_deep`` and
  ``accent_edge`` its pressed and outlined forms, ``selection`` the row you
  picked, ``ok`` green, ``warn`` amber, ``danger_fill``/``danger_edge``/
  ``danger_text`` the red button;
* small things: ``close_ink`` and ``close_hot`` for the cross on a tab,
  ``icon_alt`` and ``icon_tint`` for the second colour of an icon,
  ``toggle_handle`` and ``toggle_handle_off`` for a switch's knob.

A colour is ``#rgb``, ``#rrggbb``, ``#rrggbbaa``, one of CSS's names, or
``transparent``. Anything else is refused when the file is written, with the
token named in the message. The wheel is behind the chip on the editor's hex
line, so a colour can be picked rather than typed.
"""),
    ("The tab bar's knobs", """
``tabs`` says the shape of the outer tab bar - the one the program owns, not
the sub-tabs an addon puts inside its own page. Thirteen knobs, twelve of them a
number of pixels at 100% zoom, and two switches that turn into twelve:

* ``icon_px`` the smallest a tab's picture is drawn;
* ``radius_px`` how round a tab's corners are;
* ``pad_y_px`` and ``pad_x_px`` the air inside a tab, and ``gap_px`` the space
  left between two of them;
* ``underline_px`` the line a chosen tab is marked with;
* ``min_width_px`` the least room a tab may be squeezed into before its caption
  is dropped;
* ``nest_pad_y_px``, ``nest_pad_x_px``, ``nest_gap_px``, ``nest_min_width_px``
  the same five for a tab nested in a group, which is what an addon's own
  sub-tabs are drawn with;
* ``captions`` whether a tab shows its name beside its picture at all, and
  ``dots`` whether the SSH/SFTP marks are worn next to it.

Numbers between 0 and 200 are accepted; a switch is ``true`` or ``false``. A
knob nobody heard of is reported and ignored, which is the right outcome for a
theme written for a newer program than the one reading it.

The two switches are the two that change what the bar looks like most: a theme
with ``"captions": false`` is an icon-only bar from the first tab to the last,
and one with ``"dots": false`` hides the marks that say whether a tab has a
connection behind it.
"""),
    ("Your own lines", """
``styles`` is a string: the last part of the sheet the window is built from,
after the colours and the knobs have done their work. It is where anything goes
that the two cannot express - a tooltip's font, a scroll bar's width, one
widget's whole look.

Because the sheet is written out of a template, a ``$token`` in ``styles`` is
replaced by the colour it names before the sheet is used:

    "styles": "QToolTip { background-color: $menu; color: $bright; }"

is the same file as the two hexes spelled out, and follows the theme if the
theme is changed afterwards. Unknown words are left exactly as they are, so a
mistake is visible in the window rather than silently swallowed.

One warning that is worth the typing: the sheet is a program's whole look, and a
line of it that Qt dislikes is a line that will not paint. ``styles`` is applied
as it is written. A theme that leaves the interface unreadable can be left
behind by choosing ``default`` again in Settings > Appearance, or by deleting
the file - the program falls back to the shipped look rather than refusing to
start.
"""),
    ("Following the look in an addon", """
An addon owns its own page and the program owns the tab it sits in. A theme
therefore changes two things about an addon and none of its contents: the tab
bar - its picture's size, its corners, its caption, its colour - and every
colour the addon asks for by token.

``api.theme`` is how an addon asks:

    base = api.theme.color("surface")
    api.tabs.set_style("My tab", f"QWidget {{ background: {base}; }}")

or, when the whole sheet is wanted at once:

    api.tabs.set_style("My tab", api.theme.stylesheet())

There is no need to watch for the look changing. The program redraws the window
when a theme is applied, which repaints every tab that was styled from it, and
``theme_changed`` is sent as an event for an addon that caches a colour of its
own:

    api.events.on("theme_changed", lambda data: restyle())

The colour tokens are a fixed list, and an addon should treat them as a palette
and not as numbers to be compared: a theme is allowed to make ``accent`` cyan
and ``base`` a light grey, so a page that assumes the accent is dark and the
base is light will be unreadable on a look that has not been thought about. The
safe addon reads two or three tokens, uses them, and lets the theme manager
worry about the rest.

Templates are offered to addons too: ``api.theme.templates()`` is the list of
looks a new theme may start from, each with the dict it starts as, which is what
the editor's own New line is filled from.
"""),
    ("When a theme is wrong", """
Everything a theme says is checked on the way in, and whatever it does not say
is filled in from the shipped look, so the failure modes are narrow:

* **a file that is not JSON** reads as an empty theme: the window comes out as
  the default look and the file is left alone, because a half-saved file is not
  a reason to stop a program;
* **a colour that is not a colour** is refused by ``api.theme.save`` with the
  token named, unless the write is forced; ``validate`` answers the same
  question without writing anything, which is what the editor uses before it
  will take a typed JSON;
* **a token nobody heard of** is reported as a problem for the same reason - it
  will be ignored, and a theme author should know that rather than wonder why
  nothing changed;
* **a name that cannot be a file** has its bad characters turned into ``_`` on
  the way in, so ``sys/admin`` becomes ``sys_admin``, and ``default`` is kept
  aside as the shipped look: it cannot be deleted, and a request for a theme
  with no file behind it comes back as ``default`` rather than an error.
"""),
)

#: The reference's headings, and which calls fall under each. Anything
#: ``api.theme`` grows later lands under ``Other``, so the tree cannot go out of
#: date without going out of date being visible.
DOC_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Which themes there are", ("name", "names", "exists", "folder")),
    ("What a theme says", ("read", "raw", "colors", "color", "tabs", "metric",
                           "icon_px", "chrome_px", "shows_captions", "shows_dots",
                           "groups", "token_knobs")),
    ("What it would look like", ("stylesheet", "sheet_of", "validate",
                                 "templates", "template_text", "reference")),
    ("Changing them", ("use", "save", "copy", "rename", "remove")),
)

DOC_FALLBACK = "Other"


def reference() -> dict[str, dict]:
    """Every call on ``api.theme``, by name: ``{name, args, doc}``."""
    if api is None:
        return {}
    try:
        return {str(one.get("name")): one for one in (api.theme.reference() or [])}
    except (AddonError, TypeError):
        return {}


def fill_docs() -> None:
    """The Docs tree: the guides, then every call under its heading."""
    global doc_branches
    doc_branches = {}
    rows = []
    guides = [{"cells": [title]} for title, _body in GUIDES]
    doc_branches[f"Guides ({len(guides)})"] = [title for title, _ in GUIDES]
    rows.append({"cells": [f"Guides ({len(guides)})", ""], "color": accent(),
                 "expanded": True, "children": guides})
    known = reference()
    placed = {name for _title, names in DOC_HEADINGS for name in names}
    headings = list(DOC_HEADINGS) + [
        (DOC_FALLBACK, tuple(name for name in known if name not in placed))]
    for title, names in headings:
        kids = [{"cells": [name]} for name in names if name in known]
        if not kids:
            continue
        label = f"{title} ({len(kids)})"
        doc_branches[label] = [str(one["cells"][0]) for one in kids]
        rows.append({"cells": [label, ""], "color": accent(),
                     "expanded": False, "children": kids})
    put("docs", rows, "rows")
    show_doc(GUIDES[0][0])


def branch_members(heading: str) -> list:
    """The lines under one heading of the Docs tree, found by its words alone.

    The tree writes a heading with the count of calls it holds after it, so the
    same page opens whether the words come from a picked row or are said plain.
    """
    members = doc_branches.get(heading)
    if members is not None:
        return list(members)
    label = str(heading).rsplit(" (", 1)[0]
    for title, names in doc_branches.items():
        if str(title).rsplit(" (", 1)[0] == label:
            return list(names)
    return []


def show_doc(heading: str) -> None:
    """One page of the reference: a call, a guide, or the lines under a heading."""
    heading = one_line(heading)
    if not heading:
        return
    known = reference()
    if heading in known:
        one = known[heading]
        body = str(one.get("doc") or "no description")
        put("dochead", f"api.theme.{heading}")
        put("docpage", f"{one.get('args')}\n\n{wrap(body)}")
        return
    for title, text in GUIDES:
        if title == heading:
            put("dochead", title)
            put("docpage", text.strip("\n"))
            return
    members = branch_members(heading)
    put("dochead", heading)
    lines = []
    for name in members:
        if name in known:
            brief = str(known[name].get("doc") or "").strip().splitlines()
            lines.append(f"api.theme.{name}" + (f"\n    {brief[0]}" if brief else ""))
        else:
            # A guide is not an api call: it is listed by its own title.
            lines.append(f"  {name}")
    put("docpage", "\n\n".join(lines) or "nothing is under that heading yet")


def wrap(text: str) -> str:
    """A docstring kept as written, minus the indentation it carries in code."""
    out = []
    for line in str(text or "").splitlines():
        out.append(line.strip())
    return "\n".join(out).strip()


# --- the hooks the app calls ---

def focus_editor(_checked: bool = False) -> None:
    """The Tools menu entry: the tab, on the editor."""
    if api is None:
        return
    api.tabs.activate(TAB)
    show_page(1)


def refresh_all(_ignored: Any = None) -> None:
    """Every part of the tab that reads from the program: list, editor, docs."""
    if api is None:
        return
    refresh_themes()
    if draft is None:
        open_draft(in_force())
    show_draft()
    fill_docs()


def apply_schedule(config: dict | None = None) -> None:
    """Nothing on a clock: the tab keeps itself current when the look changes.

    A theme is not something that moves by itself, so the only refresh wanted
    is the one the ``theme_changed`` event asks for - and a timer that re-read
    the folder while a person is mid-edit would only get in their way. The hook
    is kept because the app calls it when this addon's settings are applied.
    """
    return None


def on_event(name: str, payload: dict) -> None:
    """The look changed elsewhere in the program: say so and re-read."""
    if str(name) != "theme_changed":
        return
    used = one_line((payload or {}).get("theme"))
    refresh_all()
    say(f"the window is drawn from {used or 'a theme'}")


#: Which handler each widget's interaction goes to, by the id it was built with.
COMMANDS = {
    "use": apply_picked, "edit": edit_picked, "dup": duplicate_picked,
    "rename": rename_picked, "del": delete_picked, "folder": open_folder,
    "create": create_from_template,
    "newdraft": blank_draft, "setcolor": set_colour, "wheel": pick_colour,
    "setknob": set_knob, "getsource": show_source, "putsource": use_source,
    "save": lambda: save_draft(False),
    "saveapply": lambda: save_draft(True), "discard": discard_draft,
}

#: The picks that are not buttons: a row of one of the three trees, or another
#: theme in the editor's combo.
PICKS = {"themes": "theme", "tokens": "token", "knobs": "knob", "docs": "doc"}


def pick_from(kind: str, payload: dict) -> None:
    """One row picked somewhere: open what it stands for."""
    text = one_line((payload or {}).get("text"))
    if kind == "theme":
        if not text and api is not None:
            rows = get("themes", "selected", [])
            text = one_line(rows[0]) if isinstance(rows, (list, tuple)) and rows else ""
        if text:
            show_info(text)
            say(f"{text} - {theme_blurb(text)[1] or 'no description given'}")
        return
    if not text:
        return
    if kind == "token":
        if any(str(one.get("name")) == text for group in
               (api.theme.groups() if api is not None else [])
               for one in (group.get("tokens") or [])):
            show_token(text)
        return
    if kind == "knob":
        if knob_kind(text):
            show_knob(text)
        return
    if kind == "doc":
        show_doc(text)


def theme_menu(payload: dict) -> None:
    """Right-click on a theme: what can be done to it, as a small menu.

    The row's name rides in the event, so the commands are about the theme
    under the pointer; :func:`picked_theme` hands it on to whoever is called
    next and then forgets it, so a later click on the list is not still
    answering to an older right-click.
    """
    global context_name
    if api is None:
        return
    name = one_line((payload or {}).get("text"))
    if not name:
        return
    chosen = api.ui.choose([text for _id, text, _tip in THEME_BUTTONS])
    wanted = next((one for one in THEME_BUTTONS if one[1] == chosen), None)
    if wanted is None:
        return
    context_name = name
    handler = COMMANDS.get(wanted[0])
    if callable(handler):
        handler()


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Something on the tab was used: run what its id and interaction mean.

    Handlers are looked up by name when the click lands, not fixed at import,
    so a test - or a hot patch - that swaps one of these functions on the
    module truly gets the click.
    """
    if str(tab) != TAB:
        return
    name = str(identifier)
    kind = str(event)
    if kind == "selected" and name in PICKS:
        pick_from(PICKS[name], payload or {})
        return
    if kind == "context" and name == "themes":
        theme_menu(payload or {})
        return
    if kind == "changed" and name == "tpl":
        note_template()
        return
    if kind == "changed" and name == "draft":
        chosen = one_line((payload or {}).get("text")) or one_line(
            get("draft", "value", ""))
        if chosen and chosen != draft_name:
            edit_picked_by_name(chosen)
        return
    if kind != "clicked":
        return
    handler = COMMANDS.get(name)
    if callable(handler):
        handler()


def edit_picked_by_name(name: str) -> None:
    """Open one theme in the editor, if what is in it already is not worth keeping.

    The editor's combo and the theme list both end up here, so the question
    about unsaved work is asked once, in the place that changes what is held.
    """
    if api is None:
        return
    if draft is not None and not same_draft():
        if not api.ui.question("Change what is in the editor",
                               f"{draft_name} has changes that are not saved. "
                               f"Open {name} and lose them?", False):
            put("draft", draft_name, "value")
            say("still editing " + str(draft_name))
            return
    show_page(1)
    open_draft(name)


def init(plugin_api: Any) -> None:
    """Build the tab from the theme folder as it stands."""
    global api
    api = plugin_api
    config = api.config.declare(
        FIELDS, DEFAULTS,
        title="Theme Manager - addons/_data/theme_manager/config.json")
    build_tab()
    put("applylive", bool(config_get(config, "save_applies", True)), "checked")
    api.ui.menu_item("Edit the themes", focus_editor, menu="Tools")
    api.events.on("theme_changed", lambda data: on_event("theme_changed", data))
    refresh_all()
    say(f"{len(api.theme.names())} themes, the window drawn from {in_force()}")
    return None


def start() -> None:
    """Nothing to do beyond what init did."""
    return None


def shutdown() -> None:
    """Forget the draft in hand and the api it was read through."""
    global api, draft, draft_name, picked_token, picked_knob
    global doc_branches, context_name
    draft = None
    draft_name = ""
    picked_token = ""
    picked_knob = ""
    doc_branches = {}
    context_name = ""
    api = None


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: re-read what it controls."""
    if api is None or str((payload or {}).get("addon") or "") != api.id:
        return
    config = values()
    put("applylive", bool(config_get(config, "save_applies", True)), "checked")
    show_tokens()
    apply_schedule(config)


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
