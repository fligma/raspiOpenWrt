#!/usr/bin/env python3
"""Theme Manager: make, edit, try and download the looks this program is drawn from.

Adds a ``Theme Manager`` tab - pinned icon-only in the task bar, beside
Settings - with four sub-tabs: **Themes**, **Store**, **Theme Maker** and
**Docs**. It is the program's own way of doing what a ``.theme`` file is for:
a look is one small JSON file in the ``themes`` folder next to the program, and
this addon reads, writes and applies it through ``api.theme`` rather than
touching the disk or the window itself, so the same file works whether the addon
runs inside the app or as its own process.

The **Store** sub-tab is to themes what the addon store is to addons: it reads
``themes.store/index.json`` from GitHub - this program's own repository first
and any other source a person adds - and installs a chosen theme with one
click. The catalog's own copy is bundled with the program as well, so the
built-in looks show even with the network away, and each published row says
which commit its file last changed in, the same way the addon store does.

Both the Themes list and the store wear a **preview**: a small sample of the
interface - a note, a heading, a line, two buttons and the theme's own
colours - drawn with ``api.theme.sheet_of`` from whatever theme is pointed
at, so a look can be seen before it is worn. Themes themselves are set in
Settings > Appearance, and the tab's banner carries the link to that page.

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
into directly. A colour can be changed in one move - press **Change colour...**
or right-click a token and pick the wheel - and the **Live preview** at the top
of the maker repaints on every change, so the look is seen as it is made.
Nothing reaches the disk until **Save**, and nothing reaches the window until
**Apply**, so a look can be ruined without anyone noticing.

The **Docs** sub-tab is laid out like the Addon Dev panel's: a tree of the
``api.theme`` calls under headings, beside the page each one opens. It is read
off the API object itself, so an addon author can learn the namespace here and
it cannot disagree with the program it describes.
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from addon_common import (AddonError, config_get, find_addon_picture, jsonable,
                          merge_config, one_line)

ADDON = {
    "name": "Theme Manager",
    "version": "1.3",
    "author": "fligma",
    "description": "Makes, edits and downloads the .theme files the program is "
                   "drawn from: every look on disk with a live preview beside "
                   "the list, a store that reads themes.store on GitHub (the "
                   "bundled catalog included, with the commit each published "
                   "theme came from), an editor that works on one theme at a "
                   "time - colours by their groups (change one with the wheel "
                   "in a click, against a live preview), the tab bar by its "
                   "knobs, or the JSON direct - and a reference of every "
                   "api.theme call. Themes are set in Settings > Appearance, "
                   "which the tab links to.",
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

#: The repository the theme store was written against, and the one it offers
#: back when a source is added by name or every source has been removed.
MAIN_SOURCE = {
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "store_path": "utils/update util/themes.store",
}

#: What one source holds: the four pieces of a GitHub address, nothing else.
SOURCE_KEYS = ("owner", "repo", "ref", "store_path")

#: The answer a source field starts out with when it has none of its own.
SOURCE_HINTS = {"owner": "", "repo": "", "ref": "main",
                "store_path": MAIN_SOURCE["store_path"]}

DEFAULTS = {
    "confirm_delete": True,
    "save_applies": True,
    "expand_groups": True,
    # The store: which repositories the catalog is read from, and how GitHub
    # is reached. The same shape the addon store keeps.
    "sources": [dict(MAIN_SOURCE)],
    "token": "",
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
    {"key": "token", "label": "GitHub token", "kind": "text",
     "hint": "Used for every store source. Only needed for a private "
             "repository, or when anonymous requests are refused",
     "placeholder": "blank for a public repository"},
]

GROUPS = (("The editor", ["save_applies", "expand_groups"]),
          ("Files", ["confirm_delete"]),
          ("The store", ["token"]))

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
SUB_PAGES = ("Themes", "Store", "Theme Maker", "Docs")

#: The sub-tab each jump lands on, by the order of ``SUB_PAGES``.
PAGE_THEMES, PAGE_STORE, PAGE_MAKER, PAGE_DOCS = range(4)

#: The buttons under the theme list, split the way a person works with it: the
#: three things done every day, and the three done occasionally - the odd ones
#: out of the way but never hidden. Each carries the words on it and the words
#: that explain it.
THEME_BUTTONS = (
    ("use", "Apply", "Draw the whole window from the picked theme, and "
                     "remember the choice for this profile"),
    ("edit", "Edit it", "Open the picked theme in the Theme Maker"),
    ("folder", "Themes folder", "Open the folder the .theme files live in, to "
                                "copy one in or out"),
)

THEME_MANAGE_BUTTONS = (
    ("dup", "Duplicate", "Copy the picked theme to a new name"),
    ("rename", "Rename", "The picked theme under a new name, the old file gone"),
    ("del", "Delete", "Take the picked theme's file away; the shipped default "
                      "cannot be deleted"),
)

#: The buttons under the store list. The store never deletes anything from the
#: themes folder - Install writes a file, and the Themes list looks after it
#: from then on.
STORE_BUTTONS = (
    ("srefresh", "Refresh", "Read the bundled catalog and every source's "
                            "again from GitHub"),
    ("sinstall", "Install", "Fetch the picked theme into the themes folder"),
    ("sapply", "Install & Apply", "Fetch it if you have not yet, and draw the "
                                  "window from it now"),
    ("sdetails", "Details...", "Everything the listing says about the picked "
                               "theme, including the commit its file came from"),
    ("ssources", "Sources...", "Which repositories the store reads"),
)

#: The colour tokens a preview row shows: the surfaces, the accent, the words
#: and the two states a person reads a look by.
PREVIEW_TOKENS = ("base", "surface", "raise", "accent", "text", "ok", "warn",
                  "danger_fill")

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

#: The draft theme in the editor's hand: the dict its file stands for,
#: complete, and the name of the file it came from (or would be written to).
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

#: The store's own state: the catalog read last time, the rows the filter
#: leaves showing, the entry the preview and the buttons are about, the filter
#: line as it stands, and the words picked from a right-click menu.
entries: list[dict] = []
shown: list[dict] = []
picked_store = ""
filter_text = ""

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


# --- the store: the small text rules its addresses are made of ---

#: A catalog id is a name a table and a download can both use.
_SAFE = re.compile(r"[^A-Za-z0-9_]")


def clean_id(value: Any) -> str:
    """A catalog id made safe, the way the generator and the store both do it."""
    return _SAFE.sub("_", one_line(value))


def safe_segment(value: Any) -> str:
    """A repository name or path piece with the shell-junk and traversal gone."""
    text = one_line(value).replace("..", "")
    return re.sub(r"[^A-Za-z0-9._/ -]", "", text).strip(" /")


def clean_token(value: Any) -> str:
    """A token stripped of the spaces a copy-paste tends to add."""
    return one_line(value).replace(" ", "")


# --- the sources: which repositories the theme catalog is read from ---

def normalize_source(raw: Any) -> dict:
    """One source, cleaned: what a person typed, turned into the parts of an
    address.

    A branch that was left blank is ``main``, and a store folder left blank is
    this program's own, since that is the layout a published theme store uses.
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
    """One source as the flat values dict every address builder reads."""
    data = source if isinstance(source, dict) else {}
    return {"owner": one_line(data.get("owner")),
            "repo": one_line(data.get("repo")),
            "ref": one_line(data.get("ref")) or "main",
            "store_path": one_line(data.get("store_path")).strip("/")
                         or MAIN_SOURCE["store_path"],
            "token": clean_token(token)}


def _source_key(source: dict) -> tuple:
    return tuple(str(source.get(key) or "") for key in SOURCE_KEYS)


def read_sources(config: dict) -> list[dict]:
    """Every source the config names, in the order they are read.

    An empty ``sources`` list is what the person asked for - they removed every
    source - and stays empty; only a config that names no list at all falls back
    to this program's own. A source missing its owner or repository is a
    half-typed line, not a source, and is dropped rather than fetched badly.
    """
    data = config if isinstance(config, dict) else {}
    raw = config_get(data, "sources", None)
    if not isinstance(raw, list):
        raw = [dict(MAIN_SOURCE)]
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in raw:
        source = normalize_source(item)
        if not source["owner"] or not source["repo"]:
            continue
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return out


def current_sources(config: dict | None = None) -> list[dict]:
    """What the config names now, read from the file when nothing is to hand."""
    return read_sources(values() if config is None else config)


def write_sources(sources: list[dict]) -> list[dict]:
    """Put the list in the config, cleaned, so the next read uses it."""
    kept = read_sources({"sources": list(sources or [])})
    if api is None:
        return kept
    api.config.update({"sources": jsonable(kept)})
    return kept


# --- network: standard library only, like the addon store ---

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


def _http(config: dict) -> Fetcher:
    key = clean_token(config_get(config, "token", ""))
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = Fetcher(token=key)
        _fetchers[key] = fetcher
    return fetcher


def _drop_http() -> None:
    for fetcher in _fetchers.values():
        fetcher.close()
    _fetchers.clear()


def raw_address(config: dict, filename: str) -> str:
    """The raw.githubusercontent path for one file in a source's store folder.

    A theme's whole identity in the catalog is its file name, so this is the
    one door every download and every preview walk through.
    """
    owner = quote(safe_segment(config_get(config, "owner", MAIN_SOURCE["owner"])))
    repo = quote(safe_segment(config_get(config, "repo", MAIN_SOURCE["repo"])))
    ref = quote(one_line(config_get(config, "ref", "main")).strip("/"), safe="-./_")
    where = "/".join(part for part in (
        safe_segment(config_get(config, "store_path",
                                MAIN_SOURCE["store_path"])).strip("/"),
        safe_segment(filename)) if part)
    return f"/{owner}/{repo}/{ref}/{quote(where)}"


def _refused(status: int, path: str) -> str:
    if status == 404:
        return (f"GitHub has no such file at {path}: check the owner, repository, "
                "branch and store folder in the sources")
    if status == 403:
        return (f"GitHub refused the request for {path} (403). Anonymous requests "
                "are limited, so add a token under Settings or try again later")
    return f"GitHub answered {status} for {path}"


# --- reading the catalog ---

def parse_catalog(body: Any) -> list[dict]:
    """index.json as a tidy list of theme entries; a bare list is accepted too.

    The fields are the ones the generator writes from a ``.theme`` file's own
    JSON: name, version, author, blurb and file name. The search line is built
    once here so a keystroke at the filter is one test, not a re-lowering of
    every field of every row.
    """
    if isinstance(body, dict):
        rows = body.get("themes") or body.get("entries") or []
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
        entry = {
            "id": ident,
            "name": one_line(raw.get("name") or ident),
            "version": one_line(raw.get("version") or ""),
            "author": one_line(raw.get("author") or ""),
            "blurb": one_line(raw.get("blurb") or raw.get("description") or ""),
            "filename": one_line(raw.get("filename") or f"{ident}.theme"),
            "status": one_line(raw.get("status") or ""),
        }
        entry["search"] = " ".join(one_line(entry.get(part)).lower() for part in
                                   ("name", "id", "author", "blurb"))
        out.append(entry)
    return out


def load_catalog(config: dict) -> list[dict]:
    """Fetch and read ``index.json`` from one source's store folder."""
    path = raw_address(config, "index.json")
    try:
        status, body = _http(config).get(
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


def local_folder() -> Path | None:
    """The ``themes.store`` folder bundled with this program, when it is there.

    A checkout has it beside the app; a built program has it only if the pack
    carried it along. Either way the built-in looks can be listed and installed
    without a single request leaving the machine.
    """
    homes: list[Path] = []
    try:
        if api is not None:
            homes.append(Path(str(api.paths.base)))
    except (AddonError, AttributeError, TypeError, OSError):
        pass
    homes.append(Path(__file__).resolve().parents[1])
    for home in homes:
        try:
            folder = home / "themes.store"
            if (folder / "index.json").is_file():
                return folder
        except OSError:
            continue
    return None


def local_entries() -> list[dict]:
    """The bundled catalog, read off disk; its rows say where each file is."""
    folder = local_folder()
    if folder is None:
        return []
    try:
        data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for row in parse_catalog(data):
        entry = dict(row)
        entry["source"] = "this program"
        entry["local"] = str(folder / entry["filename"])
        entry["search"] = f"{entry['search']} this program"
        out.append(entry)
    return out


def merge_catalogs(sources: list[dict], token: str = "") -> tuple[list[dict], list[str]]:
    """The bundled catalog, then each source's, with the clashes said out loud.

    A theme two publishers both list is taken from the first - the bundled
    folder is read before any source, and the order of the list is the priority
    after it - and the row says where the copy kept came from, so the download
    that follows is looked for in the same place. A source that will not answer
    is reported and passed over, because the rest of them are still worth
    reading.
    """
    found: list[dict] = []
    failed: list[str] = []
    seen: dict[str, str] = {}
    for row in local_entries():
        if row["id"] in seen:
            continue
        seen[row["id"]] = row["source"]
        found.append(row)
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
            entry["search"] = f"{entry['search']} {label.lower()}"
            seen[entry["id"]] = label
            found.append(entry)
            kept += 1
        note(f"{label}: {kept} theme(s)")
    return found, failed


# --- what the store says about each theme ---

#: What the generator leaves in the catalog to point at a row nobody has taken.
_MARKS = {"new": "  \u25cf new", "updated": "  \u21bb updated"}


def theme_state(entry: dict) -> tuple[str, str]:
    """How one store theme stands here, and the colour its row is drawn in.

    The store never takes anything away, so the states only ever grow warmer:
    not installed, on disk from an earlier install, and the one the window is
    actually wearing.
    """
    name = one_line(entry.get("name"))
    if api is not None and name and api.theme.exists(name):
        if name == in_force():
            return ("in use", "#23d18b")
        return ("on disk", "#7fb3d5")
    status = one_line(entry.get("status"))
    if status == "new":
        return ("new", "#ffb020")
    if status == "updated":
        return ("updated", "#ffb020")
    return ("not installed", "#9a9a9a")


def show_store_entries() -> None:
    """The catalog as rows: the name, how it stands here, who made it, where it
    came from and - once GitHub has answered - which commit it came in."""
    global shown
    if api is None:
        return
    needle = filter_text.strip().lower()
    shown = [entry for entry in entries
             if not needle or needle in (entry.get("search") or "")]
    rows = []
    for entry in shown:
        state, tint = theme_state(entry)
        name = one_line(entry.get("name")) or one_line(entry.get("id"))
        commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
        whence = " ".join(part for part in (
            str(one_line(commit.get("sha")))[:7], one_line(commit.get("date")))
            if part)
        rows.append({
            "cells": [name + _MARKS.get(one_line(entry.get("status")), ""),
                      state, one_line(entry.get("author")) or "-",
                      one_line(entry.get("source")),
                      whence or ("bundled" if entry.get("local") else "")],
            "color": tint})
    put("slist", rows, "rows")
    total = len(entries)
    put("scount", (f"{len(shown)} of {total}" if needle else
                   (f"{total} listed" if total else
                    "nothing read yet - press Refresh")), "text")


def store_find(text: str) -> dict | None:
    """The row a person means, whether they named it by id or by the name the
    list shows.

    The first column carries a name with any mark beside it, while every command
    takes an id, so both are tried - the shown rows first, then the whole
    catalog - before a row is given up on.
    """
    wanted = one_line(text)
    if not wanted:
        return None
    ident = clean_id(wanted)
    for pool in (shown, entries):
        for entry in pool:
            if entry["id"] == ident or one_line(entry.get("name")) == wanted:
                return entry
    lowered = wanted.lower()
    for pool in (shown, entries):
        for entry in pool:
            name = one_line(entry.get("name")).lower()
            if name and (name in lowered or lowered.startswith(name)):
                return entry
    return None


def store_target() -> dict | None:
    """The row the store's buttons are about: the selection, then the theme the
    preview is showing.

    A repaint drops Qt's selection without dropping what the preview is
    holding, so a button then still means the theme the person is looking at
    rather than nothing at all.
    """
    rows = get("slist", "selected", [])
    text = one_line(rows[0]) if isinstance(rows, (list, tuple)) and rows else ""
    entry = store_find(text) if text else None
    if entry is None and picked_store:
        for one in entries:
            if one["id"] == picked_store:
                return one
    if entry is None:
        say("no store theme is picked", bad=True)
    return entry


def filter_store(needle: Any = "") -> None:
    """Repaint the list with only the rows the filter fits; nothing is fetched."""
    global filter_text
    filter_text = one_line(needle)
    show_store_entries()


# --- the commit each published theme came from ---

#: The catalog is regenerated whenever the store folder changes and CI commits
#: the result, so nothing baked into it could honestly name a commit - the
#: store asks for the last one that touched the theme's own file, once per
#: theme, and never for the bundled half, which has no commit to name.
_API_HOST = "api.github.com"


def commit_address(config: dict, filename: str) -> str:
    """The commits query for one file, held to the ref the source names."""
    owner = quote(safe_segment(config_get(config, "owner", MAIN_SOURCE["owner"])))
    repo = quote(safe_segment(config_get(config, "repo", MAIN_SOURCE["repo"])))
    ref = quote(one_line(config_get(config, "ref")) or "main", safe="-./_")
    where = "/".join(part for part in (
        safe_segment(config_get(config, "store_path",
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
            "date": str(writer.get("date") or "")[:10],
            "who": one_line(author.get("login") or writer.get("name"))}


def _ask_commit(entry: dict) -> None:
    """Queue the lookup of one theme's commit; the answer comes on a later tick.

    Asking on the spot would make reading the store wait on GitHub once per
    row, so it is put off a moment. A theme whose commit is already in hand, or
    already being asked about, is never asked twice.
    """
    if api is None or entry.get("asking") or isinstance(entry.get("commit"), dict):
        return
    if not isinstance(entry.get("src"), dict):
        return                      # the bundled half has no commit to ask for
    entry["asking"] = True
    try:
        api.schedule.after(0.05, lambda: _read_commit(entry))
    except (AddonError, AttributeError, TypeError):
        entry["asking"] = False


def _read_commit(entry: dict) -> None:
    """Fill in the commit one theme's file last changed in; quiet when it cannot.

    Rate limits, a private repository with no token and an unreachable network
    all leave the row without a commit line rather than a wall of text,
    because provenance is a nicety the store works perfectly well without.
    """
    if api is None:
        return
    source = entry.get("src")
    if not isinstance(source, dict):
        return
    entry["asking"] = False
    config = source_values(source, clean_token(config_get(values(), "token", "")))
    path = commit_address(config, one_line(entry.get("filename")))
    try:
        status, body = _http(config).get(_API_HOST, path, accept="application/json")
    except (http.client.HTTPException, OSError):
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
        show_store_entries()        # the From column fills itself in


# --- fetching one theme's own file ---

def _entry_config(entry: dict) -> dict:
    """The flat address parts the fetchers want for one store row."""
    config = values()
    source = entry.get("src")
    if not isinstance(source, dict):
        source = (read_sources(config) or [dict(MAIN_SOURCE)])[0]
    return source_values(source, clean_token(config_get(config, "token", "")))


def fetch_theme_text(entry: dict) -> str:
    """The ``.theme`` file of one store row, from the bundled folder or GitHub."""
    held = one_line(entry.get("local"))
    if held:
        try:
            return Path(held).read_text(encoding="utf-8")
        except OSError as error:
            raise AddonError(f"the bundled theme file could not be read: {error}")
    config = _entry_config(entry)
    path = raw_address(config, one_line(entry.get("filename")))
    try:
        status, body = _http(config).get("raw.githubusercontent.com", path,
                                         accept="application/json")
    except (http.client.HTTPException, OSError) as error:
        raise AddonError(f"the theme could not be fetched: {error}")
    if status != 200:
        raise AddonError(_refused(status, path))
    return body.decode("utf-8", errors="replace")


def _ask_theme(entry: dict) -> None:
    """Queue the read of one store theme's own file, for the preview only.

    The preview wants the actual colours, and only the file has them; a row
    whose file is already in hand previews from the cache and asks nothing.
    """
    if api is None or entry.get("asking_theme"):
        return
    if isinstance(entry.get("theme"), dict):
        return
    entry["asking_theme"] = True
    try:
        api.schedule.after(0.05, lambda: _read_theme(entry))
    except (AddonError, AttributeError, TypeError):
        entry["asking_theme"] = False


def _read_theme(entry: dict) -> None:
    """Fetch one store theme's file into the row, and preview it if it is still
    the one the person is looking at."""
    entry["asking_theme"] = False
    if api is None:
        return
    try:
        text = fetch_theme_text(entry)
    except AddonError:
        note(f"{one_line(entry.get('id'))}: its file could not be read "
             "for the preview")
        return
    try:
        data = json.loads(text)
    except ValueError:
        return
    if isinstance(data, dict):
        entry["theme"] = data
        if picked_store == one_line(entry.get("id")):
            preview_theme(data, one_line(entry.get("name")),
                          "spreview", "sprevnote", "sprevtokens")


# --- what the store's buttons do ---

def install_entry(entry: dict, apply_after: bool = False) -> bool:
    """Fetch one store theme and write it to the themes folder; whether it landed.

    The file is checked the way the app checks every save, so a catalog row
    that turns out to hold a broken theme is refused with the app's own words
    rather than half-installed.
    """
    if api is None or not isinstance(entry, dict):
        return False
    name = one_line(entry.get("name")) or clean_id(entry.get("id"))
    text = guarded(lambda: fetch_theme_text(entry), f"fetch {name}")
    if text is None:
        return False
    try:
        data = json.loads(text)
    except ValueError as error:
        say(f"{name} is not a readable theme file: {error}", bad=True)
        return False
    if not isinstance(data, dict):
        say(f"{name} is not one JSON object", bad=True)
        return False
    data["name"] = name
    problems = api.theme.validate(data)
    if problems:
        say("; ".join(str(one) for one in problems), bad=True)
        return False
    if api.theme.exists(name) and not api.ui.question(
            "Install a theme", f"themes/{name}.theme is already here. Replace "
            "it with the store's copy?", False):
        say(f"{name} left as it was")
        return False
    saved = guarded(lambda: api.theme.save(name, data), f"save {name}")
    if saved is None:
        return False
    entry["theme"] = data
    refresh_themes()
    if apply_after:
        used = guarded(lambda: api.theme.use(str(saved)), f"apply {saved}")
        say(f"{saved} installed and the window is drawn from {used or saved}; "
            "it is set in Settings > Appearance")
    else:
        say(f"{saved} installed into the themes folder")
    return True


def install_picked(_checked: bool = False) -> None:
    """Fetch the picked store theme into the themes folder."""
    entry = store_target()
    if entry is not None:
        install_entry(entry)


def apply_picked_store(_checked: bool = False) -> None:
    """Draw the window from the picked store theme, installing it first if the
    themes folder does not have it yet."""
    if api is None:
        return
    entry = store_target()
    if entry is None:
        return
    name = one_line(entry.get("name"))
    if name and api.theme.exists(name):
        used = guarded(lambda: api.theme.use(name), f"apply {name}")
        if used is not None:
            refresh_themes()
            say(f"the window is drawn from {used}")
        return
    install_entry(entry, apply_after=True)


def details_picked(_checked: bool = False) -> None:
    """Everything the listing says about one store theme, commit included."""
    if api is None:
        return
    entry = store_target()
    if entry is None:
        return
    _ask_commit(entry)
    state, _tint = theme_state(entry)
    commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
    lines = [["Author", one_line(entry.get("author")) or "not stated"],
             ["Version", one_line(entry.get("version")) or "not stated"],
             ["Published by", one_line(entry.get("source")) or "not stated"],
             ["Here", state],
             ["File", one_line(entry.get("filename"))]]
    if commit.get("sha"):
        lines.append(["Commit", f"{str(commit['sha'])[:7]} "
                               f"{one_line(commit.get('title'))}".strip()])
        when = " ".join(part for part in (one_line(commit.get("date")),
                                          one_line(commit.get("who"))) if part)
        if when:
            lines.append(["Committed", when])
    body = "\n".join(f"{label}: {value}" for label, value in lines)
    blurb = one_line(entry.get("blurb"))
    api.ui.information(f"Theme: {one_line(entry.get('name'))}",
                       (f"{blurb}\n\n{body}" if blurb else body))


def refresh_store(_checked: bool = False) -> None:
    """Read the bundled catalog and each source's, then repaint.

    The bundled half costs no network, so the built-in looks are listed even
    when GitHub cannot be reached; a commit already looked up is carried across
    the reload with it, so reading the store again is not a reason to ask the
    same question twice.
    """
    global entries
    if api is None:
        return
    config = values()
    sources = read_sources(config)
    say(f"reading {len(sources) or 'the bundled'} catalog(s)...")
    known = {one_line(entry.get("id")): entry["commit"] for entry in entries
             if isinstance(entry.get("commit"), dict)}
    held = {one_line(entry.get("id")): entry["theme"] for entry in entries
            if isinstance(entry.get("theme"), dict)}
    entries, failed = merge_catalogs(sources,
                                     clean_token(config_get(config, "token", "")))
    for entry in entries:
        ident = one_line(entry.get("id"))
        if known.get(ident) is not None:
            entry["commit"] = known[ident]
        if held.get(ident) is not None:
            entry["theme"] = held[ident]
        _ask_commit(entry)
    show_store_entries()
    word = (f"{len(entries)} theme(s) in the store" if entries
            else "no themes are published here yet")
    if failed and entries:
        say(f"store read: {word}; {len(failed)} source(s) could not be read "
            f"({failed[0]})", bad=True)
    elif failed:
        say("; ".join(failed), bad=True)
    else:
        say(f"store read: {word}")


def load_local_store() -> None:
    """The bundled catalog only - no network - so the store opens with the
    built-in looks showing before anybody presses Refresh."""
    global entries
    if api is None or entries:
        return
    entries, _failed = merge_catalogs([], "")
    show_store_entries()


def store_pick(payload: dict) -> None:
    """A store row was pointed at: preview the theme it stands for.

    The file itself is fetched on a later tick - the row previews as soon as
    the answer lands, and until then the frame says it is reading rather than
    freezing the tab on the network.
    """
    global picked_store
    entry = store_find(one_line((payload or {}).get("text")))
    if entry is None:
        return
    picked_store = one_line(entry.get("id"))
    held = entry.get("theme") if isinstance(entry.get("theme"), dict) else None
    preview_theme(held, one_line(entry.get("name")) or picked_store,
                  "spreview", "sprevnote", "sprevtokens")
    _ask_theme(entry)
    _ask_commit(entry)


def store_menu(payload: dict) -> None:
    """Right-click on a store row: the actions that fit it, as a small menu."""
    if api is None:
        return
    entry = store_find(one_line((payload or {}).get("text")))
    if entry is None:
        return
    store_pick(payload or {})
    offered = (("Install it", install_picked),
               ("Install and apply", apply_picked_store),
               ("See its details", details_picked),
               ("Refresh the catalog", refresh_store))
    line = api.ui.choose([words for words, _fn in offered] + ["Nothing"])
    for words, function in offered:
        if line == words:
            function()
            return


# --- the sources editor: what the store's Sources button does ---

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
    global entries
    if api is None:
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
                    else _ask_source("Add a theme store source"))
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
                note(f"{source_label(source)} is read first, so its themes win")
        elif action == "Remove":
            if not api.ui.question(
                    "Remove a theme store source",
                    f"Stop reading themes from {source_label(source)}?\n\n"
                    "Nothing already installed is touched, and the repository "
                    "can be added back."):
                continue
            sources.pop(which)
            changed = True
            note(f"{source_label(source)} was removed as a source")
    if not changed:
        return
    write_sources(sources)
    entries = []
    show_store_entries()
    refresh_store()


# --- the preview: a theme drawn small, next to you the whole time ---

def preview_spec(frame: str, note_id: str, tokens_id: str,
                 title: str = "What it looks like") -> dict:
    """A sample of the interface the picked theme's sheet is painted onto.

    Enough of the common widgets are here - a heading, a line, buttons, a
    checkbox, a combo and a progress bar - that a theme's accent, surface and
    state colours all show at once; the sheet is laid on the whole group, so
    every one of them wears the theme being previewed.
    """
    return {"kind": "group", "id": frame, "text": title,
            "children": [
                {"kind": "label", "id": note_id,
                 "text": "Pick a theme to see it here.", "word_wrap": True},
                {"kind": "label", "text": "The berry in a heading, the text "
                                           "beside it",
                 "style": _HEAD_STYLE},
                {"kind": "line", "value": "a line to read"},
                {"kind": "row", "children": [
                    {"kind": "button", "text": "A button"},
                    {"kind": "button", "text": "Another"},
                ]},
                {"kind": "row", "children": [
                    {"kind": "check", "text": "a switch", "checked": True},
                    {"kind": "combo", "items": ["one", "two", "three"],
                     "expand": True},
                ]},
                {"kind": "progress", "value": 60},
                {"kind": "table", "id": tokens_id, "columns": ["Colour", "Hex"],
                 "stretch": 1, "col_widths": [110],
                 "tooltip": "The theme's own colour tokens, and the colour "
                            "each one is where this sample is drawn"},
            ]}


def preview_theme(data: Any, label: str, frame: str, note_id: str,
                  tokens_id: str) -> None:
    """Draw one sample with one theme's colours, and name it in the note.

    ``api.theme.sheet_of`` builds the very sheet the window would wear, so the
    sample cannot disagree with the look it previews; and only the sample frame
    is styled, so the program keeps wearing what it wore while you browse.
    """
    if api is None:
        return
    if not isinstance(data, dict) or not data:
        put(frame, "", "style")
        put(tokens_id, [], "rows")
        put(note_id, f"reading {label}'s file..." if label
            else "Pick a theme to see it here.", "text")
        return
    sheet = guarded(lambda: api.theme.sheet_of(data), "build the preview")
    if sheet is None:
        return
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    put(frame, str(sheet), "style")
    put(tokens_id, [{"cells": [token, str(colors.get(token))]} for token
                    in PREVIEW_TOKENS if colors.get(token)], "rows")
    put(note_id, f"previewing {label} - themes are set in Settings > "
                 "Appearance", "text")


def preview_current() -> None:
    """The Themes page opens on the look the window already wears."""
    if api is None:
        return
    name = in_force()
    preview_theme(guarded(lambda: api.theme.read(name), f"read {name}"),
                  name, "tpreview", "tprevnote", "tprevtokens")


def open_appearance_settings(_checked: bool = False) -> None:
    """The banner's link: stand the app on Settings > Appearance."""
    if api is None:
        return
    if api.theme.open_appearance():
        say("the look is set here, in Settings > Appearance")
    else:
        say("themes are set in Settings > Appearance", bad=True)


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
    """Every look on disk, and a sample of the one you are pointing at."""
    return {"kind": "splitter", "sizes": [3, 2], "children": [
        {"kind": "column", "children": [
            {"kind": "table", "id": "themes",
             "columns": ["Theme", "In use", "Author", "What it is"],
             "stretch": 3, "col_widths": [140, 60, 110],
             "on": ["selected", "context"],
             "tooltip": "Every themes/<name>.theme on disk. Pick one to see it "
                        "in the preview beside the list; right-click it for "
                        "what can be done to it."},
            {"kind": "row", "children": buttons(THEME_BUTTONS)},
            {"kind": "row", "children": buttons(THEME_MANAGE_BUTTONS)},
        ]},
        preview_spec("tpreview", "tprevnote", "tprevtokens"),
    ]}


def store_page() -> dict:
    """The catalog: the bundled looks and every source's, preview beside the
    row you are pointing at.

    The store never takes anything away: Install writes a file into the themes
    folder, and the Themes list looks after it from then on.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Find", "style": _HEAD_STYLE},
            {"kind": "line", "id": "sfilter", "expand": True,
             "placeholder": "Filter by name, author or where it came from",
             "tooltip": "Nothing is looked for until you type; the store on "
                        "GitHub is not touched",
             "on": ["changed", "returned"]},
            {"kind": "label", "id": "scount", "text": "", "style": _HEAD_STYLE,
             "tooltip": "How many of the catalog's themes are showing"},
        ]},
        {"kind": "splitter", "sizes": [3, 2], "children": [
            {"kind": "column", "children": [
                {"kind": "table", "id": "slist",
                 "columns": ["Theme", "Here", "Author", "Source", "From"],
                 "stretch": 4, "col_widths": [150, 80, 100, 130],
                 "on": ["selected", "context"],
                 "tooltip": "Every theme the bundled catalog and the sources "
                            "list. Pick one to preview it; right-click it for "
                            "what can be done. The From column names the commit "
                            "its file came in, once GitHub has answered."},
                {"kind": "row", "children": buttons(STORE_BUTTONS)},
            ]},
            preview_spec("spreview", "sprevnote", "sprevtokens"),
        ]},
    ]}


def edit_page() -> dict:
    """The Theme Maker: start a look from a template, then change it token by
    token, knob by knob, or as JSON."""
    return {"kind": "column", "children": [
        {"kind": "splitter", "sizes": [3, 3], "children": [
            {"kind": "group", "text": "A look that does not exist yet",
             "children": [
                 {"kind": "row", "children": [
                     {"kind": "label", "text": "from"},
                     {"kind": "combo", "id": "tpl", "expand": True,
                      "on": ["changed"],
                      "tooltip": "The starting point: each is a whole theme, "
                                 "ready to edit, and none of them is on disk "
                                 "until you create it"},
                     {"kind": "label", "text": "named"},
                     {"kind": "line", "id": "newname", "expand": True,
                      "placeholder": "my look",
                      "tooltip": "A file name, so no / \\ : * ? or quotes; "
                                 "spaces are allowed and become part of the "
                                 "theme's name"},
                     {"kind": "button", "id": "create", "text": "Create",
                      "on": ["clicked"], "tooltip": "Write the new theme and "
                                                    "open it in the editor "
                                                    "below"},
                 ]},
                 {"kind": "label", "id": "tplnote", "text": "", "word_wrap": True,
                  "style": _HEAD_STYLE},
             ]},
            preview_spec("mpreview", "mprevnote", "mprevtokens",
                         title="Live preview"),
        ]},
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
             "on": ["selected", "context"],
             "tooltip": "Every colour the interface is painted with, in the "
                        "blocks it is used for. Pick one and set it on the "
                        "right; right-click it to change it with the wheel."},
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
                    {"kind": "button", "id": "chgcolor", "text": "Change colour...",
                     "on": ["clicked"], "tooltip": "Open the wheel on the picked "
                                                   "token and set it in one go"},
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
    """The whole tab: a banner over the four sub-tabs.

    Declarative, so the same file runs in the app's process or outside it, and
    every widget is reached by name through ``set_widget`` afterwards. The
    banner's Appearance button is the one link out: themes are worn on the
    whole window, and where they are picked is Settings > Appearance.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": TAB, "style": _BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": _STATUS_STYLE},
            {"kind": "label", "id": "where", "text": "", "style": _HEAD_STYLE},
            {"kind": "button", "id": "appearance", "text": "Appearance...",
             "on": ["clicked"],
             "tooltip": "Themes are worn on the whole window and are picked "
                        "in Settings > Appearance - this takes you there"},
        ]},
        {"kind": "tabs", "id": SUB, "children": [
            dict(themes_page(), label=SUB_PAGES[0]),
            dict(store_page(), label=SUB_PAGES[1]),
            dict(edit_page(), label=SUB_PAGES[2]),
            dict(docs_page(), label=SUB_PAGES[3]),
        ]},
    ]}


def build_tab() -> None:
    """One tab, pinned icon-only beside Settings, like the addon store.

    The mark is the whole caption by design: the tab is furniture, not a page
    somebody arranged, and what it opens is decided by the theme in force.
    """
    api.tabs.add_tab(TAB, spec=tab_spec(), style=page_style(),
                     pinned=True, icon_only=True)
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
    preview_current()


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


def note_template(_changed: Any = None) -> None:
    """Say what the template in the combo is, and draw it in the maker's preview."""
    chosen = one_line(get("tpl", "value", ""))
    for one in (api.theme.templates() if api is not None else []):
        if str(one.get("name")) == chosen:
            say(f"{chosen}: {one_line(one.get('blurb'))}")
            put("tplnote", one_line(one.get("blurb")))
            preview_theme(one.get("theme"), chosen,
                          "mpreview", "mprevnote", "mprevtokens")
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
    # Repaint the maker's preview from the draft every time it changes, so the
    # sample is live: a colour set on a token shows in the preview at once.
    preview_theme(draft, str((draft or {}).get("name") or draft_name),
                  "mpreview", "mprevnote", "mprevtokens")


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
    # One read of the knob list gives both the kind and the hint, rather than
    # calling token_knobs() twice (once via knob_kind, once for the hint).
    kind = ""
    hint = ""
    for one in (api.theme.token_knobs() if api is not None else []):
        if str(one.get("name")) == picked_knob:
            kind = str(one.get("kind") or "")
            hint = str(one.get("hint") or "")
            break
    now = dict((draft or {}).get("tabs") or {}).get(picked_knob)
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


def shipped_color(token: str) -> str:
    """The colour the shipped theme gives a token, '' when it is not a colour."""
    for group in (api.theme.groups() if api is not None else []):
        for one in group.get("tokens") or []:
            if str(one.get("name")) == str(token):
                return str(one.get("color") or "")
    return ""


def change_picked_colour(_checked: bool = False) -> None:
    """One go at the wheel for the picked token: open it, and set the answer.

    This is the direct route the two-step 'Wheel...' then 'Set colour' used to
    force: the wheel opens on the token's current colour, and a chosen colour
    is applied straight to the draft and shown live in the preview.
    """
    if api is None:
        return
    if not picked_token:
        say("pick a colour from the list first", bad=True)
        return
    try:
        found = api.ui.pick_color(f"Set {picked_token}", token_value(picked_token))
    except AddonError:
        return
    if not found:
        return
    if apply_change("colors", picked_token, one_line(found)):
        show_draft()
        say(f"{picked_token} is {one_line(found)}")


def token_menu(payload: dict) -> None:
    """Right-click a colour token: change it with the wheel, or reset it."""
    if api is None:
        return
    name = one_line((payload or {}).get("text"))
    if not name or not any(str(one.get("name")) == name for group in
                           api.theme.groups() for one in (group.get("tokens") or [])):
        return
    show_token(name)
    chosen = api.ui.choose(["Change colour (wheel)", "Reset to default"])
    if chosen == "Change colour (wheel)":
        change_picked_colour()
    elif chosen == "Reset to default":
        base = shipped_color(name)
        if base and apply_change("colors", name, base):
            show_draft()
            say(f"{name} is back to {base}")


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
    show_page(PAGE_MAKER)
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
    global draft_name
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
    ("Changing them", ("use", "save", "copy", "rename", "remove",
                       "open_appearance")),
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
    """The Tools menu entry: the tab, on the maker."""
    if api is None:
        return
    api.tabs.activate(TAB)
    show_page(PAGE_MAKER)


def refresh_all(_ignored: Any = None) -> None:
    """Every part of the tab that reads from the program: list, editor, docs."""
    if api is None:
        return
    refresh_themes()
    if draft is None:
        open_draft(in_force())
    show_draft()
    fill_docs()
    show_store_entries()


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
    "create": create_from_template, "appearance": open_appearance_settings,
    "srefresh": refresh_store, "sinstall": install_picked,
    "sapply": apply_picked_store, "sdetails": details_picked,
    "ssources": edit_sources,
    "newdraft": blank_draft, "setcolor": set_colour, "wheel": pick_colour,
    "chgcolor": change_picked_colour,
    "setknob": set_knob, "getsource": show_source, "putsource": use_source,
    "save": lambda: save_draft(False),
    "saveapply": lambda: save_draft(True), "discard": discard_draft,
}

#: The picks that are not buttons: a row of one of the trees, another theme in
#: the editor's combo, or a row of the store list.
PICKS = {"themes": "theme", "tokens": "token", "knobs": "knob", "docs": "doc",
         "slist": "store"}


def pick_from(kind: str, payload: dict) -> None:
    """One row picked somewhere: open what it stands for."""
    text = one_line((payload or {}).get("text"))
    if kind == "theme":
        if not text and api is not None:
            rows = get("themes", "selected", [])
            text = one_line(rows[0]) if isinstance(rows, (list, tuple)) and rows else ""
        if text:
            preview_theme(guarded(lambda: api.theme.read(text), f"read {text}"),
                          text, "tpreview", "tprevnote", "tprevtokens")
            say(f"previewing {text} - "
                f"{theme_blurb(text)[1] or 'no description given'}")
        return
    if kind == "store":
        store_pick(payload or {})
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
    offered = THEME_BUTTONS + THEME_MANAGE_BUTTONS
    chosen = api.ui.choose([text for _id, text, _tip in offered])
    wanted = next((one for one in offered if one[1] == chosen), None)
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
    if kind == "context" and name == "tokens":
        token_menu(payload or {})
        return
    if kind == "context" and name == "slist":
        store_menu(payload or {})
        return
    if kind == "changed" and name == "sfilter":
        filter_store(one_line((payload or {}).get("text")))
        return
    if kind == "returned" and name == "sfilter":
        filter_store(one_line((payload or {}).get("text")))
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
    show_page(PAGE_MAKER)
    open_draft(name)


def init(plugin_api: Any) -> None:
    """Build the tab from the theme folder as it stands."""
    global api, filter_text, picked_store
    api = plugin_api
    # The tab is built fresh, so its filter line and selection start empty:
    # clear the store's view state with them, or a filter left over from an
    # earlier hand would hide rows the freshly built box does not ask for.
    filter_text = ""
    picked_store = ""
    config = api.config.declare(
        FIELDS, DEFAULTS,
        title="Theme Manager - addons/_data/theme_manager/config.json")
    build_tab()
    put("applylive", bool(config_get(config, "save_applies", True)), "checked")
    api.ui.menu_item("Theme Manager (make and edit themes)", focus_editor,
                     menu="Tools")
    api.events.on("theme_changed", lambda data: on_event("theme_changed", data))
    refresh_all()
    load_local_store()
    say(f"{len(api.theme.names())} themes, the window drawn from {in_force()}")
    return None


def start() -> None:
    """Nothing to do beyond what init did."""
    return None


def shutdown() -> None:
    """Forget the draft in hand, the catalog read and the api it was read through."""
    global api, draft, draft_name, picked_token, picked_knob
    global doc_branches, context_name
    global entries, shown, picked_store, filter_text
    draft = None
    draft_name = ""
    picked_token = ""
    picked_knob = ""
    doc_branches = {}
    context_name = ""
    entries = []
    shown = []
    picked_store = ""
    filter_text = ""
    _drop_http()
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
