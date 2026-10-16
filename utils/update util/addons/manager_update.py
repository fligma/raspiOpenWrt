#!/usr/bin/env python3
"""Berry Manager update: check the published tags and replace this program.

Adds a ``Manager update`` tab. The program's own releases are the tags of this
repository whose names start with ``berry`` - ``berry1.5.9`` is version 1.5.9 -
and the addon reads them from GitHub's API, picks the newest, and compares it
with the version this copy carries (``api.session.version()``, which is
``core.const.APP_VERSION``). No ``berry`` tag existing yet is the normal
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
store keeps its own settings because they were never in play. Going *back* over
a newer copy is the one case where that is not simply assumed to be wanted, and
the tab asks instead of deciding - see the three answers below.

Once the new file is in place the addon offers to restart through the app's
own relaunch, so the copy that starts is the program that was just
downloaded; refusing leaves this one running the old version until the user
starts the new one themselves.

The repository and tag prefix live in
``addons/_data/manager_update/config.json``, so pointing the check at a fork
is a config edit. The tab holds no token of its own: every request carries
whatever ``api.session.git_token()`` answers with, which is the profile's one
GitHub token while the switch on this addon's row in the store's Addon manager
is on, and nothing at all while it is off. The tab is a declarative spec and
its buttons report back through ``on_widget``; it wears
``assets/manager_updateicon.png`` when the program ships one and a drawn
download mark when it does not.

The tab lists every published version rather than only the newest, but groups
them into their major lines - ``2.0``, ``1.9``, ``1.8`` - newest line first, each
line opening to the hotfixes under it. A flat list of every build reads as noise
once a program has a few years of tags, and the line below the one this copy is
on is only wanted occasionally, so it starts as one button and its versions are
laid out when it is opened; opening costs nothing, since the tags were all read
in one request. Where the version itself sits is said three ways: in the heading
of its line, as one coloured chip in the pane beside it, and in the words of the
entry in its line's dropdown.

What a version was published for is read on demand and cached: picking an entry
asks GitHub for that one release's account and the files it moved against the
version before it, and coming back to it afterwards costs nothing. Until a
version is picked the pane says so rather than showing an empty box, because the
list of versions itself is only two requests however many there are, and reading
the story of every one of them to draw a tab would be the slowest way to say
nothing new.

Installing is offered in both directions: a version ahead is an update, the
running one is marked, and an older one is a way back - the same swap, since a
program that has gone wrong is fixed by putting the previous file in its place.
Going back has a floor, ``1.6.0`` by default and a setting like everything else:
a version older than that predates things the swap itself relies on, so it is
refused with the reason rather than installed, warning or no warning. Above the
floor, an older version is asked about with three honest answers rather than a
yes or no, because what is at risk is not the program but everything written down
beside it: keep it and take the risk, back up ``config`` and every addon's data
folder into a zip beside the program and delete what a newer copy wrote - the
installed addons, the profile files, the written themes - or leave this copy
where it is. Nothing is fetched or written before that answer is given.

When the check runs by itself it is the user who has to notice, not the tab
that is not open, so a version found is sent through ``api.ui.popup`` and comes
up as a notice from the app's tray icon, once per version per run. When checks
happen is configured: on starting the app, and again on a timer while it runs
for anyone who leaves it open for days.

An install is the one long job here, and it is shown as one. While the bytes are
coming down the tab's browsing panes give way to the job's own view: a terminal
for a background, printing every step as it arrives, with a card in the middle
naming the version being fetched and counting its share of the download. The same
steps and the same share go up in the app's floating notice at the corner of the
screen, so the job can be followed from any other tab, and the notice ends with
the job - filled and fading when the swap landed, closed straight away when it
did not. The view is put together from the spec language alone (an ``overlay``
over a ``terminal``), and a tab built without it simply keeps the browsing panes
on screen: the job does not care where its lines are printed.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import ssl
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from addon_common import AddonError, config_get, find_addon_picture, merge_config

ADDON = {
    "name": "Berry Manager update",
    "version": "1.4",
    "author": "fligma",
    "description": "Groups the berry tags published on GitHub into their major "
                   "lines, newest first, each line opening to its own builds, and "
                   "says which version this copy is. Any of them - a newer "
                   "release, or an older one back to the floor set here - is put "
                   "in the running program's place once its release's file has "
                   "been checked. A version's words, its commit and its file list "
                   "are read only when it is picked, and kept; going back to an "
                   "older copy asks three questions instead of one, so the choice "
                   "is between taking what is written down as it stands, backing "
                   "it up and starting clean, or leaving this copy alone. One "
                   "found by a check that ran on its own is announced as a notice, "
                   "and a download is shown as a job: a terminal behind a card "
                   "that names the version and counts its bytes, with the same "
                   "progress in a floating notice.",
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
lines: list[dict] = []      # those versions, grouped into their major lines
opened: list[str] = []      # which major line has its dropdown pulled out
notes: dict[str, dict] = {}  # what a pick read about one tag, kept for later
slot_names: dict[str, list] = {}   # the words each dropdown was last handed
filling = False             # a dropdown is being laid out, not chosen from

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

#: The GitHub token is not one of these: it belongs to the profile and is read
#: through ``profile_token``, so one field feeds the store, the updater and every
#: other addon that asks GitHub for anything.
GROUPS = [("Where updates come from",
           ["owner", "repo", "tag_prefix"]),
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


def profile_token() -> str:
    """The token this addon is allowed to send to GitHub, which is the profile's.

    There is one token for a profile and it is written in Settings > Connection;
    the switch on this addon's row in the store's Addon manager decides whether
    ``api.session.git_token()`` answers with it at all. An empty string is a real
    answer rather than a failure - it means anonymous, which is how a public
    repository has always been read - so a build with no api, no token and a
    refused call all check exactly the same way.
    """
    if api is None:
        return ""
    try:
        return clean_token(api.session.git_token())
    except (AddonError, AttributeError, TypeError):
        return ""


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

# --- the look: every colour and every size taken from the theme in force ---

#: What each helper below falls back to is the sheet this tab has always been
#: drawn with, so a copy of it with no theme to read - a test, or this addon run
#: on its own - looks exactly the way it did before themes had a say.

def _colour(token: str, fallback: str) -> str:
    """One colour out of the interface, or the hex that stood in for it."""
    if api is None:
        return fallback
    try:
        found = one_line(api.theme.color(token))
    except (AddonError, AttributeError, TypeError):
        return fallback
    return found or fallback


def _metric(knob: str, fallback: int) -> int:
    """One of the theme's own sizes, so the tab spends the air the app spends."""
    if api is None:
        return fallback
    try:
        value = api.theme.metric(knob)
    except (AddonError, AttributeError, TypeError):
        return fallback
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(0, number)


def _room(times: int = 1) -> int:
    """The theme's gap between two things, taken several times for a loose line."""
    return _metric("gap_px", 8) * max(1, int(times))


def tab_style() -> str:
    """The sheet the tab wears: the theme's ground, edges, accent and sizes.

    Written as a function rather than kept as a block of text because the theme
    is only in hand once the app has handed over an api - a constant would be
    settled before then and could not name a colour at all.
    """
    radius = _metric("radius_px", 6)
    gap = _room()
    return f"""
QWidget {{ background-color: {_colour('base', '#161616')}; }}
QGroupBox {{ border: 1px solid {_colour('border', '#3c3c3c')};
             border-radius: {radius}px; margin-top: {gap + 4}px;
             padding: {gap + 4}px {gap + 2}px {gap + 2}px {gap + 2}px;
             font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
                    left: {gap + 4}px; padding: 0 {max(2, gap // 2)}px;
                    color: {_colour('accent', '#c51a4a')}; }}
QPlainTextEdit {{ font-family: Consolas, monospace; }}
QPushButton {{ padding: {max(2, gap - 1)}px {_metric('pad_x_px', 14)}px; }}
"""


def banner_style() -> str:
    """The tab's own name, in the theme's brightest words."""
    return (f"QLabel {{ color: {_colour('bright', '#e8e8e8')};"
            f" font-size: 13pt; font-weight: 600;"
            f" padding: {max(2, _room() // 2)}px 2px 2px 2px; }}")


def chip_style() -> str:
    """The small plate the repository's address is written on."""
    gap = max(2, _room() - 2)
    return (f"QLabel {{ color: {_colour('note', '#9a9a9a')};"
            f" padding: 2px {gap}px;"
            f" border: 1px solid {_colour('border', '#3c3c3c')};"
            f" border-radius: {_metric('radius_px', 6) + 3}px; }}")

TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M12 3.5v9M8.4 9.2 12 12.8l3.6-3.6" fill="none" stroke="#23d18b" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M4.5 15v3a1.5 1.5 0 0 0 1.5 1.5h12A1.5 1.5 0 0 0 19.5 18v-3" '
    'fill="none" stroke="#c51a4a" stroke-width="1.5" stroke-linecap="round"/>'
    '</svg>'
)

#: The picture the tab wears when a folder it is read from holds one: this
#: addon's id and then ``icon.png``, the one name every addon's picture is
#: looked up under. Written out only to say where the file goes.
TAB_ICON_FILE = "manager_updateicon.png"

#: This addon's own id: what the icon rule and its data folder are named for.
OWN_ID = "manager_update"


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark.

    ``manager_updateicon.png`` is the only name the lookup makes, in the addon's
    own assets folder and then in the program's, so the file in either decides
    what the tab wears and nothing here has to be kept in step with it.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    try:
        return find_addon_picture(paths, OWN_ID) or TAB_ICON
    except (OSError, AddonError):
        return TAB_ICON


def version_style() -> str:
    """A picked version's own name."""
    return (f"QLabel {{ color: {_colour('bright', '#e8e8e8')};"
            f" font-size: 12pt; font-weight: 600;"
            f" padding: {max(2, _room() // 2)}px 2px 0 2px; }}")


def meta_style() -> str:
    """The line of facts under it, in the theme's quietest words."""
    return (f"QLabel {{ color: {_colour('note', '#9a9a9a')};"
            f" font-size: 9pt; padding: 0 2px; }}")


def mono_style() -> str:
    """The commit a version was cut from, in the theme's mono face.

    A sha is read, not spoken, so it is set apart from the sentence it sits in
    and given the one font that keeps its columns lined up.
    """
    return (f"QLabel {{ color: {_colour('muted', '#aaaaaa')};"
            f" font-family: Consolas, monospace; font-size: 9pt;"
            f" padding: 0 2px; }}")


def section_style() -> str:
    """The heading over a panel of the picked version's account."""
    return (f"QLabel {{ color: {_colour('accent', '#c51a4a')};"
            f" font-size: 9pt; font-weight: 600;"
            f" padding: {max(2, _room() - 2)}px 2px 2px 2px; }}")


def major_style() -> str:
    """The heading of one major line of versions."""
    return (f"QLabel {{ color: {_colour('text', '#e0e0e0')};"
            f" font-size: 10pt; font-weight: 600;"
            f" padding: {max(2, _room() // 2)}px 2px 0 2px; }}")


def state_style() -> str:
    """What installing the picked version would do, in the theme's own words."""
    return (f"QLabel {{ color: {_colour('ok', '#23d18b')};"
            f" padding: 2px 2px {max(2, _room() - 4)}px 2px; }}")


def words_style(colour: str) -> str:
    """One of the header lines, tinted by whatever the caller says it means."""
    return (f"color: {colour};"
            f" padding: 2px 2px {max(2, _room() - 4)}px 2px;")


def chip_words_style(colour: str) -> str:
    """The one chip a version's state is written on, worn in that state's colour."""
    return (f"QLabel {{ color: {colour};"
            f" border: 1px solid {colour}; border-radius:"
            f" {_metric('radius_px', 6)}px;"
            f" padding: 1px {max(4, _room() - 2)}px; }}")

#: The splitter that holds the browsing panes over the job's log, and the share
#: of the tab's height each of the two is promised. The log starts folded away -
#: see :func:`show_log` - so the versions fill the screen, and it opens back to
#: two parts in seven when it is wanted.
LOG_PANE = "logpane"
LOG_SHARE = [5, 2]

#: How many major lines the left pane can lay out at once. A spec is built once
#: and an addon cannot grow a column while it runs, so the rows are promised
#: here and shown or hidden as the published versions need them; a line past the
#: last of these is counted in words instead, and the releases page holds every
#: one of them whatever their number.
MAJOR_SLOTS = 10

# --- the view a job puts over the browsing panes ---

#: The card that floats in the middle of the job's console. It has to be read
#: over the output moving behind it, so it is drawn on its own dark plate rather
#: than on the terminal's own ground, and kept to a width that leaves the lines
#: behind it visible at the sides.
def job_card_style() -> str:
    """The plate a job's name and its bar sit on, over the moving console."""
    return (f"#jobcard {{ background-color: rgba(24, 24, 24, 236);"
            f" border: 1px solid {_colour('border', '#3c3c3c')};"
            f" border-radius: {_metric('radius_px', 6) + 4}px;"
            f" padding: {_room() + 4}px; max-width: 340px; }}")


def job_title_style() -> str:
    """The version being fetched, in the theme's brightest words."""
    return (f"QLabel {{ color: {_colour('bright', '#f2f2f2')};"
            f" font-size: 13pt; font-weight: 700; padding: 0px; }}")


def job_sub_style() -> str:
    """The step it is on, in the mono face the console itself is drawn in."""
    return (f"QLabel {{ color: {_colour('note', '#9a9a9a')};"
            f" font-size: 9pt; padding: 0px;"
            f" font-family: Consolas, monospace; }}")

#: How much of a step the card is allowed to be. The download's whole line goes
#: onto the console behind the card anyway; the card only has to be readable.
_JOB_STEP_LEN = 52


def _job_view() -> dict:
    """The pane a download puts over the version list: its console, and the version in hand.

    The ``terminal`` is never connected to anything - it is used for what it is as
    a screen as well as a shell, so the bytes coming down are counted onto it as
    they arrive. The card floats over it through an ``overlay``, which keeps it
    centred when the tab is resized rather than leaving the addon to count pixels;
    the pane starts hidden, since an install is only one press of a button away.
    """
    return {"kind": "overlay", "id": "jobpane", "visible": False, "children": [
        {"kind": "terminal", "id": "jobterm", "font_size": 10},
        {"kind": "column", "id": "jobcard", "object_name": "jobcard",
         "align": "center", "style": job_card_style(), "children": [
             {"kind": "image", "id": "jobicon", "size": 64},
             {"kind": "label", "id": "jobname", "text": "",
              "style": job_title_style(), "word_wrap": True},
             {"kind": "label", "id": "jobstep", "text": "",
              "style": job_sub_style(), "word_wrap": True},
             {"kind": "progress", "id": "jobbar", "maximum": 100, "value": 0},
         ]},
    ]}


# --- the left pane: one row per major line, its hotfixes in a dropdown ---

def _major_slot(index: int) -> dict:
    """One major line as it is laid out: its heading, its dropdown, its button.

    All three are built and left hidden, because a row of the pane is decided by
    how many lines the published tags make and a spec cannot be grown afterwards.
    The button is the line when it is shut - ``1.9  more`` - and stays as the way
    to shut it again; the heading and the dropdown are the line opened.
    """
    return {"kind": "column", "id": f"major{index}", "visible": False,
            "children": [
                {"kind": "label", "id": f"mhead{index}", "text": "",
                 "word_wrap": True, "visible": False, "style": major_style()},
                {"kind": "combo", "id": f"mpick{index}", "visible": False,
                 "tooltip": "The versions published on this line, newest first. "
                            "Picking one reads what it was for; the button puts "
                            "whichever one is picked in this program's place.",
                 "on": ["changed"]},
                {"kind": "button", "id": f"mopen{index}", "text": "",
                 "visible": False, "on": ["clicked"]},
            ]}


def _majors_pane() -> dict:
    """The published versions as their major lines, newest line first.

    A flat list of every hotfix reads as noise once a program has a few years of
    tags: what a person wants is the line this copy is on and the handful of
    builds under it, so the pane is one row per line and the line's versions are
    the dropdown's. Lines below the one being read start shut, and opening one
    costs nothing, because the tags were all read in one request.
    """
    return {"kind": "column", "children": [
        {"kind": "label", "text": "Published version lines",
         "style": section_style()},
        {"kind": "scroll", "children": [{"kind": "column", "children": [
            {"kind": "column", "id": "majors", "children":
             [_major_slot(index) for index in range(MAJOR_SLOTS)] + [
                 {"kind": "label", "id": "mrest", "text": "", "visible": False,
                  "word_wrap": True, "style": meta_style()},
                 {"kind": "spacer"}]},
        ]}]},
    ]}


def _story_pane() -> dict:
    """The account of the version picked: its name, its state, its words, its files."""
    return {"kind": "column", "children": [
        {"kind": "label", "id": "vtitle", "text": "", "word_wrap": True,
         "style": version_style()},
        {"kind": "label", "id": "vmeta", "text": "", "word_wrap": True,
         "style": meta_style()},
        {"kind": "row", "children": [
            {"kind": "label", "id": "vchip", "text": "", "visible": False},
            {"kind": "label", "id": "vsha", "text": "", "visible": False,
             "style": mono_style()},
        ]},
        {"kind": "label", "id": "vstate", "text": "", "word_wrap": True,
         "style": state_style()},
        {"kind": "splitter", "vertical": True, "sizes": [3, 1], "children": [
            {"kind": "column", "children": [
                {"kind": "label", "text": "What it was published for",
                 "style": section_style()},
                {"kind": "multi", "id": "vdetail", "readonly": True,
                 "expand": True,
                 "placeholder": "pick a version to read what it was for"},
            ]},
            {"kind": "column", "children": [
                {"kind": "label", "text": "The files it moved",
                 "style": section_style()},
                {"kind": "table", "id": "vfiles",
                 "columns": ["File", "Change"],
                 "stretch": 0, "col_widths": [300, 130],
                 "tooltip": "What this version moved against the "
                            "one before it"},
            ]},
        ]},
    ]}


def tab_spec() -> dict:
    """The published versions beside what the picked one was for, over the log.

    A version list is only useful beside the story of the version picked from
    it, so the tab is two panes: the major lines newest first on the left, each
    opening to its own builds, and on the right the release's own title, who
    published it and when, the commit it was cut from, the words it was
    published with, and the files it moved. The job's log keeps the bottom half,
    where a download says what it is doing step by step.

    The two browsing panes are held in a column of their own, because a job
    lays its console over them - see :func:`_job_view` - and the log stays under
    the whole of it.

    Every line between those panes moves: the list beside its account of the
    picked version, the version's words above the files it moved, and the log
    under both. The log starts folded away and stays out of the way until it is
    asked for - the box in the header, a drag of the fold line, or a job that
    ended badly, which opens it by itself.
    """
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Berry Manager update",
             "style": banner_style()},
            {"kind": "label", "id": "status", "text": "Ready.",
             "word_wrap": True, "expand": True,
             "style": words_style(_colour('ok', '#23d18b'))},
            {"kind": "label", "id": "source", "text": "",
             "style": chip_style()},
            {"kind": "check", "id": "showlog", "text": "show log",
             "checked": False,
             "tooltip": "The job's log under the two panes. It starts folded "
                        "away; tick it to pull it back up, or drag - or "
                        "double-click - the line between the panes and it",
             "on": ["toggled"]},
        ]},
        {"kind": "splitter", "vertical": True, "id": LOG_PANE,
         "sizes": LOG_SHARE, "children": [
            {"kind": "column", "children": [
                {"kind": "splitter", "id": "browse", "sizes": [2, 3],
                 "children": [
                    _majors_pane(),
                    _story_pane(),
                ]},
                _job_view(),
            ]},
            {"kind": "multi", "id": "log", "collapsed": True, "readonly": True,
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
    api.tabs.add_tab(TAB, spec=tab_spec(), style=tab_style(),
                     pinned=True, icon_only=True)
    api.tabs.set_icon(TAB, tab_icon())


def _set(identifier: str, prop: str, value: Any) -> None:
    """One write to one control of this tab, quietly.

    A control the spec does not hold is not a failure worth a traceback over -
    an older spec, or a test that stopped at the check - so it is dropped here
    rather than at every call.
    """
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, identifier, prop, value)
    except (AddonError, TypeError):
        pass


def say(text: str, bad: bool = False) -> None:
    """One line on the tab's status, and nothing once this copy is gone."""
    if api is None:
        return
    colour = _colour('warn', '#ffb020') if bad else _colour('ok', '#23d18b')
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style", f"color: {colour}; padding: 2px 4px;")
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
    _job_write(text)
    api.log(text)


def show_log(open_it: bool) -> None:
    """Fold the job's log away, or pull it back up under the browsing panes.

    A folded pane keeps its fold line, so the log is reachable by dragging that
    line, or double-clicking it, as much as by the box in the header; the two
    stay in step because the box is set from what is being asked for, and Qt only
    reports a check box whose tick really moved.
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
    """The reused connections for this job, carrying the profile's token.

    ``values`` names the repository a check is aimed at, which is nothing to do
    with what is sent as credentials: a profile has one token and every fork it
    points at is read with that one. Keying the pool by the token is what keeps
    a switched-off addon, or a profile that never wrote one, on the anonymous
    fetcher - the only kind there was before the field.
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
                "limited, so put a token in Settings > Connection, switch "
                "this addon on for it, or try again later")
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

#: Which token of the theme each of those states is asked for by. The hex tables
#: above stay as the answer when there is no theme to ask - a test, or this addon
#: run as its own program - so the colours below are the same ones either way.
STANCE_TOKEN = {"newer": "ok", "this": "warn", "older": "icon_alt",
                "too old": "accent", "unknown": "note"}

FILE_TOKEN = {"added": "ok", "removed": "accent", "changed": "warn",
              "renamed": "icon_alt", "copied": "icon_alt"}


def stance_colour(stance: Any) -> str:
    """Where a version stands, in the theme's colour for it."""
    name = one_line(stance)
    return _colour(STANCE_TOKEN.get(name, "note"),
                   STANCE_COLOUR.get(name, "#d6d6d6"))


def file_colour(status: Any) -> str:
    """What happened to one file, in the theme's colour for it."""
    name = one_line(status).lower()
    return _colour(FILE_TOKEN.get(name, "note"),
                   FILE_COLOUR.get(name, "#d6d6d6"))


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


# --- the major lines the left pane is built from ---

def major_of(version: Any) -> str:
    """The line one published version belongs to, named by its first two numbers.

    ``1.6.1`` and ``1.6.9`` are both on the ``1.6`` line: what separates them is a
    hotfix, and a pane that lists them one under the other reads as noise once a
    program has a few years of tags behind it. A version with a single number is
    its own line, and a tag nothing here reads as a version belongs to none.
    """
    parts = _version_parts(version)
    if not parts:
        return ""
    return ".".join(str(part) for part in parts[:2])


def majors(entries: list) -> list[dict]:
    """The published versions grouped into their major lines, newest line first.

    The entries arrive newest first, so the first version of a line is its head -
    the one a dropdown opens on - and the lines come out in the order their heads
    were read, which is newest line first without a sort of their own. Each line
    holds the ``published`` indices of the versions on it rather than copies of
    them, so a repaint never has to match a version back up by its text and the
    story already read for one stays where it belongs.
    """
    lines: list[dict] = []
    by_name: dict[str, dict] = {}
    for index, entry in enumerate(entries or []):
        name = major_of(entry.get("version"))
        if not name:
            continue
        line = by_name.get(name)
        if line is None:
            line = {"major": name, "head": index, "entries": []}
            by_name[name] = line
            lines.append(line)
        line["entries"].append(index)
    return lines


def line_at(slot: int) -> dict | None:
    """The major line laid out in one row of the pane, when there is one to lay."""
    if 0 <= slot < len(lines):
        return lines[slot]
    return None


def slot_of(major: Any) -> int:
    """Which row of the pane one major line is laid out in; -1 past the last.

    A version published on a line older than the pane can hold has no row and no
    dropdown, which is why the rows it could not show are counted in words under
    the last of them rather than left out in silence.
    """
    wanted = one_line(major)
    for slot, line in enumerate(lines[:MAJOR_SLOTS]):
        if str(line.get("major") or "") == wanted:
            return slot
    return -1


def open_line_for(index: int) -> str:
    """The line the pane has to be pulled open on for one version to be visible."""
    if not 0 <= index < len(published):
        return ""
    return major_of(published[index].get("version"))


def slot_number(identifier: Any) -> int:
    """Which row of the pane one of this pane's controls belongs to; -1 for none.

    The heading, the dropdown and the button of a line are named for their row,
    so a report coming back from ``mpick3`` is answered by the fourth line - the
    number is read off the name rather than kept in a table that could fall out
    of step with the pane.
    """
    text = re.findall(r"(\d+)$", one_line(identifier))
    if not text:
        return -1
    number = int(text[0])
    return number if 0 <= number < MAJOR_SLOTS else -1


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


def read_already(tag: Any) -> bool:
    """Whether one version's story has been read and kept from an earlier pick."""
    return one_line(tag) in notes


def keep_read(tag: str, entry: dict) -> None:
    """Put what was just read for one version where the next pick can find it.

    The download a release carries is left out on purpose: which file this machine
    can take is worked out from whatever the release is known to be at the moment
    the pane is drawn, so a version whose file has since come off its release is
    not offered from a remembered answer.
    """
    notes[tag] = {key: entry.get(key) for key in
                  ("release", "commit", "files")}


def load_detail(entry: dict, values: dict) -> None:
    """Read what only a further request can give, once for each version.

    The list itself is read in two requests whatever the number of versions, so
    the tab can come up quickly; a commit's words and a file list belong to the
    one version picked and are asked for when it is picked, then kept under its
    tag. That is what lets the pane say *not read yet* honestly, and what makes
    coming back to a version already read cost nothing at all.

    Each part is only asked for when the entry says nothing about it, which is how
    the batch read of the releases is used rather than repeated - and how a
    version known to carry no file is left known to carry no file.
    """
    tag = one_line(entry.get("tag"))
    kept = notes.get(tag) or {}
    if entry.get("release") is None:
        release = kept.get("release")
        if not isinstance(release, dict):
            try:
                release = release_for(values, tag)
            except (AddonError, http.client.HTTPException, OSError,
                    ValueError) as error:
                note(f"the release under {tag} could not be read: {error}")
                release = {}
        entry["release"] = release
        entry["download"] = download_source(release, system_name())
    if entry.get("commit") is None:
        commit = kept.get("commit")
        if isinstance(commit, dict) and commit:
            entry["commit"] = commit
        elif entry.get("sha"):
            entry["commit"] = commit_of(values, str(entry.get("sha")))
        else:
            entry["commit"] = {}
    if entry.get("files") is None:
        files = kept.get("files")
        entry["files"] = files if isinstance(files, list) else changed_files(
            values, tag, str(entry.get("previous")))
    if tag:
        keep_read(tag, entry)


def meta_of(entry: dict, values: dict) -> str:
    """The one line of facts beside a version's name, its commit aside.

    The sha is left out of the sentence it used to sit in: it is read rather than
    spoken, so it wears the mono face of its own beside the state chip.
    """
    release = entry.get("release") if isinstance(entry.get("release"), dict) else {}
    commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
    pieces = []
    day = _day(release.get("published_at")) or _day(commit.get("when"))
    who = one_line((release.get("author") or {}).get("login")
                   if isinstance(release.get("author"), dict) else "")
    who = who or str(commit.get("who") or "")
    if day or who:
        pieces.append(" ".join(part for part in (who, day) if part))
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
    """Repaint the left pane: one row per major line, newest line first.

    The grouping is redone from the versions in hand, so a repository that grew a
    line or lost one is reflected without another read, and a line whose versions
    have all gone is taken off the page rather than left shut. Which lines are
    open survives a repaint - the user's own doing, not this pane's - but only
    for lines that still exist, and a dropdown never lays its versions down here
    unless its line is already open.
    """
    global lines, filling
    if api is None:
        return
    lines = majors(published)
    named = {str(line.get("major") or "") for line in lines}
    opened[:] = [major for major in opened if major in named]
    was, filling = filling, True
    try:
        for slot in range(MAJOR_SLOTS):
            line = line_at(slot)
            if line is None:
                _clear_slot(slot)
            else:
                _paint_slot(slot, line, have, floor)
        rest = len(lines) - MAJOR_SLOTS
        _set("mrest", "visible", rest > 0)
        if rest > 0:
            _set("mrest", "text",
                 f"{rest} older line(s) have no room here; the releases page "
                 "under the tab holds every version on them")
    finally:
        filling = was


# --- the left pane, line by line ---

def item_text(entry: dict) -> str:
    """One entry of a line's dropdown: its version, and where it stands.

    A dropdown only shows one of its entries at a time, so the marking beside the
    version is what tells a person which of the hotfixes on a line is worth
    opening before they open it.
    """
    return f"{one_line(entry.get('version'))}  \u2014  {offer_of(entry)}"


def combo_text(payload: Any) -> str:
    """What a dropdown says it is showing, out of whatever it reported."""
    if isinstance(payload, dict):
        return one_line(payload.get("text") or payload.get("value"))
    return one_line(payload)


def index_of(major: Any, text: Any) -> int:
    """Which published version one dropdown entry names, within its own line.

    The version is the first word of the entry, so the marking beside it never
    gets in the way of the match, and the search stays inside the line that
    reported it: a version number published on two lines of a fork's repository
    is answered by the line the user was looking at rather than the first one
    that comes to mind.
    """
    wanted = one_line(text).split(" ")[0].strip()
    slot = slot_of(major)
    if slot < 0 or not wanted:
        return -1
    for index in lines[slot].get("entries") or []:
        if not 0 <= index < len(published):
            continue
        entry = published[index]
        if wanted in (entry.get("version"), entry.get("tag")):
            return index
    return -1


def major_words(line: dict, have: str, floor: str) -> str:
    """One major line's heading: its number, how many builds it holds, and where
    this copy sits in it.

    A line this copy is on is worth telling apart from a line that is only ahead
    of it or only behind it, because the builds under a person's own version are
    the ones they might actually want to go back to - and a line further back than
    the floor is named as such, since opening it can only ever end in a refusal.
    """
    entries = [published[index] for index in line.get("entries") or []
               if 0 <= index < len(published)]
    stances = {str(entry.get("stance") or "") for entry in entries}
    major = str(line.get("major") or "")
    if major and major == major_of(have):
        where = "this copy is on it"
    elif "this" in stances:
        where = "this copy is on it"
    elif stances == {"too old"}:
        where = f"further back than {floor}, where going back stops"
    elif "newer" in stances and len(stances) > 1:
        where = "ahead of this copy, and a way back lower down"
    elif "newer" in stances:
        where = "ahead of this copy"
    elif "older" in stances:
        where = "a way back"
    else:
        where = "nothing here reads as a version"
    builds = len(entries)
    return (f"berry{major}.x  \u2014  {builds} published "
            f"{'build' if builds == 1 else 'build(s)'}, {where}")


def not_read_words(entry: dict) -> str:
    """What the account panel says of a version nobody has picked yet.

    Honest rather than empty: the tag list named this version and the release
    batch said whether it has a file, but its words, its commit and its files are
    a request apiece and are only asked for when a person picks it.
    """
    return (f"{one_line(entry.get('tag'))} has not been read beyond the tag "
            "list - pick it from its line and what it was published for, the "
            "commit it was cut from and the files it moved will be fetched, and "
            "kept for whenever you come back to it.")


def _fill(slot: int, line: dict) -> None:
    """Hand one open line its versions, and point them at what the pane is showing.

    A dropdown is laid out when its line is opened and not before, which is what
    makes a line below the one this copy is on cost nothing until it is asked for.
    Names a line already holds are left alone, because clearing a dropdown that
    has not changed would move the user's choice out from under their hand. All of
    it happens with ``filling`` up: laying versions down reports a choice of its
    own, and that report is not anybody picking anything.
    """
    key = f"mpick{slot}"
    names = [item_text(published[index]) for index in line.get("entries") or []
             if 0 <= index < len(published)]
    global filling
    was, filling = filling, True
    try:
        if names and slot_names.get(key) != names:
            _set(key, "items", names)
            slot_names[key] = names
        if 0 <= picked < len(published) and picked in (line.get("entries") or []):
            _set(key, "value", item_text(published[picked]))
    finally:
        filling = was


def _clear_slot(slot: int) -> None:
    """Take one row of the pane off the page, and forget what its dropdown held."""
    _set(f"major{slot}", "visible", False)
    for name in ("mhead", "mpick", "mopen"):
        _set(f"{name}{slot}", "visible", False)
    slot_names.pop(f"mpick{slot}", None)


def _paint_slot(slot: int, line: dict, have: str, floor: str) -> None:
    """One major line on the page: open with its versions, or shut as one button.

    The button is the line when it is shut and the way to shut it again once it is
    open, so a pane of six lines is six lines tall rather than six lines and all
    of their builds.
    """
    major = str(line.get("major") or "")
    open_it = bool(major) and major in opened
    builds = len(line.get("entries") or [])
    _set(f"major{slot}", "visible", True)
    _set(f"mhead{slot}", "visible", open_it)
    _set(f"mhead{slot}", "text", major_words(line, have, floor))
    _set(f"mpick{slot}", "visible", open_it)
    _set(f"mopen{slot}", "visible", True)
    _set(f"mopen{slot}", "text", f"{major}  {'less' if open_it else 'more'}")
    _set(f"mopen{slot}", "tooltip",
         f"{'Hide' if open_it else 'Show'} the {builds} version(s) published on "
         f"the {major} line")
    if open_it:
        _fill(slot, line)


def toggle_major(slot: int, force: Any = None) -> None:
    """Pull one line's dropdown out, or shut it back into its button.

    Opening a line is the only thing that lays its versions down, and even that is
    made from the tags already read, so it costs no request at all; what costs a
    request is picking a version, whose story is then read once and kept. A line
    opened that holds nothing the right-hand pane is telling the story of points
    itself at its own newest, so the two panes never disagree about the pick.
    """
    line = line_at(slot)
    if line is None or api is None:
        return
    major = str(line.get("major") or "")
    want = (major not in opened) if force is None else bool(force)
    if want:
        if major not in opened:
            opened.append(major)
    elif major in opened:
        opened.remove(major)
    values = merge_config(DEFAULTS, api.config.stored())
    _paint_slot(slot, line, local_version(), _floor(values))
    if want and picked not in (line.get("entries") or []):
        pick_row(int(line.get("head") or 0))


def pick_text(slot: int, payload: Any) -> None:
    """A dropdown reported what it is showing: move the pane to that version.

    Laying a line's versions down reports a choice as it goes, and this addon run
    as its own process hears that back later rather than under the hand that set
    it, so the guard up during a fill is not enough on its own: a version already
    picked is not picked a second time, and a line already showing the pick is not
    laid out again.
    """
    if filling:
        return
    line = line_at(slot)
    if line is None:
        return
    index = index_of(line.get("major"), combo_text(payload))
    if index < 0 or index == picked:
        return
    pick_row(index)


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
                     "color": file_colour(status)})
    _set("vfiles", "rows", rows)


def show_chip(entry: dict) -> None:
    """One version's own state, as a single chip in the colour it is worn in.

    The chip is the short answer - an update, this copy, a way back, below the
    floor - so a person scrolling back to a version they read earlier can place it
    without reading the sentence under it again.
    """
    if api is None:
        return
    stance = str(entry.get("stance") or "unknown")
    _set("vchip", "visible", True)
    _set("vchip", "text", offer_of(entry))
    _set("vchip", "style", chip_words_style(stance_colour(stance)))
    sha = one_line(entry.get("sha"))
    _set("vsha", "visible", bool(sha))
    _set("vsha", "text", f"commit {sha[:7]}" if sha else "")


def hide_chip() -> None:
    """Take the chip and the commit off the pane, for a version there is none of."""
    _set("vchip", "visible", False)
    _set("vsha", "visible", False)


def show_detail(values: dict, entry: dict | None, have: str, floor: str,
                fetch: bool = True) -> None:
    """The right-hand pane, for one version or for the state of having none.

    ``fetch`` is whether picking this version was the reason the pane is being
    drawn. A redraw that is not a pick - the list being re-placed after an
    install, say - must not go to GitHub for a story it has never been asked for,
    so it says as much in the account panel and leaves the rest of the pane, which
    the tag list already named, in place.
    """
    if api is None:
        return
    if entry is None:
        show_words("vtitle", "Nothing published yet", stance_colour("unknown"))
        show_words("vmeta", "")
        show_words("vstate", "")
        hide_chip()
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
    colour = stance_colour(entry.get("stance"))
    show_words("vtitle", title, colour)
    show_words("vmeta", meta_of(entry, values))
    show_chip(entry)
    show_words("vstate", state_of(entry, have, ok, why),
               colour if ok else stance_colour("too old"))
    if fetch or read_already(entry.get("tag")):
        show_text("vdetail", account_of(entry, values, have, floor))
    else:
        show_text("vdetail", not_read_words(entry))
    show_files(entry)


def pick_row(index: int) -> None:
    """Point the tab at one published version and read that one's story.

    The line the version sits on is pulled open on the way, so the left pane is
    never left showing the dropdown of one line while the right pane tells the
    story of another: a pick made from anywhere, by the check or by a person, is
    a pick the pane can see.
    """
    global picked
    if api is None or not 0 <= index < len(published):
        return
    picked = index
    values = merge_config(DEFAULTS, api.config.stored())
    have, floor = local_version(), _floor(values)
    major = major_of(published[index].get("version"))
    slot = slot_of(major)
    if slot >= 0:
        if major and major not in opened:
            opened.append(major)
        _paint_slot(slot, lines[slot], have, floor)
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
    # A version that has gone out of the repository takes the story that was read
    # for it with it, so a copy left checking on a timer cannot fill its cache
    # with tags nothing lists any more.
    live = {one_line(entry.get("tag")) for entry in entries}
    for gone in [tag for tag in notes if tag not in live]:
        notes.pop(gone, None)
    announce(values, have)
    if not entries:
        picked = -1
        opened.clear()
        say(f"no {prefix}<version> tag is published yet - nothing to compare "
            "with")
        show_versions(have, floor)
        show_detail(values, None, have, floor)
        return
    picked = _default_pick(entries)
    # The pane opens on the line the picked version is on and on nothing else:
    # the newest of the newest major is what a person came for, and every older
    # line waits as one button until it is asked for.
    opened[:] = [open_line_for(picked)]
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


# --- what a way back may ask to be taken away first ---

#: What can go wrong when an older program is put where a newer one runs, said in
#: the dialog rather than left to be discovered on the next start. All three are
#: written-down things, which is why the answer to them is a folder of files and not
#: an apology.
DOWNGRADE_RISKS = (
    "a profile file the older copy cannot read, which loses the connections and "
    "settings of whoever wrote it",
    "an addon installed against a newer api, which the older program refuses to "
    "load",
    "a theme key that does not exist yet, which draws a half-styled window until "
    "the theme is changed",
)

#: The three answers the downgrade asks for, as their buttons are named and as
#: they come back: keep what is written, start clean, or leave it alone.
ERASE_ANSWER = "Erase data and download"
KEEP_ANSWER = "Download with current setup"
CANCEL_ANSWER = "Cancel"

#: The one ``.theme`` file that came inside the program, so an erase takes every
#: copy somebody wrote beside it and leaves the look that always works.
SHIPPED_THEME = "default.theme"


def program_folder() -> Path:
    """The folder the program runs from, which everything beside it is named for.

    The backup of an erase lands here - the one place somebody who cannot start the
    program can still find a file - and so do the three kinds of thing it takes away:
    the written config, the written themes and the addons that did not come inside
    the program. A copy running from its source files has no program to look beside,
    so it falls back on the folder the app was started from.
    """
    try:
        return Path(program_path()).parent
    except AddonError:
        pass
    try:
        return Path(str(api.paths.base))
    except (AddonError, AttributeError, TypeError) as error:
        raise AddonError(f"the folder the program runs from cannot be found: {error}")


def installed_rows() -> list[dict]:
    """Every addon the app has in its folder, as the app's own list names them.

    Read through the api rather than off the disk because the app is what knows
    which of them came inside the program: an erase leaves those and takes
    everything a person installed, and telling them apart by whether a file is
    there would leave an installed addon's program behind or take a shipped one
    with it.
    """
    if api is None:
        return []
    try:
        rows = api.session.addons()
    except (AddonError, AttributeError, TypeError) as error:
        note(f"the installed addons could not be listed: {error}")
        return []
    return [row for row in (rows or []) if isinstance(row, dict)]


def erasable(row: dict) -> bool:
    """Whether one addon row names something an erase may take away.

    Two things keep it from taking the parts that have to keep working: an addon
    that came inside the program stays, and this addon stays whatever the scan
    calls it, because the download that follows an erase is being run out of these
    very files.
    """
    if row.get("bundled") or not row.get("id"):
        return False
    return one_line(row.get("id")) != OWN_ID


def addon_files(base: Path, addon_id: Any) -> list[Path]:
    """Every file and folder one addon was written as, and its own data beside it.

    An addon the store installed is a program named for its id - a compiled
    ``.exe`` or ``.bin``, or the source file a person dropped in - with its
    settings and downloads under ``addons/_data``, and, for a build that keeps its
    files apart, a folder of its own beside them. Every shape is named here so that
    taking an addon away takes the whole of it, leaving nothing that would come back
    to life on the next scan.
    """
    name = safe_segment(addon_id)
    if not name:
        return []
    folder = base / "addons"
    return [folder / f"{name}.exe", folder / f"{name}.bin",
            folder / f"{name}.py", folder / name, folder / "_data" / name]


def written_themes(base: Path) -> list[Path]:
    """The ``.theme`` files somebody wrote, the shipped one left where it is.

    A copy of a theme made by the theme manager is the thing an older program may
    name a colour it has never heard of; the file the program came with is the one
    look that always works, so it stays put.
    """
    folder = base / "themes"
    if not folder.is_dir():
        return []
    return [path for path in sorted(folder.glob("*.theme"))
            if path.name != SHIPPED_THEME]


def backup_targets(base: Path) -> list[Path]:
    """What gets copied into the zip before any of it goes: the written data.

    The config folder and every addon's data folder, the shipped addons' among
    them, since their settings were written by a person too. Nothing the program
    came with is kept, because it comes with the older program as well.
    """
    out: list[Path] = []
    config = base / "config"
    if config.is_dir():
        out.append(config)
    data = base / "addons" / "_data"
    if data.is_dir():
        out += [folder for folder in sorted(data.iterdir()) if folder.is_dir()]
    return out


def erase_targets(base: Path) -> list[Path]:
    """Everything an erase would take away, as things stand right now.

    Only paths that really exist are named, so a program that has never written
    anything is erased by doing almost nothing - and the list is the one the dialog
    is asking permission for, so what a person agrees to is what goes.
    """
    gone: list[Path] = [base / "config"]
    gone += written_themes(base)
    for row in installed_rows():
        if erasable(row):
            gone += addon_files(base, row.get("id"))
    return [path for path in gone if path.exists()]


def write_backup(base: Path, version: str) -> tuple[Path | None, int]:
    """Copy everything written down into one zip beside the program.

    The zip is named for the version being gone back to, so two erases on one
    machine say which of them belongs to which program, and the paths inside it
    start at the program's folder - unpacking it over any copy puts every file
    back where it was found. ``(where, how many)``, with ``None`` for a program
    that had nothing written down to keep.
    """
    wanted = backup_targets(base)
    if not wanted:
        return None, 0
    target = base / f"backup-before-{one_line(version) or 'unknown'}.zip"
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for root in wanted:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(base).as_posix())
                    count += 1
    return target, count


def stop_addons(rows: list[dict]) -> list[str]:
    """Take down every addon that did not come inside the program, and name them.

    Stopped before their files are deleted, because a running addon holds its own
    program open and the delete would fail on the one file that matters. The app's
    own switch is what kills the process - this addon has no business reaching into
    another's - and the shipped ones are left running, this one among them, since
    the download that follows is theirs to finish.
    """
    gone: list[str] = []
    for row in rows:
        if not erasable(row):
            continue
        ident = one_line(row.get("id"))
        if row.get("running") or row.get("enabled"):
            try:
                api.session.set_addon_running(ident, False)
            except (AddonError, AttributeError, TypeError) as error:
                note(f"{ident} could not be stopped: {error}")
        gone.append(ident)
    return gone


def factory_reset(version: str) -> list[str]:
    """Erase what was written down, and account for it in lines.

    This is the answer for a way back that a profile written by a newer program
    would break: everything the user or an installed addon made goes, and what is
    left is the state the older copy would start in on a first run. The account is
    handed back rather than logged on the way, so the caller can put it in its log
    in its own order. Nothing here rescans the addons folder: a rescan shuts this
    addon down while its own download is in the air, and the program that starts
    after the swap reads the folder for itself.
    """
    base = program_folder()
    rows = installed_rows()
    words: list[str] = []
    where, count = write_backup(base, version)
    if where is None:
        words.append("nothing was written down to keep, so no backup was made")
    else:
        words.append(f"{count} file(s) copied to {where}; unpack it over the "
                     "program's folder to put them back")
    doomed = erase_targets(base)
    stopped = stop_addons(rows)
    if stopped:
        words.append(f"{len(stopped)} installed addon(s) stopped and deleted: "
                     + ", ".join(stopped))
    else:
        words.append("no addon had to be taken away: every one here came inside "
                     "the program")
    deleted = 0
    for path in doomed:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted += 1
        except OSError as error:
            words.append(f"{path.name} could not be deleted: {error}")
    words.append(f"{deleted} of the written things went: the config folder and "
                 "the addon records in it, the themes somebody copied, and the "
                 "addons the store installed")
    return words


def downgrade_words(entry: dict, have: str, wanted: str, across: bool) -> str:
    """The first half of the question: what is being replaced, and how far back.

    A hotfix on the same line and a line nobody has been on are not the same risk,
    so they are not said the same way - and the version still being named here is
    the one the picked entry carries, not whatever the list was showing when the
    button was first looked at.
    """
    step = ("That reaches across a major line: the older program predates whole "
            "features of this one."
            if across else
            "It is a hotfix on the same line as this copy, so the older program "
            "is close to this one.")
    return (f"This copy is berry{have or '?'} and {one_line(entry.get('tag'))} "
            f"is berry{wanted}, so installing puts an older program where the "
            f"newer one runs now.\n{step}\n\nThe swap itself moves one file and "
            "nothing else; what is at risk is everything written down beside it.")


def downgrade_dialog(entry: dict, have: str, wanted: str, across: bool) -> str:
    """Ask somebody going backwards what should happen to what is written down.

    Three honest answers rather than a yes or no, because the question really has
    three: keep everything and take the risk, start clean and take the loss, or
    leave the program where it is. Closing the box without an answer comes back
    ``""``, which is treated exactly as **Cancel** is - nothing fetched, nothing
    written, one line on the status.
    """
    spec = {"kind": "column", "children": [
        {"kind": "label", "text": downgrade_words(entry, have, wanted, across),
         "word_wrap": True},
        {"kind": "label", "text": "What may go wrong:", "word_wrap": True},
        {"kind": "multi", "readonly": True, "value":
         "\n".join(f"- {risk}" for risk in DOWNGRADE_RISKS)},
    ]}
    try:
        answer = api.ui.dialog(spec, f"Go back to berry{wanted}?",
                               answers=[ERASE_ANSWER, KEEP_ANSWER, CANCEL_ANSWER])
    except AddonError as error:
        note(f"the question could not be asked: {error}")
        return ""
    if not isinstance(answer, dict):
        return ""
    return one_line(answer.get("answer"))


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
        from core.views import cancel_restart_request, restart_process
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

def _job_show(show: bool) -> bool:
    """Swap the browsing panes for the job's console, or hand them back.

    ``set_widget`` answers ``False`` for an id the tab does not hold, which is how
    a job started on a tab built without the pane - an older layout, or a bare
    test harness - finds out it has nowhere to print and carries on regardless.
    """
    if api is None:
        return False
    try:
        placed = api.tabs.set_widget(TAB, "jobpane", "visible", bool(show))
        api.tabs.set_widget(TAB, "browse", "visible", not show)
    except AddonError:
        return False
    return bool(placed)


def _job_write(text: str) -> None:
    """One line onto the job's console, in the same words the log is given.

    The screen is a terminal rather than a text box, so a bare newline would walk
    down the page without going back to the left margin. Nothing is written at all
    until a job has put the pane on screen.
    """
    if api is None or job is None or not job.get("pane"):
        return
    try:
        api.tabs.set_widget(TAB, "jobterm", "write", f"{text}\r\n")
    except AddonError:
        pass


def _job_card(name: str = "", step: str | None = None,
              percent: int | None = None, picture: str | None = None) -> None:
    """Fill the card floating over the job's console: the version, its step, its share.

    Only what is said is written, so a step that names no share leaves the bar
    where it stands - a download whose length was never told has no share to
    count up to. The step is cut short: the whole of it goes onto the screen
    behind the card as well, and the card is the one thing here that has to stay
    readable over it.
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


def _progress(text: str, frac: float | None = None) -> None:
    """One step of the job: the floating notice's line and share, and the card's.

    The share is the download's own bytes so far, so a 20 MB program counts up as
    it arrives rather than in made-up stages; a step that knows its part of the
    whole - the swap at the end - is given as ``frac`` instead. An answer that
    never told its length has no share to show: the bar is left where it stands
    and the line carries the byte count on its own.

    Nothing here touches the tab's status line: the notice wears the steps, and
    the page hears the job only at its start and its end, so a download does not
    read as a flicker behind the card's bar. The step is printed on the job's
    console too, in full, because for a download these counts are the whole of
    what is going on - there is no build output behind the card to read instead.
    """
    if api is None or job is None:
        return
    if frac is not None:
        percent = max(0, min(99, int(round(100.0 * float(frac)))))
    else:
        total = int(job.get("total") or 0)
        got = int(job.get("got") or 0)
        percent = min(99, int(round(100.0 * got / total))) if total else None
    try:
        api.ui.progress_notice("Berry Manager update", str(text),
                               percent if percent is not None else 0, 100)
    except Exception:
        pass
    _job_write(text)
    _job_card(step=text, percent=percent)


def _bytes_line() -> str:
    """How far the download has come, in the words the notice and the card wear."""
    got = int((job or {}).get("got") or 0)
    total = int((job or {}).get("total") or 0)
    return (f"downloading... {_size(got)}"
            + (f" of {_size(total)}" if total else ""))


def _begin(verb: str, values: dict, name: str = "") -> dict:
    """Start a job: the buttons grey out and its console goes up over the tab.

    ``pane`` says whether the tab could put that console on screen, and so whether
    the download's steps have anywhere to be printed on; nothing else about the job
    depends on the answer. ``name`` is what the card in the middle of it is to
    call the version being fetched.
    """
    global working, job
    working = True
    job = {"verb": verb, "values": values, "host": "", "path": "",
           "response": None, "chunks": [], "got": 0, "total": 0,
           "hops": 0, "ticks": 0, "pane": False}
    _buttons(False)
    try:
        api.tabs.set_widget(TAB, "log", "value", "")
    except AddonError:
        pass
    job["pane"] = _job_show(True)
    if job["pane"]:
        try:
            api.tabs.set_widget(TAB, "jobterm", "clear", True)
        except AddonError:
            pass
        _job_card(name, step="", picture=tab_icon())
    say(f"{verb} starting...")
    _progress(f"{verb} starting...", 0.0)
    return job


def _finish(text: str, bad: bool = False) -> None:
    """End the job in hand: the browsing panes come back and one line is left up.

    The live notice ends either way: filled to hundred percent, to be held a
    moment and fade, when all is well; closed straight away when the job ended
    bad.
    """
    global working, job
    if job is not None and job.get("response") is not None:
        try:
            job["response"].close()
        except (http.client.HTTPException, OSError):
            pass
    was_pane = bool((job or {}).get("pane"))
    job = None
    working = False
    _buttons(True)
    if was_pane:
        # The browsing view is what the tab was showing before the job, and the
        # job's log is still underneath for anyone who wants to read it over.
        _job_show(False)
    if bad:
        # The log starts folded, but a failure is exactly when its full text is
        # wanted; the status line only ever carries one line of it.
        show_log(True)
    if api is not None:
        try:
            if bad:
                api.ui.progress_notice_close()
            else:
                api.ui.progress_notice("Berry Manager update", str(text),
                                       100, 100)
        except Exception:
            pass
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
        _progress(_bytes_line())
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
                _progress(_bytes_line())
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
    erased = bool((found or {}).get("erased"))
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
        # The bytes are down, so the job's own part of the work is done and what
        # is left is the swap: one line, said once, on the notice and the card.
        _progress(f"{_size(len(program))} {how}; putting it in place", 1.0)
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
            f"{words}. "
            + ("Its written data was backed up and erased before this landed."
               if erased else "The config folder and the addons were not "
                              "touched."))
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

    The same careful swap either way: one file replaced. What differs is the
    asking - going up is a yes or no, while going back over a newer version has
    three honest answers, because the thing at risk is not the program but
    everything written down beside it. The answer is taken before anything is
    fetched or written, and the floor says no before the answer is even asked.
    """
    global found
    if api is None or working:
        return
    entry = picked_entry()
    if entry is None:
        say("there is no version picked yet - press Check, then pick one from "
            "a line", bad=True)
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
    erase = False
    if back:
        choice = downgrade_dialog(entry, have, wanted,
                                  major_of(wanted) != major_of(have))
        if choice == ERASE_ANSWER:
            erase = True
        elif choice != KEEP_ANSWER:
            say("the way back was turned down; nothing was fetched and nothing "
                "was written")
            return
    elif not api.ui.question(
            "Update Berry Manager?",
            (f"Replace this copy (berry{have or '?'}) with berry{wanted}?\n\n"
             "The new program is put where the old one ran. Nothing else "
             "moves: the config folder, the addons folder and every addon's "
             "settings beside them stay exactly as they are.")):
        say("the update was declined")
        return
    if erase:
        # Everything the user or an installed addon wrote goes before the older
        # program lands, so the copy that starts is a clean one; the backup is
        # written first and its address is said in the log, whatever happens to
        # the download after it.
        for line in factory_reset(wanted):
            note(line)
        say(f"the written data is erased; fetching berry{wanted}")
    name, url, _how = entry["download"]
    found = dict(entry)
    found["erased"] = erase
    wanted_line = f"berry{one_line(entry.get('version'))}"
    job_local = _begin("update", values, name=wanted_line)
    note(f"{'going back' if back else 'updating'} to "
         f"{one_line(entry.get('tag'))}: fetching {name}")
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
    global api, working, job, found, published, picked, ticker
    _drop_http()
    _stop_timer(ticker)
    ticker = None
    announced.clear()
    published = []
    picked = -1
    working = False
    job = None
    found = None
    lines.clear()
    opened.clear()
    notes.clear()
    slot_names.clear()
    api = None


def _run(name: str, *arguments: Any) -> None:
    """Call one of this addon's own steps by the name it is held under now.

    Looking the step up when the interaction lands rather than fixing it at
    import is what lets a test - or a hot patch - swap a function on the module
    and truly get the click.
    """
    handler = globals().get(name)
    if callable(handler):
        handler(*arguments)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """Something on the tab was used: run what its id and interaction mean.

    Three kinds of thing report in here. The buttons at the bottom are looked up
    by their id, the log's fold box is its own case, and the version pane answers
    for itself: a dropdown whose line was laid out says what it is showing, and
    the button of a line says to open or shut it. Both of the last two carry the
    row of the pane they belong to in their name, so a pick is matched against the
    versions of that line alone - which is the one place a fork's repository can
    publish the same number on two lines and still be answered correctly.
    """
    if str(tab) != TAB:
        return
    name = one_line(identifier)
    slot = slot_number(name)
    if slot >= 0 and name.startswith("mopen") and str(event) == "clicked":
        _run("toggle_major", slot)
        return
    if slot >= 0 and name.startswith("mpick") and str(event) == "changed":
        _run("pick_text", slot, payload or {})
        return
    if name == "showlog" and str(event) in ("toggled", "changed"):
        show_log((payload or {}).get("checked"))
        return
    if str(event) != "clicked":
        return
    _run({"check": "check_now", "update": "update_selected",
          "page": "open_page"}.get(name or "") or "")


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
