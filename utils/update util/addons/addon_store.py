#!/usr/bin/env python3
"""Berry addon store: pull addon sources from GitHub and build them here.

Adds an ``Addon store`` tab. The catalog of what is available is read from
``index.json`` in the ``addon.store`` folder of one or more GitHub repositories,
so the list of installable addons lives on GitHub and is never baked into the
program. Each catalog entry names a plain ``.py`` addon sitting beside that
index, and an optional list of asset files under ``addon.store/assets/<id>/``.
``index.json`` is generated from the store folder on build, so it carries every
addon's name, version, author and assets, with new and updated entries marked.

Where the addons come from is a list rather than one repository. The store starts
with this program's own repository and any number more can be added, changed,
moved to the front or removed from the tab's ``Sources`` button: a source names an
owner, a repository, a branch, tag or commit, and the store folder inside it. The
sources are read in order and their catalogs put together, so an addon published
by two of them is taken from the first and the rest of that catalog still shows
what its own repository has; a source that cannot be reached is reported and
skipped rather than losing the others. Which one an entry came from is kept with
it, and is what the download, the assets, the test and the modules an addon
imports are all fetched from, so an addon arrives with exactly what sat beside
the catalog entry naming it.

Installing one mirrors what the web installer does with its files: the addon's
``.py`` and any assets it ships are fetched over HTTPS into the addon's own
staging folder under ``addons/_data/addon_store``, then compiled into a program
of its own by PyInstaller - the assets packed into the executable's ``assets``
folder so ``api.paths.assets`` finds them - and dropped into ``addons/`` exactly
where the runtime looks, the same result the Addon Dev tab's Compile button
produces. The build is staged in a folder of the store's own beside the
downloads, under ``addons/_data/addon_store/build``, and both the downloads and
the build are thrown away as soon as the program is in ``addons/``: only the
installed file and the addon's own settings are left behind.

A freshly installed addon is written into the active profile's settings as
switched off, so nothing of it runs until it is turned on in Settings > Addons,
where its row appears as soon as the app has rescanned the folder.

A program only says what it is once it runs, and the app needs some of that
before then, so what the addon's source declared is written down as it is
installed: a ``<id>.json`` beside the program, naming it and listing the
transports ``api.device`` may hand it, and the addon's ``fields.json`` under
``addons/_data/<id>``, the copy of its settings form that Settings reads while
the addon is off. That is also what marks the addon as one the user installed -
listed under Installed addons, with its own settings like any other drop-in -
instead of among the addons that came inside the program.

Before the compile, when the store ships a ``test_<id>.py`` for the addon, it is
fetched too and run against the freshly downloaded ``.py``: a test that does not
pass stops the install there, so a broken addon is never built. An addon with no
test published is compiled as it is, and so is one whose test cannot start on
this machine - a run that never got past importing Qt, or the app's own modules,
says nothing about the addon, and only what the addon itself failed is held
against it.

Both the test and the program built from an addon import two of the app's own
modules, and a program that came out of PyInstaller keeps them inside itself,
where the Python doing the building cannot read them. So the store finds them on
disk - in the app's folder in a checkout, in the copy a build packs beside the
addons - and, when neither is there, fetches those two files from the same
repository and ref as the addon. Without them nothing could be built: a program
that cannot talk to the app is not an addon. A fetched pair is only what a build
needs, not what a test needs, so when a test stops on one of the app's other
modules the rest of them are fetched too and the test is run again.

Loading the catalog also checks what is already installed against it: the version
written down beside a program as it was installed is compared with the one the
catalog names now, and where the repository is ahead the row says *update
available*. Update then runs the same download, test and build as a fresh install
and puts the result where the old program was, replacing it - what the addon
saved for itself under ``addons/_data/<id>`` is never part of an install and is
left exactly as it is, and the switch in Settings > Addons is not touched, so an
addon that was on stays on.

Deleting one takes that compiled program (or folder) back out of ``addons/`` and
rescans. By default it takes the addon's own ``_data`` settings with it, since
those are what an install prepared; the switch on the store's Settings page
leaves them in place for every start, and the check box beside the source at the
top of the tab leaves them, or takes them, for this one. Either way the settings
of an addon the store did not install are never touched, and this store's own
folder - its sources and its token - is not up for deletion by itself.

Compiling needs a Python on this machine and PyInstaller within it. The
interpreter is looked for as ``python3``, then ``python``, then the Windows ``py``
launcher, and a name that turns out to be the Microsoft Store's stand-in rather
than a Python is passed over. When PyInstaller is missing the store installs it
itself with ``pip`` - for this user alone when the plain install is refused -
and the user is not asked to. The token is editable in
``addons/_data/addon_store/config.json``, and so are the sources, in that file's
``sources`` list - a list of records rather than the one value per line the
Settings form can hold, which is why they get their own button on the tab.

The tab is laid out like the app store on a phone: a filter line over a list of
what is on offer, each row wearing the addon's own picture, and a detail pane
beside it that fills in when a row is picked - the description, who wrote it,
which repository published it, which commit the file came out of, what it needs
on the device and how it reaches that device, with the job log underneath the
pair. Everything is built from a declarative spec and its buttons and rows report
back through ``on_widget``, so the same file works imported into the app and
compiled into a program of its own by the Addon Dev tab. It marks itself with
``assets/addon_storeicon.png`` when the program ships one, and with a drawn store
bag when it does not, the way the other bundled addons' tabs do.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Qt-free contract half: runs standalone and in tests.
from addon_common import (AddonError, ORIGIN_KEY, STORE_ORIGIN, addon_manifest_path,
                          addon_origin, config_get, connection_kind,
                          connection_words, default_icon_path, find_addon_picture,
                          jsonable,
                          merge_config, read_config_json, requires_list, write_config_json)

ADDON = {
    "name": "Berry addon store",
    "version": "1.3",
    "author": "fligma",
    "description": "Lists the addons published in the repository's addon.store "
                   "folder, downloads a chosen one into a staging folder and "
                   "compiles it for this machine with PyInstaller, then installs "
                   "or removes it from the addons folder. Installs PyInstaller "
                   "itself when it is not already present.",
    "requires": [],
    # GitHub over HTTPS and this machine's disk: it never reaches a device.
    "connection": "none",
    "api": 1,
}

TAB = "Addon store"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the job in progress, or nothing
entries: list[dict] = []    # the catalog read last time
shown: list[dict] = []      # the rows the list is painting right now
picked = ""                 # the id the detail pane is describing
updates: dict[str, dict] = {}   # id -> {installed, available}, what the catalog is ahead of
#: None while the session's check box has not been touched, and after that what
#: it says. The config file keeps the answer for every session; this holds a
#: one-off change made at the top of the tab without writing it anywhere.
session_delete_config: bool | None = None
#: An addon id to the picture of it this machine has, and the ids still waiting
#: for theirs. The pictures are only ever fetched once each and kept under
#: ``icons``, so a catalog with fifty addons in it costs fifty downloads once.
icon_paths: dict[str, str] = {}
icon_queue: list[str] = []

#: A module name PyInstaller can import: anything else becomes an underscore.
_SAFE = re.compile(r"[^A-Za-z0-9_]")

#: What an addon's launcher, and a published test, import from the app.
HARNESS_FILES = ("addon_common.py", "addon_client.py")

#: The app's other modules, by the name a test imports them under. A published
#: test reaches for the Qt half of them to build real widgets, and only a
#: checkout, or a build that packed its source, can answer for these.
_APP = ("addon_api", "berry_manager", "manager_addondev", "manager_addons",
        "manager_browser", "manager_config", "manager_const", "manager_files",
        "manager_gui", "manager_net", "manager_term", "manager_terminal",
        "manager_widgets")

#: A test that cannot import one of these got nowhere, and that is this
#: machine's doing rather than the addon's: the harness above, the app's own
#: modules beside it, and what those need in turn.
_MACHINE = ("addon_client", "addon_common", "PyQt6", "paramiko", "keyring",
            "secretstorage", "jeepney")

#: How a Python says an import failed, out of a traceback's last line.
_MISSING = re.compile(r"No module named ['\"]([^'\"]+)['\"]")

# --- what can be configured, and what it starts out as ---

#: The repository the store was written against, and the one it offers back when
#: a source is added by name or every source has been removed.
MAIN_SOURCE = {
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "store_path": "utils/update util/addon.store",
}

#: What one source holds: the four pieces of a GitHub address, nothing else.
SOURCE_KEYS = ("owner", "repo", "ref", "store_path")

#: The answer a source field starts out with when it has none of its own.
SOURCE_HINTS = {"owner": "", "repo": "", "ref": "main",
                "store_path": MAIN_SOURCE["store_path"]}

DEFAULTS = {
    "sources": [dict(MAIN_SOURCE)],
    "token": "",
    "delete_config": True,
}

FIELDS = [
    {"key": "token", "label": "GitHub token", "kind": "text",
     "hint": "Used for every source. Only needed for a private repository, or "
             "when anonymous requests are refused",
     "placeholder": "blank for a public repository"},
    {"key": "delete_config", "label": "Delete takes an addon's settings too",
     "kind": "bool",
     "hint": "On removes addons/_data/<id>/ with the program, so nothing the "
             "addon was asked is left behind. Off leaves it, and an addon "
             "installed again later finds its answers where it left them"},
]

GROUPS = [("How the store reaches GitHub", ["token"]),
          ("What Delete takes back", ["delete_config"])]

BUTTONS = (("refresh", "Refresh"), ("install", "Install"),
           ("update", "Update"), ("delete", "Delete"),
           ("sources", "Sources..."))

# --- small text helpers ---

def one_line(value: Any) -> str:
    """One clean line of text from whatever the form or file holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def clean_id(value: Any) -> str:
    """A catalog id turned into a name Python and PyInstaller can both use."""
    return _SAFE.sub("_", one_line(value))


def safe_segment(value: Any) -> str:
    """A repository name or path piece with the shell-junk and traversal gone."""
    text = one_line(value).replace("..", "")
    return re.sub(r"[^A-Za-z0-9._/ -]", "", text).strip(" /")


def clean_token(value: Any) -> str:
    """A token stripped of the spaces a copy-paste tends to add."""
    return one_line(value).replace(" ", "")


# --- the sources: which repositories the catalog is read from ---

def normalize_source(raw: Any) -> dict:
    """One source, cleaned: what a person typed, turned into the parts of an address.

    A branch that was left blank is ``main``, and a store folder left blank is
    this program's own, since that is the layout a published addon store uses.
    """
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, str] = {}
    for key in SOURCE_KEYS:
        text = (one_line(data.get(key)) if key != "store_path" else
                one_line(data.get(key)).strip("/"))
        if not text:
            text = str(SOURCE_HINTS.get(key) or "")
        out[key] = (safe_segment(text) if key in ("owner", "repo", "store_path")
                    else text.strip("/"))
    return out


def source_label(source: dict) -> str:
    """One source as a person names it: ``owner/repo @ ref``."""
    data = source if isinstance(source, dict) else {}
    return (f"{one_line(data.get('owner'))}/{one_line(data.get('repo'))}"
            f" @ {one_line(data.get('ref') or 'main')}")


def source_values(source: dict, token: str = "") -> dict:
    """One source as the flat values dict every fetch already knows how to read.

    The address builders take ``owner``/``repo``/``ref``/``store_path``/``token``,
    so a source shaped like the old single-source config means none of them had
    to learn about a list.
    """
    data = source if isinstance(source, dict) else {}
    return {"owner": one_line(data.get("owner")),
            "repo": one_line(data.get("repo")),
            "ref": one_line(data.get("ref")) or "main",
            "store_path": one_line(data.get("store_path")).strip("/")
                         or MAIN_SOURCE["store_path"],
            "token": clean_token(token)}


def _source_key(source: dict) -> tuple:
    return tuple(str(source.get(key) or "") for key in SOURCE_KEYS)


def read_sources(values: dict) -> list[dict]:
    """Every source the config names, in the order they are read.

    A config from before the list existed named one repository with four plain
    keys, and that is migrated rather than lost. An empty ``sources`` list is
    what the user asked for - they removed every source - and stays empty; only
    a config that names no list at all falls back to this program's own.
    """
    data = values if isinstance(values, dict) else {}
    raw = config_get(data, "sources", None)
    if not isinstance(raw, list):
        named = [normalize_source({key: config_get(data, key, "")
                                   for key in SOURCE_KEYS})]
        raw = named if any(one_line(config_get(data, key, ""))
                           for key in ("owner", "repo")) else [dict(MAIN_SOURCE)]
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in raw:
        source = normalize_source(item)
        if not source["owner"] or not source["repo"]:
            continue                      # a half-typed source is not a source
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return out


def current_sources(values: dict | None = None) -> list[dict]:
    """What the config names now, read from the file when nothing is to hand."""
    if values is None:
        return read_sources(source_config())
    return read_sources(values)


def source_config() -> dict:
    """The values the sources are read out of: the file, with the defaults under it.

    ``sources`` and the four flat keys a single-source config used cannot both
    answer for where the addons come from, and only the file knows which one the
    user actually wrote - so the shipped list of sources is held back from under
    a repository that is still sitting in the file under its old keys, and the
    main source answers only when the file names neither.
    """
    stored = api.config.stored() if api is not None else {}
    if isinstance(stored, dict) and isinstance(stored.get("sources"), list):
        return merge_config(DEFAULTS, stored)
    under = {key: value for key, value in DEFAULTS.items() if key != "sources"}
    return merge_config(under, stored if isinstance(stored, dict) else {})


def write_sources(sources: list[dict]) -> list[dict]:
    """Put the list in the config, and clear the old flat keys out of the file.

    ``sources`` replaces the whole list, so it is written as the cleaned one;
    the four keys a single-source config used go back to being what the list
    says, so nothing reads a repository the tab no longer shows.
    """
    kept = read_sources({"sources": list(sources or [])})
    if api is None:
        return kept
    api.config.update({"sources": jsonable(kept)})
    for key in SOURCE_KEYS:
        api.config.pop(key, None)
    return kept


# --- versions ---

def _version_parts(value: Any) -> tuple:
    """The digits out of a dotted version, as a tuple that can be compared."""
    return tuple(int(part) for part in re.findall(r"\d+", str(value or "")))


def version_newer(remote: Any, local: Any) -> bool:
    """Whether the catalog's version is ahead of the one installed here.

    A dotted number is read as digits, so 1.10 beats 1.9 rather than losing to
    it as text would. An addon with nothing recorded for what is installed is
    taken for behind whatever the catalog names, since there is no way to prove
    it is current; a version with no digits at all is only compared as text.
    """
    wanted, have = _version_parts(remote), _version_parts(local)
    if wanted and have:
        length = max(len(wanted), len(have))
        return (wanted + (0,) * (length - len(wanted))
                > have + (0,) * (length - len(have)))
    clean_remote, clean_local = one_line(remote), one_line(local)
    return bool(clean_remote) and clean_remote != clean_local


# --- the tab ---

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QTreeWidget, QPlainTextEdit { font-family: Consolas, monospace; }
QPushButton { padding: 7px 14px; }
"""

BANNER_STYLE = ("QLabel { color: #e8e8e8; font-size: 13pt; font-weight: 600;"
                " padding: 4px 2px 2px 2px; }")

_STATUS_STYLE = ("QLabel { color: #9a9a9a; padding: 2px 6px;"
                 " border: 1px solid #3c3c3c; border-radius: 9px; }")

#: The status line's two colours: the news it carries is the only thing that
#: changes between them, so ``say`` and the spec cannot disagree about padding.
_STATUS_OK = "color: #23d18b; padding: 2px 4px;"

_STATUS_BAD = "color: #ffb020; padding: 2px 4px;"

_STATUS_TEXT = _STATUS_OK

#: The detail pane's own type, down the side of the picked addon's picture:
#: its name, who published it, and how it stands against what is installed.
_DETAIL_TITLE = ("QLabel { color: #f2f2f2; font-size: 15pt; font-weight: 700;"
                 " padding: 0px; }")

_DETAIL_SUB = "QLabel { color: #9a9a9a; font-size: 9pt; padding: 0px; }"

_DETAIL_STATE = ("QLabel { color: #23d18b; font-size: 10pt; font-weight: 600;"
                 " padding: 2px 0px 0px 0px; }")

#: The small red line that starts each block of the pane off.
_DETAIL_HEAD = ("QLabel { color: #c51a4a; font-size: 9pt; font-weight: 700;"
                " padding: 8px 0px 2px 0px; }")

#: The description, which is prose: it is set in the interface's own face rather
#: than the monospace the log and the facts keep.
_ABOUT_STYLE = ("QPlainTextEdit { color: #d6d6d6; background-color: #1c1c1c;"
                " border: 1px solid #303030; border-radius: 6px; padding: 6px;"
                " font-family: 'Segoe UI', sans-serif; }")

#: The one word the filter line is answered with, and the count beside it.
_FILTER_LABEL = "QLabel { color: #9a9a9a; font-weight: 600; padding: 0px 4px 0px 2px; }"

#: The list's columns. A row is read by its picture and its name first, so the
#: id - what every command takes - is not crowding the front of it; the detail
#: pane says the id, and picking the row remembers it.
LIST_COLUMNS = ("In the store", "Version", "Where it stands")

#: The detail pane's facts are a label and what it answers, two columns wide.
FACT_COLUMNS = ("", "")

TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M4 7h16l-1.4 12.1a2 2 0 0 1-2 1.9H7.4a2 2 0 0 1-2-1.9Z" '
    'fill="#3a3a3a" stroke="#c51a4a" stroke-width="1.3"/>'
    '<path d="M8.5 7V5.5a3.5 3.5 0 0 1 7 0V7" fill="none" stroke="#c51a4a" '
    'stroke-width="1.3"/>'
    '<path d="M12 10v5.5M9.8 13.6 12 15.8l2.2-2.2" fill="none" stroke="#23d18b" '
    'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>'
    '</svg>'
)

#: The picture the tab wears when the assets folder carries one: this addon's id
#: and then ``icon.png``, the name every addon's picture is looked up under, so
#: putting the file in is enough and there is no older name to keep trying.
TAB_ICON_FILE = "addon_storeicon.png"

#: This addon's own id, which is what its picture is named from.
OWN_ID = "addon_store"


def tab_icon() -> str:
    """The shipped picture if the assets folder has one, else the drawn mark.

    ``addon_storeicon.png`` is the one name the lookup is made under, so the file
    in ``assets`` decides what the tab wears and this file names nothing it has to
    keep in step with.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    return find_addon_picture(paths, OWN_ID) or TAB_ICON


def tab_spec() -> dict:
    """The whole tab as an app store has it: a list of what is on offer and, beside
    it, the one addon currently picked, with the job's log under both.

    The status line sits in the header between the title and the repository chip,
    so nothing of its own steals height from the list; it takes the width the two
    leave over and wraps inside that, so a long report stays readable and the chip
    stays put. Under the header a filter line narrows the list as it is typed.

    Two splitters do the rest, and every part of the tab can therefore be given
    room by dragging: the outer one is the panes over the log, the inner one is
    the list over its detail pane. The detail pane starts with the larger share,
    since an addon's description is the thing a person came here to read.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry addon store", "style": BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": _STATUS_TEXT},
            {"kind": "label", "id": "source", "text": "",
             "tooltip": "The repository, or the first of them, the catalog is "
                        "read from", "style": _STATUS_STYLE},
            {"kind": "check", "id": "delcfg", "text": "delete settings",
             "checked": True,
             "tooltip": "Whether Delete takes an addon's own settings under "
                        "addons/_data/<id>/ with its program. Unticked here it "
                        "holds for this session only; the switch in the store's "
                        "Settings page is the answer kept for every one",
             "on": ["toggled"]},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "text": "Find", "style": _FILTER_LABEL},
            {"kind": "line", "id": "filter", "expand": True,
             "placeholder": "Filter the list by name, author, id or what it needs",
             "tooltip": "Nothing is looked for until you type; the catalog on "
                        "GitHub is not touched",
             "on": ["changed", "returned"]},
            {"kind": "label", "id": "count", "text": "", "style": _STATUS_STYLE,
             "tooltip": "How many of the catalog's addons are showing"},
        ]},
        {"kind": "splitter", "vertical": True, "sizes": [3, 1], "children": [
            {"kind": "splitter", "sizes": [2, 3], "children": [
                {"kind": "table", "id": "list", "columns": list(LIST_COLUMNS),
                 "stretch": [0], "col_widths": [0, 70, 130], "font_size": 11,
                 "on": ["selected", "context"],
                 "tooltip": "Every addon the configured sources list; click one "
                            "to read about it, then use the buttons below"},
                {"kind": "column", "children": [
                    {"kind": "row", "children": [
                        {"kind": "image", "id": "dicon", "size": 64},
                        {"kind": "column", "children": [
                            {"kind": "label", "id": "dtitle",
                             "text": "Pick an addon", "style": _DETAIL_TITLE,
                             "word_wrap": True},
                            {"kind": "label", "id": "dby", "text": "",
                             "style": _DETAIL_SUB, "word_wrap": True},
                            {"kind": "label", "id": "dstate", "text": "",
                             "style": _DETAIL_STATE, "word_wrap": True},
                        ]},
                    ]},
                    {"kind": "label", "text": "What it is", "style": _DETAIL_HEAD},
                    {"kind": "multi", "id": "dabout", "readonly": True,
                     "expand": True, "style": _ABOUT_STYLE,
                     "placeholder": "Nothing picked yet - choose an addon on the "
                                    "left to read what it does"},
                    {"kind": "label", "text": "What the listing says",
                     "style": _DETAIL_HEAD},
                    {"kind": "table", "id": "dfacts", "columns": list(FACT_COLUMNS),
                     "stretch": [1], "col_widths": [120, 0], "font_size": 10,
                     "tooltip": "The catalog's own words about this addon, and "
                                "where on GitHub it came from"},
                ]},
            ]},
            {"kind": "multi", "id": "log", "readonly": True,
             "placeholder": "Every step of the last install, as it happened",
             "tooltip": "The same lines go into the program's own log"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": identifier, "text": text,
             "on": ["clicked"]} for identifier, text in BUTTONS],
        },
    ]}


def build_tab() -> None:
    """The tab as a spec; nothing here holds a widget.

    Pinned and icon-only on purpose: the store is furniture, like Settings, so it
    holds at the right edge of the bar and is recognised by its mark instead of
    taking a place in the row of tabs the user arranged.
    """
    api.tabs.add_tab(TAB, spec=tab_spec(), style=TAB_STYLE,
                     pinned=True, icon_only=True)
    api.tabs.set_icon(TAB, tab_icon())


def say(text: str, bad: bool = False) -> None:
    """One line on the tab's status, and nothing once this copy has been reloaded."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style", _STATUS_BAD if bad else _STATUS_OK)
    except AddonError:
        pass


def note(text: str) -> None:
    """One line into the tab's own log and the program's log, the same words."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "log", "append", str(text))
    except AddonError:
        pass
    api.log(text)


def show_sources(sources: list[dict]) -> None:
    """The header chip: which repositories the catalog is read from, in order.

    One source is named in full; several are named by the first and counted, so
    the chip stays a chip and the status line keeps the width it needs.
    """
    if not sources:
        text = "no source configured"
    elif len(sources) == 1:
        text = source_label(sources[0])
    else:
        text = f"{source_label(sources[0])}  +{len(sources) - 1}"
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "source", "text", text)
    except AddonError:
        pass


def _buttons(usable: bool) -> None:
    if api is None:
        return
    for identifier, _text in BUTTONS:
        try:
            api.tabs.set_widget(TAB, identifier, "enabled", bool(usable))
        except AddonError:
            pass


# --- what Delete takes back ---

def delete_config_wanted() -> bool:
    """Whether an addon's settings folder is to go with its program.

    The check box at the top of the tab answers first once it has been touched -
    a one-off clear should not have to be written to the config and undone
    afterwards - and until then the value the config file carries does, which is
    the shipped default of taking them until the Settings page says otherwise.
    """
    if session_delete_config is not None:
        return bool(session_delete_config)
    if api is None:
        return bool(DEFAULTS["delete_config"])
    try:
        return bool(config_get(api.config.all(), "delete_config",
                               DEFAULTS["delete_config"]))
    except AddonError:
        return bool(DEFAULTS["delete_config"])


def show_delete_toggle() -> None:
    """Put the header's check box in step with what Delete is going to do."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "delcfg", "checked", delete_config_wanted())
    except AddonError:
        pass


def set_delete_config_session(payload: dict) -> None:
    """The header's check box: change what Delete takes back, for this session.

    Nothing is written to the config, so the store's Settings page keeps saying
    the same thing it did, and the next start is back under its answer; the log
    records which of the two the tick was moved to, since a delete that follows
    reads differently from one that would have happened on its own.
    """
    global session_delete_config
    session_delete_config = bool((payload or {}).get("checked"))
    if session_delete_config:
        say("Delete will take an addon's settings with it, this session")
        note("the header's check box now has Delete take addons/_data/<id> too, "
             "for this session only")
    else:
        say("Delete will leave an addon's settings alone, this session")
        note("the header's check box now has Delete leave addons/_data/<id> "
             "where it is, for this session only")


def _data_removable(id_value: str) -> Path | None:
    """The settings folder Delete may take, or nothing when it is to be left.

    Three things have to hold: the toggle above says the settings go with the
    program, the install is one a store made - a folder an addon wrote for
    itself, or a program built by the Addon Dev tab, is not the store's to
    erase - and it is not this store's own folder, whose settings are the
    sources, taken off by a delete this very copy is running.
    """
    if not delete_config_wanted():
        return None
    ident = clean_id(id_value)
    if not ident or ident == OWN_ID:
        return None
    if addon_origin(addons_dir(), id_value) != STORE_ORIGIN:
        return None
    keeps = addon_data_folder(id_value)
    return keeps if keeps.exists() else None


# --- network: standard library only ---

class Fetcher:
    """A few HTTPS connections, kept open and reused for one job."""

    def __init__(self, token: str = "") -> None:
        self.token = str(token or "")
        self.connections: dict[str, http.client.HTTPSConnection] = {}

    def _connection(self, host: str) -> http.client.HTTPSConnection:
        connection = self.connections.get(host)
        if connection is None:
            connection = http.client.HTTPSConnection(
                host, timeout=25, context=ssl.create_default_context())
            self.connections[host] = connection
        return connection

    def get(self, host: str, path: str,
            accept: str = "application/octet-stream") -> tuple[int, bytes]:
        """``(status, body)`` for one GET, retried once on a dropped connection."""
        status, body = 0, b""
        for attempt in (0, 1):
            headers = {"User-Agent": "berry-manager", "Accept": accept}
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            connection = self._connection(host)
            try:
                connection.request("GET", path, headers=headers)
                answer = connection.getresponse()
                body = answer.read()
                status = int(answer.status)
                if status < 500:
                    return status, body
            except (http.client.HTTPException, OSError):
                self.close(host)
                if attempt:
                    raise
                time.sleep(0.4)
        return status, body

    def close(self, host: str = "") -> None:
        for name in ([host] if host else list(self.connections)):
            connection = self.connections.pop(name, None)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass


_fetchers: dict[str, Fetcher] = {}


def _http(values: dict) -> Fetcher:
    key = clean_token(config_get(values, "token", ""))
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = Fetcher(token=key)
        _fetchers[key] = fetcher
    return fetcher


def _drop_http() -> None:
    for fetcher in _fetchers.values():
        fetcher.close()
    _fetchers.clear()


def app_folder(values: dict) -> str:
    """The folder the app's own modules are in: the store folder's parent.

    ``store_path`` points at the folder of addons; the files an addon imports
    sit one level above it, and a store folder at the repository's own root
    leaves nothing above it to name.
    """
    folder = one_line(config_get(values, "store_path",
                                 MAIN_SOURCE["store_path"])).strip("/")
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def raw_address(values: dict, filename: str, folder: str | None = None) -> str:
    """The raw.githubusercontent path for one file, in the store folder or another.

    ``folder`` stands in for the configured store folder when the file is not in
    it, which is how the two modules an addon's test and its program import are
    reached above the store.
    """
    owner = quote(safe_segment(config_get(values, "owner", MAIN_SOURCE["owner"])))
    repo = quote(safe_segment(config_get(values, "repo", MAIN_SOURCE["repo"])))
    ref = quote(one_line(config_get(values, "ref", "main")).strip("/"), safe="-./_")
    where = (one_line(folder) if folder is not None else
             config_get(values, "store_path", MAIN_SOURCE["store_path"]))
    whole = "/".join(part for part in (safe_segment(where).strip("/"),
                                       safe_segment(filename)) if part)
    return f"/{owner}/{repo}/{ref}/{quote(whole)}"


def _refused(status: int, path: str) -> str:
    if status == 404:
        return (f"GitHub has no such file at {path}: check the owner, repository, "
                "branch and store folder in the settings")
    if status == 403:
        return (f"GitHub refused the request for {path} (403). Anonymous requests "
                "are limited, so add a token or try again later")
    return f"GitHub answered {status} for {path}"


# --- reading the catalog ---

def parse_catalog(body: Any) -> list[dict]:
    """index.json as a tidy list of entries; a bare list is accepted too.

    What an addon needs on the device comes over in two keys the generator writes
    from the addon's own manifest: ``requires`` the transports it asks for, and
    ``connection`` how it reaches the device at all. Both are read through the
    same helpers the app uses, so a listing and a Settings row say the same thing
    about the same addon.
    """
    if isinstance(body, dict):
        rows = body.get("addons") or body.get("entries") or []
    elif isinstance(body, list):
        rows = body
    else:
        rows = []
    out: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        ident = clean_id(raw.get("id") or raw.get("name") or "")
        if not ident:
            continue
        filename = one_line(raw.get("filename") or f"{ident}.py")
        listed = raw.get("assets") or []
        assets = [one_line(name) for name in listed if one_line(name)] \
            if isinstance(listed, list) else []
        out.append({
            "id": ident,
            "name": one_line(raw.get("name") or ident),
            "version": one_line(raw.get("version") or ""),
            "author": one_line(raw.get("author") or ""),
            "description": one_line(raw.get("description") or ""),
            "filename": filename,
            "assets": assets,
            "icon": one_line(raw.get("icon") or ""),
            "test": one_line(raw.get("test") or ""),
            "status": one_line(raw.get("status") or ""),
            "requires": requires_list(raw),
            "connection": connection_kind(raw),
        })
    return out


def load_catalog(values: dict) -> list[dict]:
    """Fetch and read ``index.json`` from the store folder."""
    path = raw_address(values, "index.json")
    try:
        status, body = _http(values).get(
            "raw.githubusercontent.com", path, accept="application/json")
    except (http.client.HTTPException, OSError) as error:
        raise AddonError(f"the catalog could not be reached: {error}")
    if status != 200:
        raise AddonError(_refused(status, path))
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise AddonError(f"index.json was not the JSON expected: {error}")
    return parse_catalog(data)


# --- what is installed, and where downloads land ---

def addons_dir() -> Path:
    return Path(api.paths.addons)


def installed(id_value: str) -> bool:
    """Whether an addon id already sits in the addons folder in any shape."""
    folder = addons_dir()
    name = clean_id(id_value)
    if not name:
        return False
    for candidate in (folder / f"{name}.exe", folder / f"{name}.bin", folder / name):
        if candidate.exists():
            return True
    return False


def installed_version(id_value: str) -> str:
    """The version written down beside an install when it was put there."""
    name = clean_id(id_value)
    if not name:
        return ""
    path = addon_manifest_path(addons_dir(), name)
    if path is None:
        return ""
    return one_line(read_config_json(path).get("version"))


def staging() -> Path:
    """The addon's own folder under ``api.paths.data`` for what it downloads."""
    folder = Path(api.paths.data) / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def work_dir() -> Path:
    """The addon's own folder under ``api.paths.data`` for the build's work.

    PyInstaller writes a spec, a ``build`` folder and a ``dist`` folder for every
    program it makes. All of that belongs to the store, not to the Addon Dev tab
    beside it, so it is staged here, under ``addons/_data/addon_store``.
    """
    folder = Path(api.paths.data) / "build"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def harness_folder(values: dict) -> Path | None:
    """A folder holding the modules an addon imports, or nothing.

    ``addon_common`` and ``addon_client`` are what the launcher written for a
    compiled addon, and every published test, import in order to reach the app.
    In a source checkout they lie in the app's own folder, and a build made by
    the current workflow carries a copy inside it. A program built before that
    keeps them only in its archive, where the Python doing the building cannot
    read them, so for that one case the two files are fetched from the same
    repository and ref as the addon, into the staging folder they are cleared
    from afterwards.
    """
    try:
        ready = _dev().harness_dir()
    except Exception as error:                 # a dev module that will not import
        note(f"the app's own modules could not be looked for: {error}")
        ready = None
    if ready is not None:
        return Path(ready)
    folder = staging() / "harness"
    for name in HARNESS_FILES:
        why = _fetch_file(values, name, folder / name, app_folder(values))
        if why:
            note(f"the modules an addon imports could not be fetched "
                 f"({name}): {why}")
            return None
    return folder


def _harness_was_fetched() -> bool:
    """Whether the harness in hand came out of the download folder.

    That is the one case where it holds only the two files an addon needs for a
    build, because they were fetched: a harness found on disk is the app's own
    folder and already holds everything a test could import.
    """
    if job is None or job.get("harness") is None:
        return False
    try:
        return Path(str(job["harness"])).is_relative_to(staging())
    except (OSError, ValueError):
        return False


def _widen_harness(values: dict) -> bool:
    """Fetch the app's other modules into a fetched harness; whether any arrived.

    A published test imports the app's Qt half to build real widgets, and the two
    files an addon needs to talk to the app are only its Qt-free half. What the
    repository does not have is said and left out: a harness of one file more is
    still worth running the test over.
    """
    if job is None or job.get("harness") is None:
        return False
    folder = Path(str(job["harness"]))
    got = False
    for name in _APP:
        target = folder / f"{name}.py"
        if target.is_file():
            continue
        if _fetch_file(values, target.name, target, app_folder(values)):
            note(f"{target.name} was not there to fetch")
        else:
            got = True
    return got


def _reset_staging() -> None:
    for child in staging().iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def _tidy(id_value: str) -> None:
    """Throw the downloads and the build away once the program is in ``addons``.

    The source, its assets, its test and everything PyInstaller made on the way
    were only a means to the one file that got installed, so they are cleared;
    what the addon itself keeps in its ``_data`` folder is left alone.
    """
    try:
        shutil.rmtree(work_dir() / id_value, ignore_errors=True)
    except OSError as error:
        note(f"the build folder could not be cleared: {error}")
    _reset_staging()


# --- the pictures an addon is shown with ---

#: What a catalog's ``icon`` may name, and where a downloaded picture is kept. A
#: picture is fetched once and read off disk afterwards, so a store of forty
#: addons costs forty downloads the first time and nothing at all after it.
_PICTURES = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")

#: How many ticks a picture waits between itself and the next one. Slow enough
#: that a build starting in between gets the connection back in front of it.
_ICON_GAP = 0.3


def icons_dir() -> Path:
    """``addons/_data/addon_store/icons``, made when asked for."""
    folder = Path(api.paths.data) / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cached_icon(id_value: str) -> str:
    """The picture of one addon this machine already has, whatever name it came under.

    The catalog's own name is what is looked for first, since that is the file it
    points at; anything else already downloaded under this id's name is just as
    good, so a store that renamed its picture is not left showing the default.
    """
    name = clean_id(id_value)
    if not name:
        return ""
    folder = icons_dir()
    target = None
    for candidate in sorted(folder.glob(f"{name}.*")):
        if candidate.suffix.lower() in _PICTURES and candidate.is_file():
            target = candidate
            break
    return str(target) if target is not None else ""


def fallback_icon() -> str:
    """The picture an addon with none of its own is shown with.

    ``assets/storedefaultaddon.png`` beside the program, looked for in the
    program's own assets folder as well: a store compiled into a program of its
    own carries only its own pictures, and the default is the app's.
    """
    tries: list[Any] = []
    try:
        tries.append(api.paths.assets)
        tries.append(Path(api.paths.base) / "assets")
    except (AddonError, AttributeError, TypeError):
        pass
    for folder in tries:
        found = default_icon_path(folder)
        if found:
            return found
    return ""


def icon_of(entry: dict) -> str:
    """What one catalog row is drawn with: its own picture, or the default."""
    id_value = one_line(entry.get("id"))
    return (icon_paths.get(id_value) or cached_icon(id_value)
            or fallback_icon())


def _fetch_quiet(values: dict, remote: str, target: Path) -> bool:
    """One file straight to disk, without the log line a download usually makes.

    A picture nobody asked for is not worth a page of the job log, and one that
    will not arrive is simply a store that shows the default icon.
    """
    path = raw_address(values, remote)
    try:
        status, body = _http(values).get("raw.githubusercontent.com", path)
    except (http.client.HTTPException, OSError):
        return False
    if status != 200 or not body:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError:
        return False
    return True


def queue_icons() -> None:
    """Every listed addon whose picture this machine lacks, in the order it shows.

    Only addons the catalog names a picture for are asked about, so an addon
    published without one costs nothing and is drawn with the default.
    """
    del icon_queue[:]
    for entry in entries:
        id_value = one_line(entry.get("id"))
        if not id_value or not one_line(entry.get("icon")):
            continue
        if not cached_icon(id_value):
            icon_paths.pop(id_value, None)
            icon_queue.append(id_value)
    if icon_queue:
        api.schedule.after(_ICON_GAP, _pump_icons)


def _pump_icons() -> None:
    """Fetch one waiting picture, then come back for the next on a later tick.

    One per tick, because these are the app's own downloads and a store with a
    slow connection would otherwise sit filling a row of empty squares while it
    caught up. A picture that will not come is left for the default.
    """
    if api is None or not icon_queue:
        return
    if working:                       # a build in hand goes first
        api.schedule.after(1.0, _pump_icons)
        return
    id_value = str(icon_queue.pop(0))
    entry = entry_for(id_value)
    if entry is not None:
        name = one_line(entry.get("icon"))
        source = entry.get("src")
        if name and isinstance(source, dict) and not cached_icon(id_value):
            suffix = Path(name).suffix.lower() or ".png"
            target = icons_dir() / f"{clean_id(id_value)}{suffix}"
            values = source_values(source, clean_token(
                config_get(source_config(), "token", "")))
            if _fetch_quiet(values, f"assets/{id_value}/{name}", target):
                icon_paths[id_value] = str(target)
                show_entries()
                if picked == id_value:
                    show_detail(entry)
    if icon_queue:
        api.schedule.after(_ICON_GAP, _pump_icons)


# --- which commit a published addon came out of ---

#: GitHub's own address for questions about a repository rather than its files.
#: The catalog is regenerated whenever the store folder changes and CI commits
#: the result, so nothing baked into it could honestly name a commit - the store
#: asks for the last one that touched the addon's own file, once per addon per
#: load, and only when somebody opens it.
_API_HOST = "api.github.com"


def commit_address(values: dict, filename: str) -> str:
    """The commits query for one file, held to the ref the source names."""
    owner = quote(safe_segment(config_get(values, "owner", MAIN_SOURCE["owner"])))
    repo = quote(safe_segment(config_get(values, "repo", MAIN_SOURCE["repo"])))
    ref = quote(one_line(config_get(values, "ref")) or "main", safe="-./_")
    where = "/".join(part for part in (
        safe_segment(config_get(values, "store_path",
                                MAIN_SOURCE["store_path"])).strip("/"),
        safe_segment(filename)) if part)
    return (f"/repos/{owner}/{repo}/commits?sha={ref}"
            f"&path={quote(where, safe='/-._')}&per_page=1")


def _commit_words(record: dict) -> dict:
    """One commit as the few parts a listing shows: what, who, when."""
    data = record if isinstance(record, dict) else {}
    inner = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    lines = [line.strip() for line in str(inner.get("message") or "").splitlines()
             if line.strip()]
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    writer = inner.get("author") if isinstance(inner.get("author"), dict) else {}
    return {"sha": one_line(data.get("sha")),
            "title": lines[0] if lines else "",
            "body": "\n".join(lines[1:]),
            "date": str(writer.get("date") or "")[:10],
            "who": one_line(author.get("login") or writer.get("name"))}


def _ask_commit(entry: dict) -> None:
    """Queue the lookup of one addon's commit; the answer comes on a later tick.

    Asking on the spot would make opening a row wait on GitHub for as long as the
    connection cares to take, so it is put off a moment. An addon whose commit is
    already in hand, or already being asked about, is never asked twice.
    """
    if api is None or entry.get("asking") or isinstance(entry.get("commit"), dict):
        return
    if not isinstance(entry.get("src"), dict):
        return
    entry["asking"] = True
    try:
        api.schedule.after(0.05, lambda: _read_commit(entry))
    except (AddonError, AttributeError, TypeError):
        entry["asking"] = False


def _read_commit(entry: dict) -> None:
    """Fill in the commit one addon's file last changed in; quiet when it cannot.

    Rate limits, a private repository with no token and an unreachable network
    all leave the row without a commit line rather than a wall of text, because
    provenance is a nicety that the store works perfectly well without.
    """
    if api is None:
        return
    source = entry.get("src")
    if not isinstance(source, dict):
        return
    entry["asking"] = True
    values = source_values(source, clean_token(
        config_get(source_config(), "token", "")))
    path = commit_address(values, one_line(entry.get("filename")))
    try:
        status, body = _http(values).get(_API_HOST, path, accept="application/json")
    except (http.client.HTTPException, OSError) as error:
        note(f"{entry['id']}: its commit could not be read ({error})")
        return
    if status != 200:
        return
    try:
        records = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return
    first = records[0] if isinstance(records, list) and records else None
    if isinstance(first, dict):
        entry["commit"] = _commit_words(first)
        if picked == entry.get("id"):
            show_detail(entry)


# --- the table ---

def check_updates() -> dict[str, dict]:
    """Installed addons whose version in the catalog is ahead of what is here."""
    found: dict[str, dict] = {}
    for entry in entries:
        if not installed(entry["id"]):
            continue
        have = installed_version(entry["id"])
        if version_newer(entry.get("version"), have):
            found[entry["id"]] = {"installed": have,
                                  "available": one_line(entry.get("version"))}
    return found


#: What the generator leaves in the catalog to point at a row nobody has taken.
_MARKS = {"new": "  \u25cf new", "updated": "  \u21bb updated"}

#: The filter line as it stands, and the rows it leaves showing.
filter_text = ""


def state_of(entry: dict) -> tuple[str, str, str]:
    """How one addon stands here: the short words for its row, the long words for
    the pane beside it, and the colour both are drawn in.

    The version counted as behind is the one written down beside the program when
    it was installed, so a row can say what an update would replace rather than
    only that something is due.
    """
    id_value = one_line(entry.get("id"))
    pending = updates.get(id_value) or {}
    done = installed(id_value)
    have = (one_line(pending.get("installed"))
            or (installed_version(id_value) if done else ""))
    if pending:
        return ("update available",
                f"Update available - version {have or 'unknown'} here, "
                f"{one_line(pending.get('available')) or '?'} in the store",
                "#ffb020")
    if done:
        return ("installed", f"Installed, version {have or 'unknown'}", "#23d18b")
    status = one_line(entry.get("status"))
    if status == "new":
        return ("new", "New in the store, never installed here", "#ffb020")
    if status == "updated":
        return ("updated", "Updated in the store since it was last built", "#ffb020")
    return ("not installed", "Not installed on this machine", "#9a9a9a")


def matches(entry: dict, needle: str) -> bool:
    """Whether one catalog row is what the filter line is asking for.

    Every word a person might type at a store is searched - the name, the id, who
    wrote it, what the listing says it does, how it reaches a device and which
    repository published it - and none of it reaches GitHub to do it.
    """
    if not needle:
        return True
    for value in (entry.get("name"), entry.get("id"), entry.get("author"),
                  entry.get("description"), entry.get("source"),
                  connection_words(entry), " ".join(entry.get("requires") or [])):
        if needle in one_line(value).lower():
            return True
    return False


def show_entries() -> None:
    """The catalog as rows: each addon's own picture and name, its version, and
    how it stands against what is installed here."""
    global shown
    if api is None:
        return
    needle = filter_text.strip().lower()
    shown = [entry for entry in entries if matches(entry, needle)]
    rows = []
    for entry in shown:
        state, _words, tint = state_of(entry)
        name = one_line(entry.get("name")) or one_line(entry.get("id"))
        row = {"cells": [name + _MARKS.get(one_line(entry.get("status")), ""),
                         one_line(entry.get("version")), state],
               "color": tint}
        picture = icon_of(entry)
        if picture:
            row["icon"] = picture
        rows.append(row)
    total = len(entries)
    try:
        api.tabs.set_widget(TAB, "list", "rows", rows)
        api.tabs.set_widget(TAB, "count", "text",
                            f"{len(shown)} of {total}" if needle
                            else f"{total} listed")
    except AddonError:
        pass


def apply_filter(needle: Any = "") -> None:
    """Repaint the list with only the rows the filter fits; the catalog is kept."""
    global filter_text
    filter_text = one_line(needle)
    show_entries()


def find_entry(text: str) -> dict | None:
    """The row a person means, whether they named it by id or by the name the list shows.

    The first column carries a name with any mark beside it, while every command
    takes an id, so both are tried - the id first, then an exact caption, then a
    caption the words fit - before a row is given up on.
    """
    wanted = one_line(text)
    if not wanted:
        return None
    ident = clean_id(wanted)
    for entry in entries:
        if entry["id"] == ident:
            return entry
    lowered = wanted.lower()
    for pool in (shown, entries):
        for entry in pool:
            if one_line(entry.get("name")).lower() == lowered:
                return entry
    for pool in (shown, entries):
        for entry in pool:
            name = one_line(entry.get("name")).lower()
            if name and (name in lowered or lowered.startswith(name)):
                return entry
    return None


def entry_for(id_value: str) -> dict | None:
    for entry in entries:
        if entry["id"] == clean_id(id_value):
            return entry
    return None


def selected_id() -> str:
    """The addon the buttons work on: the chosen row, or the one being described.

    A repaint drops Qt's selection without dropping what the pane is showing, so
    the pane answers second - a button then still means the addon the person is
    looking at rather than nothing at all.
    """
    if api is None:
        return clean_id(picked)
    try:
        chosen = api.tabs.widget_value(TAB, "list", "selected")
    except AddonError:
        chosen = None
    if isinstance(chosen, (list, tuple)):
        text = str(chosen[0]) if chosen else ""
    else:
        text = str(chosen or "")
    entry = find_entry(text.split("\t")[0])
    if entry is not None:
        return entry["id"]
    return clean_id(picked)


# --- the detail pane: what one addon really is ---

def _detail(id_value: str, prop: str, value: Any) -> None:
    """One widget of the detail pane, with the tab's own failures kept to itself."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, id_value, prop, value)
    except AddonError:
        pass


def _fact_rows(entry: dict) -> list[dict]:
    """The listing's own words about one addon, as the pairs the pane shows.

    What it needs on the device is said twice over on purpose: the transports
    ``api.device`` will hand it, and in plain words whose connection it runs on -
    the two things a person can only find out the hard way otherwise.
    """
    state, _words, _tint = state_of(entry)
    commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
    needs = ", ".join(entry.get("requires") or []) or "nothing on the device"
    facts = [["id", one_line(entry.get("id"))],
             ["Version", one_line(entry.get("version")) or "not stated"],
             ["Author", one_line(entry.get("author")) or "not stated"],
             ["Published by", one_line(entry.get("source")) or "not stated"],
             ["Here", state],
             ["Needs", needs],
             ["Connection", connection_words(entry)],
             ["File", one_line(entry.get("filename"))],
             ["Its own assets", ", ".join(entry.get("assets") or []) or "none"],
             ["Its own test", one_line(entry.get("test")) or "none published"]]
    if commit.get("sha"):
        title = one_line(commit.get("title"))
        facts.insert(5, ["Commit", f"{str(commit['sha'])[:7]} {title}".strip()])
        when = " ".join(part for part in (one_line(commit.get("date")),
                                          one_line(commit.get("who"))) if part)
        if when:
            facts.insert(6, ["Committed", when])
    return [{"cells": [label, value]} for label, value in facts]


def show_detail(entry: dict | None) -> None:
    """Fill in the pane beside the list with everything the listing says.

    Opening an addon is the only time its commit is asked for, so the store makes
    one request per addon somebody shows an interest in rather than one per row
    on every refresh; the line fills itself in when the answer lands.
    """
    global picked
    if api is None:
        return
    if not isinstance(entry, dict):
        picked = ""
        _detail("dicon", "image", "")
        _detail("dtitle", "text", "Pick an addon")
        _detail("dby", "text", "")
        _detail("dstate", "text", "")
        _detail("dabout", "value", "")
        _detail("dfacts", "rows", [])
        return
    picked = one_line(entry.get("id"))
    _state, words, tint = state_of(entry)
    author = one_line(entry.get("author"))
    source = one_line(entry.get("source"))
    by = "   \u00b7   ".join(part for part in ((f"by {author}" if author else ""),
                                               source) if part)
    _detail("dicon", "image", icon_of(entry))
    _detail("dtitle", "text", one_line(entry.get("name")) or picked)
    _detail("dby", "text", by)
    _detail("dstate", "text", words)
    _detail("dstate", "style", f"color: {tint}; font-size: 10pt; font-weight: 600;"
                               " padding: 2px 0px 0px 0px;")
    _detail("dabout", "value", one_line(entry.get("description"))
            or "The listing says nothing about this addon.")
    _detail("dfacts", "rows", _fact_rows(entry))
    _ask_commit(entry)


def pick_row(payload: dict) -> None:
    """A row was picked: describe the addon the list has under its selection.

    A repaint arrives here as a selection of nothing, and that is not the person
    changing their mind - so the pane keeps whatever it was already showing.
    """
    text = one_line((payload or {}).get("text"))
    if not text and api is not None:
        try:
            chosen = api.tabs.widget_value(TAB, "list", "selected")
        except AddonError:
            return
        text = str(chosen[0]) if isinstance(chosen, (list, tuple)) and chosen else ""
    entry = find_entry(text)
    if entry is not None:
        show_detail(entry)


def row_context(text: str) -> None:
    """A right-click on a row: show what it is, then offer the buttons that fit it.

    The two things that can only be done to an installed addon are left out of a
    row that is not one, so the menu never offers to delete thin air.
    """
    if api is None:
        return
    entry = find_entry(text) or (entry_for(picked) if picked else None)
    if entry is None:
        return
    show_detail(entry)
    offered = [("Install it", "install"), ("Update it", "update"),
               ("Delete it", "delete"), ("Refresh the catalog", "refresh")]
    if not installed(entry["id"]):
        offered = [pair for pair in offered if pair[1] not in ("update", "delete")]
    line = api.ui.choose([words for words, _key in offered] + ["Nothing"])
    if not line:
        return
    for words, key in offered:
        if line == words:
            HANDLERS[key]()
            return


def merge_catalogs(sources: list[dict], token: str = "") -> tuple[list[dict], list[str]]:
    """Every source's catalog, in order, with the clashes said out loud.

    An id two repositories both publish is taken from the first - the order of
    the list is the priority - and the row says where the one kept came from, so
    the download that follows is looked for in the same place. A source that will
    not answer is reported and passed over, because the rest of them are still
    worth reading.
    """
    found: list[dict] = []
    failed: list[str] = []
    seen: dict[str, str] = {}
    for source in sources:
        label = source_label(source)
        try:
            rows = load_catalog(source_values(source, token))
        except AddonError as error:
            failed.append(f"{label}: {error}")
            note(f"catalog {label}: {error}")
            continue
        kept = 0
        for row in rows:
            entry = dict(row)
            clash = seen.get(entry["id"])
            if clash is not None:
                note(f"{entry['id']} is published by {label} as well; the copy "
                     f"from {clash} is the one that will be installed")
                continue
            entry["source"] = label
            entry["src"] = source
            seen[entry["id"]] = label
            found.append(entry)
            kept += 1
        note(f"{label}: {kept} addon(s)")
    return found, failed


def refresh(_checked: bool = False) -> None:
    """Read each source's catalog from GitHub, check what is behind it, repaint.

    A commit already looked up is carried across the reload with it, so reading
    the store again is not a reason to ask GitHub the same question twice, and the
    pictures are queued again only for the addons this machine still lacks.
    """
    global entries, updates
    if api is None or working:
        return
    values = source_config()
    sources = read_sources(values)
    show_sources(sources)
    if not sources:
        entries, updates = [], {}
        show_entries()
        show_detail(None)
        say("no source is configured: press Sources and add a repository", bad=True)
        return
    say(f"reading {len(sources)} catalog(s)...")
    known = {one_line(entry.get("id")): entry["commit"] for entry in entries
             if isinstance(entry.get("commit"), dict)}
    entries, failed = merge_catalogs(sources, clean_token(config_get(values,
                                                                     "token", "")))
    for entry in entries:
        saved = known.get(entry["id"])
        if saved is not None:
            entry["commit"] = saved
    updates = check_updates()
    show_entries()
    queue_icons()
    show_detail(entry_for(picked) if picked else None)
    word = ("no addons are published here yet" if not entries
            else f"{len(entries)} addon(s) in the store")
    if updates:
        word += f", {len(updates)} update(s) available"
        note(f"{len(updates)} installed addon(s) have a newer version in the store: "
             + ", ".join(sorted(updates)))
    if failed and entries:
        say(f"catalog loaded: {word}; {len(failed)} source(s) could not be read "
            f"({failed[0]})", bad=True)
    elif failed:
        say("; ".join(failed), bad=True)
    else:
        say(f"catalog loaded: {word}")


# --- the sources editor: what the tab's Sources button does ---

_ADD = "Add a GitHub repository"
_DONE = "Done"
_MAIN = "Add this program's own source"

#: What can be done to the source picked from the list, and the key each one
#: writes when it is a field rather than a move.
_SOURCE_ACTIONS = ("Change owner", "Change repository",
                   "Change branch, tag or commit", "Change store folder",
                   "Move first", "Remove")

_SOURCE_FIELDS = {"Change owner": "owner",
                  "Change repository": "repo",
                  "Change branch, tag or commit": "ref",
                  "Change store folder": "store_path"}

_SOURCE_LABELS = {"owner": "GitHub owner - the account or organisation",
                  "repo": "Repository name",
                  "ref": "Branch, tag or commit",
                  "store_path": "Store folder inside the repository"}


def _source_menu(sources: list[dict]) -> tuple[list[str], list[tuple]]:
    """The menu's lines and what each of them means, kept in step.

    Every line is numbered or worded so it cannot be mistaken for one of the
    actions, and the program's own repository is only offered again when it is
    not already in the list.
    """
    items: list[str] = []
    actions: list[tuple] = []
    for index, source in enumerate(sources, 1):
        items.append(f"{index}. {source_label(source)}")
        actions.append(("source", index - 1))
    items.append(_ADD)
    actions.append(("add", None))
    main = normalize_source(MAIN_SOURCE)
    if not any(_source_key(source) == _source_key(main) for source in sources):
        items.append(f"{_MAIN} ({source_label(main)})")
        actions.append(("main", None))
    items.append(_DONE)
    actions.append(("done", None))
    return items, actions


def _ask_source(title: str) -> dict | None:
    """Owner, repository, ref and store folder, one box at a time; nothing on cancel.

    ``api.ui.dialog`` is modal and builds its spec without an events callback, so
    it can host no live button of its own; a short run of single-line boxes asks
    the same four questions and needs nothing extra of the app.
    """
    answers: dict[str, str] = {}
    for key in SOURCE_KEYS:
        text = api.ui.input(title, _SOURCE_LABELS[key],
                            str(SOURCE_HINTS.get(key) or ""))
        if text is None:                      # given up on
            return None
        answers[key] = text
    made = normalize_source(answers)
    if not made["owner"] or not made["repo"]:
        api.ui.warning(title, "A source needs an owner and a repository.")
        return None
    return made


def _ask_field(source: dict, key: str) -> dict | None:
    """One part of a source changed, or the source as it was when the box was cancelled."""
    label = _SOURCE_LABELS[key]
    start = one_line(source.get(key)) or str(SOURCE_HINTS.get(key) or "")
    text = api.ui.input(f"{source_label(source)}: {label}", label, start)
    if text is None:
        return None
    updated = dict(source)
    updated[key] = text
    made = normalize_source(updated)
    if not made["owner"] or not made["repo"]:
        api.ui.warning("Change a store source",
                       "A source needs an owner and a repository.")
        return None
    return made


def edit_sources(_checked: bool = False) -> None:
    """The Sources button: add a repository, change one, put it first or drop it.

    Each change is written to the config as it is accepted, so closing the app
    cannot lose it, and the catalog is re-read once, when the menu is done - a
    repository added is a catalog fetched, and there is no sense in doing that
    for every keystroke on the way.
    """
    global entries, updates
    if api is None or working:
        return
    sources = current_sources()
    changed = False
    while True:
        items, actions = _source_menu(sources)
        picked = api.ui.choose(items)
        if picked is None or picked == _DONE:
            break
        kind, which = actions[items.index(picked)]
        if kind in ("add", "main"):
            made = (normalize_source(MAIN_SOURCE) if kind == "main"
                    else _ask_source("Add a store source"))
            if made is None or any(_source_key(one) == _source_key(made)
                                   for one in sources):
                if made is not None:
                    say(f"{source_label(made)} is already a source")
                continue
            sources.append(made)
            changed = True
            note(f"{source_label(made)} was added as a source")
            continue
        source = sources[which]
        action = api.ui.choose(list(_SOURCE_ACTIONS))
        if action is None:
            continue
        if action in _SOURCE_FIELDS:
            made = _ask_field(source, _SOURCE_FIELDS[action])
            if made is None or _source_key(made) == _source_key(source):
                continue
            sources[which] = made
            changed = True
            note(f"a source is now {source_label(made)}")
        elif action == "Move first":
            if which:
                sources.insert(0, sources.pop(which))
                changed = True
                note(f"{source_label(source)} is read first, so its versions win")
        elif action == "Remove":
            if not api.ui.question(
                    "Remove a store source",
                    f"Stop reading addons from {source_label(source)}?\n\n"
                    "Nothing already installed is touched, and the repository "
                    "can be added back."):
                continue
            sources.pop(which)
            changed = True
            note(f"{source_label(source)} was removed as a source")
    if not changed:
        return
    kept = write_sources(sources)
    show_sources(kept)
    entries, updates = [], {}
    show_entries()
    if not working:
        refresh()


# --- the job: install and delete, without holding the tab still ---

def _dev() -> Any:
    """The Addon Dev machinery, imported only when a real build is asked for."""
    import manager_addondev

    return manager_addondev


def _begin(verb: str, entry: dict | None = None) -> dict:
    """Start a job: the buttons grey out and one dict holds its whole state.

    The address the job works from is the one that published the entry it was
    asked about, so its assets, its test and the modules it imports are fetched
    from the repository the catalog row came out of. With no entry to follow -
    a job started from a bare id - the first configured source answers.
    """
    global working, job
    values = source_config()
    token = clean_token(config_get(values, "token", ""))
    source = (entry or {}).get("src")
    if not isinstance(source, dict):
        source = (read_sources(values) or [dict(MAIN_SOURCE)])[0]
    working = True
    job = {"verb": verb, "values": source_values(source, token),
           "source": source_label(source), "stream": None, "on_ok": None,
           "fail": "", "lines": [], "harness": None, "widened": False,
           "details": {}}
    _buttons(False)
    try:
        api.tabs.set_widget(TAB, "log", "value", "")
    except AddonError:
        pass
    note(f"{job['source']}: this job reads the store from here")
    return job


def _finish(text: str, bad: bool = False) -> None:
    """End the job in hand: the buttons come back and one line is left up."""
    global working, job
    job = None
    working = False
    _buttons(True)
    say(text, bad=bad)
    note(text)


def _stream(command: list[str], cwd: Path | None, on_ok: Any,
            env: dict | None = None, fail: str = "",
            on_bad: Any = None) -> None:
    """Run one child process and call ``on_ok`` when it ends clean.

    ``env`` is the environment for this one process (``None`` keeps the app's),
    and ``fail`` the line left up when it stops non-zero, in place of the plain
    exit-code message, so a failed test says what it was. ``on_bad``, when given,
    is called instead of ending the job, so a step can try another way first.
    """
    dev = _dev()
    stream = dev._Stream(command, cwd=cwd, env=env)
    stream.start()
    if job is not None:
        job["stream"] = stream
        job["on_ok"] = on_ok
        job["fail"] = fail
        job["on_bad"] = on_bad
        job["lines"] = []
    _poll_stream()


def _poll_stream() -> None:
    """Drain the running job's process; come back on the next tick until it ends."""
    if job is None:
        return
    stream = job.get("stream")
    if stream is None:
        return
    for line in stream.drain(400):
        if line is not None:
            note(line)
            job.setdefault("lines", []).append(line)
    if len(job.get("lines") or ()) > 120:
        del job["lines"][:-120]        # a step only ever reads back its own tail
    if not stream.finished:
        if api is None:
            return
        api.schedule.after(0.2, _poll_stream)
        return
    code = int(stream.code or 0)
    job["stream"] = None
    if code != 0:
        on_bad = job.pop("on_bad", None)
        if callable(on_bad):
            on_bad()
            return
        _finish(job.get("fail") or f"the job stopped with code {code}", bad=True)
        return
    job.pop("on_bad", None)
    on_ok = job.pop("on_ok", None)
    if callable(on_ok):
        on_ok()


def _clear_ready() -> None:
    """Put away what the dev machinery remembered: the interpreter and PyInstaller."""
    try:
        _dev().forget_interpreter()
    except Exception:
        pass


def _ensure_python() -> str:
    """The interpreter that would build, or empty with the reason already shown."""
    where, why = _dev().build_interpreter()
    if where is None:
        _finish(why, bad=True)
        return ""
    note(f"building with {where}")
    return str(where)


def _fetch_file(values: dict, remote: str, target: Path,
                folder: str | None = None) -> str:
    """One store file down into ``target``; empty text on success, else why it stopped."""
    path = raw_address(values, remote, folder)
    try:
        status, body = _http(values).get("raw.githubusercontent.com", path)
    except (http.client.HTTPException, OSError) as error:
        return f"the download stopped: {error}"
    if status != 200:
        return _refused(status, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError as error:
        return f"the download could not be written here: {error}"
    note(f"{len(body)} bytes into {target}")
    return ""


def _compile(id_value: str) -> None:
    """Fetch the addon's .py and any assets into staging, then build it here.

    A picture that will not arrive is said and skipped rather than refused: the
    assets are decoration, and every addon can draw its own tab mark, so losing
    one is worth a line in the log and nothing more. The source itself is not -
    without it there is no addon to build.
    """
    if job is None:
        return
    entry = entry_for(id_value)
    values = job["values"]
    if entry is None:
        _finish(f"the store no longer lists {id_value}", bad=True)
        return
    _reset_staging()
    source = staging() / f"{id_value}.py"
    say(f"downloading {entry['filename']}...")
    why = _fetch_file(values, entry["filename"], source)
    if why:
        _finish(why, bad=True)
        return
    # Read before the build, since the source is gone by the time it is installed
    job["details"] = _details(source)
    names = list(entry.get("assets") or [])
    if entry.get("icon") and entry["icon"] not in names:
        names.append(entry["icon"])
    assets = staging() / "assets"
    left = []
    for index, name in enumerate(names, 1):
        say(f"downloading asset {name} ({index} of {len(names)})...")
        why = _fetch_file(values, f"assets/{id_value}/{name}", assets / name)
        if why:
            note(f"{name} could not be fetched: {why}")
            left.append(name)
    if left:
        say(f"{len(left)} asset(s) are not packed with it; the addon still builds "
            "and wears the mark it draws itself")
    test_path = None
    test_name = entry.get("test") or ""
    if test_name:
        say(f"downloading {test_name} to check the addon first...")
        why = _fetch_file(values, f"_tests/{test_name}", staging() / test_name)
        if why:
            _finish(f"the addon's test could not be fetched: {why}", bad=True)
            return
        test_path = staging() / test_name
    job["harness"] = harness_folder(values)
    _maybe_test(id_value, source, test_path)


def _maybe_test(id_value: str, source: Path, test_path: Path | None) -> None:
    """Run the addon's own test before spending a build on it; skip it when none.

    The test is handed the downloaded ``.py`` through ``RASCONF_ADDON_SOURCE``
    and the modules it imports through ``PYTHONPATH``, so it runs against the
    very copy that is about to be compiled. A result the addon itself failed
    ends the job; a run that never got as far as the addon is only said out loud,
    because it proved nothing either way. The build goes ahead when there is no
    test published, no way to run one, or a test that passed.
    """
    if job is None:
        return
    if test_path is None or not test_path.is_file():
        say("no test is published for this addon; compiling as it is")
        _build(id_value, source)
        return
    harness = job.get("harness")
    if harness is None:
        say("the addon's test cannot be run here; compiling as it is")
        _build(id_value, source)
        return
    dev = _dev()
    where, why = dev.build_interpreter()
    if where is None:
        _finish(why, bad=True)
        return
    env = dict(os.environ)
    had = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(harness) + (os.pathsep + had if had else "")
    env["RASCONF_ADDON_SOURCE"] = str(source)      # the copy just downloaded
    env["QT_QPA_PLATFORM"] = "offscreen"           # a test may build real widgets
    say(f"running {test_path.name} before compiling...")
    _stream([str(where), str(test_path)], Path(harness),
            lambda: _build(id_value, source), env=env,
            fail="the addon's own test did not pass, so it was not compiled",
            on_bad=lambda: _test_said_nothing(id_value, source, test_path))


def _test_said_nothing(id_value: str, source: Path,
                       test_path: Path | None) -> None:
    """Sort a test that stopped into the addon's fault and this machine's.

    A published test imports the app's protocol and, for its widget checks, Qt,
    and the machine's own Python is not obliged to have either. A run that died
    on one of those imports never reached the addon, so it is no reason to refuse
    an install. Anything else it stopped for is, and is ended on.

    Dying on one of the app's own modules is worth one retry, because it may be
    the harness that is short rather than the Python: when the modules were
    fetched rather than found, the rest of the app is asked for and the test is
    run again against a harness that can hold the whole thing up.
    """
    missing = _machine_missing()
    if not missing:
        _finish("the addon's own test did not pass, so it was not compiled",
                bad=True)
        return
    if (missing in _APP and test_path is not None
            and not job.get("widened") and _harness_was_fetched()):
        note(f"the test wants {missing}, which a fetched harness does not hold: "
             "asking for the app's other modules and running it again")
        job["widened"] = True
        say(f"fetching the app's other modules to run {test_path.name} properly...")
        if _widen_harness(job.get("values") or {}):
            _maybe_test(id_value, source, test_path)
            return
    note(f"the test could not start: this Python has no {missing}, so nothing "
         "was proved about the addon either way")
    say("the addon's test cannot be run here; compiling as it is")
    _build(id_value, source)


def _machine_missing() -> str:
    """The module a run could not import, when its own tail says it was one of
    the test's rather than the addon's; nothing when it says something else.

    What counts as the test's own is the app and its dependencies: the names
    above, and every module the harness folder holds - a checkout's answer is
    the whole app, a fetched folder is only the two files an addon imports.
    """
    if job is None:
        return ""
    own = set(_MACHINE) | set(_APP)
    harness = job.get("harness")
    if harness is not None:
        try:
            own |= {path.stem for path in Path(harness).glob("*.py")}
        except OSError as error:                  # a folder that cannot be read
            note(f"the harness folder could not be listed: {error}")
    for line in reversed(list(job.get("lines") or [])):
        found = _MISSING.search(str(line))
        if not found:
            continue
        name = found.group(1).split(".")[0]
        return name if name in own else ""
    return ""


def _build(id_value: str, source: Path) -> None:
    """Write a launcher and run PyInstaller over the staged source and its assets.

    The launcher imports the app's two modules, so the build is only worth
    starting when they can be read from disk: the folder that holds them is put
    on the search path with the addon's own.
    """
    if job is None:
        return
    dev = _dev()
    harness = job.get("harness")
    if harness is None:
        _finish("the modules an addon imports could not be reached, so a program "
                "built here could not talk to the app", bad=True)
        return
    root = work_dir()
    work = root / id_value
    try:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        launcher = work / "launcher.py"
        dev._write_text(launcher, dev._LAUNCHER.substitute(
            id=id_value, module=dev.module_name(source)))
    except OSError as error:
        _finish(f"the build folder could not be made: {error}", bad=True)
        return
    entry = entry_for(id_value) or {}
    assets = staging() / "assets"
    options = {"onefile": True, "console": True, "icon": True, "hidden": ""}
    if assets.is_dir() and any(assets.iterdir()):
        options["add_data"] = [(assets, "assets")]       # packed to the exe's assets folder
        icon_name = entry.get("icon") or ""
        icon = assets / icon_name if icon_name else None
        options["icon"] = str(icon) if (icon is not None and icon.is_file()) else True
    command = dev.build_command(launcher, id_value, options, source, root=root,
                                paths=[Path(harness)])
    say(f"compiling {id_value} with PyInstaller...")
    note("$ " + " ".join(command))
    _stream(command, work, lambda: _install_step(id_value))


def _stay_off(id_value: str) -> None:
    """Write the new addon into the active profile as switched off.

    The app starts only what the profile enables, so an addon that has just
    arrived runs nothing: it appears in Settings > Addons with its switch down
    and waits to be turned on. The row shows up in the rescan that follows.
    """
    if api is None:
        return
    try:
        api.session.set_addon_enabled(addon_id=id_value, enabled=False)
    except Exception as error:              # a settings hiccup never loses an install
        note(f"{id_value} could not be written to the profile as switched off: "
             f"{error}")


def _details(source: Path) -> dict:
    """What an addon's own source says about it, read without running any of it.

    The downloaded ``.py`` is cleared away as soon as the program is installed,
    so its manifest and its settings form are taken out of it once, here - the
    same way the Addon Dev tab reads an addon it has never loaded, with no import
    and no Qt.
    """
    try:
        return dict(_dev().addon_metadata(source))
    except Exception as error:              # a dev module that will not answer
        note(f"the addon's own details could not be read: {error}")
    return {}


def addon_data_folder(id_value: str) -> Path:
    """``addons/_data/<id>``, the settings folder of some other addon than this.

    The app hands out the store's own folder, and every addon's folder sits
    beside it, so the folder a fresh install needs is made from the same parent -
    without this addon's own data ever being in the way.
    """
    return addons_dir() / "_data" / clean_id(id_value)


def _manifest_values(id_value: str, details: dict, entry: dict) -> dict:
    """The ``<id>.json`` describing a program the store has just built.

    The addon's own ``ADDON`` dict answers what the app has to know before the
    program runs, and the catalog's words fill any gap the source leaves.
    """
    declared = dict(details.get("manifest") or {})
    for key in ("name", "version", "author", "description"):
        if not one_line(declared.get(key)) and one_line(entry.get(key)):
            declared[key] = one_line(entry.get(key))
    wanted = [one_line(item).lower() for item in (declared.get("requires") or [])]
    declared["requires"] = [item for item in dict.fromkeys(wanted)
                            if item in ("ssh", "sftp")]
    declared["api"] = declared.get("api") or 1
    declared["id"] = clean_id(id_value)
    declared[ORIGIN_KEY] = STORE_ORIGIN
    return jsonable(declared)


def _describe(id_value: str, program: Path, details: dict, entry: dict) -> None:
    """Write down what an install is, beside it and in its own settings folder.

    Two files come of this: the manifest the app reads an addon's name, version
    and transports from before the program has run once, and the addon's
    ``fields.json`` - the form Settings shows while the addon is off, which a
    running addon writes itself through ``api.config.declare``. An addon that is
    installed is still installed when neither file can be written, so a failure
    is said and nothing more.
    """
    manifest = _manifest_values(id_value, details, entry)
    try:
        write_config_json(program.parent / f"{clean_id(id_value)}.json", manifest)
    except OSError as error:
        note(f"{id_value}'s manifest could not be written beside it: {error}")
    fields = [dict(item) for item in (details.get("fields") or [])
              if isinstance(item, dict) and item.get("key")]
    defaults = dict(details.get("defaults") or {})
    if not fields and not defaults:
        return                      # the addon keeps no settings: there is no form
    name = one_line(manifest.get("name")) or id_value
    folder = addon_data_folder(id_value)
    try:
        write_config_json(folder / "fields.json", jsonable({
            "title": f"{name} - addons/_data/{clean_id(id_value)}/config.json",
            "fields": fields,
            "defaults": defaults}))
        if not (folder / "config.json").is_file():
            write_config_json(folder / "config.json", jsonable(defaults))
    except OSError as error:
        note(f"{id_value}'s settings could not be prepared: {error}")


def _install_step(id_value: str) -> None:
    """Move the fresh program into ``addons/``, say what it is, leave it off.

    The rescan that lets the app see the new file re-loads this addon as well,
    which takes the module this code runs in out of the app: the api is gone the
    moment ``_reload()`` starts. So everything that still needs it - clearing the
    build away, marking the addon off, the table and the status line - is done
    first, and the rescan is the last thing here.
    """
    if job is None:
        return
    dev = _dev()
    staged = dev.staged_name(id_value, True, root=work_dir())
    if not staged.is_file():
        _tidy(id_value)
        _finish("no program was produced; see the log above", bad=True)
        return
    try:
        target = dev.install_build(staged, id_value, True)
    except OSError as error:
        _tidy(id_value)
        _finish(f"it could not be put in the addons folder: {error}", bad=True)
        return
    _describe(id_value, target, job.get("details") or {}, entry_for(id_value) or {})
    _tidy(id_value)
    updating = job.get("verb") == "update"
    if updating:
        note(f"{id_value}'s own settings under addons/_data/{id_value}/ were left "
             "as they are")
        updates.pop(id_value, None)     # it now matches the catalog, so it is current
    else:
        _stay_off(id_value)
    show_entries()
    if updating:
        had = one_line(job.get("from_version"))
        now = one_line((job.get("details") or {}).get("manifest", {})
                       .get("version")) \
            or one_line((entry_for(id_value) or {}).get("version"))
        _finish(f"updated {target.name}"
                + (f" from {had} to {now}" if had and now else ""))
    else:
        _finish(f"installed {target.name}, switched off until you turn it on in "
                "Settings > Addons")
    _reload()


def _pip_install(where: str, user: bool = False) -> None:
    """Install PyInstaller into the interpreter that would do the building.

    Plain first and ``--user`` second: a machine-wide Python on Windows refuses
    a normal install without elevation, and a distribution's Python on Linux
    refuses it altogether unless the packages are for this user alone.
    """
    if job is None:
        return
    who = " for this user" if user else ""
    say(f"getting PyInstaller ready (pip install{who})...")
    command = [where, "-m", "pip", "install", "--disable-pip-version-check"]
    if user:
        command.append("--user")
    command.append("pyinstaller")
    _stream(command, None, lambda: _after_pip(where),
            on_bad=lambda: _pip_again(where, user),
            fail="pip could not install PyInstaller; the lines above say why")


def _pip_again(where: str, user: bool) -> None:
    """The pip that failed: once more for this user, and then it is reported."""
    if user or job is None:
        _finish("PyInstaller could not be installed with pip, for this user "
                "either; the lines above say why. A distribution's Python on "
                "Linux may need a virtualenv instead", bad=True)
        return
    note("that install did not go through; trying again for this user alone")
    _pip_install(where, user=True)


def _after_pip(where: str) -> None:
    _clear_ready()
    ready, hint = _dev().pyinstaller_ready()
    if not ready:
        _finish(f"PyInstaller still cannot be found: {hint}", bad=True)
        return
    id_value = job.get("id") if job is not None else ""
    _compile(str(id_value))


def install_selected(_checked: bool = False) -> None:
    """Download and compile the chosen addon, installing PyInstaller first if needed."""
    global working
    if api is None or working:
        return
    id_value = selected_id()
    if not id_value:
        say("pick an addon in the list first", bad=True)
        return
    if entry_for(id_value) is None:
        say("the list is empty - press Refresh first", bad=True)
        return
    if installed(id_value):
        say(f"{id_value} is already installed; use Update if the store lists a "
            "newer version, or Delete to remove it", bad=True)
        return
    job_local = _begin("install", entry_for(id_value))
    job_local["id"] = id_value
    where = _ensure_python()
    if not where:
        return
    ready, _hint = _dev().pyinstaller_ready()
    if not ready:
        _pip_install(where)
        return
    _compile(id_value)


def update_selected(_checked: bool = False) -> None:
    """Rebuild the chosen addon from the catalog's version, keeping its settings.

    An update is an install over the top of what is already there: the same
    download, test and compile as a fresh install, and the program already in
    ``addons`` is replaced as the new one is copied in. What the addon saved for
    itself under ``addons/_data/<id>`` is never part of what an install clears,
    so its config keeps the answers already given to it, and the switch in
    Settings > Addons is not written to, so an addon that was on stays on.
    """
    global working
    if api is None or working:
        return
    id_value = selected_id()
    if not id_value:
        say("pick an installed addon in the list first", bad=True)
        return
    entry = entry_for(id_value)
    if entry is None:
        say("the list is empty - press Refresh first", bad=True)
        return
    if not installed(id_value):
        say(f"{id_value} is not installed yet; use Install to add it", bad=True)
        return
    have = installed_version(id_value)
    wanted = one_line(entry.get("version"))
    if not version_newer(wanted, have):
        say(f"{id_value} is already up to date (version {have or 'unknown'})")
        return
    if not api.ui.question(
            "Update addon",
            f"Update {one_line(entry.get('name')) or id_value} from "
            f"{have or 'an unknown version'} to {wanted}?\n\nIts settings in "
            f"addons/_data/{id_value}/ are kept, and the switch in Settings > "
            "Addons is left as it is."):
        say("the update was declined")
        return
    job_local = _begin("update", entry)
    job_local["id"] = id_value
    job_local["from_version"] = have
    where = _ensure_python()
    if not where:
        return
    ready, _hint = _dev().pyinstaller_ready()
    if not ready:
        _pip_install(where)
        return
    _compile(id_value)


def _delete_paths(id_value: str) -> list[Path]:
    """What an install of this addon left in ``addons`` that delete may take back.

    The program, and the manifest the store wrote beside it: that one only, since
    a ``<id>.json`` made by the Addon Dev tab or by hand is not the store's to
    remove. Beside them the addon's own ``_data`` settings folder, when
    :func:`_data_removable` says it is this delete's business - which the store's
    Settings page decides for every session and the check box at the top of the
    tab decides for this one.
    """
    folder = addons_dir()
    found = [candidate for candidate in
             (folder / f"{id_value}.exe", folder / f"{id_value}.bin", folder / id_value)
             if candidate.exists()]
    sidecar = folder / f"{clean_id(id_value)}.json"
    if sidecar.is_file() and addon_origin(folder, id_value) == STORE_ORIGIN:
        found.append(sidecar)
    keeps = _data_removable(id_value)
    if keeps is not None:
        found.append(keeps)
    return found


def delete_selected(_checked: bool = False) -> None:
    """Take the compiled addon back out of ``addons/``, its settings with it, and rescan.

    The program going is what the button was pressed for, so that happens on its
    own word. The settings are only taken when the toggle says so, and are asked
    about first, since they are the one thing a delete cannot undo and an addon
    that is coming back would otherwise have to be filled in again.
    """
    if api is None or working:
        return
    id_value = selected_id()
    if not id_value:
        say("pick an installed addon in the list first", bad=True)
        return
    removed = _delete_paths(id_value)
    if not removed:
        say(f"{id_value} is not installed", bad=True)
        return
    keeps = _data_removable(id_value)
    if keeps is not None and not api.ui.question(
            "Delete addon",
            f"Delete {id_value} and the settings it saved in addons/_data/"
            f"{clean_id(id_value)}/ too?\n\n"
            "An addon installed again later will ask for everything from the "
            "start. Untick 'delete settings' beside the source at the top of "
            "the tab, or the switch on the store's Settings page, to leave "
            "them where they are."):
        say("the delete was declined")
        return
    try:
        for path in removed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
    except OSError as error:
        say(f"{id_value} could not be removed: {error}", bad=True)
        return
    _tidy(id_value)
    show_entries()
    words = f"deleted {id_value} from the addons folder"
    if keeps is None:
        words += f"; its settings were left in addons/_data/{clean_id(id_value)}/"
    else:
        words += " with its settings"
    say(words)
    note(words)
    _reload()


def _reload() -> None:
    """Ask the running app to look at ``addons/`` again and pick up what changed.

    The rescan shuts this addon down on its way to starting it again from the
    file on disk - and in a compiled store it ends this process too - so it is
    the last thing a job does and nothing after it reaches for the api.
    """
    if api is None:
        return
    try:
        api.session.rescan_addons()
    except Exception as error:                 # never let a rescan break the tab
        try:
            api.log(f"addon rescan after a store job: {error}")
        except Exception:
            pass


# --- the hooks the app calls ---

def focus_tab(_checked: bool = False) -> None:
    """The Tools menu entry: go to the tab."""
    if api is None:
        return
    api.tabs.activate(TAB)


HANDLERS: dict = {
    "refresh": refresh,
    "install": install_selected,
    "update": update_selected,
    "delete": delete_selected,
    "sources": edit_sources,
}


def init(plugin_api: Any) -> None:
    """Build the tab from the saved config and read the catalog once."""
    global api, session_delete_config
    api = plugin_api
    session_delete_config = None
    api.config.declare(
        FIELDS, DEFAULTS,
        title="Berry addon store - addons/_data/addon_store/config.json")
    build_tab()
    show_sources(current_sources())
    show_delete_toggle()
    api.ui.menu_item("Berry addon store", focus_tab, menu="Tools")
    api.schedule.after(0.2, refresh)


def start() -> None:
    """Nothing to do beyond what init queued."""
    return None


def shutdown() -> None:
    """Let go of everything this copy held: the connections, the job, the pane."""
    global api, working, job, shown, picked, filter_text, session_delete_config
    _drop_http()
    working = False
    job = None
    shown = []
    picked = ""
    filter_text = ""
    session_delete_config = None
    del icon_queue[:]
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Everything the tab reports back, in one place.

    ``clicked`` runs the button its id names; a line ``changed`` on the filter box
    narrows the list as it is typed; the check box beside the source says what
    Delete is to do with an addon's settings for this session; and a row picked -
    or right-clicked - is what fills in the pane beside the list. Nothing here
    waits on the network except the one request a right-click's menu and a commit
    lookup each make for themselves.
    """
    if str(tab) != TAB:
        return
    ident = str(identifier)
    name = str(event)
    data = payload if isinstance(payload, dict) else {}
    if name == "clicked":
        handler = HANDLERS.get(ident)
        if handler is not None:
            handler()
        return
    if ident == "delcfg" and name in ("toggled", "changed"):
        set_delete_config_session(data)
        return
    if ident == "filter" and name in ("changed", "returned"):
        apply_filter(data.get("text", ""))
        return
    if ident != "list":
        return
    if name == "selected":
        pick_row(data)
    elif name == "context":
        row_context(one_line(data.get("text")))


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: point the header and reload.

    An answer written on the Settings page is newer than whatever the check box
    at the top of the tab was moved to, so the session's override is dropped and
    the box is put back under the file before the catalog is read again.
    """
    global session_delete_config
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    session_delete_config = None
    show_sources(current_sources())
    show_delete_toggle()
    if not working:
        refresh()


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
