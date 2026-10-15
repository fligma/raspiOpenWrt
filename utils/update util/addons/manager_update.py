#!/usr/bin/env python3
"""Berry Manager update: check the published tags and replace this program.

Adds a ``Manager update`` tab. The program's own releases are the tags of this
repository whose names start with ``berry`` - ``berry1.5.9`` is version 1.5.9 -
and the addon reads them from GitHub's API, picks the newest, and compares it
with the version this copy carries (``api.session.version()``, which is
``manager_const.APP_VERSION``). No ``berry`` tag existing yet is the normal
state of a fresh repository: the check then says so plainly and calls it
nothing, no error.

When a tag is ahead, the new program comes from the release made for it.
GitHub tags point at source only, so the build a push produced has to be
published as a release under the same tag for anything to download; the addon
prefers a release *asset* for this system and, when the release carries none,
falls back to a plain download link in the release's description. Links to the
workflow's artifacts cannot work on their own - they answer with a sign-in
page, not bytes - and the addon says exactly that when it is handed one.

Installing an update is one careful swap. The downloaded bytes are checked
they are a program at all (a release asset is raw bytes; a zip, like the
workflow ships, is unpacked for its ``berry_manager`` file), staged inside
this addon's own ``addons/_data/manager_update`` folder, and only then moved
into the program's place: on Windows the running file is renamed to
``<name>.old`` first - Windows lets a running program be renamed but not
deleted - the new file takes its place, and the leftover is deleted, or, if
it is still held, by the fresh copy on its next start. On Linux the new file
simply replaces the old in one step. What an update never touches is what
the user wrote: the ``config`` folder beside the program, the ``addons``
folder and everything under ``addons/_data`` stay exactly where they are -
the swap moves one file and nothing else - and an addon installed by the
store keeps its own settings because they were never in play.

Once the new file is in place the addon offers to restart through the app's
own relaunch, so the copy that starts is the program that was just
downloaded; refusing leaves this one running the old version until the user
starts the new one themselves.

The repository, tag prefix and token live in
``addons/_data/manager_update/config.json``, so pointing the check at a fork
is a config edit. The tab is a declarative spec and its buttons report back
through ``on_widget``; it wears ``assets/manager_updateicon.png`` when the
program ships one and a drawn download mark when it does not.

The tab lists every published version rather than only the newest, newest
first, and says which of them this copy is: a version ahead is offered as an
update, the running one is marked, and an older one is offered as a way back -
the same swap, since a program that has gone wrong is fixed by putting the
previous file in its place. Going back has a floor, ``1.6.0`` by default and a
setting like everything else: a version older than that predates things the
swap itself relies on, so it is refused with the reason rather than installed.
Picking a row shows what that release was for - its title, the words it was
published with, the commit it was made from, who published it and when - and
the files it moved against the version before it, which GitHub answers from
the difference between the two tags.

When the check runs by itself it is the user who has to notice, not the tab
that is not open, so a version found is sent through ``api.ui.popup`` and comes
up as a notice from the app's tray icon, once per version per run. When checks
happen is configured: on starting the app, and again on a timer while it runs
for anyone who leaves it open for days.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from addon_common import AddonError, config_get, find_icon, merge_config

ADDON = {
    "name": "Berry Manager update",
    "version": "1.1",
    "author": "fligma",
    "description": "Lists the berry tags published on GitHub newest first, says "
                   "which one this copy is, and puts any of them - a newer "
                   "release, or an older one back to the floor set here - in the "
                   "running program's place once its release's file has been "
                   "checked, without touching the config or the addons. Each "
                   "version shows what it was published for and which files it "
                   "moved, and one found by a check that ran on its own is "
                   "announced as a notice.",
    "requires": [],
    # This talks to GitHub and to the disk it runs on, never to a device.
    "connection": "none",
    "api": 1,
}

TAB = "Manager update"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the download in progress, or nothing
published: list[dict] = []  # every version the last check read, newest first
picked: int = -1            # which of them the tab is showing, by index
found: dict | None = None   # the version the install button is pointed at
announced: set[str] = set()  # versions already sent up as a notice this run
ticker: Any = None          # the repeat-while-running timer, when one is on

#: What follows is far too small to be this program; a page or a stub.
MIN_PROGRAM_BYTES = 200_000

#: How many redirect hops one download may follow before it is called lost.
MAX_REDIRECTS = 8

#: The oldest version the swap will go back to when the config says nothing.
#: Below it the program predates how this addon stages and swaps a file, so a
#: move that far back is more likely to leave a broken install than a working
#: older copy, and is refused rather than attempted.
OLDEST_ALLOWED = "1.6.0"

# --- what can be configured, and what it starts out as ---

DEFAULTS = {
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "tag_prefix": "berry",
    "token": "",
    "check_startup": True,
    "check_again": False,
    "check_minutes": 60,
    "notify_found": True,
    "min_version": OLDEST_ALLOWED,
}

FIELDS = [
    {"key": "owner", "label": "GitHub owner", "kind": "text",
     "hint": "The account or organisation the repository belongs to"},
    {"key": "repo", "label": "Repository", "kind": "text"},
    {"key": "tag_prefix", "label": "Version tag prefix", "kind": "text",
     "hint": "Tags read as versions start with this: berry1.5.9 is version "
             "1.5.9. Anything else the tags name is ignored."},
    {"key": "token", "label": "GitHub token", "kind": "text",
     "hint": "Only needed for a private repository, or when anonymous "
             "requests are refused",
     "placeholder": "blank for a public repository"},
    {"key": "check_startup", "label": "Check when the app starts", "kind": "bool",
     "hint": "One read of the published tags a moment after the window is up. "
             "It costs a few requests, and says nothing when nothing is ahead."},
    {"key": "check_again", "label": "Keep checking while the app runs", "kind": "bool",
     "hint": "For a program left open for days: the check runs again on the "
             "interval below instead of only once at startup."},
    {"key": "check_minutes", "label": "Check again every", "kind": "number",
     "minimum": 5, "maximum": 24 * 60, "suffix": " min",
     "hint": "How long between the repeat checks"},
    {"key": "notify_found", "label": "Pop up a notice when a version is found",
     "kind": "bool",
     "hint": "A check that ran on its own sends the version it found up through "
             "the app's notifications, once per version, so it does not have to "
             "be looked for on a tab that is not open. Off, the tab's own "
             "status line is the only place it is said."},
    {"key": "min_version", "label": "Oldest version it may go back to",
     "kind": "text",
     "hint": "Going backwards is offered for any version at or above this one, "
             f"and refused below it. Blank is {OLDEST_ALLOWED}; a fork that "
             "publishes differently may name its own."},
]

GROUPS = [("Where updates come from",
           ["owner", "repo", "tag_prefix", "token"]),
          ("When it checks, and what it says",
           ["check_startup", "check_again", "check_minutes", "notify_found",
            "min_version"])]

BUTTONS = (("check", "Check now"), ("update", "Install the picked version"),
           ("page", "Open releases page"))

# --- small text helpers ---

def one_line(value: Any) -> str:
    """One clean line of text from whatever the form or file holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def safe_segment(value: Any) -> str:
    """A repository name or path piece with the shell-junk and traversal gone."""
    text = one_line(value).replace("..", "")
    return re.sub(r"[^A-Za-z0-9._/ -]", "", text).strip(" /")


def clean_token(value: Any) -> str:
    """A token stripped of the spaces a copy-paste tends to add."""
    return one_line(value).replace(" ", "")


# --- versions and tags ---

def _version_parts(value: Any) -> tuple:
    """The digits out of a dotted version, as a tuple that can be compared."""
    return tuple(int(part) for part in re.findall(r"\d+", str(value or "")))


def version_newer(remote: Any, local: Any) -> bool:
    """Whether the published version is ahead of the one running here.

    A dotted number is read as digits, so 1.10 beats 1.9 rather than losing
    to it as text would; a version with no digits at all is only compared
    as text.
    """
    wanted, have = _version_parts(remote), _version_parts(local)
    if wanted and have:
        length = max(len(wanted), len(have))
        return (wanted + (0,) * (length - len(wanted))
                > have + (0,) * (length - len(have)))
    clean_remote, clean_local = one_line(remote), one_line(local)
    return bool(clean_remote) and clean_remote != clean_local


def tag_version(name: Any, prefix: str) -> str:
    """The version a tag names, or nothing when it is not an update tag.

    ``berry1.5.9`` with the default prefix reads as ``1.5.9``; anything that
    does not start with the prefix, or starts with it and holds no digits,
    is not one of this program's releases.
    """
    text = one_line(name)
    mark = one_line(prefix)
    if not text or not mark or not text.lower().startswith(mark.lower()):
        return ""
    rest = text[len(mark):].strip()
    return rest if _version_parts(rest) else ""


def pick_latest(tags: list, prefix: str) -> tuple[str, str] | None:
    """The newest ``(tag, version)`` of a tag list, or nothing.

    GitHub hands tags back in an order of its own, so this does not trust
    it: every one that reads as a version is compared and the best kept.
    """
    best: tuple[str, str] | None = None
    for name in tags or []:
        version = tag_version(name, prefix)
        if not version:
            continue
        if best is None or version_newer(version, best[1]):
            best = (one_line(name), version)
    return best


def local_version() -> str:
    """The version this copy carries, as the app answers it."""
    if api is None:
        return ""
    try:
        return one_line(api.session.version())
    except (AddonError, AttributeError, TypeError):
        return ""


def system_name() -> str:
    """Which word the downloads speak this machine's language with."""
    return "windows" if sys.platform == "win32" else "linux"


# --- the tab ---

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QPlainTextEdit { font-family: Consolas, monospace; }
QPushButton { padding: 7px 14px; }
"""

BANNER_STYLE = ("QLabel { color: #e8e8e8; font-size: 13pt; font-weight: 600;"
                " padding: 4px 2px 2px 2px; }")

_STATUS_STYLE = ("QLabel { color: #9a9a9a; padding: 2px 6px;"
                 " border: 1px solid #3c3c3c; border-radius: 9px; }")

TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M12 3.5v9M8.4 9.2 12 12.8l3.6-3.6" fill="none" stroke="#23d18b" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M4.5 15v3a1.5 1.5 0 0 0 1.5 1.5h12A1.5 1.5 0 0 0 19.5 18v-3" '
    'fill="none" stroke="#c51a4a" stroke-width="1.5" stroke-linecap="round"/>'
    '</svg>'
)

#: The name this program shipped the tab's picture under, before the
#: ``{package}icon.png`` rule; tried after the standard name.
TAB_ICON_FILE = "updateicon.png"

#: This addon's own id: what the icon rule and its data folder are named for.
OWN_ID = "manager_update"


def tab_icon() -> str:
    """The shipped picture if the assets folder has one, else the drawn mark.

    ``manager_updateicon.png`` is the standard name every addon's picture is
    looked for under first; the older ``updateicon.png`` is still tried, so a
    copy that has worn one since before the rule keeps wearing it.
    """
    folder = None
    try:
        folder = api.paths.assets
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        folder = None
    if folder is None:
        return TAB_ICON
    try:
        return find_icon(folder, OWN_ID, TAB_ICON_FILE) or TAB_ICON
    except (OSError, AddonError):
        return TAB_ICON


_VERSION_STYLE = ("QLabel { color: #e8e8e8; font-size: 12pt; font-weight: 600;"
                  " padding: 4px 2px 0 2px; }")

_META_STYLE = "QLabel { color: #9a9a9a; font-size: 9pt; padding: 0 2px; }"

_STATE_STYLE = "QLabel { color: #23d18b; padding: 2px 2px 4px 2px; }"


def tab_spec() -> dict:
    """The published versions beside what the picked one was for, over the log.

    A version list is only useful beside the story of the version picked from
    it, so the tab is two panes: the tags newest first on the left, and on the
    right the release's own title, who published it and when, the commit it
    was made from, the words it was published with, and the files it moved.
    The job's log keeps the bottom half, where a download says what it is
    doing step by step.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry Manager update", "style": BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": "color: #23d18b; padding: 2px 4px;"},
            {"kind": "label", "id": "source", "text": "", "style": _STATUS_STYLE},
        ]},
        {"kind": "splitter", "vertical": True, "sizes": [5, 2], "children": [
            {"kind": "splitter", "sizes": [2, 3], "children": [
                {"kind": "table", "id": "list", "columns": ["Version",
                                                             "Where it stands"],
                 "stretch": 1, "col_widths": [90, 170],
                 "tooltip": "Newest first. Pick one to read what it was "
                            "published for; the button below puts whichever "
                            "one is picked in this program's place.",
                 "on": ["selected"]},
                {"kind": "column", "children": [
                    {"kind": "label", "id": "vtitle", "text": "", "word_wrap": True,
                     "style": _VERSION_STYLE},
                    {"kind": "label", "id": "vmeta", "text": "", "word_wrap": True,
                     "style": _META_STYLE},
                    {"kind": "label", "id": "vstate", "text": "", "word_wrap": True,
                     "style": _STATE_STYLE},
                    {"kind": "multi", "id": "vdetail", "readonly": True,
                     "expand": True,
                     "placeholder": "pick a version to read what it was for"},
                    {"kind": "table", "id": "vfiles", "columns": ["File",
                                                                  "Change"],
                 "stretch": 0, "col_widths": [300, 130],
                 "tooltip": "What this version moved against the one before it"},
                ]},
            ]},
            {"kind": "multi", "id": "log", "readonly": True,
             "placeholder": "every step of the last update, as it happened",
             "tooltip": "The same lines go into the program's own log"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": identifier, "text": text,
             "on": ["clicked"]} for identifier, text in BUTTONS],
        },
    ]}


def build_tab() -> None:
    """The tab as a spec; nothing here holds a widget.

    Pinned and icon-only, the same as the store's: an update check is something
    the program always offers, not a page the user laid out, so it holds beside
    Settings and is found by its mark.
    """
    api.tabs.add_tab(TAB, spec=tab_spec(), style=TAB_STYLE,
                     pinned=True, icon_only=True)
    api.tabs.set_icon(TAB, tab_icon())


def say(text: str, bad: bool = False) -> None:
    """One line on the tab's status, and nothing once this copy is gone."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style",
                            "color: #ffb020; padding: 2px 4px;" if bad
                            else "color: #23d18b; padding: 2px 4px;")
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


def show_text(identifier: str, text: str) -> None:
    """One of the read-only panels: the whole account, replaced in one go."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, identifier, "value", text)
    except (AddonError, TypeError):
        pass


def show_words(identifier: str, text: str, colour: str = "") -> None:
    """One of the header lines, and its colour when the caller has a mind to."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, identifier, "text", text)
        if colour:
            api.tabs.set_widget(TAB, identifier, "style",
                                f"color: {colour}; padding: 2px 2px 4px 2px;")
    except (AddonError, TypeError):
        pass


def show_source(values: dict) -> None:
    """The header chip: which owner/repo the tags are read from."""
    text = (f"{config_get(values, 'owner', '')}/{config_get(values, 'repo', '')}"
            f" @ {config_get(values, 'tag_prefix', 'berry')}*")
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


def _later(seconds: float, callback: Any) -> None:
    """Run one step a little later, on the app's clock, not this thread's."""
    api.schedule.after(float(seconds), callback)


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

    def headers(self, accept: str) -> dict:
        head = {"User-Agent": "berry-manager", "Accept": accept}
        if self.token:
            head["Authorization"] = f"token {self.token}"
        return head

    def get(self, host: str, path: str,
            accept: str = "application/octet-stream") -> tuple[int, bytes]:
        """``(status, body)`` for one small GET, retried once on a drop."""
        status, body = 0, b""
        for attempt in (0, 1):
            try:
                connection = self._connection(host)
                connection.request("GET", path, headers=self.headers(accept))
                answer = connection.getresponse()
                body = answer.read()
                status = int(answer.status)
                if status < 500 or attempt:
                    return status, body
            except (http.client.HTTPException, OSError):
                self.close(host)
                if attempt:
                    raise
                time.sleep(0.4)
        return status, body

    def open(self, host: str, path: str) -> Any:
        """One GET whose answer is handed over unread, for a large body."""
        connection = self._connection(host)
        connection.request("GET", path,
                           headers=self.headers("application/octet-stream"))
        return connection.getresponse()

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


def api_path(values: dict, tail: str) -> str:
    """One path under this repository on GitHub's API."""
    owner = safe_segment(config_get(values, "owner", DEFAULTS["owner"]))
    repo = safe_segment(config_get(values, "repo", DEFAULTS["repo"]))
    return f"/repos/{owner}/{repo}/{tail.lstrip('/')}"


def _refused(status: int, path: str) -> str:
    if status == 404:
        return (f"GitHub has nothing at {path}: check the owner and repository "
                "in the settings")
    if status in (403, 429):
        return (f"GitHub refused {path} ({status}). Anonymous requests are "
                "limited, so add a token in the settings or try again later")
    return f"GitHub answered {status} for {path}"


def get_json(values: dict, tail: str) -> Any:
    """One API answer as parsed JSON, or an AddonError saying why not."""
    path = api_path(values, tail)
    status, body = _http(values).get("api.github.com", path,
                                     accept="application/json")
    if status != 200:
        raise AddonError(_refused(status, path))
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise AddonError(f"what came back from {path} was not the JSON "
                         "GitHub's API answers with")


def list_tag_rows(values: dict) -> list[dict]:
    """Every published tag, as ``{"tag": its name, "sha": the commit it names}``.

    The commit is kept because it is what a version's story is read from -
    a tag on its own says only that a point in history has a name.
    """
    answer = get_json(values, "tags?per_page=100")
    rows = answer if isinstance(answer, list) else []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = one_line(row.get("name"))
        if not name:
            continue
        commit = row.get("commit") if isinstance(row.get("commit"), dict) else {}
        out.append({"tag": name, "sha": one_line(commit.get("sha"))})
    return out


def list_tags(values: dict) -> list[str]:
    """Every tag name the repository publishes, newest pages first read."""
    return [str(row.get("tag")) for row in list_tag_rows(values)]


def list_releases(values: dict) -> dict:
    """Every published release, keyed by the tag it was made under.

    One request for the lot: a list that needed a request per tag would run
    into GitHub's anonymous limit on the way to telling the user nothing new.
    Drafts are left out - they are an author's own notes, not a version.
    """
    answer = get_json(values, "releases?per_page=100")
    rows = answer if isinstance(answer, list) else []
    out: dict[str, dict] = {}
    for row in rows:
        tag = one_line(row.get("tag_name")) if isinstance(row, dict) else ""
        if not tag or row.get("draft"):
            continue
        out[tag] = row
    return out


def release_for(values: dict, tag: str) -> dict:
    """The release published under one tag; an empty account when there is none.

    A tag without a release is the normal state of a repository that tags its
    versions but has not published them, so this is an answer, not an error.
    """
    try:
        answer = get_json(values, f"releases/tags/{tag}")
    except AddonError as error:
        if "404" in str(error):
            return {}
        raise
    return answer if isinstance(answer, dict) else {}


def commit_of(values: dict, sha: str) -> dict:
    """The commit a tag points at: its title, its words, who made it and when.

    What a release was published *for* is usually written in the release; when
    the author wrote nothing, the commit message underneath the tag is the only
    account there is, so the tab reads it rather than showing a blank panel.
    """
    if not one_line(sha):
        return {}
    try:
        answer = get_json(values, f"commits/{sha}")
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        note(f"the commit it was made from could not be read: {error}")
        return {}
    if not isinstance(answer, dict):
        return {}
    inner = answer.get("commit") if isinstance(answer.get("commit"), dict) else {}
    message = str(inner.get("message") or "")
    lines = [line.rstrip() for line in message.splitlines() if line.strip()]
    author = inner.get("author") if isinstance(inner.get("author"), dict) else {}
    owner = answer.get("author") if isinstance(answer.get("author"), dict) else {}
    return {"sha": one_line(answer.get("sha") or sha),
            "title": lines[0] if lines else "",
            "body": "\n".join(lines[1:]).strip(),
            "who": one_line(owner.get("login")) or one_line(author.get("name")),
            "when": one_line(author.get("date"))}


# --- what a release offers this machine ---

_WIN_WORDS = ("windows", "win32", "win64", ".exe")
_LIN_WORDS = ("linux", "ubuntu", "debian")


def _asset_name(asset: dict) -> str:
    return one_line(asset.get("name"))


def pick_asset(release: dict, system: str) -> dict | None:
    """The release asset built for this system, or nothing.

    A name naming this system wins; a name naming the other one is never
    chosen; one naming neither is taken only when nothing better came.
    """
    assets = [row for row in (release.get("assets") or []) if isinstance(row, dict)]
    mine = _WIN_WORDS if system == "windows" else _LIN_WORDS
    other = _LIN_WORDS if system == "windows" else _WIN_WORDS
    named, neutral = [], []
    for asset in assets:
        low = _asset_name(asset).lower()
        if not one_line(asset.get("browser_download_url")):
            continue
        if any(word in low for word in mine):
            named.append(asset)
        elif not any(word in low for word in other):
            neutral.append(asset)
    return (named or neutral or [None])[0]


def body_links(release: dict) -> list[str]:
    """Every plain link the release's description carries, in order."""
    text = str(release.get("body") or release.get("body_text") or "")
    return re.findall(r"https?://[^\s<>()\[\]\"']+", text)


def pick_body_link(release: dict, system: str) -> str:
    """A download link from the description, for this system if one is there.

    This is what lets a release that carries no assets still update anything:
    a direct link to the program file - not to the workflow's artifact page,
    which needs a login and cannot be read anonymously, a fact the download
    itself reports when it is handed one.
    """
    mine = _WIN_WORDS if system == "windows" else _LIN_WORDS
    other = _LIN_WORDS if system == "windows" else _WIN_WORDS
    links = [one_line(link).rstrip(".,;") for link in body_links(release)]
    links = [link for link in links
             if not any(word in link.lower() for word in other)]
    for link in links:
        if any(word in link.lower() for word in mine):
            return link
    for link in links:
        if "berry" in link.lower() or "download" in link.lower():
            return link
    return ""


def download_source(release: dict, system: str) -> tuple[str, str, str] | None:
    """``(what it is called, where it comes from, how it was found)``, or None."""
    asset = pick_asset(release, system)
    if asset is not None:
        return (_asset_name(asset), one_line(asset.get("browser_download_url")),
                "release asset")
    link = pick_body_link(release, system)
    if link:
        return (link.rstrip("/").rsplit("/", 1)[-1], link, "link in the "
                "release description")
    return None


def changed_files(values: dict, tag: str, previous: str) -> list[dict]:
    """What one version moved against the version published before it.

    GitHub answers the difference between two tags with the file list, which is
    what lets the tab say what changed instead of only that something new was
    built. Nothing to compare against, a repository too big for GitHub to
    answer, a refused request: all of them are an empty list, because a first
    release having no account of itself beside it is normal, not a failure.
    """
    if not one_line(tag) or not one_line(previous):
        return []
    try:
        answer = get_json(values, f"compare/{previous}...{tag}")
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        note(f"the files it moved could not be read: {error}")
        return []
    files = answer.get("files") if isinstance(answer, dict) else None
    rows = [row for row in (files or []) if isinstance(row, dict)]
    if not rows and isinstance(answer, dict):
        note(f"GitHub named no files between {previous} and {tag}; "
             f"the answer carried {(answer.get('status') or 'nothing')}")
    return rows


# --- how one published version stands against this copy ---

#: The list's second column, and the colour the row is written in.
STANCE_WORDS = {"newer": "an update", "this": "this copy",
               "older": "a way back", "too old": "older than the floor",
               "unknown": "a tag nothing here reads as a version"}

STANCE_COLOUR = {"newer": "#23d18b", "this": "#ffb020", "older": "#6ea8fe",
                 "too old": "#c51a4a", "unknown": "#9a9a9a"}

#: The colours the file list is written in, one per kind of change GitHub names.
FILE_COLOUR = {"added": "#23d18b", "removed": "#c51a4a", "changed": "#ffb020",
               "renamed": "#6ea8fe", "copied": "#6ea8fe"}


def _floor(values: dict) -> str:
    """The oldest version the swap will go back to, as this copy is configured."""
    return one_line(config_get(values, "min_version", "")) or OLDEST_ALLOWED


def _prefix(values: dict) -> str:
    """The word a version tag starts with, defaulted rather than blank."""
    return (one_line(config_get(values, "tag_prefix", DEFAULTS["tag_prefix"]))
            or "berry")


def versions_equal(one: Any, other: Any) -> bool:
    """Whether two dotted versions name the same release.

    ``1.6`` and ``1.6.0`` are the same program, and a list that said otherwise
    would mark a copy as behind its own version.
    """
    left, right = one_line(one), one_line(other)
    if not left or not right:
        return False
    return not version_newer(left, right) and not version_newer(right, left)


def _sort_key(version: Any) -> tuple:
    """A version as a fixed-length row of digits, so a list of them sorts."""
    parts = list(_version_parts(version))[:6]
    return tuple(parts + [0] * (6 - len(parts)))


def stance_of(version: str, have: str, floor: str) -> str:
    """Where one published version stands: an update, this copy, a way back,
    or further back than the swap will go.

    The floor exists because a program older than it predates how this addon
    stages a download and moves it into place; below that line the risk of
    leaving a broken install beats the point of an older copy.
    """
    if not one_line(version):
        return "unknown"
    if versions_equal(version, have):
        return "this"
    if version_newer(version, have):
        return "newer"
    if version_newer(floor, version):
        return "too old"
    return "older"


def offer_of(entry: dict) -> str:
    """What the version list says beside one tag's version."""
    words = STANCE_WORDS.get(str(entry.get("stance") or ""), STANCE_WORDS["unknown"])
    if entry.get("stance") in ("newer", "older"):
        words += " - file attached" if entry.get("download") else " - no file"
    return words


def installable(entry: dict, values: dict, have: str, floor: str) -> tuple[bool, str]:
    """Whether the picked version may be put in this program's place, and why not.

    Going back is offered on the same terms as going forward - one file swapped
    for another, the config and the addons untouched - down to the floor, which
    is named in the refusal so a person can see the line and move it.
    """
    stance = str(entry.get("stance") or stance_of(entry.get("version"), have, floor))
    version = one_line(entry.get("version"))
    if stance == "this":
        return False, (f"berry{version} is the version already running here, so "
                       "there is nothing to put in its place")
    if stance == "too old":
        return False, (f"berry{version} is older than {floor}, the oldest version "
                       "this program will go back to. A build below that line "
                       "predates how an update stages and swaps its file, so the "
                       "move is refused rather than risked; the floor is a "
                       "setting, and a fork that publishes differently may name "
                       "its own.")
    if not entry.get("download"):
        return False, (f"{one_line(entry.get('tag'))} carries no download this "
                       "system can use - what to attach is written beside")
    return True, ""


def picked_entry() -> dict | None:
    """The version the tab is pointed at, or nothing when none is."""
    if 0 <= picked < len(published):
        return published[picked]
    return None


def row_of(text: str) -> int:
    """Which row of the version list one picked label names."""
    wanted = one_line(text)
    for index, entry in enumerate(published):
        if wanted in (entry.get("version"), entry.get("tag")):
            return index
    return -1


def restance(have: str, floor: str) -> None:
    """Re-place every version against a new running version, reading nothing.

    Used once an update or a downgrade has landed: this copy changed under the
    list, and a list that still called the installed version "an update" would
    be an account of a moment that has gone.
    """
    for entry in published:
        entry["stance"] = stance_of(entry.get("version"), have, floor)


# --- the check ---

def no_tags_account(values: dict, have: str, prefix: str) -> str:
    """The state of a repository that has never published anything, in words."""
    owner = one_line(config_get(values, "owner", DEFAULTS["owner"]))
    repo = one_line(config_get(values, "repo", DEFAULTS["repo"]))
    return (f"{owner}/{repo} publishes no {prefix}<version> tags, so there is "
            f"nothing to compare this copy (berry{have or '?'}) with.\n\n"
            f"Until a tag like {prefix}{have or '1.5.9'} is made, the check has "
            "nothing to say - and that is normal for a program that has never "
            "been released from here, not a failure.")


def no_download_account(entry: dict, prefix: str) -> str:
    """Why a tag that is ahead has nothing to download, and what fixes it.

    This is the one mistake the feature invites - a tag exists, so the program
    must be obtainable - so the reason and the remedy are both written out.
    """
    tag = one_line(entry.get("tag"))
    return (f"{tag} is published as a tag, but the release made for it carries "
            "no download this system can use.\n\n"
            f"Publish the tag as a release and attach the program file for this "
            f"system - its name saying windows or linux is what tells them "
            f"apart - or put a direct download link in the release's "
            f"description. Links to the workflow's artifacts cannot be read "
            f"without a sign-in, so those do not count.")


def _day(value: Any) -> str:
    """The date part of the timestamp GitHub answers with."""
    text = one_line(value)
    return text[:10] if text else ""


def _release_words(release: dict) -> str:
    """The words an author wrote when publishing one release."""
    return str(release.get("body") or release.get("body_text") or "").strip()


def load_detail(entry: dict, values: dict) -> None:
    """Read what only a further request can give, and keep it for next time.

    A version list is read in two requests whatever the number of versions, so
    the tab can come up quickly; the commit's own words and the file list belong
    to one version and are asked for when a row is picked, then cached on the
    entry - clicking back and forth costs nothing.
    """
    if entry.get("release") is None:
        try:
            entry["release"] = release_for(values, str(entry.get("tag")))
        except (AddonError, http.client.HTTPException, OSError,
                ValueError) as error:
            note(f"the release under {entry.get('tag')} could not be read: {error}")
            entry["release"] = {}
        entry["download"] = download_source(entry["release"], system_name())
    if entry.get("commit") is None and entry.get("sha"):
        entry["commit"] = commit_of(values, str(entry.get("sha")))
    if entry.get("files") is None:
        entry["files"] = changed_files(values, str(entry.get("tag")),
                                       str(entry.get("previous")))


def meta_of(entry: dict, values: dict) -> str:
    """The one line of facts beside a version's name."""
    release = entry.get("release") if isinstance(entry.get("release"), dict) else {}
    commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
    pieces = []
    day = _day(release.get("published_at")) or _day(commit.get("when"))
    who = one_line((release.get("author") or {}).get("login")
                   if isinstance(release.get("author"), dict) else "")
    who = who or str(commit.get("who") or "")
    if day or who:
        pieces.append(" ".join(part for part in (who, day) if part))
    sha = str(entry.get("sha") or commit.get("sha") or "")
    if sha:
        pieces.append(f"commit {sha[:7]}")
    source = entry.get("download")
    pieces.append(f"{source[2]}: {source[0]}" if source else "no file attached")
    files = entry.get("files")
    if isinstance(files, list):
        pieces.append(f"{len(files)} file(s) moved" if files
                      else "no files listed against the version before")
    elif entry.get("previous"):
        pieces.append(f"against {entry['previous']}")
    return "  \u00b7  ".join(pieces)


def state_of(entry: dict, have: str, ok: bool, why: str) -> str:
    """What the install button would do with this version picked."""
    version = one_line(entry.get("version"))
    if not ok:
        return why
    if entry.get("stance") == "older":
        return (f"A way back: installing puts berry{version} where "
                f"berry{have or 'this copy'} runs now. The config folder and "
                "the addons stay exactly where they are.")
    return (f"An update is ready: installing puts berry{version} where "
            f"berry{have or 'this copy'} runs now. The config folder and the "
            "addons stay exactly where they are.")


def account_of(entry: dict, values: dict, have: str, floor: str) -> str:
    """The story of one version: what it was published for, the commit under it."""
    release = entry.get("release") if isinstance(entry.get("release"), dict) else {}
    commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
    blocks = []
    if not entry.get("download") and entry.get("stance") in ("newer", "older"):
        blocks.append(no_download_account(entry, _prefix(values)))
    words = _release_words(release)
    from_commit = False
    if not words:
        words = "\n".join(part for part in (str(commit.get("title") or ""),
                                           str(commit.get("body") or ""))
                          if part)
        from_commit = bool(words)
    blocks.append("What it was published for\n"
                  + (words or "nothing was written for this release"))
    if commit and not from_commit:
        head = f"The commit it was made from\n{str(commit.get('sha'))[:7]}  " \
               f"{commit.get('title') or '(the commit carries no title)'}"
        rest = str(commit.get("body") or "").strip()
        stamp = "  ".join(part for part in (str(commit.get("who") or ""),
                                           str(commit.get("when") or "")) if part)
        blocks.append("\n".join(part for part in (head, stamp, rest) if part))
    files = entry.get("files")
    if isinstance(files, list) and files:
        blocks.append(f"{len(files)} file(s) moved against "
                      f"{entry.get('previous') or 'the version before'}; they "
                      "are listed below. GitHub answers the comparison with a "
                      "limit, so a very large release may show fewer than it "
                      "really moved.")
    return "\n\n".join(blocks)


def show_versions(have: str, floor: str) -> None:
    """Repaint the version list: newest first, each row saying where it stands."""
    if api is None:
        return
    rows = []
    for entry in published:
        rows.append({"cells": [one_line(entry.get("version")), offer_of(entry)],
                     "color": STANCE_COLOUR.get(str(entry.get("stance")), "#d6d6d6")})
    try:
        api.tabs.set_widget(TAB, "list", "rows", rows)
    except AddonError:
        pass


def show_files(entry: dict | None) -> None:
    """The files one version moved, coloured by what happened to each."""
    if api is None:
        return
    rows = []
    for file in (entry or {}).get("files") or []:
        status = one_line(file.get("status")).lower() or "changed"
        name = one_line(file.get("filename")) or one_line(file.get("previous_filename"))
        if not name:
            continue
        rows.append({"cells": [name, f"{status} "
                                     f"+{int(file.get('additions') or 0)}"
                                     f" -{int(file.get('deletions') or 0)}"],
                     "color": FILE_COLOUR.get(status, "#d6d6d6")})
    try:
        api.tabs.set_widget(TAB, "vfiles", "rows", rows)
    except (AddonError, TypeError, ValueError):
        pass


def show_detail(values: dict, entry: dict | None, have: str, floor: str,
                fetch: bool = True) -> None:
    """The right-hand pane, for one version or for the state of having none."""
    if api is None:
        return
    if entry is None:
        show_words("vtitle", "Nothing published yet", STANCE_COLOUR["unknown"])
        show_words("vmeta", "")
        show_words("vstate", "")
        show_text("vdetail", no_tags_account(values, have, _prefix(values)))
        show_files(None)
        return
    if fetch:
        load_detail(entry, values)
    release = entry.get("release") if isinstance(entry.get("release"), dict) else {}
    title = one_line(entry.get("tag"))
    named = one_line(release.get("name"))
    if named and named != title:
        title = f"{title}  \u2014  {named}"
    ok, why = installable(entry, values, have, floor)
    colour = STANCE_COLOUR.get(str(entry.get("stance")), "#d6d6d6")
    show_words("vtitle", title, colour)
    show_words("vmeta", meta_of(entry, values))
    show_words("vstate", state_of(entry, have, ok, why),
               colour if ok else STANCE_COLOUR["too old"])
    show_text("vdetail", account_of(entry, values, have, floor))
    show_files(entry)


def pick_row(index: int) -> None:
    """Point the tab at one row of the version list and read that one's story."""
    global picked
    if api is None or not 0 <= index < len(published):
        return
    picked = index
    values = merge_config(DEFAULTS, api.config.stored())
    have, floor = local_version(), _floor(values)
    show_detail(values, published[index], have, floor)


def _default_pick(entries: list) -> int:
    """Which version the tab opens on: an update if one is out, else this copy."""
    for stance in ("newer", "this"):
        for index, entry in enumerate(entries):
            if entry.get("stance") == stance:
                return index
    return 0


def announce(values: dict, have: str) -> None:
    """Send a version ahead of this copy up through the app's own notifications.

    A check that ran by itself is the user who has to notice, not a tab that is
    not open, so the newest version out is put up as a notice - once per version
    while this copy runs, which is what keeps a timer from repeating itself.
    """
    if api is None or not bool(config_get(values, "notify_found", True)):
        return
    ahead = [entry for entry in published if entry.get("stance") == "newer"]
    if not ahead:
        return
    entry = ahead[0]
    version = one_line(entry.get("version"))
    if version in announced:
        return
    announced.add(version)
    line = f"berry{version} is out"
    if have and not versions_equal(version, have):
        line += f" - this copy is berry{have}"
    line += (". Its file is attached, so the Manager update tab can install it."
             if entry.get("download") else
             ", but the release made for it has no download this system can use.")
    note(line)
    try:
        api.ui.popup("Berry Manager update", line)
    except AddonError as error:
        note(f"the notice could not be sent: {error}")


def check_now(_checked: bool = False) -> None:
    """Read every published version, place this copy among them, and say where
    it stands."""
    global published, picked
    if api is None or working:
        return
    values = merge_config(DEFAULTS, api.config.stored())
    have, floor = local_version(), _floor(values)
    prefix = _prefix(values)
    show_source(values)
    say("reading the published versions...")
    try:
        rows = list_tag_rows(values)
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        say(f"{error}", bad=True)
        note(f"tags: {error}")
        return
    try:
        releases = list_releases(values)
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        note(f"the releases will be read one at a time instead: {error}")
        releases = {}
    system = system_name()
    entries: list[dict] = []
    for row in rows:
        version = tag_version(row.get("tag"), prefix)
        if not version:
            continue
        release = releases.get(str(row.get("tag")))
        entry = {"tag": one_line(row.get("tag")), "version": version,
                 "sha": one_line(row.get("sha")), "previous": "",
                 "release": release if isinstance(release, dict) else None,
                 "commit": None, "files": None, "download": None,
                 "stance": stance_of(version, have, floor)}
        if entry["release"] is not None:
            entry["download"] = download_source(entry["release"], system)
        entries.append(entry)
    entries.sort(key=lambda item: _sort_key(item["version"]), reverse=True)
    for index, entry in enumerate(entries):
        entry["previous"] = (entries[index + 1]["tag"]
                             if index + 1 < len(entries) else "")
    published = entries
    announce(values, have)
    if not entries:
        picked = -1
        say(f"no {prefix}<version> tag is published yet - nothing to compare "
            "with")
        show_versions(have, floor)
        show_detail(values, None, have, floor)
        return
    picked = _default_pick(entries)
    newest, oldest = entries[0], entries[-1]
    counts = {"newer": 0, "this": 0, "older": 0, "too old": 0, "unknown": 0}
    for entry in entries:
        counts[str(entry.get("stance"))] = counts.get(str(entry.get("stance")), 0) + 1
    if counts["newer"]:
        word = "update" if counts["newer"] == 1 else "updates"
        say(f"{newest['tag']} is out - pick a version and Install puts it in "
            f"this copy's place ({counts['newer']} {word} ahead)", bad=True)
    elif counts["this"]:
        say(f"berry{have} is the newest published version - {counts['older']} "
            f"older one(s) can still be gone back to")
    else:
        say(f"nothing here is ahead of berry{have or '?'}; "
            f"{oldest['tag']} is the oldest tag published, "
            f"{floor} is how far back installing goes")
    show_versions(have, floor)
    show_detail(values, published[picked], have, floor)
    note(f"{len(entries)} published version(s) read; this copy is "
         f"berry{have or '?'}, the floor for going back is {floor}")


# --- the program this update replaces ---

def program_path() -> Path:
    """The running program's own file, beside which the update lands.

    Only a compiled build has one: a copy running from source is updated by
    replacing the checkout's files, and moving the interpreter's own binary
    aside would be moving the wrong thing.
    """
    if not getattr(sys, "frozen", False):
        raise AddonError("this copy runs from its source files: update the "
                         "checkout from the repository instead, the program "
                         "has no single file to replace")
    return Path(sys.executable)


def looks_like_program(data: bytes, system: str) -> bool:
    """Whether these bytes are a program or a page standing in for one."""
    if len(data) < MIN_PROGRAM_BYTES:
        return False
    if system == "windows":
        return data[:2] == b"MZ"
    return data[:4] == b"\x7fELF"


def extract_from_zip(data: bytes, system: str) -> bytes:
    """The program inside a zip, as the workflow's artifact ships it.

    The build uploads ``berry_manager.exe`` under a folder of its own inside
    the archive, so names are matched on their last piece, not their path;
    with nothing named for this system the biggest file is what was handed
    over, and a zip that holds nothing is an error, not a shrug.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (OSError, ValueError, zipfile.BadZipFile, NotImplementedError):
        raise AddonError("what came down calls itself a zip and is not one")
    with archive:
        rows = [row for row in archive.infolist()
                if not row.is_dir() and row.file_size > 0]
        if not rows:
            raise AddonError("the archive that came down holds no file")
        want = "berry_manager.exe" if system == "windows" else "berry_manager"
        named = [row for row in rows if row.filename.rsplit("/", 1)[-1].lower()
                 == want.lower()]
        if not named and system == "windows":
            named = [row for row in rows
                     if row.filename.rsplit("/", 1)[-1].lower().endswith(".exe")]
        best = max(named or rows, key=lambda row: row.file_size)
        with archive.open(best) as handle:
            return handle.read()


def check_bytes(data: bytes, system: str) -> tuple[bytes, str]:
    """What the download really is: the program's bytes and their story.

    A zip is unpacked; a raw program is taken as it stands; an HTML answer is
    called what it is - a page that wants a sign-in, like the workflow's
    artifact links - and refused by name, because that is the one mistake
    this feature invites.
    """
    if len(data) < 4:
        raise AddonError("what came down is far too small to be a program")
    if data[:2] == b"PK":
        inner = extract_from_zip(data, system)
        if not looks_like_program(inner, system):
            raise AddonError("the file inside that archive is not a program "
                             f"for {system}; attach the right one to the "
                             "release")
        return inner, f"unpacked from {len(data)} zip bytes"
    head = data[:64].lower()
    if b"<!doctype html" in head or b"<html" in head:
        raise AddonError("that address answered with a web page, not a "
                         "program. Links to the workflow's artifacts need a "
                         "GitHub sign-in and cannot be downloaded from the "
                         "app: publish the tag as a release and attach the "
                         "program file, or link one that needs no sign-in")
    if not looks_like_program(data, system):
        raise AddonError("what came down is not a program for this system, "
                         "or is too small to be one")
    return data, "as it came"


def staging_target(name: str) -> Path:
    """Where a download waits inside this addon's own data folder."""
    folder = api.paths.data
    return Path(folder) / name


def swap_program(target: Path, staged: Path) -> str:
    """Put the new program where the old one ran, and take the old one back.

    The two files live in the same folder, so each move is a rename, not a
    copy - the download never has to survive a half-written program. On
    Windows the running file cannot be deleted, only renamed, so it is
    parked as ``<name>.old``, deleted there and then if nothing holds it,
    or by the fresh copy on its next start. On Linux the new file simply
    replaces the old: the running process keeps the inode it opened, and
    the folder loses the name to the new program.
    """
    target, staged = Path(target), Path(staged)
    if not staged.is_file():
        raise AddonError("the downloaded program is not where it was staged")
    old = target.with_name(target.name + ".old")
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass                       # the next start takes both, or fails quietly
    if os.name == "nt":
        try:
            os.replace(target, old)
        except OSError as error:
            raise AddonError(f"the running program could not be moved "
                             f"aside: {error}")
        try:
            os.replace(staged, target)
        except OSError as error:
            try:
                os.replace(old, target)         # put back what was moved
            except OSError:
                pass
            raise AddonError(f"the new program could not be put in place: "
                             f"{error}")
        try:
            old.unlink()
            return "the old copy was moved aside and deleted"
        except OSError:
            return (f"the old copy stays beside it as {old.name} until the "
                    "new program's next start")
    try:
        staged.chmod(0o755)
    except OSError:
        pass
    try:
        os.replace(staged, target)
    except OSError as error:
        raise AddonError(f"the new program could not be put in place: {error}")
    return "the old copy was replaced in one step"


def restart_app() -> None:
    """Close this copy and start the new program through the app's own
    relaunch, which waits until this process has ended."""
    if api is None:
        return
    try:
        from manager_browser import cancel_restart_request, restart_process
    except Exception as error:                      # noqa: BLE001 - no app here
        say(f"the new program is in place; start it yourself when ready ({error})",
            bad=True)
        return
    restart_process()
    win = getattr(api, "win", None)
    if win is None:
        note("this copy cannot close itself from where it runs; start the "
             "new program when ready")
        return
    try:
        if win.close():
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()
        else:
            # A 'save changes?' answer of cancel keeps the window up: the
            # restart is no more owed than the close was carried out.
            cancel_restart_request()
            note("the restart was turned down; the new program is in place "
                 "for the next start")
    except Exception as error:                      # noqa: BLE001 - never fatal
        say(f"the new program is in place; start it yourself when ready ({error})",
            bad=True)


# --- the job: download one step per tick, then swap ---

def _begin(verb: str, values: dict) -> dict:
    global working, job
    working = True
    job = {"verb": verb, "values": values, "host": "", "path": "",
           "response": None, "chunks": [], "got": 0, "total": 0,
           "hops": 0, "ticks": 0}
    _buttons(False)
    try:
        api.tabs.set_widget(TAB, "log", "value", "")
    except AddonError:
        pass
    return job


def _finish(text: str, bad: bool = False) -> None:
    global working, job
    if job is not None and job.get("response") is not None:
        try:
            job["response"].close()
        except (http.client.HTTPException, OSError):
            pass
    job = None
    working = False
    _buttons(True)
    say(text, bad=bad)
    note(text)


def split_url(url: str) -> tuple[str, str]:
    """``(host, path?)`` of an https address; anything else is refused.

    The path keeps its query: GitHub's download addresses carry their
    one-time signature there, and dropping it is dropping the answer.
    """
    parts = urlsplit(one_line(url))
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise AddonError(f"{url} is not an https address to download from")
    tail = parts.path or "/"
    if parts.query:
        tail = f"{tail}?{parts.query}"
    return parts.netloc, tail


def connect_step() -> None:
    """One GET on its way: follow the redirects until bytes answer."""
    if job is None:
        return
    try:
        host, path = job["host"], job["path"]
        response = _http(job["values"]).open(host, path)
        status = int(response.status)
        if status in (301, 302, 303, 307, 308):
            location = one_line(response.getheader("Location"))
            try:
                response.read()
            except (http.client.HTTPException, OSError):
                pass
            job["hops"] += 1
            if not location or job["hops"] > MAX_REDIRECTS:
                _finish("the download kept being pointed somewhere else and "
                        "was given up on", bad=True)
                return
            job["host"], job["path"] = split_url(location)
            _later(0.0, connect_step)
            return
        if status != 200:
            try:
                response.close()
            except OSError:
                pass
            _finish(_refused(status, f"{host}{path.split('?')[0]}"), bad=True)
            return
        job["response"] = response
        length = one_line(response.getheader("Content-Length"))
        job["total"] = int(length) if length.isdigit() else 0
        peek = response.read(4096)
        if not peek:
            _finish("the download answered with nothing at all", bad=True)
            return
        job["chunks"] = [peek]
        job["got"] = len(peek)
        say(f"downloading... {_size(job['got'])}"
            + (f" of {_size(job['total'])}" if job["total"] else ""))
        _later(0.0, read_step)
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        _finish(f"the download could not start: {error}", bad=True)


def read_step() -> None:
    """One sip of the body per tick, so the tab keeps breathing."""
    if job is None:
        return
    try:
        data = job["response"].read(1024 * 1024)
        if data:
            job["chunks"].append(data)
            job["got"] += len(data)
            job["ticks"] += 1
            if job["ticks"] % 10 == 0:
                say(f"downloading... {_size(job['got'])}"
                    + (f" of {_size(job['total'])}" if job["total"] else ""))
            _later(0.0, read_step)
            return
        if job["total"] and job["got"] < job["total"]:
            _finish(f"the download stopped early at {_size(job['got'])} of "
                    f"{_size(job['total'])}", bad=True)
            return
        finish_download()
    except (http.client.HTTPException, OSError, ValueError) as error:
        _finish(f"the download broke off: {error}", bad=True)


def _size(count: int) -> str:
    """A byte count in the words a status line has room for."""
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MB"
    if count >= 1024:
        return f"{count // 1024} KB"
    return f"{count} B"


def finish_download() -> None:
    """Bytes are in hand: check them, stage the program, swap, offer restart."""
    global found
    if job is None:
        return
    wanted = one_line((found or {}).get("version"))
    tag = one_line((found or {}).get("tag"))
    back = str((found or {}).get("stance") or "") == "older"
    data = b"".join(job["chunks"])
    try:
        job["response"].close()
    except (http.client.HTTPException, OSError):
        pass
    job["response"] = None
    staged: Path | None = None
    try:
        system = system_name()
        program, how = check_bytes(data, system)
        say(f"{_size(len(program))} {how}; putting it in place")
        target = program_path()
        staged = staging_target(target.name + ".new")
        staged.write_bytes(program)
        words = swap_program(target, staged)
    except (AddonError, OSError, ValueError) as error:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass
        _finish(f"the update stopped: {error}", bad=True)
        return
    found = None
    _finish(f"berry{wanted or ''} is in place from {tag or 'a published tag'}; "
            f"{words}. The config folder and the addons were not touched.")
    # This copy now runs the version that was picked, so the list's account of
    # which ones are ahead of it has to be redone - without reading anything.
    values = merge_config(DEFAULTS, api.config.stored())
    have, floor = wanted or local_version(), _floor(values)
    restance(have, floor)
    show_versions(have, floor)
    entry = picked_entry()
    if entry is not None:
        show_detail(values, entry, have, floor, fetch=False)
    asking = ("Go back to it now?" if back else "Start the new one now?")
    if api.ui.question(
            "Restart Berry Manager?",
            (f"The program was {'put back' if back else 'installed'} where the "
             f"old one ran.\n\n{asking}")):
        restart_app()
    else:
        note("no restart asked for: this copy keeps running the old program "
             "until it is closed and the new one started")


def update_selected(_checked: bool = False) -> None:
    """Download the picked release and swap it for this program, forwards or back.

    The same careful swap either way: one file replaced, nothing else moved.
    What differs is the asking - going back over a newer version is worth a
    plainer warning than going up to one is, and the floor says no before
    anything is fetched rather than halfway through a swap.
    """
    global found
    if api is None or working:
        return
    entry = picked_entry()
    if entry is None:
        say("there is no version picked yet - press Check, then pick one from "
            "the list", bad=True)
        return
    values = merge_config(DEFAULTS, api.config.stored())
    have, floor = local_version(), _floor(values)
    ok, why = installable(entry, values, have, floor)
    if not ok:
        say(why, bad=True)
        return
    try:
        program_path()                        # a checkout says why not here
    except AddonError as error:
        say(str(error), bad=True)
        return
    back = str(entry.get("stance")) == "older"
    wanted = one_line(entry.get("version"))
    if not api.ui.question(
            "Go back to this version?" if back else "Update Berry Manager?",
            (f"Replace this copy (berry{have or '?'}) with berry{wanted}?\n\n"
             + ("This puts an older program where the newer one runs now. The "
                "config folder and the addons keep what they have, but a "
                "setting written by a newer version may mean nothing to this "
                "one.\n\n" if back else "")
             + "The new program is put where the old one ran. Nothing else "
               "moves: the config folder, the addons folder and every addon's "
               "settings beside them stay exactly as they are.")):
        say("the update was declined")
        return
    name, url, _how = entry["download"]
    found = entry
    note(f"{'going back' if back else 'updating'} to "
         f"{one_line(entry.get('tag'))}: fetching {name}")
    job_local = _begin("update", values)
    try:
        job_local["host"], job_local["path"] = split_url(url)
    except AddonError as error:
        found = None
        _finish(str(error), bad=True)
        return
    _later(0.0, connect_step)


def open_page(_checked: bool = False) -> None:
    """The releases, in an embedded tab of the app's own."""
    if api is None:
        return
    values = merge_config(DEFAULTS, api.config.stored())
    owner = safe_segment(config_get(values, "owner", DEFAULTS["owner"]))
    repo = safe_segment(config_get(values, "repo", DEFAULTS["repo"]))
    url = f"https://github.com/{owner}/{repo}/releases"
    try:
        api.tabs.add_web_tab("Berry Manager releases", url=url)
        say(f"opened {url}")
    except AddonError as error:
        say(f"the page could not be opened: {error}", bad=True)


# --- the hooks the app calls ---

def focus_tab(_checked: bool = False) -> None:
    """The Tools menu entry: go to the tab."""
    if api is None:
        return
    api.tabs.activate(TAB)


def _stop_timer(handle: Any) -> None:
    """Cancel one scheduled callback, whichever clock it was set on.

    A timer the app set up is a QTimer and answers to ``stop``; a timer in an
    addon running as its own process owns a thread and answers to ``cancel``.
    This addon can be either, so it asks for both names.
    """
    for name in ("cancel", "stop"):
        call = getattr(handle, name, None)
        if callable(call):
            try:
                call()
            except Exception:                   # a dead clock is still stopped
                pass
            return


def apply_schedule(values: dict | None = None) -> None:
    """Set the repeat-while-running check up, or take it down.

    A check left open for days wants to notice a release published at three in
    the morning; a check that only runs when the window opens is what nobody has
    to think about, so the timer is the opt-in and the interval its choice.
    Changing either setting re-makes the timer rather than leaving an old one
    running at the interval the user just changed.
    """
    global ticker
    if api is None:
        return
    if values is None:
        values = merge_config(DEFAULTS, api.config.stored())
    _stop_timer(ticker)
    ticker = None
    if not bool(config_get(values, "check_again", DEFAULTS["check_again"])):
        return
    try:
        minutes = int(config_get(values, "check_minutes", DEFAULTS["check_minutes"]))
    except (TypeError, ValueError):
        minutes = int(DEFAULTS["check_minutes"])
    minutes = max(1, minutes)
    ticker = api.schedule.every(minutes * 60.0, check_now)
    note(f"checking again every {minutes} min while the app runs")


def init(plugin_api: Any) -> None:
    """Build the tab from the saved config and check once on the app's own time."""
    global api
    api = plugin_api
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Berry Manager update - addons/_data/manager_update/config.json")
    build_tab()
    show_source(values)
    api.ui.menu_item("Check for Berry Manager updates", focus_tab, menu="Tools")
    apply_schedule(values)
    if config_get(values, "check_startup", True):
        _later(1.0, check_now)


def start() -> None:
    """Nothing to do beyond what init queued."""
    return None


def shutdown() -> None:
    global api, working, job, found, published, picked, ticker, announced
    _drop_http()
    _stop_timer(ticker)
    ticker = None
    announced.clear()
    published = []
    picked = -1
    working = False
    job = None
    found = None
    api = None


def pick_from(payload: dict) -> None:
    """Show the version the user just picked in the list.

    A table's ``selected`` answer carries only how many rows are picked, so the
    row is read back from the widget; a double-click arrives with its text
    already, and either way an unknown label is ignored rather than guessed at.
    """
    text = one_line((payload or {}).get("text"))
    if not text and api is not None:
        try:
            rows = api.tabs.widget_value(TAB, "list", "selected")
        except (AddonError, AttributeError, TypeError):
            rows = None
        if isinstance(rows, (list, tuple)) and rows:
            text = one_line(rows[0])
    index = row_of(text)
    if index >= 0:
        pick_row(index)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Something on the tab was used: run what its id and interaction mean.

    The handler is looked up by name when the click lands, not fixed at
    import: a test - or a hot patch - that swaps one of these functions on
    the module then truly gets the click. Picking a row is the one interaction
    that is not a button, and it goes to :func:`pick_from` instead.
    """
    if str(tab) != TAB:
        return
    if str(event) == "selected" and str(identifier) == "list":
        handler = globals().get("pick_from")
        if callable(handler):
            handler(payload or {})
        return
    if str(event) != "clicked":
        return
    name = {"check": "check_now", "update": "update_selected",
            "page": "open_page"}.get(str(identifier))
    handler = globals().get(name or "")
    if callable(handler):
        handler()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: re-point, re-time and re-check.

    The interval and the notice switch are worth honouring without a restart,
    since changing them is the reason most people open this addon's settings.
    """
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    values = api.config.all()
    show_source(values)
    apply_schedule(values)
    if not working:
        check_now()


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
