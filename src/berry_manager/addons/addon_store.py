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

Before a program is replaced, the addon it is being written for is stopped and
switched off: its running process is killed, so the file is not held open, and
its tab is marked off while the new one goes in. A freshly installed addon is
left that way - written into the active profile's settings as switched off, so
nothing of it runs until it is turned on - and an update puts its switch back
where it stood before the job, so an addon that was on comes back on when the
app has rescanned the folder. Every job is answered on the tab it was started
on: the card over the job's console names the addon in hand and counts the
batch's share, and the log underneath holds every line the build said.

A program only says what it is once it runs, and the app needs some of that
before then, so what the addon's source declared is written down as it is
installed: a ``<id>.json`` beside the program, naming it and listing the
transports ``api.device`` may hand it, and the addon's ``fields.json`` under
``addons/_data/<id>``, the copy of its settings form that the Addon manager edits
while the addon is off. That is also what marks the addon as one the user
installed - listed on the Addon manager page, with its own settings and its own
connection gates like any other drop-in - instead of among the addons that came
inside the program, which keep a page of their own in Settings.

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
left exactly as it is - and the addon's on/off switch is returned to the state
the person had it in before the job started, so an addon that was on stays on.

Install and Update work on whatever rows are selected, so ctrl-clicking several
addons runs them as one batch behind a single card on the job's pane, one job
after another until the queue is empty. An installed addon's row wears a check
box that switches it on or off where it stands, and the rescan an install ends
with keeps the tab it was started from showing, so a job never moves the person
away from the store.

Deleting one takes that compiled program (or folder) back out of ``addons/`` and
rescans. By default it takes the addon's own ``_data`` settings with it, since
those are what an install prepared; the switch on the store's Settings page
leaves them in place for every start, and the check box beside the source at the
top of the tab leaves them, or takes them, for this one. Either way the settings
of an addon the store did not install are never touched, and this store's own
folder - its sources - is not up for deletion by itself.

Compiling needs a Python on this machine and PyInstaller within it. The
interpreter is looked for as ``python3``, then ``python``, then the Windows ``py``
launcher, and a name that turns out to be the Microsoft Store's stand-in rather
than a Python is passed over. When PyInstaller is missing the store installs it
itself with ``pip`` - for this user alone when the plain install is refused -
and the user is not asked to. The store holds no token of its own: every request
carries whatever ``api.session.git_token()`` answers with, which is the profile's
one git token while the switch on this addon's tab in the Addon manager is
on, and
nothing at all while it is off. The sources are editable in
``addons/_data/addon_store/config.json``, in that file's
``sources`` list - a list of records rather than the one value per line the
Settings form can hold, which is why they get their own button on the tab.

The tab is laid out like the app store on a phone: a filter line over a list of
what is on offer, each row wearing the addon's own picture, and a detail pane
beside it that fills in when a row is picked - the description, who wrote it,
which repository published it, which commit the file came out of, what it needs
on the device and how it reaches that device, with the job log underneath the
pair. While a job runs, that pair gives way to the job's own view: a terminal for
a background, printing every step of the build as it arrives, with a card in the
middle naming the addon being installed and counting its share of the batch. The
job holds the tab bar and this tab's own sub-bar so neither can be switched away
from until it ends, and the pane goes back to the browsing when it does. The bar
is the job's only showing; nothing floats over the window beside it, since a card
that does not travel with the app and vanishes when the app is hidden explains
nothing. Everything is built from a declarative spec and its buttons and rows
report back through ``on_widget``, so the same file works imported into the app
and compiled into a program of its own by the Addon Dev tab. It marks itself with
``assets/addon_storeicon.png`` when the program ships one, and with a drawn store
bag when it does not, the way the other bundled addons' tabs do.

All of that is the first of the tab's two pages now. The second is the Addon
manager: every addon the program did not ship with, one sub-tab apiece, each
showing that addon's switches by default - run it, force its tabs into a window
of their own, let it read the profile's git token - beside the addon's own gates
over the transports its manifest asked for, the choice of which connection it
rides, and the buttons that raise its window, restart it, open its folder, edit
the settings it declared, put a Desktop link for it on the machine or delete it.
An addon that is switched off keeps its caption grey instead of being taken off
the bar, so its page can still be opened and switched back on. The first addon
opens by default, with the Show the Addon Dev tab check in the header above them.
An addon that came inside the program keeps its own page in the app's Settings and
is left out of this list; these used to be the Installed addons page in Settings,
and everything that page could do for them - the traffic gates and the settings
form - is done here instead.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Qt-free contract half: runs standalone and in tests.
from addon_common import (AddonError, ORIGIN_KEY, STORE_ORIGIN, addon_manifest_path,
                          addon_origin, config_get, connection_kind,
                          connection_words, default_icon_path, find_addon_picture,
                          icon_filename, jsonable,
                          merge_config, read_config_json, requires_list, write_config_json)

ADDON = {
    "name": "Berry addon store",
    "version": "3.0",
    "author": "fligma",
    "description": "Lists the addons published in the repository's addon.store "
                   "folder, downloads a chosen one into a staging folder and "
                   "compiles it for this machine with PyInstaller, then installs "
                   "or removes it from the addons folder. Installs PyInstaller "
                   "itself when it is not already present. A job is shown as a "
                   "terminal behind a card that names the addon in hand and "
                   "counts the batch's progress, and the tab is held on that "
                   "view until the job ends. The tab's second page is the Addon "
                   "manager: every addon the program did not ship with, one "
                   "sub-tab apiece, each showing that addon's switches for it - "
                   "run it, force its tabs into a window of their own, let it "
                   "read the profile's git token - beside its own gates over SSH "
                   "and SFTP, its choice of which connection it rides, and its "
                   "restart, folder, settings, Desktop shortcut and delete "
                   "buttons. An addon that is switched off keeps a grey caption.",
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
#: The Addon manager page's own view: the records the app listed for it, the
#: caption each of those is filed under, the addon whose page is on show, and -
#: since every page carries the addon's slug in its control ids - the slug each
#: tab is drawn under and the order those slugs are on the bar in. None of it is
#: read off the disk: the app's record is the one answer, so a page here and a
#: page in Settings cannot disagree.
managed: list[dict] = []
manager_rows: dict[str, dict] = {}
manager_picked = ""
#: The sub-tab each addon is drawn under: its slug - the id as a widget name may
#: spell it - to the record, the slugs in the order their tabs are on the bar, and
#: whether the pages are being laid out right now. That last one is what keeps a
#: repaint from reading its own tab change back as the person clicking about.
manager_slugs: dict[str, dict] = {}
manager_order: list[str] = []
manager_building = False
#: The settings pages the manager's bar is holding open: the addon's slug to the
#: record its form was built from - the fields it edits, the captions and the
#: file they belong to. ``refresh_manager`` lays these pages down again after a
#: repaint, with whatever the controls held, so editing settings survives a
#: toggle on the page above it, and closing one is the page's own business.
config_pages: dict[str, dict] = {}

#: A module name PyInstaller can import: anything else becomes an underscore.
_SAFE = re.compile(r"[^A-Za-z0-9_]")

#: What an addon's launcher, and a published test, import from the app.
HARNESS_FILES = ("addon_common.py", "addon_client.py")

#: The app's other modules, by the name a test imports them under. A published
#: test reaches for the Qt half of them to build real widgets, and only a
#: checkout, or a build that packed its source, can answer for these. The
#: package these names start with needs no file fetched of its own: a folder of
#: modules beside the harness is a package to Python already, and this one's
#: ``__init__`` holds nothing but words about the layers inside it.
_APP = ("addon_api", "berry_manager", "core.addon", "core.const", "core.net",
        "core.ui", "core.views", "core.window")

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
    "store_path": "addon.store",
}

#: What one source holds: the four pieces of a GitHub address, nothing else.
SOURCE_KEYS = ("owner", "repo", "ref", "store_path")

#: The answer a source field starts out with when it has none of its own.
SOURCE_HINTS = {"owner": "", "repo": "", "ref": "main",
                "store_path": MAIN_SOURCE["store_path"]}

DEFAULTS = {
    "sources": [dict(MAIN_SOURCE)],
    "delete_config": True,
}

FIELDS = [
    {"key": "delete_config", "label": "Delete takes an addon's settings too",
     "kind": "bool",
     "hint": "On removes addons/_data/<id>/ with the program, so nothing the "
             "addon was asked is left behind. Off leaves it, and an addon "
             "installed again later finds its answers where it left them"},
]

# There is no token field here any more: GitHub is reached with the profile's one
# git token, which Settings > Connection holds and the Addon manager's switch
# hands over. See ``profile_token``.
GROUPS = [("What Delete takes back", ["delete_config"])]

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


def source_values(source: dict) -> dict:
    """One source as the flat values dict every fetch already knows how to read.

    The address builders take ``owner``/``repo``/``ref``/``store_path``, so a
    source shaped like the old single-source config means none of them had to
    learn about a list. The token is not part of it: what GitHub is sent is asked
    of the profile when a request is actually made, see :func:`profile_token`.
    """
    data = source if isinstance(source, dict) else {}
    return {"owner": one_line(data.get("owner")),
            "repo": one_line(data.get("repo")),
            "ref": one_line(data.get("ref")) or "main",
            "store_path": one_line(data.get("store_path")).strip("/")
                         or MAIN_SOURCE["store_path"]}


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

#: The job's own view, which stands in for the browsing panes while a build is
#: running: the compiler's console filling the tab, and a card floating in the
#: middle of it naming the addon in hand. The card is kept to its own width so
#: the screen behind it stays readable at the edges, and dark enough that the
#: two do not fight for the same pixels.
_JOB_CARD_STYLE = ("#jobcard { background-color: rgba(24, 24, 24, 236);"
                   " border: 1px solid #3c3c3c; border-radius: 10px;"
                   " padding: 12px; max-width: 340px; }")

#: What the card is called: the same words the detail pane uses for a name, a
#: size down, since it has to be read at a glance over a moving screen.
_JOB_TITLE = ("QLabel { color: #f2f2f2; font-size: 13pt; font-weight: 700;"
              " padding: 0px; }")

_JOB_SUB = ("QLabel { color: #9a9a9a; font-size: 9pt; padding: 0px;"
            " font-family: Consolas, monospace; }")

#: The longest step line the card shows. Everything the build says belongs on the
#: console behind the card; the card only ever wears the current step, so one
#: whole compiler traceback cannot push the rest of the screen off view.
_JOB_STEP_LEN = 52

#: The manager's state words: the same three colours the store's status line
#: uses for news, a failure and an addon that is simply switched off. The last
#: one doubles as the grey an off addon's tab caption is drawn in.
_STATE_OK = "#23d18b"
_STATE_WARN = "#ffb020"
_STATE_OFF = "#8f8f8f"

#: The list's columns. A row is read by its picture and its name first, so the
#: id - what every command takes - is not crowding the front of it; the detail
#: pane says the id, and picking the row remembers it.
LIST_COLUMNS = ("In the store", "Version", "Where it stands")

#: The detail pane's facts are a label and what it answers, two columns wide.
FACT_COLUMNS = ("", "")

#: The splitter that holds the browsing panes over the job's log, and the share
#: of the tab's height each of the two is promised. The log starts folded away -
#: see ``show_log`` - so the store fills the screen, and it opens back to one
#: part in four when it is wanted.
LOG_PANE = "logpane"
LOG_SHARE = [3, 1]

#: The tab's two pages. The first is the store as it has always been, ids and all;
#: the second is the Addon manager, which is what the app's Settings used to call
#: its Installed addons page. The sub-bar carries no events of its own, so an
#: addon wanting the manager asks for ``maintabs`` by number.
SUBTABS = "maintabs"
PAGE_ADDONS = "Addons"
PAGE_MANAGER = "Addon manager"
MANAGER_PAGE = 1

#: The manager's own page: a header line, and under it one sub-tab per addon.
#: The pages are not in the spec at all - the addons that are installed here are
#: only known once the app has answered - so ``refresh_manager`` builds them into
#: ``MANAGER_TABS`` with ``add_page`` and lays the whole row out again whenever
#: the record changes. A person then opens the addon they mean by its own tab
#: rather than by finding its row in a table and reading the panel under it, and
#: that addon's switches are on screen from the start.
MANAGER_TABS = "managetabs"
MANAGER_HEADING = "What the app does for it"

#: Each addon's page holds the same controls as every other page, and a tab's
#: widgets all live in one registry, so a page's ids are the shared name and this
#: addon's own, joined. Which addon a report came from is read back out of the
#: same split, so no page has to be looked up by its caption.
MANAGER_SEP = "@@"

#: The switches on an addon's page. The first three are the app's own: the
#: record its run switch writes, the force-popout switch, and whether the
#: profile's one git token is this addon's business. The last three are the
#: addon's own gates, moved here when the Settings page became shipped-only: one
#: per transport its manifest asked for - a switch is drawn only for the transports
#: it named - and the choice of which connection it rides, which belongs to an
#: addon that said "both" and to nobody else.
MANAGER_SWITCHES = ("mg_enable", "mg_popout", "mg_token", "mg_ssh", "mg_sftp",
                    "mg_own")

#: The two transports a page can be asked to gate, in the order their switches are
#: drawn, each with the id it lives under.
MANAGER_TRANSPORTS = (("ssh", "mg_ssh"), ("sftp", "mg_sftp"))

#: The same two the other way round, so a toggle reports which transport it stands
#: for without counting positions in the tuple above.
MANAGER_GATE_IDS = {identifier: name for name, identifier in MANAGER_TRANSPORTS}

#: What can be done to the addon from its own page. The first button's words
#: change with the addon it stands for: one whose tabs live in a window is
#: raised, one whose tabs are in the main bar is merely shown. The settings button
#: opens the form the addon's own ``fields.json`` declares as a tab of the
#: manager's bar - the same tab, not a window of its own - which is what the
#: panel folded under its name in Settings used to be.
MANAGER_BUTTONS = (("mg_window", "Show tab"), ("mg_restart", "Restart"),
                   ("mg_folder", "Open folder"), ("mg_data", "Open addon data"),
                   ("mg_config", "Edit settings"),
                   ("mg_shortcut", "Desktop shortcut"), ("mg_delete", "Delete"))

#: The buttons by id alone, so a click on this page is answered here rather than
#: looked for among the store's own handlers.
MANAGER_IDS = tuple(identifier for identifier, _text in MANAGER_BUTTONS)

#: The two buttons at the foot of an addon's settings page: one writes what the
#: controls hold through the app, the other drops the page and changes nothing.
#: Their ids carry the addon's slug like every other control on this bar, so a
#: save means the addon whose form is open.
MANAGER_CONFIG_BUTTONS = ("cfg_save", "cfg_cancel")

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


def _job_view() -> dict:
    """The pane a job puts over the browsing panes: its console, and the addon in hand.

    The ``terminal`` is never connected to anything - it is used for what it is
    as a screen as well as a shell, so the build's own output is printed on it as
    it arrives. The card floats over it through an ``overlay``, which keeps it
    centred when the tab is resized rather than leaving the addon to count pixels;
    the pane starts hidden, since a job is only one press of a button away.
    """
    return {"kind": "overlay", "id": "jobpane", "visible": False, "children": [
        {"kind": "terminal", "id": "jobterm", "font_size": 10},
        {"kind": "column", "id": "jobcard", "object_name": "jobcard",
         "align": "center", "style": _JOB_CARD_STYLE, "children": [
             {"kind": "image", "id": "jobicon", "size": 64},
             {"kind": "label", "id": "jobname", "text": "", "style": _JOB_TITLE,
              "word_wrap": True},
             {"kind": "label", "id": "jobstep", "text": "", "style": _JOB_SUB,
              "word_wrap": True},
             {"kind": "progress", "id": "jobbar", "maximum": 100, "value": 0},
         ]},
    ]}


def addons_page() -> dict:
    """The store's own page: a list of what is on offer and, beside it, the one
    addon currently picked, with the job's log under both.

    The status line sits in the header between the title and the repository chip,
    so nothing of its own steals height from the list; it takes the width the two
    leave over and wraps inside that, so a long report stays readable and the chip
    stays put. Under the header a filter line narrows the list as it is typed.

    Two splitters do the rest, and every part of the tab can therefore be given
    room by dragging: the outer one is the panes over the log, the inner one is
    the list over its detail pane. The detail pane starts with the larger share,
    since an addon's description is the thing a person came here to read, and it
    is itself split, so the description and the catalog's facts can be given room
    against each other too.

    The log lies folded under the browsing panes until it is asked for: the tick
    box in the header, or a drag of the fold line, pulls it back up to its share.
    A job that ends badly opens it on its own, since the log is where the reason
    for a failure is written out at length.
    """
    return {"kind": "column", "label": PAGE_ADDONS, "children": [
        {"kind": "row", "id": "headrow", "children": [
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
            {"kind": "check", "id": "showlog", "text": "show log",
             "checked": False,
             "tooltip": "The job's log under the two panes. It starts folded "
                        "away; tick it to pull it back up, or drag - or "
                        "double-click - the line between the panes and it",
             "on": ["toggled"]},
        ]},
        {"kind": "row", "id": "filterrow", "children": [
            {"kind": "label", "text": "Find", "style": _FILTER_LABEL},
            {"kind": "line", "id": "filter", "expand": True,
             "placeholder": "Filter the list by name, author, id or what it needs",
             "tooltip": "Nothing is looked for until you type; the catalog on "
                        "GitHub is not touched",
             "on": ["changed", "returned"]},
            {"kind": "label", "id": "count", "text": "", "style": _STATUS_STYLE,
             "tooltip": "How many of the catalog's addons are showing"},
        ]},
        {"kind": "splitter", "vertical": True, "id": LOG_PANE,
         "sizes": LOG_SHARE, "children": [
            {"kind": "column", "children": [
                {"kind": "splitter", "id": "browse", "sizes": [2, 3], "children": [
                    {"kind": "table", "id": "list", "columns": list(LIST_COLUMNS),
                     "stretch": [0], "col_widths": [0, 70, 130], "font_size": 11,
                     "checkbox": 0, "multi": True,
                     "on": ["selected", "context", "toggled"],
                     "tooltip": "Every addon the configured sources list; click one "
                                "to read about it, ctrl-click several to work on them "
                                "as a batch, and tick an installed one to switch it on"},
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
                        {"kind": "splitter", "vertical": True, "sizes": [3, 2],
                         "children": [
                             {"kind": "column", "children": [
                                 {"kind": "label", "text": "What it is",
                                  "style": _DETAIL_HEAD},
                                 {"kind": "multi", "id": "dabout",
                                  "readonly": True, "expand": True,
                                  "style": _ABOUT_STYLE,
                                  "placeholder": "Nothing picked yet - choose an "
                                                 "addon on the left to read what "
                                                 "it does"},
                             ]},
                             {"kind": "column", "children": [
                                 {"kind": "label", "text": "What the listing says",
                                  "style": _DETAIL_HEAD},
                                 {"kind": "table", "id": "dfacts",
                                  "columns": list(FACT_COLUMNS),
                                  "stretch": [1], "col_widths": [120, 0],
                                  "font_size": 10,
                                  "tooltip": "The catalog's own words about this "
                                             "addon, and where on GitHub it came "
                                             "from"},
                             ]},
                         ]},
                    ]},
                ]},
                _job_view(),
            ]},
            {"kind": "multi", "id": "log", "collapsed": True, "readonly": True,
             "placeholder": "Every step of the last install, as it happened",
             "tooltip": "The same lines go into the program's own log"},
        ]},
        {"kind": "row", "id": "actionrow", "children": [
            {"kind": "button", "id": identifier, "text": text,
             "on": ["clicked"]} for identifier, text in BUTTONS],
        },
    ]}


def manager_slug(row: dict) -> str:
    """The addon's id as it may be spelled inside a widget id.

    The same characters a catalog id is turned into a module name with, so the
    id of a control says which addon it belongs to and can be read back whole.
    """
    return clean_id(one_line(row.get("id")))


def mid(base: str, slug: str) -> str:
    """One control's id on one addon's page: the shared name and the addon's."""
    return f"{base}{MANAGER_SEP}{slug}"


def split_mid(identifier: str) -> tuple[str, str]:
    """The control a page id names, and the addon slug it was built with."""
    base, found, slug = str(identifier or "").partition(MANAGER_SEP)
    return (base, slug) if found else (base, "")


def manager_page(row: dict, slug: str = "") -> dict:
    """One addon's page: its own switches, its own buttons, and the app's say.

    What the table used to keep behind a picked row is on screen from the moment
    the page opens, so nothing has to be selected before something can be changed.
    The page is built out of the record the app last answered for, which means a
    transport the manifest never named is not drawn at all rather than drawn and
    greyed, and every switch's tick and every button's words are already right -
    a page costs no round trip to put together, and none to look at.

    Every control carries the addon's slug in its id, because all these pages
    share the tab's one registry and two addons called "Run it" would otherwise
    be the same widget to ``on_widget``. The caller passes the slug it has filed
    the record under, so a second addon whose id cleans to the same name still
    owns controls nobody else can answer for.

    The column is folded into a ``scroll`` because a page carries a heading, up to
    six switches, a paragraph of reason and seven buttons: on a short window that
    is more than the tab has room for, and the last row of buttons being cut off
    is worse than a scroll bar.
    """
    slug = str(slug or "") or manager_slug(row)
    wants = manager_transports(row)
    switches: list[dict] = [
        {"kind": "check", "id": mid("mg_enable", slug), "label": "Run it",
         "checked": bool(row.get("enabled")), "on": ["toggled"],
         "tooltip": "The addon's own switch: off stops it and takes its tabs "
                    "away, on starts it again and gives them back"},
        {"kind": "check", "id": mid("mg_popout", slug), "label": "Force popout",
         "checked": bool(row.get("popout_switch")),
         "enabled": bool(row.get("popout_switchable")), "on": ["toggled"],
         "tooltip": "Off, its tabs sit in the main window's bar. On, "
                    "every page it owns is moved into a window of its "
                    "own, which can be raised, resized and put on a "
                    "Desktop link of its own. Greyed out when the "
                    "addon's own manifest has already said"},
        {"kind": "check", "id": mid("mg_token", slug),
         "label": "Use the global git token",
         "checked": bool(row.get("git_token")), "on": ["toggled"],
         "tooltip": "On, the addon is handed the token kept in "
                    "Settings > Connection, which is what reaches a "
                    "private repository and lifts the anonymous rate "
                    "limit. Off, it asks GitHub as anybody else would"},
    ]
    for name, gate in MANAGER_TRANSPORTS:
        if name not in wants:
            continue
        switches.append(
            {"kind": "check", "id": mid(gate, slug),
             "label": f"Use {name.upper()}",
             # A record from an app that answers no gate at all means the addon
             # use the transport it declared, which is a fresh install's shape.
             "checked": bool(row.get(f"{name}_allowed", True)),
             "on": ["toggled"],
             "tooltip": f"Off: this addon is refused every {name.upper()} job, "
                        f"even with the main connection open and {name.upper()} "
                        f"on in the Connection panel. On: it may use "
                        f"{name.upper()}, along with that app-wide switch and "
                        f"its own switch above"})
    switches.append(
        {"kind": "check", "id": mid("mg_own", slug),
         "label": "Open a connection of its own",
         "checked": bool(row.get("own_connection")),
         "enabled": bool(row.get("own_switchable")), "on": ["toggled"],
         "tooltip": "Off: the addon shares the app's one main "
                    "connection, and can use it no earlier than that "
                    "connection is open. On: it opens a connection of "
                    "its own for each call, so it works whether or not "
                    "the main one is up, and through "
                    "api.device.bind() it can reach a different "
                    "device. Greyed out for an addon whose manifest "
                    "already picked one or the other"})
    body = {"kind": "column", "expand": True, "children": [
        {"kind": "row", "children": [
            {"kind": "image", "id": mid("mg_icon", slug), "size": 48},
            {"kind": "column", "expand": True, "children": [
                {"kind": "label", "id": mid("mg_name", slug),
                 "text": manager_caption(row), "style": _DETAIL_TITLE,
                 "word_wrap": True},
                {"kind": "label", "id": mid("mg_sub", slug),
                 "text": manager_summary(row), "style": _DETAIL_SUB,
                 "word_wrap": True},
            ]},
        ]},
        {"kind": "label", "text": MANAGER_HEADING, "style": _DETAIL_HEAD},
        {"kind": "form", "children": switches},
        {"kind": "label", "id": mid("mg_note", slug), "text": manager_note(row),
         "word_wrap": True, "expand": True, "style": _DETAIL_SUB},
        {"kind": "row", "children": [
            {"kind": "button", "id": mid(identifier, slug),
             "text": ("Raise window" if (identifier == "mg_window" and
                                         (row.get("popout") or
                                          row.get("window_open")))
                      else "Remove shortcut" if (identifier == "mg_shortcut" and
                                                 row.get("shortcut"))
                      else text),
             "on": ["clicked"]} for identifier, text in MANAGER_BUTTONS],
        },
    ]}
    return {"kind": "scroll", "children": [body]}


def manager_spec() -> dict:
    """The Addon manager page: a header, and one sub-tab per installed addon.

    The tabs are not in this spec. What is installed here is only known once the
    app has answered, so :func:`refresh_manager` builds the pages into
    ``MANAGER_TABS`` and lays them out again whenever the record changes; all that
    is fixed is the header and the bar they hang on. An addon that is switched off
    keeps its tab with a grey caption rather than losing it, because the page that
    says it is off is the page that switches it back on - disabling the tab would
    hide the only answer.

    The Show the Addon Dev tab check sits in the header with the count because it
    is an addon setting like the rest of the page - it used to sit on the app's own
    Startup page - and the count is the number of tabs below it.
    """
    return {"kind": "column", "label": PAGE_MANAGER, "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Addon manager", "style": BANNER_STYLE},
            {"kind": "label", "id": "managerstatus",
             "text": "Nothing installed outside the program.", "word_wrap": True,
             "expand": True, "style": _STATUS_TEXT},
            {"kind": "label", "id": "managercount", "text": "",
             "style": _STATUS_STYLE,
             "tooltip": "How many addons in the addons folder did not come "
                        "inside the program"},
            {"kind": "check", "id": "devtab", "text": "Show the Addon Dev tab",
             "checked": True,
             "tooltip": "The tab an addon is written, tested and compiled in. "
                        "It is hidden rather than removed, so ticking this again "
                        "brings it back at once",
             "on": ["toggled"]},
        ]},
        {"kind": "tabs", "id": MANAGER_TABS, "children": [],
         "on": ["changed"],
         "tooltip": "One tab per addon this profile runs that did not come "
                    "inside the program; a grey name is one that is switched off"},
    ]}


def tab_spec() -> dict:
    """The tab's two pages under one sub-bar: the store, and the Addon manager.

    Only the wrapper is new - every id the store's page has always used is where
    it was, so the jobs, the log and the detail pane are reached exactly as they
    were and the manager is spec-only beside them. The sub-bar shows both captions
    because the two pages are the tab's own, not something an addon opens later.
    """
    return {"kind": "tabs", "id": SUBTABS, "children": [
        addons_page(), manager_spec(),
    ]}


def build_tab() -> None:
    """The tab as a spec; nothing here holds a widget.

    Pinned and icon-only on purpose: the store is furniture, like Settings, so it
    holds at the right edge of the bar and is recognised by its mark instead of
    taking a place in the row of tabs the user arranged. Its own two pages - the
    store and the Addon manager - sit under a sub-bar inside it, so the bar the
    user arranged is never two tabs wide here.
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
    """One line into the tab's own log and the program's log, the same words.

    While a job is on screen its words go out onto the job's console as well,
    which is what that pane is for: a step is read as it arrives rather than
    scrolled back to once the job has ended.
    """
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "log", "append", str(text))
    except AddonError:
        pass
    if job is not None and job.get("pane"):
        _job_write(text)
    api.log(text)


def show_log(open_it: bool) -> None:
    """Fold the job's log away, or pull it back up under the browsing panes.

    A folded pane keeps its fold line, so the log is reachable by dragging that
    line, or double-clicking it, as much as by the box in the header; the two are
    kept in step because the box is set from what is being asked for, and Qt only
    reports a check box when its tick really moves.
    """
    if api is None:
        return
    open_it = bool(open_it)
    shares = [LOG_SHARE[0], LOG_SHARE[1]] if open_it else [LOG_SHARE[0], 0]
    try:
        api.tabs.set_widget(TAB, LOG_PANE, "sizes", shares)
    except AddonError:
        pass
    try:
        api.tabs.set_widget(TAB, "showlog", "checked", open_it)
    except AddonError:
        pass


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
    if install_origin(id_value) != STORE_ORIGIN:
        return None
    keeps = addon_data_folder(id_value)
    return keeps if keeps.exists() else None


# --- network: standard library only ---

def profile_token() -> str:
    """The token this addon is allowed to send to GitHub, which is the profile's.

    There is one token for a profile and it is written in Settings > Connection;
    the switch on this addon's tab in the store's Addon manager decides whether
    ``api.session.git_token()`` answers with it at all. An empty string is a real
    answer rather than a failure - it means anonymous, which is what every public
    repository is read with - so a build with no api, no token and a refused call
    all fetch exactly the same way.
    """
    if api is None:
        return ""
    try:
        return clean_token(api.session.git_token())
    except (AddonError, AttributeError, TypeError):
        return ""


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
        """``(status, body)`` for one GET, retried before it is given up.

        Three tries with a longer pause each time: a momentary refusal or a
        dropped keep-alive used to cost a whole catalog read or a whole
        addon file, and an install is a dozen of those in a row.
        """
        status, body = 0, b""
        for attempt in (0, 1, 2):
            headers = {"User-Agent": "berry-manager", "Accept": accept}
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            connection = self._connection(host)
            try:
                connection.request("GET", path, headers=headers)
                answer = connection.getresponse()
                body = answer.read()
                status = int(answer.status)
                if status < 500 or attempt == 2:
                    return status, body
            except (http.client.HTTPException, OSError):
                self.close(host)
                if attempt == 2:
                    raise
            time.sleep(0.4 * (attempt + 1))
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
    """The reused connections for this job, carrying the profile's token.

    ``values`` names the source a fetch is aimed at, which is nothing to do with
    what is sent as credentials: a profile has one token and every source in it
    is read with that one. Keying the pool by the token rather than by the source
    is therefore what keeps a switched-off addon, or a profile that never wrote
    one, on the anonymous fetcher - the only kind there was before the field.
    """
    key = profile_token()
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
    sit one level above it. A store folder at a repository's own root leaves
    nothing above it to name, so for this program's own layout - the store at
    the root - the app is the folder it is known to live in.
    """
    folder = one_line(config_get(values, "store_path",
                                 MAIN_SOURCE["store_path"])).strip("/")
    return folder.rsplit("/", 1)[0] if "/" in folder else "src/berry_manager"


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
                "are limited, so put a token in Settings > Connection, switch "
                "this addon on for it, or try again later")
    return f"GitHub answered {status} for {path}"


# --- reading the catalog ---

def _search_haystack(entry: dict) -> str:
    """The lowercased words one catalog row is matched against by the filter.

    Building it once, when the row is read, saves re-lowering every field and
    recomputing the connection words for every row on every keystroke.
    """
    parts = [entry.get("name"), entry.get("id"), entry.get("author"),
             entry.get("description"), entry.get("source"),
             connection_words(entry), " ".join(entry.get("requires") or [])]
    return " ".join(one_line(part).lower() for part in parts)


def parse_catalog(body: Any) -> list[dict]:
    """index.json as a tidy list of entries; a bare list is accepted too.

    What an addon needs on the device comes over in two keys the generator writes
    from the addon's own manifest: ``requires`` the transports it asks for, and
    ``connection`` how it reaches the device at all. Both are read through the
    same helpers the app uses, so a listing and an addon's own row say the same
    thing about the same addon.
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
        entry = {
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
        }
        # The search haystack is filled in again in merge_catalogs, once the
        # publishing source is known; here it covers everything the row carries.
        entry["search"] = _search_haystack(entry)
        out.append(entry)
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
    """The version written down beside an install when it was put there.

    A loose program keeps its manifest out where the addons folder can see it;
    a folder program carries it inside the folder, under the same name.
    """
    name = clean_id(id_value)
    if not name:
        return ""
    folder = addons_dir()
    for home in (folder / name, folder):
        path = addon_manifest_path(home, name)
        if path is not None:
            return one_line(read_config_json(path).get("version"))
    return ""


def install_origin(id_value: str) -> str:
    """Where an install came from, in whichever shape it sits in ``addons``.

    The folder is read first because it is where a store install lands now;
    a loose program's manifest, one level up, is the older shape.
    """
    name = clean_id(id_value)
    if not name:
        return ""
    folder = addons_dir()
    for home in (folder / name, folder):
        origin = addon_origin(home, name)
        if origin:
            return origin
    return ""


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
    repository does not have is said and left out: a harness of one module more is
    still worth running the test over. A dotted module name is the file under the
    folder of the package it belongs to, which is made on the way there.
    """
    if job is None or job.get("harness") is None:
        return False
    folder = Path(str(job["harness"]))
    got = False
    for name in _APP:
        relative = name.replace(".", "/") + ".py"
        target = folder / relative
        if target.is_file():
            continue
        if _fetch_file(values, relative, target, app_folder(values)):
            note(f"{relative} was not there to fetch")
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
    were only a means to the program that got installed, so they are cleared;
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
            values = source_values(source)
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

    Rate limits, a private repository the profile has no token for and an
    unreachable network all leave the row without a commit line rather than a wall
    of text, because provenance is a nicety that the store works perfectly well
    without.
    """
    if api is None:
        return
    source = entry.get("src")
    if not isinstance(source, dict):
        return
    entry["asking"] = True
    values = source_values(source)
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
    repository published it - and none of it reaches GitHub to do it. The row's
    haystack is built once when the catalog is read, so a keystroke is one test.
    """
    if not needle:
        return True
    return needle in (entry.get("search") or _search_haystack(entry))


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
        if installed(entry["id"]):
            row["check"] = bool(enabled_of(entry["id"]))
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


def selected_ids() -> list[str]:
    """Every row a person has picked, in list order; the described one when none.

    A batch is taken from the whole selection, so ctrl-clicking several rows
    hands Install and Update all of them at once. A repaint that drops Qt's
    selection without dropping the pane still answers with the addon showing.
    """
    if api is None:
        return [clean_id(picked)] if picked else []
    try:
        chosen = api.tabs.widget_value(TAB, "list", "selected")
    except AddonError:
        chosen = None
    if isinstance(chosen, (list, tuple)):
        texts = [str(item) for item in chosen]
    elif chosen:
        texts = [str(chosen)]
    else:
        texts = []
    ids = []
    for text in texts:
        entry = find_entry(text.split("\t")[0])
        if entry is not None and entry["id"] not in ids:
            ids.append(entry["id"])
    if not ids and picked:
        ids = [clean_id(picked)]
    return ids


def enabled_of(id_value: str) -> bool | None:
    """Whether an installed addon's switch is on, as the app's record has it.

    The store's list carries that switch as a check box, so the rows are drawn
    from the same answer the toggle writes; nothing here reads settings files.
    """
    if api is None:
        return None
    try:
        state = api.session.addon_state(addon_id=id_value)
    except Exception:
        return None
    if not isinstance(state, dict) or not state.get("installed"):
        return None
    return bool(state.get("enabled"))


def toggle_installed(payload: dict) -> None:
    """A row's check box was moved: switch that installed addon on or off here.

    The list's box is the Addon manager's switch, not a copy of it: the same
    record is written and the same load or stop follows, so ticking the box
    starts the addon and unticking it stops it. The store then takes the tab
    back for itself, because an addon waking up may claim one, and a tick that
    came in during a job is put right by the next repaint - the job owns the
    switches until it ends.
    """
    if api is None or working:
        return
    data = payload if isinstance(payload, dict) else {}
    entry = find_entry(one_line(data.get("text")).split("\t")[0])
    if entry is None:
        return
    if not installed(entry["id"]):
        show_entries()          # only an installed addon has a switch to move
        return
    want = bool(data.get("checked"))
    try:
        api.session.set_addon_running(addon_id=entry["id"], running=want)
    except Exception as error:
        say(f"{entry['id']} could not be switched "
            f"{'on' if want else 'off'}: {error}", bad=True)
        show_entries()
        return
    words = f"switched {entry['id']} {'on' if want else 'off'}"
    say(words)
    note(words)
    try:
        api.tabs.activate(TAB)  # loading an addon must not steal the store tab
    except Exception:
        pass
    show_entries()


# --- the Addon manager page ---

def manager_say(text: str, bad: bool = False) -> None:
    """One line on the manager's own status, kept apart from the store's."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "managerstatus", "text", str(text))
        api.tabs.set_widget(TAB, "managerstatus", "style",
                            _STATUS_BAD if bad else _STATUS_OK)
    except AddonError:
        pass


def manager_state(row: dict) -> tuple[str, str]:
    """One addon as the words said under its name, and the colour they wear."""
    if one_line(row.get("error")):
        return "failed to start", _STATE_WARN
    if row.get("running"):
        return "running", _STATE_OK
    if not row.get("enabled"):
        return "switched off", _STATE_OFF
    return "not running", _STATE_OFF


def manager_popout_words(row: dict) -> str:
    """The second half of that line: where this addon's tabs are right now.

    A window that was closed is named as closed rather than left out, because the
    addon behind it is still running and the page's first button is what brings it
    back - the same button reads *Raise window* for it.
    """
    if row.get("window_open"):
        return "  \u25b8 in its window"
    if row.get("popout"):
        return "  \u25b8 window closed"
    return ""


def manager_summary(row: dict) -> str:
    """The one line under an addon's name: version, author, and how it stands.

    The four columns the table spent on this are one line here, since an addon's
    page only ever describes the one addon and needs no headings to be read.
    """
    words, _tint = manager_state(row)
    parts = [one_line(row.get("version")) or "no version",
             one_line(row.get("author")) or "no author",
             words + manager_popout_words(row)]
    return "  \u00b7  ".join(parts)


def manager_caption(row: dict) -> str:
    """The name the tab shows for one addon, with its id when two share a name.

    A tab is looked up by its caption as much as by its place on the bar, so the
    caption has to be one that names exactly one addon - which two addons of the
    same name would not be.
    """
    caption = one_line(row.get("name")) or one_line(row.get("id"))
    same = [one for one in managed
            if (one_line(one.get("name")) or one_line(one.get("id"))) == caption]
    return f"{caption} ({one_line(row.get('id'))})" if len(same) > 1 else caption


def manager_row_for(text: Any) -> dict | None:
    """The row a caption, an id, a name or a widget slug stands for; not a stray.

    A page reports the addon it belongs to through the slug in its control ids
    rather than through words, so the slug is tried as well - which is also what
    lets an addon be named by the id its folder is filed under.
    """
    wanted = one_line(text)
    if not wanted:
        return None
    row = manager_slugs.get(wanted) or manager_rows.get(wanted)
    if row is not None:
        return row
    lowered = wanted.lower()
    for one in managed:
        if one_line(one.get("id")).lower() == lowered:
            return one
    for one in managed:
        if manager_slug(one) == wanted:
            return one
    for one in managed:
        if manager_caption(one).lower() == lowered:
            return one
    return None


def manager_transports(row: dict) -> list[str]:
    """Which of the two transports this addon's manifest asked for, in switch order.

    A page's switches are drawn off this rather than off the record's gates,
    because an addon that never named SFTP has no opinion about it and would only
    be confused by a switch for it.
    """
    needs = {one_line(one).lower() for one in (row.get("requires") or [])}
    return [name for name, _identifier in MANAGER_TRANSPORTS if name in needs]


def manager_note(row: dict) -> str:
    """What the switches mean for this addon, in the row's own words."""
    words: list[str] = []
    declared = one_line(row.get("popout_declared")).lower()
    if declared:
        words.append(f"Its manifest says popout {declared}, so there is no "
                     f"force-popout switch here for the app to offer; only the "
                     f"addon's own api.window calls can move its tabs.")
    elif not row.get("popout_switchable"):
        words.append("It came inside the program, so its tabs stay in the main "
                     "window unless its own code asks for a window of their own.")
    elif row.get("popout"):
        words.append("Its tabs live in a window of their own; closing that "
                     "window leaves the addon running, and the first button "
                     "under this note brings the window back.")
    else:
        words.append("Its tabs sit in the main window's bar.")
    if row.get("shortcut"):
        words.append(f"A Desktop link for it is already there: "
                     f"{one_line(row.get('shortcut'))}")
    else:
        words.append("A Desktop link would start this addon on its own, loading "
                     "the profile's main connection only when the addon rides it.")
    words.append("With the token switch on it is handed the git token kept in "
                 "Settings > Connection, which is what a private repository "
                 "needs; off, it asks GitHub as anybody else would.")
    needs = manager_transports(row)
    if needs:
        words.append("It reads the device over "
                     + " and ".join(one.upper() for one in needs) + "; the "
                     "switches above are its own gates on that traffic, and the "
                     "app-wide one in the Connection panel has to be on too.")
    else:
        words.append("Its manifest asks for no transport, so it reaches no device "
                     "and there is nothing here to gate.")
    if row.get("own_switchable"):
        words.append("It may ride either connection, so the own-connection switch "
                     "is its own to move: the app's session, or a fresh "
                     "connection per call.")
    else:
        kind = one_line(row.get("connection")).lower() or "none"
        words.append(f'Its manifest said "connection": "{kind}", so which '
                     "connection it rides is not here to choose.")
    return " ".join(words)


def manager_pages() -> list[str]:
    """The captions the manager's bar is holding, left to right.

    The pages are built as the app's record changes rather than as the tab is
    built, so this is the only honest answer to how many of them there are - and
    the only way to find out how many have to be taken away before a new row of
    them can be laid down.
    """
    if api is None:
        return []
    try:
        held = api.tabs.widget_value(TAB, MANAGER_TABS, "pages")
    except AddonError:
        return []
    return [one_line(one) for one in (held or [])]


def manager_open() -> int:
    """Which page the manager's bar is showing, or -1 while it shows none."""
    if api is None:
        return -1
    try:
        place = api.tabs.widget_value(TAB, MANAGER_TABS, "index")
    except (AddonError, TypeError, ValueError):
        return -1
    return int(place) if isinstance(place, int) else -1


def manager_picture(row: dict) -> str:
    """The mark an addon's page wears: its own picture, or the store's default.

    The same lookup the list uses, so a page and its row agree; an addon nobody
    has fetched a picture for simply wears the default rather than a blank box.
    """
    try:
        return icon_of(row)
    except Exception:
        return ""


def manager_unique_slug(base: str) -> str:
    """A slug no other page is using, so two addons cannot share a control.

    Every control on a page is named from its slug, and all the pages write into
    the tab's one registry, so two ids that clean to the same name - "my-addon"
    and "my_addon" - would be the same switch twice over. The second of them is
    numbered, and its page is built from the numbered slug like everything else.
    """
    slug = base or "addon"
    if slug not in manager_slugs:
        return slug
    extra = 2
    while f"{base}_{extra}" in manager_slugs:
        extra += 1
    return f"{base}_{extra}"


def refresh_manager(_checked: bool = False) -> None:
    """Lay the addon pages out again, straight from the app's own record.

    One call answers for every addon in the folder, and what is listed is what
    the app says rather than what this machine's disk looks like, so an addon put
    in since the last look is here without a rescan. The addons that came inside
    the program are left out - they keep their own pages in Settings - and each of
    the rest gets a tab of its own, so the switches are on screen for the addon
    already opened rather than hidden under a row that has to be picked first.

    The pages are laid out whole: taken away and built again, because a page is
    only ever as true as the record it was drawn from and a toggle moves that
    record. The bar is put back on the page it was showing, and a repaint reports
    its own tab change no more than a person clicking about does - the flag round
    the rebuild is what keeps the two apart.

    An addon that is switched off keeps its tab, drawn grey: the page that says it
    is off is the page that switches it back on, so a caption being faded is a
    fact to be read and not a door to be shut.
    """
    global managed, manager_rows, manager_slugs, manager_order
    global manager_picked, manager_building
    if api is None:
        return
    try:
        rows = api.session.addons()
    except Exception as error:
        manager_say(f"the app would not say what is installed: {error}", bad=True)
        return
    managed = [dict(row) for row in (rows or []) if not row.get("settings_page")]
    was_open = manager_open()
    was_pages = len(manager_pages())
    # Whatever the open settings pages hold is read before the bar is emptied,
    # so a toggle on an addon's page does not cost the person half-typed
    # answers in a form one tab over.
    held_configs = {slug: config_current_values(slug, record)
                    for slug, record in config_pages.items()}
    manager_rows, manager_slugs, manager_order = {}, {}, []
    keep = was_open if 0 <= was_open < len(managed) else 0
    # The bar is the addon pages and, after them, one settings page per addon
    # being edited: an index past the addon run is a settings tab, and it is
    # put back on its own page rather than on the first addon's.
    was_addons = max(0, was_pages - len(config_pages))
    if was_pages > was_addons and was_open >= was_addons:
        place = len(managed) + min(was_open - was_addons,
                                   max(0, len(config_pages) - 1))
    else:
        place = keep
    manager_building = True
    try:
        for _page in range(was_pages):
            _detail(MANAGER_TABS, "remove_page", 0)
        for row in managed:
            slug = manager_unique_slug(manager_slug(row))
            caption = manager_caption(row)
            manager_slugs[slug] = row
            manager_rows[caption] = row
            manager_order.append(slug)
            _detail(MANAGER_TABS, "add_page",
                    {"label": caption, "spec": manager_page(row, slug),
                     "current": False})
            _detail(mid("mg_icon", slug), "image", manager_picture(row))
        for slug in list(config_pages):
            held = held_configs.get(slug) or None
            _detail(MANAGER_TABS, "add_page",
                    {"label": config_pages[slug]["caption"],
                     "spec": config_page_spec(slug, config_pages[slug], held),
                     "current": False})
        _detail(MANAGER_TABS, "tab_colors",
                ["" if row.get("enabled") else _STATE_OFF for row in managed])
        if managed or config_pages:
            _detail(MANAGER_TABS, "current", place)
    finally:
        manager_building = False
    manager_picked = (one_line((manager_slugs.get(manager_order[keep])
                                or {}).get("id")) if managed else "")
    _detail("managercount", "text", f"{len(managed)} managed"
            if managed else "nothing installed")
    show_devtab_toggle()
    if managed:
        row = manager_slugs[manager_order[keep]]
        words, _tint = manager_state(row)
        manager_say(f"{manager_caption(row)}: {words}")
    else:
        manager_say("Nothing installed outside the program.")


def show_devtab_toggle() -> None:
    """The header's Addon Dev check, put in step with what the profile says."""
    wanted = True
    if api is not None:
        try:
            answer = api.session.app_setting("addon_dev_enabled")
            wanted = True if answer is None else bool(answer)
        except Exception:
            wanted = True                 # a profile that will not answer is on
    _detail("devtab", "checked", wanted)


def set_devtab(payload: dict) -> None:
    """The header's check: show the Addon Dev tab again, or hide it for now.

    The key is the app's own and is written through it, so the tab appears or goes
    at once rather than at the next start - this is the only place it is written
    now that the switch has left the app's Startup page.
    """
    if api is None:
        return
    want = bool((payload or {}).get("checked"))
    try:
        api.session.set_app_setting("addon_dev_enabled", want)
    except Exception as error:
        manager_say(f"the Addon Dev tab could not be switched: {error}", bad=True)
        show_devtab_toggle()
        return
    words = ("the Addon Dev tab is being shown again" if want
             else "the Addon Dev tab has been hidden")
    show_devtab_toggle()
    manager_say(words)
    note(words)


def manager_row_at(index: Any) -> dict | None:
    """The row whose tab sits at that place on the manager's bar.

    The bar counts from zero and an addon may be filed under a numbered slug, so
    the order the pages were laid down in is the only thing a position can be read
    against - and it is the same order the app answered for.
    """
    try:
        place = int(index)
    except (TypeError, ValueError):
        return None
    if not (0 <= place < len(manager_order)):
        return None
    return manager_slugs.get(manager_order[place])


def manager_tab_shown(payload: dict) -> None:
    """One addon's tab was opened: say which, and how it stands.

    Nothing is asked of the app - the page was built from the row the last paint
    got - so moving about the bar costs no round trip. A repaint lands here too,
    while it is still laying the pages out, and that is the rebuild's own doing
    rather than the person's, so it is dropped.
    """
    global manager_picked
    if manager_building:
        return
    data = payload if isinstance(payload, dict) else {}
    row = manager_row_at(data.get("index"))
    if row is None:
        row = manager_row_for(data.get("text"))
    if row is None:
        return
    manager_picked = one_line(row.get("id"))
    words, _tint = manager_state(row)
    manager_say(f"{manager_caption(row)}: {words}")


def manager_refused(words: str) -> None:
    """A switch that is not this addon's to move: say so, and mean it.

    The pages are laid out first and the reason written after, because the repaint
    ends by stating how the open page's addon stands - said the other way round,
    the refusal would be painted straight over before it could be read.
    """
    refresh_manager()
    manager_say(words, bad=True)


def manager_toggle(ident: str, slug: str, payload: dict) -> None:
    """One switch on an addon's own page, written through the app.

    Every one of them is the app's own record rather than anything the store
    keeps: the first starts or stops the addon, the second moves its tabs in or out
    of a window of their own, the third decides whether the profile's git token is
    its business at all, the next two are the addon's gates over the traffic its
    manifest asked for, and the last puts it on a connection of its own rather than
    on the app's session. Which addon is meant is read from the slug its page's
    control ids carry, so a switch on the wrong page cannot be answered for the
    wrong addon. A switch the addon has not got is refused with the reason on the
    status line, and the pages are laid out again either way so what is on screen
    is the record rather than what was clicked.
    """
    if api is None or working:
        return
    row = manager_slugs.get(str(slug or "")) or manager_row_for(manager_picked)
    if row is None:
        manager_say("open one of the addon tabs first", bad=True)
        return
    addon_id = one_line(row.get("id"))
    want = bool((payload or {}).get("checked"))
    try:
        if ident == "mg_enable":
            api.session.set_addon_running(addon_id=addon_id, running=want)
            words = f"switched {addon_id} {'on' if want else 'off'}"
        elif ident == "mg_popout":
            moved = bool(api.session.set_addon_popout(addon_id=addon_id, on=want))
            if not moved:
                manager_refused(f"{addon_id} has no force-popout switch to move - "
                                f"its manifest names its own state, or it came "
                                f"inside the program")
                return
            words = (f"{addon_id} is now in a window of its own" if want
                     else f"{addon_id}'s tabs are back in the main window")
        elif ident == "mg_token":
            api.session.set_addon_git_token(addon_id=addon_id, on=want)
            words = (f"{addon_id} may read the profile's git token" if want
                     else f"{addon_id} will ask GitHub anonymously")
        elif ident in MANAGER_GATE_IDS:
            gate = MANAGER_GATE_IDS[ident]
            moved = bool(api.session.set_addon_transport(addon_id=addon_id,
                                                        transport=gate, on=want))
            if not moved:
                manager_refused(f"{addon_id} has no {gate.upper()} switch to move - "
                                f"its manifest does not ask for that transport")
                return
            words = (f"{addon_id} may use {gate.upper()} again" if want
                     else f"{addon_id} is refused {gate.upper()}")
        elif ident == "mg_own":
            moved = bool(api.session.set_addon_own_connection(addon_id=addon_id,
                                                             on=want))
            if not moved:
                manager_refused(f"{addon_id} has no own-connection switch to move - "
                                f"its manifest already said which connection it "
                                f"rides")
                return
            words = (f"{addon_id} now opens a connection of its own" if want
                     else f"{addon_id} rides the app's main connection again")
        else:
            return
    except Exception as error:
        manager_refused(f"{addon_id} could not be switched: {error}")
        return
    try:
        api.tabs.activate(TAB)        # an addon waking up must not steal the page
    except Exception:
        pass
    refresh_manager()
    manager_say(words)
    note(words)


def reveal(path: Path) -> None:
    """Show one folder in the machine's own file browser.

    A compiled store carries no Qt of its own, so the ask goes to the shell: the
    Windows ``startfile`` call where there is one, ``xdg-open`` everywhere else.
    Neither is waited on - a file browser is not the store's business once it has
    been opened - and one that will not start is said on the status line.
    """
    where = str(path)
    opener = getattr(os, "startfile", None)
    try:
        if callable(opener):
            opener(where)
            return
        subprocess.Popen(["xdg-open", where], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as error:
        manager_say(f"{where} could not be shown: {error}", bad=True)


def manager_show(row: dict) -> None:
    """Bring the addon up: its own window when it has one, its tab when not.

    The button's words come from the same answer, so a person is never left
    guessing which of the two a press means, and an addon that owns neither says so
    rather than doing nothing quietly.
    """
    addon_id = one_line(row.get("id"))
    if row.get("popout") or row.get("window_open"):
        try:
            api.session.show_addon_window(addon_id=addon_id)
        except Exception as error:
            manager_say(f"{addon_id}'s window could not be raised: {error}",
                        bad=True)
            return
        # The window is where the tabs are now, so the page has to be told - and
        # the words above are written after the repaint, which would otherwise
        # state the open page's condition straight over them.
        refresh_manager()
        manager_say(f"{addon_id}'s window is up")
        return
    names = [one_line(name) for name in (row.get("tabs") or []) if one_line(name)]
    if not names:
        manager_say(f"{addon_id} owns no tab and no window of its own", bad=True)
        return
    try:
        api.tabs.activate(names[0])
    except Exception as error:
        manager_say(f"{addon_id}'s tab could not be shown: {error}", bad=True)
        return
    manager_say(f"{addon_id}'s tab is on screen")


def manager_restart(addon_id: str) -> None:
    """Stop the addon and start it again, through the app's own switch twice.

    Off and on are both the record this page reads, so a restart leaves nothing
    half-done when the second half fails, and the store takes its page back after
    it because an addon waking up may claim a tab for itself.
    """
    if working:
        return
    try:
        api.session.set_addon_running(addon_id=addon_id, running=False)
        api.session.set_addon_running(addon_id=addon_id, running=True)
        api.tabs.activate(TAB)
    except Exception as error:
        manager_say(f"{addon_id} could not be restarted: {error}", bad=True)
        refresh_manager()
        return
    words = f"restarted {addon_id}"
    refresh_manager()
    manager_say(words)
    note(words)


def manager_shortcut(addon_id: str, want: bool) -> None:
    """Put, or take away, the Desktop link that starts this addon on its own.

    The link is the app's to write - it is the same one ``api.window.set_shortcut``
    asks for - and what comes back is whether the file is there afterwards, so a
    desktop that refused the write is reported rather than assumed.
    """
    try:
        made = bool(api.session.set_addon_shortcut(addon_id=addon_id, on=want))
    except Exception as error:
        manager_say(f"{addon_id}'s Desktop link could not be written: {error}",
                    bad=True)
        return
    if want:
        words = (f"wrote a Desktop link for {addon_id}" if made
                 else f"no Desktop link could be written for {addon_id}")
    else:
        words = (f"{addon_id}'s Desktop link is still there" if made
                 else f"took the Desktop link away from {addon_id}")
    refresh_manager()
    manager_say(words)
    note(words)


#: The control each kind of declared config key is edited with, in the settings
#: dialog: the same six shapes the app builds its own config panels from, in the
#: spec language this page already speaks.
CONFIG_WIDGETS = {"text": "line", "number": "spin", "bool": "check",
                  "choice": "combo", "list": "line", "multiline": "multi"}


#: The id the raw JSON of a settings file is edited under, for the one addon that
#: declared no keys at all and still keeps a file.
CONFIG_RAW = "rawconfig"


def config_leaf(field: dict, value: Any, ident: str = "") -> dict:
    """One declared config key, as the leaf of a spec that edits it.

    The key is the leaf's id, so what is read back is taken under the name the
    addon itself wrote rather than under a position in a list; a page of the
    manager's bar passes ``ident`` instead, the key joined to the addon's slug,
    since every page on that bar shares the tab's one registry.
    The kinds are the six a ``fields.json`` may name: a choice carries its own
    entries, a number the range it will accept, a list the words that say how it is
    typed, and every one of them wears the addon's hint as a tool tip.
    """
    kind = one_line(field.get("kind")).lower() or "text"
    leaf = {"kind": CONFIG_WIDGETS.get(kind, "line"),
            "id": str(ident or "") or one_line(field.get("key")),
            "label": one_line(field.get("label")) or one_line(field.get("key"))}
    if kind == "choice":
        leaf["items"] = [one_line(choice) for choice in (field.get("choices") or [])
                         if one_line(choice)]
    elif kind == "number":
        try:
            leaf["minimum"] = int(field.get("minimum", 0))
            leaf["maximum"] = int(field.get("maximum", 1000000))
        except (TypeError, ValueError):
            leaf["maximum"] = 1000000
    elif kind == "list":
        leaf["placeholder"] = "comma separated"
    if field.get("hint"):
        leaf["tooltip"] = one_line(field.get("hint"))
    if kind == "bool":
        leaf["checked"] = bool(value)
    elif isinstance(value, (list, tuple)):
        leaf["value"] = ("\n".join(str(one) for one in value) if kind == "multiline"
                         else ", ".join(str(one) for one in value))
    elif value is not None:
        leaf["value"] = value
    return leaf


def config_answer(field: dict, answer: Any) -> Any:
    """What one dialog control holds, in the shape the config file should keep.

    The file is what the addon reads, so a list of words stays a list and a number
    stays a number instead of going back as the text the control shows - the same
    six conversions the app's own config panel makes, done here against what a
    dialog hands back by id.
    """
    kind = one_line(field.get("kind")).lower() or "text"
    if kind == "bool":
        return bool(answer)
    if kind == "number":
        try:
            return int(answer)
        except (TypeError, ValueError):
            return 0
    if kind == "list":
        return [part.strip() for part in str(answer or "").split(",") if part.strip()]
    if kind == "multiline":
        return [line.strip() for line in str(answer or "").splitlines() if line.strip()]
    return str(answer or "").strip()


def config_page_spec(slug: str, record: dict, values: dict | None = None) -> dict:
    """One addon's settings form, as a page of the manager's own bar.

    The same six controls the form has always been built out of, under the same
    labels, but wearing the addon's slug in their ids like every other control on
    this bar - and shut off with a save and a cancel that belong to the page, so
    editing an addon's settings is one more tab rather than a window of its own.
    An addon that declared no keys is offered the JSON text of its file itself.
    """
    held = values if isinstance(values, dict) else record.get("values") or {}
    fields = record.get("fields") or []
    if fields:
        body = {"kind": "form", "children": [
            config_leaf(field, config_get(held, one_line(field.get("key"))),
                        mid(f"cfg_{one_line(field.get('key'))}", slug))
            for field in fields]}
    else:
        body = {"kind": "multi", "id": mid(CONFIG_RAW, slug), "font_size": 10,
                "value": str(held.get(CONFIG_RAW) or record.get("raw_text")
                             or "{}")}
    return {"kind": "scroll", "children": [{"kind": "column", "children": [
        {"kind": "label", "text": f"{record.get('title')}\n{record.get('path')}",
         "word_wrap": True, "style": _DETAIL_SUB},
        body,
        {"kind": "row", "children": [
            {"kind": "button", "id": mid("cfg_save", slug), "text": "Save",
             "tooltip": "Write what these controls hold to the addon's own "
                        "settings file", "on": ["clicked"]},
            {"kind": "button", "id": mid("cfg_cancel", slug), "text": "Cancel",
             "tooltip": "Close this form without writing anything",
             "on": ["clicked"]},
        ]},
    ]}]}


def config_current_values(slug: str, record: dict) -> dict:
    """What one open settings page holds, read before a repaint takes it away.

    Every control answers to ``value`` - the tick of a check, the number in a
    spin, the text of a line - and anything the tab cannot answer for keeps the
    value the page was opened with, so a repaint of the manager lays the same
    form back down with the person's half-written answers still in it.
    """
    held: dict = {}
    if api is None:
        return held
    for field in record.get("fields") or []:
        key = one_line(field.get("key"))
        try:
            got = api.tabs.widget_value(TAB, mid(f"cfg_{key}", slug), "value")
        except (AddonError, TypeError, ValueError):
            got = None
        held[key] = config_get(record["values"], key) if got is None else got
    if not record.get("fields"):
        try:
            raw = api.tabs.widget_value(TAB, mid(CONFIG_RAW, slug), "value")
        except (AddonError, TypeError, ValueError):
            raw = None
        held[CONFIG_RAW] = record["raw_text"] if raw in (None, "") else raw
    return held


def config_close(slug: str) -> None:
    """Take one addon's settings page off the manager's bar, by its caption."""
    record = config_pages.pop(str(slug or ""), None)
    if record is None or api is None:
        return
    _detail(MANAGER_TABS, "remove_page", record["caption"])


def manager_config(addon_id: str, slug: str = "") -> None:
    """Edit the picked addon's own settings, in a tab of the manager's bar.

    The app holds both the file and the keys, so nothing here reads
    ``addons/_data`` off the disk: ``api.session.addon_config`` answers with the
    labels, kinds and current values the addon's ``fields.json`` names, and the
    form is laid into the manager's own bar as one more page - the same tab,
    never a window of its own. Save reads what those controls hold and writes
    it through ``api.session.set_addon_config``, which tells the addon its
    settings changed; Cancel drops the page having changed nothing at all. An
    addon that declared no keys and has written no file has nothing to edit and
    is told so plainly; one that keeps a file nobody declared is offered the
    JSON text itself.
    """
    if api is None:
        return
    slug = str(slug or "") or clean_id(addon_id)
    try:
        card = api.session.addon_config(addon_id=addon_id)
    except Exception as error:
        manager_say(f"{addon_id}'s settings could not be read: {error}", bad=True)
        return
    if not isinstance(card, dict) or not card:
        manager_say(f"{addon_id} answered nothing about its own settings", bad=True)
        return
    fields = [dict(field) for field in (card.get("fields") or [])
              if isinstance(field, dict) and one_line(field.get("key"))]
    values = card.get("values") or {}
    path = one_line(card.get("path"))
    title = one_line(card.get("title")) or f"{addon_id} settings"
    if not fields and not card.get("exists"):
        manager_say(f"{addon_id} declared no settings keys and has written no "
                    f"file yet, so there is nothing to edit", bad=True)
        return
    config_close(slug)        # reopening starts from the file, not the last look
    record = {"addon_id": addon_id, "caption": f"{addon_id} settings",
              "fields": fields, "path": path, "title": title,
              "values": dict(values) if isinstance(values, dict) else {},
              "raw_text": json.dumps(values, indent=2, ensure_ascii=False)}
    config_pages[slug] = record
    try:
        api.tabs.set_widget(TAB, MANAGER_TABS, "add_page",
                            {"label": record["caption"],
                             "spec": config_page_spec(slug, record, values)})
    except Exception as error:
        config_pages.pop(slug, None)
        manager_say(f"{addon_id}'s settings could not be shown: {error}", bad=True)
        return
    manager_say(f"{addon_id}'s settings are open in a tab of their own")


def manager_config_save(slug: str) -> None:
    """Write what one addon's settings page holds, then take the page away.

    The answers are read off the live controls by their ids - the same six
    conversions the form has always made, so a list of words stays a list and a
    number stays a number - and go back through ``api.session.set_addon_config``,
    which tells the addon its settings changed. The page stays open when nothing
    could be written, so a bad JSON is corrected where it was written.
    """
    record = config_pages.get(str(slug or ""))
    if record is None or api is None:
        return
    addon_id = record["addon_id"]
    fields = record["fields"]
    if fields:
        written = {}
        for field in fields:
            key = one_line(field.get("key"))
            try:
                got = api.tabs.widget_value(TAB, mid(f"cfg_{key}", slug), "value")
            except AddonError:
                got = config_get(record["values"], key)
            written[key] = config_answer(field, got)
    else:
        try:
            raw = str(api.tabs.widget_value(TAB, mid(CONFIG_RAW, slug), "value"))
        except AddonError:
            raw = record["raw_text"]
        try:
            written = json.loads(raw or "{}")
        except ValueError as error:
            manager_say(f"{addon_id}'s settings were not written: the text is not "
                        f"one JSON object ({error})", bad=True)
            return
        if not isinstance(written, dict):
            manager_say(f"{addon_id}'s settings were not written: the file is one "
                        f"JSON object, not a list or a value", bad=True)
            return
    try:
        saved = api.session.set_addon_config(addon_id=addon_id, values=written)
    except Exception as error:
        manager_say(f"{addon_id}'s settings could not be written: {error}", bad=True)
        return
    if not isinstance(saved, dict):
        saved = {}
    if not saved and written:
        # Nothing came back to an addon that asked for keys to be put down: the
        # file was refused, and the reason is in the program's log.
        manager_say(f"{addon_id}'s settings were not written", bad=True)
        return
    path = record["path"]
    config_close(slug)
    words = f"wrote {addon_id}'s settings to {path or 'its own file'}"
    manager_say(words)
    note(words)


def manager_config_cancel(slug: str) -> None:
    """Close one addon's settings page having written nothing."""
    record = config_pages.get(str(slug or ""))
    if record is None:
        return
    config_close(slug)
    manager_say(f"{record['addon_id']}'s settings were left as they were")


def manager_delete(row: dict) -> None:
    """Take the picked addon out of the addons folder, on the store's own rules.

    The same paths the store's Delete button works from and the same answer about
    an addon's settings - the check box at the top of the store's own page, or the
    switch on its Settings page, decides that - so the two buttons cannot mean two
    different things by the same word.
    """
    global manager_picked
    if working:
        return
    addon_id = one_line(row.get("id"))
    removed = _delete_paths(addon_id)
    if not removed:
        manager_say(f"{addon_id} has nothing in the addons folder to delete",
                    bad=True)
        return
    keeps = _data_removable(addon_id)
    if keeps is not None and not api.ui.question(
            "Delete addon",
            f"Delete {addon_id} and the settings it saved in addons/_data/"
            f"{clean_id(addon_id)}/ too?\n\nAn addon installed again later will "
            "ask for everything from the start. Untick 'delete settings' at the "
            "top of the store's own page, or the switch on its Settings page, to "
            "leave them where they are."):
        manager_say("the delete was declined")
        return
    _stop_before_delete(addon_id)
    try:
        for path in removed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
    except OSError as error:
        manager_say(f"{addon_id} could not be removed: {error}", bad=True)
        return
    _tidy(addon_id)
    manager_picked = ""
    config_close(manager_slug(row))   # an open settings page goes with its addon
    words = f"deleted {addon_id} from the addons folder"
    if keeps is None:
        words += f"; its settings were left in addons/_data/{clean_id(addon_id)}/"
    else:
        words += " with its settings"
    show_entries()
    refresh_manager()
    manager_say(words)
    say(words)
    note(words)
    _reload()


def manager_action(ident: str, slug: str = "") -> None:
    """One of the buttons on an addon's page, for the addon that page is.

    Only the addons-folder button needs no addon in hand - that folder is wherever
    a person with nothing installed would want to look - so it is answered first,
    before the page is looked up, and the rest are refused plainly rather than
    silently. The page's own slug says which addon is meant; with no slug to go on
    the button falls back on the tab that is open, which is the same addon the
    person was looking at.
    """
    if api is None:
        return
    if ident == "mg_folder":
        reveal(addons_dir())
        return
    row = manager_slugs.get(str(slug or "")) or manager_row_for(manager_picked)
    if row is None:
        manager_say("open one of the addon tabs first", bad=True)
        return
    addon_id = one_line(row.get("id"))
    if ident == "mg_window":
        manager_show(row)
    elif ident == "mg_restart":
        manager_restart(addon_id)
    elif ident == "mg_data":
        reveal(addon_data_folder(addon_id))
    elif ident == "mg_config":
        manager_config(addon_id, slug)
    elif ident == "mg_shortcut":
        manager_shortcut(addon_id, not bool(row.get("shortcut")))
    elif ident == "mg_delete":
        manager_delete(row)


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


def merge_catalogs(sources: list[dict]) -> tuple[list[dict], list[str]]:
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
            rows = load_catalog(source_values(source))
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
            entry["search"] = _search_haystack(entry)
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
    refresh_manager()      # the manager's list is the app's, not the catalog's
    if not sources:
        entries, updates = [], {}
        show_entries()
        show_detail(None)
        say("no source is configured: press Sources and add a repository", bad=True)
        return
    say(f"reading {len(sources)} catalog(s)...")
    known = {one_line(entry.get("id")): entry["commit"] for entry in entries
             if isinstance(entry.get("commit"), dict)}
    entries, failed = merge_catalogs(sources)
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
    import core.addon

    return core.addon


def _point_job_at(entry: dict | None) -> None:
    """Aim the job's downloads at the source that published this entry.

    Each catalog row remembers which repository served it, so a batch drawn
    from several sources fetches every addon - its program, its assets, its
    test - from the place that actually lists it. With no source on the row,
    the first configured one answers.
    """
    values = source_config()
    source = (entry or {}).get("src")
    if not isinstance(source, dict):
        source = (read_sources(values) or [dict(MAIN_SOURCE)])[0]
    job["values"] = source_values(source)
    job["source"] = source_label(source)


# --- the job's own view, over the browsing panes ---

def _job_name(entry: dict | None) -> str:
    """What the addon a job is working on is called, as the card puts it."""
    given = entry if isinstance(entry, dict) else {}
    return one_line(given.get("name")) or one_line(given.get("id")) or "the store"


def _job_picture(entry: dict | None) -> str:
    """The addon's own mark for the card; nothing when there is no way to ask.

    The pictures are cached on disk under the store's data folder, and a job
    whose name has no picture there is drawn with none rather than stopped.
    """
    if not isinstance(entry, dict):
        return ""
    try:
        return icon_of(entry)
    except Exception:
        return ""


def _job_show(show: bool) -> bool:
    """Swap the browsing panes for the job's console, or hand them back.

    ``set_widget`` answers ``False`` for an id the tab does not hold, which is how
    a job started on a tab built without the pane - an older layout, or a bare
    test harness - finds out it has nowhere to print and carries on regardless.

    The console is meant to own the whole tab while a job runs, so the header,
    the filter line, the button row and the folded log under the panes all step
    aside with it and come back on the same call.
    """
    if api is None:
        return False
    try:
        placed = api.tabs.set_widget(TAB, "jobpane", "visible", bool(show))
        api.tabs.set_widget(TAB, "browse", "visible", not show)
    except AddonError:
        return False
    for quiet in ("headrow", "filterrow", "actionrow", "log"):
        _detail(quiet, "visible", not show)
    return bool(placed)


def _job_write(text: str) -> None:
    """One line onto the job's console, in the same words the log is given.

    The screen is a terminal rather than a text box, so a bare newline would walk
    down the page without going back to the left margin.
    """
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "jobterm", "write", f"{text}\r\n")
    except AddonError:
        pass


def _job_card(name: str = "", step: str | None = None,
              percent: int | None = None, picture: str | None = None) -> None:
    """Fill the card floating over the job's console: the addon, its step, its share.

    Only what is said is written, so a batch naming its next addon leaves the bar
    where it stands. The step is cut short: all of what the build says is already
    going onto the screen behind the card, and the card is the one thing here that
    has to stay readable over it.
    """
    if api is None or job is None or not job.get("pane"):
        return
    try:
        if name:
            api.tabs.set_widget(TAB, "jobname", "text", str(name))
        if step is not None:
            line = one_line(step)
            if len(line) > _JOB_STEP_LEN:
                line = line[:_JOB_STEP_LEN - 1].rstrip() + "\u2026"
            api.tabs.set_widget(TAB, "jobstep", "text", line)
        if percent is not None:
            api.tabs.set_widget(TAB, "jobbar", "value", int(percent))
        if picture:
            api.tabs.set_widget(TAB, "jobicon", "image", str(picture))
    except AddonError:
        pass


def _lock_pages(hold: bool) -> None:
    """Keep the job's page under the person's eyes, or let them browse again.

    Two bars are held and both are this addon's business: the window's tab bar,
    through ``api.tabs.lock``, so a build cannot be clicked away from and left
    running out of sight; and this tab's own sub-bar, so the Addon manager page
    cannot be opened while a job has the store's page. Neither hold is remembered
    anywhere - the window hands the bar back if the addon stops mid-job, and
    :func:`_finish` releases both on every ending, good or bad.

    The same two bars are also stowed from sight (``api.tabs.bar_visible`` and
    the ``bar_visible`` property), since a job that owns the whole window has
    nothing left to switch to anyway; the pages keep their places and the bars
    come back on the release.

    A tab built without the job pane has nothing to hold either, so the whole
    thing is quiet about it: an older layout, or a bare test harness, runs a job
    exactly as it always did.
    """
    if api is None:
        return
    try:
        api.tabs.lock(hold)
    except Exception:
        pass
    try:
        api.tabs.bar_visible(not hold)
    except Exception:
        pass
    _detail(SUBTABS, "locked", hold)
    _detail(SUBTABS, "bar_visible", not hold)


def _begin(verb: str, entry: dict | None = None) -> dict:
    """Start a job: the buttons grey out and one dict holds its whole state.

    The job may cover one addon or a batch: the queue holds what is still to
    come, ``total`` counts the whole of what was picked and ``done`` how many
    have landed, and ``restore`` remembers each stopped addon's switch so an
    update can put it back.

    ``pane`` says whether the tab could put the job's console on screen, and so
    whether the build's lines have anywhere to be printed on; nothing else about
    the job depends on the answer - except the holding of the bars below it, which
    is asked for with the pane, since a job nobody can see is a job nobody can
    stay with.
    """
    global working, job
    working = True
    job = {"verb": verb, "values": {}, "source": "", "stream": None,
           "on_ok": None, "fail": "", "lines": [], "harness": None,
           "widened": False, "details": {}, "id": "", "queue": [],
           "total": 1, "done": 0, "restore": {}, "did": [], "pane": False}
    _point_job_at(entry)
    _buttons(False)
    try:
        api.tabs.set_widget(TAB, "log", "value", "")
    except AddonError:
        pass
    job["pane"] = _job_show(True)
    if job["pane"]:
        _lock_pages(True)
        try:
            api.tabs.set_widget(TAB, "jobterm", "clear", True)
        except AddonError:
            pass
        _job_card(_job_name(entry), step="", picture=_job_picture(entry))
    note(f"{job['source']}: this job reads the store from here")
    # The page hears the job twice: here and at its end. The steps go to the
    # card's own line and to the console behind it, where the work is actually
    # being watched - a status line rewritten on every one of them reads as a
    # flicker in the middle of the tab while the bar on the card already counts
    # the same work up in front of the person.
    say(f"{verb} starting...")
    _progress(f"{verb} starting...", 0.0)
    return job


def _begin_job(verb: str, items: list[dict]) -> dict:
    """Start a job over a batch: the first entry works at once, the rest queue.

    One job covers the whole of what was picked. Its queue is worked through
    addon by addon, and the card's bar counts its percentage over all of
    them, so ten installs read as one job at one pace.
    """
    global job
    job = _begin(verb, items[0])
    job["queue"] = list(items)
    job["total"] = len(items)
    return job


def _finish(text: str, bad: bool = False) -> None:
    """End the job in hand: the buttons come back and one line is left up.

    A batch that stops part-way says how far it got, so the last line holds
    the whole truth of the job. The bars a job was holding are let go first,
    before anything else is touched: an ending that then failed would
    otherwise leave the window's tab bar answering to nobody.
    """
    global working, job
    if job is not None:
        total = int(job.get("total") or 1)
        done = int(job.get("done") or 0)
        if bad and total > 1:
            text = f"the batch stopped after {done} of {total}: {text}"
        was_pane = bool(job.get("pane"))
    else:
        was_pane = False
    job = None
    working = False
    if was_pane:
        _lock_pages(False)
    _buttons(True)
    if was_pane:
        # The browsing view is what the tab was showing before the job, and the
        # job's log is still underneath for anyone who wants to read it over.
        _job_show(False)
    if bad:
        # The log starts folded, but a failure is exactly when its full text is
        # wanted; the status line only ever carries one line of it.
        show_log(True)
    say(text, bad=bad)
    note(text)


def _progress(text: str, frac: float) -> None:
    """One step of the job, worn on the card that sits over its console.

    The percentage covers the whole batch: a finished addon counts as one full
    share and the one in hand contributes its part of a share, so the bar of
    ten installs creeps by tenths of a tenth and never runs back. Only the
    ending itself reaches hundred percent, because nothing here is asked to
    hold and fade - the card is on the tab the work is happening on and goes
    when the work does.

    Nothing here touches the tab's status line either: the page hears the job at
    its start and its end, and everything in between is written on the card and on
    the console behind it.
    """
    if api is None or job is None:
        return
    total = max(1, int(job.get("total") or 1))
    done = int(job.get("done") or 0)
    part = max(0.0, min(1.0, float(frac)))
    percent = min(99, int(round(100.0 * (done + part) / total)))
    line = f"{done + 1} of {total}: {text}" if total > 1 else str(text)
    _job_card(step=line, percent=percent)


def _prep_one(id_value: str) -> None:
    """Stop the addon and switch it off before its file is replaced.

    A running program holds its own file open on Windows, and an old build
    left running beside a fresh one is the worst of both; so the process
    goes first. What the switch said beforehand is remembered, so an update
    can put it back where it stood once the new program lands. A fresh
    install has nothing to stop, and says so by saying nothing.
    """
    if api is None or job is None:
        return
    try:
        state = api.session.addon_state(addon_id=id_value)
    except Exception as error:
        note(f"{id_value}'s running state could not be read: {error}")
        return
    if not isinstance(state, dict) or not state.get("installed"):
        return
    job["restore"][id_value] = bool(state.get("enabled"))
    if not state.get("enabled") and not state.get("running"):
        return
    try:
        api.session.set_addon_running(addon_id=id_value, running=False)
    except Exception as error:
        note(f"{id_value} could not be stopped and switched off first: {error}")
        return
    _progress(f"stopped and switched off {id_value} to work on it", 0.03)


def _restore_enabled(id_value: str) -> None:
    """Put an updated addon's switch back where it was before the job touched it.

    Only the record is written: the rescan that ends the job starts the
    program again when the switch is on, so an addon that was on comes back
    on and one that was off is left off - nothing is loaded from under the
    rescan.
    """
    if api is None or job is None:
        return
    prior = (job.get("restore") or {}).get(id_value)
    if prior is None:
        return
    try:
        api.session.set_addon_enabled(addon_id=id_value, enabled=bool(prior))
    except Exception as error:
        note(f"{id_value}'s switch could not be put back: {error}")
        return
    note(f"{id_value} is switched {'on' if prior else 'off'} again, as it was "
         "before the update")


def _start_next() -> None:
    """Take the next addon off the batch queue and set it building.

    Each entry is aimed at its own publishing source, stopped and switched
    off as it comes, and the interpreter and PyInstaller are checked again
    per addon rather than assumed from the last - the first of a batch may
    have brought pip along since the job started.
    """
    global job
    if job is None or not job.get("queue"):
        return
    entry = job["queue"].pop(0)
    id_value = one_line(entry.get("id"))
    job["id"] = id_value
    job["widened"] = False
    job["harness"] = None
    job["lines"] = []
    _point_job_at(entry)
    # The card follows the work: a batch is one console and one name in the
    # middle of it, changed as each addon comes off the queue.
    _job_card(_job_name(entry), picture=_job_picture(entry))
    if job.get("verb") == "update":
        job["from_version"] = installed_version(id_value)
    if int(job.get("total") or 1) > 1:
        _progress(f"working on {one_line(entry.get('name')) or id_value}", 0.0)
    _prep_one(id_value)
    where = _ensure_python()
    if not where:
        return
    ready, _hint = _dev().pyinstaller_ready()
    if not ready:
        _pip_install(where)
        return
    _compile(id_value)


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
    _progress(f"downloading {entry['filename']}...", 0.15)
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
        _progress(f"downloading asset {name} ({index} of {len(names)})...",
                  0.15 + 0.20 * index / max(1, len(names)))
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
        _progress(f"downloading {test_name} to check the addon first...", 0.38)
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
        _progress("no test is published for this addon; compiling as it is", 0.45)
        _build(id_value, source)
        return
    harness = job.get("harness")
    if harness is None:
        _progress("the addon's test cannot be run here; compiling as it is", 0.45)
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
    _progress(f"running {test_path.name} before compiling...", 0.55)
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
    if (_is_app(missing) and test_path is not None
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
    _progress("the addon's test cannot be run here; compiling as it is", 0.45)
    _build(id_value, source)


def _is_app(name: str) -> bool:
    """Whether a module name is the app's own - a module of it, or a package.

    ``core.ui`` is a module here and ``core`` is the folder it sits in, and a
    run that could not reach either one stopped on the app rather than on the
    addon, which is what the widening below is for.
    """
    return name in _APP or any(whole == name or whole.startswith(name + ".")
                               for whole in _APP)


def _machine_missing() -> str:
    """The module a run could not import, when its own tail says it was one of
    the test's rather than the addon's; nothing when it says something else.

    What counts as the test's own is the app and its dependencies: the names
    above, and every module the harness folder holds - a checkout's answer is
    the whole app, a fetched folder is only the two files an addon imports.
    A whole dotted name is answered with when the app holds it, since that is
    the file a widening has to fetch; otherwise the package or third-party name
    it starts with is the one that is really missing.
    """
    if job is None:
        return ""
    packages = {whole.split(".")[0] for whole in _APP}
    own = set(_MACHINE) | set(_APP) | packages
    harness = job.get("harness")
    if harness is not None:
        try:
            folder = Path(str(harness))
            own |= {path.stem for path in folder.glob("*.py")}
            for package in packages:
                own |= {f"{package}.{path.stem}"
                        for path in (folder / package).glob("*.py")}
        except OSError as error:                  # a folder that cannot be read
            note(f"the harness folder could not be listed: {error}")
    for line in reversed(list(job.get("lines") or [])):
        found = _MISSING.search(str(line))
        if not found:
            continue
        whole = found.group(1)
        for name in (whole, whole.split(".")[0]):
            if name in own:
                return name
        return ""
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
    # A folder program, deliberately, not a one-file: a one-file carries its
    # whole runtime inside one archive and unpacks all of it to a temp folder
    # on every single start - twenty seconds and 200 MB on Windows - and the
    # second bootloader it spawns can outlive being killed. A folder boots
    # straight off disk, compiles faster, and is one process to manage.
    options = {"onefile": False, "console": True, "icon": True, "hidden": ""}
    if assets.is_dir() and any(assets.iterdir()):
        options["add_data"] = [(assets, "assets")]       # packed to the program's assets folder
        icon_name = entry.get("icon") or ""
        icon = assets / icon_name if icon_name else None
        options["icon"] = str(icon) if (icon is not None and icon.is_file()) else True
    command = dev.build_command(launcher, id_value, options, source, root=root,
                                paths=[Path(harness)])
    _progress(f"compiling {id_value} with PyInstaller...", 0.70)
    note("$ " + " ".join(command))
    _stream(command, work, lambda: _install_step(id_value))


def _stay_off(id_value: str) -> None:
    """Write the new addon into the active profile as switched off.

    The app starts only what the profile enables, so an addon that has just
    arrived runs nothing: it appears on the Addon manager with its switch down
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


def _keep_picture(id_value: str, entry: dict) -> None:
    """Leave the addon's picture in ``addons/_data/<id>/``, where the app can read it.

    A program built here carries its assets inside itself, so nothing on disk
    beside it says what it looks like: the Addon Dev tab, the addon's row and the
    addon's own tab mark would all fall back to the default picture for an addon
    the catalog plainly has one of. Copied under the standard name - the id and
    then ``icon.png`` - into the addon's own data folder, it answers for the app
    (which searches that folder), for the addon (whose ``api.paths.data`` is
    exactly it) and for the next update, which overwrites it. What is copied is
    the staged asset when the download brought one, and the picture the store
    fetched for its own catalog otherwise; neither being there is an addon that
    simply wears the default, which is what it did before.
    """
    name = clean_id(id_value)
    if not name:
        return
    standard = icon_filename(name)
    source: Path | None = None
    folder = staging() / "assets"
    for wanted in (standard, one_line(entry.get("icon"))):
        if wanted:
            candidate = folder / wanted
            if candidate.is_file():
                source = candidate
                break
    if source is None:
        cached = cached_icon(id_value)
        source = Path(cached) if cached else None
    if source is None:
        return
    target = addon_data_folder(id_value) / standard
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as error:
        note(f"{id_value}'s picture could not be kept beside its settings: {error}")


def _describe(id_value: str, program: Path, details: dict, entry: dict) -> None:
    """Write down what an install is, beside it and in its own settings folder.

    Two files come of this: the manifest the app reads an addon's name, version
    and transports from before the program has run once, and the addon's
    ``fields.json`` - the form its row shows while the addon is off, which a
    running addon writes itself through ``api.config.declare``. The addon's
    picture is put in the same folder for the same reason: it has to be readable
    before the program runs. An addon that is installed is still installed when
    none of this can be written, so a failure is said and nothing more.
    """
    manifest = _manifest_values(id_value, details, entry)
    try:
        write_config_json(program.parent / f"{clean_id(id_value)}.json", manifest)
    except OSError as error:
        note(f"{id_value}'s manifest could not be written beside it: {error}")
    _keep_picture(id_value, entry)
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


def _clear_old_flat(id_value: str) -> None:
    """Take out a one-file install of ours before the folder takes its place.

    A store build before this one left ``addons/<id>.exe`` and its manifest
    loose in the addons folder, and on a machine with no executable suffix the
    one-file program is the bare ``addons/<id>`` - the very name a folder build
    needs, where it would refuse to land. An update now copies a folder program
    into ``addons/<id>/``, and two same-id programs leave the app guessing, so
    the old lot goes first - only when the loose manifest says the store wrote
    it, since anything else of that name is somebody else's. The folder itself
    is never one of these candidates: a file is what is cleared here.
    """
    folder = addons_dir()
    name = clean_id(id_value)
    if not name or addon_origin(folder, id_value) != STORE_ORIGIN:
        return
    for stale in (folder / f"{name}.exe", folder / f"{name}.bin",
                  folder / f"{name}.json", folder / name):
        try:
            if stale.is_file():
                stale.unlink()
        except OSError as error:
            note(f"the old {stale.name} could not be cleared: {error}")


def _install_step(id_value: str) -> None:
    """Move the fresh program into ``addons/<id>/``, then on to the next or end.

    A fresh install is left switched off; an update puts the switch back where
    it stood before the job stopped the addon. With more of a batch queued the
    next addon starts straight off this one; only the last addon's finish ends
    the job and asks for the rescan.

    The rescan that lets the app see the new file re-loads this addon as well,
    which takes the module this code runs in out of the app: the api is gone the
    moment ``_reload()`` starts. So everything that still needs it - clearing the
    build away, the switch, the table and the status line - is done first, and
    the rescan is the last thing here.
    """
    if job is None:
        return
    _progress(f"installing {id_value}...", 0.92)
    dev = _dev()
    staged = dev.staged_name(id_value, False, root=work_dir())
    if not staged.is_file():
        _tidy(id_value)
        _finish("no program was produced; see the log above", bad=True)
        return
    _clear_old_flat(id_value)
    try:
        target = dev.install_build(staged, id_value, False)
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
        _restore_enabled(id_value)
    else:
        _stay_off(id_value)
    job.setdefault("did", []).append(id_value)
    job["done"] = int(job.get("done") or 0) + 1
    show_entries()
    if job.get("queue"):
        _start_next()
        return
    did = job.get("did") or [id_value]
    if len(did) > 1:
        if updating:
            _finish(f"updated {len(did)} addons - " + ", ".join(did) +
                    "; each switch is back where it was")
        else:
            _finish(f"installed {len(did)} addons - " + ", ".join(did) +
                    ", switched off until you turn them on in the Addon "
                    "manager")
    elif updating:
        had = one_line(job.get("from_version"))
        now = one_line((job.get("details") or {}).get("manifest", {})
                       .get("version")) \
            or one_line((entry_for(id_value) or {}).get("version"))
        _finish(f"updated addons/{id_value}"
                + (f" from {had} to {now}" if had and now else ""))
    else:
        _finish(f"installed addons/{id_value}/, switched off until you turn it "
                "on in the Addon manager")
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
    _progress(f"getting PyInstaller ready (pip install{who})...", 0.05)
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
    """Download and compile the chosen addons, installing PyInstaller first if needed.

    One row or several: ctrl-click takes the whole selection as a batch, the
    job works through it addon by addon, and every install ends switched off.
    Anything in the selection that is already installed is left out of it with
    a line in the log, since installing over a live addon is Update's work.
    """
    if api is None or working:
        return
    ids = selected_ids()
    if not ids:
        say("pick an addon in the list first", bad=True)
        return
    pool = [entry for entry in (entry_for(ident) for ident in ids)
            if isinstance(entry, dict)]
    if not pool:
        say("the list is empty - press Refresh first", bad=True)
        return
    fresh = [entry for entry in pool if not installed(entry["id"])]
    if not fresh:
        say("every addon picked is already installed; use Update if the store "
            "lists a newer version, or Delete to remove it", bad=True)
        return
    if len(fresh) > 1:
        names = "\n".join(f"- {one_line(entry.get('name')) or entry['id']}"
                          for entry in fresh)
        if not api.ui.question(
                "Install addons",
                f"Install {len(fresh)} addons from the store?\n\n{names}\n\n"
                "Each is compiled here and left switched off until you turn "
                "it on in the Addon manager."):
            say("the install was declined")
            return
    _begin_job("install", fresh)
    for entry in pool:
        if entry not in fresh:
            note(f"{entry['id']} is already installed; it was left out of the "
                 "batch")
    _start_next()


def update_selected(_checked: bool = False) -> None:
    """Rebuild every chosen addon that is behind, keeping its settings.

    An update is an install over the top of what is already there: the same
    download, test and compile as a fresh install, and the program already in
    ``addons`` is replaced as the new one is copied in. What the addon saved for
    itself under ``addons/_data/<id>`` is never part of what an install clears,
    so its config keeps the answers already given to it. Each addon is stopped
    and switched off while its program is rebuilt, and its switch in Settings >
    Addons is put back exactly where it was once the new one lands.
    """
    if api is None or working:
        return
    ids = selected_ids()
    if not ids:
        say("pick an installed addon in the list first", bad=True)
        return
    behind = []
    for ident in ids:
        entry = entry_for(ident)
        if entry is None or not installed(ident):
            continue
        if version_newer(one_line(entry.get("version")), installed_version(ident)):
            behind.append(entry)
    if not behind:
        present = [ident for ident in ids if installed(ident)]
        if present:
            say((", ".join(present) + (" is" if len(present) == 1 else " are") +
                 " already up to date"))
        elif len(ids) == 1:
            say(f"{ids[0]} is not installed yet; use Install to add it", bad=True)
        else:
            say("none of the picked addons is installed yet; use Install to "
                "add them", bad=True)
        return
    if len(behind) == 1:
        entry = behind[0]
        id_value = entry["id"]
        have = installed_version(id_value) or "an unknown version"
        if not api.ui.question(
                "Update addon",
                f"Update {one_line(entry.get('name')) or id_value} from "
                f"{have} to {one_line(entry.get('version'))}?\n\nIts settings "
                f"in addons/_data/{id_value}/ are kept. It is switched off "
                "while its program is rebuilt, and switched back the way it "
                "was after."):
            say("the update was declined")
            return
    else:
        lines = "\n".join(
            f"- {one_line(entry.get('name')) or entry['id']} "
            f"({installed_version(entry['id']) or '?'} -> "
            f"{one_line(entry.get('version'))})" for entry in behind)
        if not api.ui.question(
                "Update addons",
                f"Update {len(behind)} addons?\n\n{lines}\n\nEach keeps its "
                "settings, is switched off while its program is rebuilt, and "
                "is switched back the way it was after."):
            say("the update was declined")
            return
    _begin_job("update", behind)
    _start_next()


def _stop_before_delete(id_value: str) -> None:
    """Take a running copy down before its files are touched.

    Windows holds a running program's own files, so a delete that walks in
    while the addon is up would half-empty an onedir build around a live
    process - the parts it cannot take leave the addon dying of missing
    pieces, and the app pays for it with a rescan of a broken install. The
    switch goes too: the addon is about to be deleted, and a rescan that
    started it back from the folder would be the worst ending. A stop that
    fails is said and left to the delete itself, which will fail honestly
    on the locked file rather than behind a broken process.
    """
    if api is None:
        return
    try:
        state = api.session.addon_state(addon_id=id_value)
        if isinstance(state, dict) and (state.get("running")
                                        or state.get("enabled")):
            api.session.set_addon_running(addon_id=id_value, running=False)
    except Exception as error:                       # noqa: BLE001 - the delete will speak
        note(f"{id_value} could not be stopped before the delete: {error}")


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
    _stop_before_delete(id_value)
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
    refresh_manager()
    api.ui.menu_item("Berry addon store", focus_tab, menu="Tools")
    api.schedule.after(0.2, refresh)


def start() -> None:
    """Nothing to do beyond what init queued."""
    return None


def shutdown() -> None:
    """Let go of everything this copy held: the connections, the job, the pane."""
    global api, working, job, shown, picked, filter_text, session_delete_config
    global managed, manager_rows, manager_picked
    global manager_slugs, manager_order, manager_building
    _drop_http()
    working = False
    job = None
    shown = []
    picked = ""
    filter_text = ""
    session_delete_config = None
    managed = []
    manager_rows = {}
    manager_slugs = {}
    del manager_order[:]
    manager_picked = ""
    manager_building = False
    config_pages.clear()      # open settings pages went with the tab's own bar
    del icon_queue[:]
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Everything the tab reports back, in one place.

    ``clicked`` runs the button its id names; a line ``changed`` on the filter box
    narrows the list as it is typed; the first check box beside the source says
    what Delete is to do with an addon's settings for this session and the second
    pulls the job's log up out of the fold or folds it back; and a row picked -
    or right-clicked - is what fills in the pane beside the list. A row's check
    box is the installed addon's own switch, moved without leaving the tab.

    The Addon manager's pages answer through the same hook. Their controls are
    named for the shared control and the addon it belongs to, so the pair is split
    out here before anything is looked up: a button does its one thing to the
    addon on its page, a switch writes that addon's record, and a ``changed`` on
    the manager's own bar says which page the person is looking at. The header's
    check that shows or hides the Addon Dev tab is the manager's one control that
    belongs to no addon. None of them waits on the network - the app's record is
    one call away, the settings form is built from what the addon declared, and
    the only file work is a delete.
    """
    if str(tab) != TAB:
        return
    ident = str(identifier)
    name = str(event)
    data = payload if isinstance(payload, dict) else {}
    base, slug = split_mid(ident)
    if slug and base in MANAGER_IDS and name == "clicked":
        manager_action(base, slug)
        return
    if slug and base in MANAGER_SWITCHES and name in ("toggled", "changed"):
        manager_toggle(base, slug, data)
        return
    if slug and base in MANAGER_CONFIG_BUTTONS and name == "clicked":
        if base == "cfg_save":
            manager_config_save(slug)
        else:
            manager_config_cancel(slug)
        return
    if name == "clicked":
        handler = HANDLERS.get(ident)
        if handler is not None:
            handler()
        return
    if ident == MANAGER_TABS and name == "changed":
        manager_tab_shown(data)
        return
    if ident == "devtab" and name in ("toggled", "changed"):
        set_devtab(data)
        return
    if ident == "delcfg" and name in ("toggled", "changed"):
        set_delete_config_session(data)
        return
    if ident == "showlog" and name in ("toggled", "changed"):
        show_log(data.get("checked"))
        return
    if ident == "filter" and name in ("changed", "returned"):
        apply_filter(data.get("text", ""))
        return
    if ident != "list":
        return
    if name == "selected":
        pick_row(data)
    elif name == "toggled":
        toggle_installed(data)
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
