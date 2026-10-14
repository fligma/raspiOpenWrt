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
through ``on_widget``; it wears ``assets/updateicon.png`` when the program
ships one and a drawn download mark when it does not.
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

from addon_common import AddonError, config_get, merge_config

ADDON = {
    "name": "Berry Manager update",
    "version": "1.0",
    "author": "fligma",
    "description": "Compares this program's version with the berry tags "
                   "published on GitHub and, when one is ahead, downloads the "
                   "release made for it, checks what came is a program, "
                   "swaps it in beside the untouched config and addons, and "
                   "offers to restart on the new copy.",
    "requires": [],
    "api": 1,
}

TAB = "Manager update"

api = None                  # filled in by init()
working = False             # one job at a time
job: dict | None = None     # the download in progress, or nothing
found: dict | None = None   # the newer tag the last check found, with its download

#: What follows is far too small to be this program; a page or a stub.
MIN_PROGRAM_BYTES = 200_000

#: How many redirect hops one download may follow before it is called lost.
MAX_REDIRECTS = 8

# --- what can be configured, and what it starts out as ---

DEFAULTS = {
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "tag_prefix": "berry",
    "token": "",
    "check_startup": True,
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
    {"key": "check_startup", "label": "Check when the app starts", "kind": "check"},
]

GROUPS = [("Where updates come from",
           ["owner", "repo", "tag_prefix", "token", "check_startup"])]

BUTTONS = (("check", "Check now"), ("update", "Download and install"),
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

#: The picture the tab wears if the assets folder has one.
TAB_ICON_FILE = "updateicon.png"


def tab_icon() -> str:
    """The shipped picture if the assets folder has one, else the drawn mark."""
    folder = None
    try:
        folder = api.paths.assets
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        folder = None
    if folder is None:
        return TAB_ICON
    try:
        candidate = Path(str(folder)) / TAB_ICON_FILE
        if candidate.is_file():
            return str(candidate)
    except (OSError, AddonError):
        return TAB_ICON
    return TAB_ICON


def tab_spec() -> dict:
    """One header line, the last check's account beside the job's log, buttons."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry Manager update", "style": BANNER_STYLE},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "expand": True, "style": "color: #23d18b; padding: 2px 4px;"},
            {"kind": "label", "id": "source", "text": "", "style": _STATUS_STYLE},
        ]},
        {"kind": "splitter", "sizes": [2, 3], "children": [
            {"kind": "multi", "id": "info", "readonly": True,
             "label": "The last check", "expand": True,
             "placeholder": "this copy's version, the newest published tag, "
                            "and where the new program would come from"},
            {"kind": "multi", "id": "log", "readonly": True, "expand": True,
             "placeholder": "every step of the last update, as it happened",
             "tooltip": "The same lines go into the program's own log"},
        ]},
        {"kind": "row", "children": [
            {"kind": "button", "id": identifier, "text": text,
             "on": ["clicked"]} for identifier, text in BUTTONS],
        },
    ]}


def build_tab() -> None:
    """The tab as a spec; nothing here holds a widget."""
    api.tabs.add_tab(TAB, spec=tab_spec(), style=TAB_STYLE)
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


def show_info(lines) -> None:
    """The 'last check' panel: the whole account of where this copy stands."""
    if api is None:
        return
    try:
        text = lines if isinstance(lines, str) else "\n".join(lines)
        api.tabs.set_widget(TAB, "info", "value", text)
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


def list_tags(values: dict) -> list[str]:
    """Every tag name the repository publishes, newest pages first read."""
    answer = get_json(values, "tags?per_page=100")
    rows = answer if isinstance(answer, list) else []
    return [one_line(row.get("name")) for row in rows
            if isinstance(row, dict) and one_line(row.get("name"))]


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


# --- the check ---

def describe(values: dict, best: tuple[str, str] | None, have: str,
             source: tuple[str, str, str] | None, count: int) -> str:
    """The 'last check' panel in the user's own words."""
    prefix = one_line(config_get(values, "tag_prefix", DEFAULTS["tag_prefix"]))
    lines = [f"this copy:    {have or 'a version with nothing recorded'}"
             f"  (from the program's own build)"]
    if best is None:
        lines.append(f"newest tag:   none - the repository publishes no "
                     f"{prefix}<version> tags yet")
        lines.append("")
        lines.append("Until a tag like " + f"{prefix}{have or '1.5.9'}" +
                     " is made there is nothing to compare with; that is "
                     "normal for a program that has never been released "
                     "from here.")
        return "\n".join(lines)
    tag, wanted = best
    lines.append(f"newest tag:   {tag}  (version {wanted}, of {count} tags read)")
    if version_newer(wanted, have):
        if source is not None:
            lines.append(f"download:     {source[0]}  ({source[2]})")
            lines.append("")
            lines.append("This copy is behind. 'Download and install' fetches "
                         "that file, swaps it for the running program and "
                         "leaves the config and addons folders exactly as "
                         "they are.")
        else:
            lines.append("download:     nothing for this system yet")
            lines.append("")
            lines.append(f"The tag {tag} is ahead of this copy, but the "
                         "release made for it carries no download this "
                         "system can use. Publish the tag as a release and "
                         "attach the program file for this system - its name "
                         "saying windows or linux is what tells them apart - "
                         "or put a direct download link in the release's "
                         "description. Links to the workflow's artifacts "
                         "cannot be read without a sign-in, so those do not "
                         "count.")
    else:
        lines.append("")
        lines.append("This copy is the newest published version.")
    return "\n".join(lines)


def check_now(_checked: bool = False) -> None:
    """Read the tags, compare them with this copy, and say where things stand."""
    global found
    if api is None or working:
        return
    values = merge_config(DEFAULTS, api.config.stored())
    show_source(values)
    say("reading the published tags...")
    prefix = one_line(config_get(values, "tag_prefix",
                                 DEFAULTS["tag_prefix"])) or "berry"
    try:
        tags = list_tags(values)
    except (AddonError, http.client.HTTPException, OSError, ValueError) as error:
        say(f"{error}", bad=True)
        note(f"tags: {error}")
        return
    best = pick_latest(tags, prefix)
    have = local_version()
    source: tuple[str, str, str] | None = None
    if best is not None and version_newer(best[1], have):
        try:
            source_row = download_source(release_for(values, best[0]),
                                         system_name())
            source = source_row
        except (AddonError, ValueError) as error:
            note(f"the release could not be read: {error}")
            source = None
    found = None
    if best is not None and version_newer(best[1], have):
        tag, wanted = best
        found = {"tag": tag, "version": wanted, "installed": have,
                 "download": source}
        if source is not None:
            say(f"{tag} is out - Download and install will replace this copy",
                bad=True)
        else:
            say(f"{tag} is out, but nothing for this system is attached to it",
                bad=True)
    elif best is None:
        say(f"no {prefix}<version> tag is published yet - nothing to compare "
            "with")
    else:
        say(f"berry{have} is the newest published tag" if have
            else "no published tag is ahead of this copy")
    show_info(describe(values, best, have, source, len(tags)))


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
    values = job["values"]
    wanted = one_line((found or {}).get("version"))
    tag = one_line((found or {}).get("tag"))
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
    _finish(f"berry{wanted or ''} is in place from {tag or 'the newest tag'}; "
            f"{words}. The config folder and the addons were not touched.")
    if api.ui.question("Restart Berry Manager?",
                       "The new program is installed where the old one ran.\n\n"
                       "Close this copy and start the new one now?"):
        restart_app()
    else:
        note("no restart asked for: this copy keeps running the old program "
             "until it is closed and the new one started")
    show_info(describe(values, None, wanted or local_version(), None, 0))


def update_selected(_checked: bool = False) -> None:
    """Download the newer release and swap it for this program."""
    if api is None or working:
        return
    if found is None:
        say("press Check first - there is no newer version in hand", bad=True)
        return
    download = found.get("download")
    if not download:
        say("the newer tag has no download for this system yet - see the "
            "account beside", bad=True)
        return
    try:
        program_path()                        # a checkout says why not here
    except AddonError as error:
        say(str(error), bad=True)
        return
    values = merge_config(DEFAULTS, api.config.stored())
    have = one_line(found.get("installed")) or local_version()
    wanted = one_line(found.get("version"))
    if not api.ui.question(
            "Update Berry Manager?",
            f"Replace this copy (berry{have or '?'}) with berry{wanted}?\n\n"
            "The new program is put where the old one ran. Nothing else "
            "moves: the config folder, the addons folder and every addon's "
            "settings beside them stay exactly as they are."):
        say("the update was declined")
        return
    name, url, _how = download
    note(f"updating to {one_line(found.get('tag'))}: fetching {name}")
    job_local = _begin("update", values)
    try:
        job_local["host"], job_local["path"] = split_url(url)
    except AddonError as error:
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
    if config_get(values, "check_startup", True):
        _later(1.0, check_now)


def start() -> None:
    """Nothing to do beyond what init queued."""
    return None


def shutdown() -> None:
    global api, working, job, found
    _drop_http()
    working = False
    job = None
    found = None
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A button on the tab was clicked: run what its id means.

    The handler is looked up by name when the click lands, not fixed at
    import: a test - or a hot patch - that swaps one of these functions on
    the module then truly gets the click.
    """
    if str(tab) != TAB or str(event) != "clicked":
        return
    name = {"check": "check_now", "update": "update_selected",
            "page": "open_page"}.get(str(identifier))
    handler = globals().get(name or "")
    if callable(handler):
        handler()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: point the header and re-check."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    values = api.config.all()
    show_source(values)
    if not working:
        check_now()


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
