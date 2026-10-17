#!/usr/bin/env python3
"""A test for addons/manager_update.py.

The update addon's job is to read this program's own published tags off GitHub,
say where each of them stands against the copy running here - an update, this
copy, a way back, or further back than it will go - tell the story of the one
picked from its release, its commit and its file list, and put any installable
one in the running program's place without touching the config or the addons.
None of that needs a device or the network, and nothing here reaches either:
every GitHub read goes through one patched ``get_json`` answering from the
records below, and the file work happens in a temporary folder with dummy files
standing in for the program.

What gets looked at:

* the pure helpers: version compares and equality, tag names, the floor a way
  back stops at, where each version stands, and the address split that has to
  keep a download's one-time signature;
* what a release offers: assets named for one system or the other, the fallback
  to plain links in the description, and the refusal of an artifact page that
  would only answer with a sign-in. The addon asks the machine it runs on which
  of a release's two files to take, so the records below carry both - as a real
  release does - and ``main`` pins that answer to ``THE_SYSTEM`` for everything
  past the two sections that are handed a system to look at. Which side happens
  to be running the test cannot then change what a check reads: a fixture
  naming one system only passes on that runner and fails on the other, and the
  failure looks like the addon having no download at all;
* the tab on a stub api: init declares and builds it, and a check reads the tags
  and the releases in two requests whatever their number, sorting them newest
  first and chaining each one to the version before it for the comparison;
* the lines and the pane that goes with them: how the published versions group
  into their major lines and in which order, what a line says in its heading, what
  its dropdown is handed, what the picked version's title, its chip, its commit,
  its line of facts, its state, its account and its file table come out as -
  including the commit's words standing in for a release whose author wrote
  nothing; a line below this copy starting shut and only laying its versions down
  when it is opened, and the pane refusing to read a version's story twice;
* going back: the same offer down as up, the floor refusing anything older, and
  a moved floor moving the line rather than the code; the three answers a way back
  asks for doing exactly what each of them says - the download alone, a backup and
  an erase of everything installed, and nothing at all;
* the notice: a check that ran on its own puts the version it found up through
  the app's notifications, once per version, and says nothing when the switch is
  off or nothing is ahead;
* the download's byte sniffing: a real program, a zip like the workflow ships,
  and a page masquerading as one;
* the swap itself in a temporary folder: new bytes land where the old program
  ran, the leftover goes, nothing else moved;
* the job machine: what Install refuses with nothing picked, with a version
  below the floor and with no file attached, the full redirect-to-swap walk
  driven through a patched scheduler, the list re-placed afterwards without a
  second read, and the schedule set up and taken down again.

No internet, no GitHub and no device are needed. Run it from the Test panel,
or::

    python addons/_tests/test_manager_update.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

SOURCE = ROOT / "addons" / "manager_update.py"
FAILURES: list[str] = []

#: The head each system's program file starts with, and the name it goes by -
#: what ``extract_from_zip`` looks for inside a shipped archive.
SYSTEM_HEAD = {"windows": b"MZ", "linux": b"\x7fELF"}
SYSTEM_FILE = {"windows": "berry_manager.exe", "linux": "berry_manager"}

#: The name each system's release file goes by, as the workflow publishes them.
SYSTEM_PROGRAM = {"windows": "berry_manager-windows.exe",
                  "linux": "berry_manager-linux"}


def program_bytes(system: str, filler: bytes = b"\x90") -> bytes:
    """Bytes big enough to pass for a program, wearing ``system``'s own head."""
    return SYSTEM_HEAD[system] + filler * 200_000


#: A body big enough to pass for this program when bytes get sniffed, one per
#: side and the one these records tell the story from.
PROGRAM_BYTES = program_bytes("windows")
ELF_BYTES = program_bytes("linux", b"\x00")

#: Which system the records below are a story about, and so which of a release's
#: two files this copy takes: see the note at the top of the file.
THE_SYSTEM = "windows"
THE_PROGRAM = SYSTEM_PROGRAM[THE_SYSTEM]
THE_FILE = SYSTEM_FILE[THE_SYSTEM]
THE_BYTES = program_bytes(THE_SYSTEM)


def offer(where: str) -> list[dict]:
    """The two files a published release carries, each at ``where`` it lives."""
    return [{"name": name, "browser_download_url": f"{where}/{system}"}
            for system, name in SYSTEM_PROGRAM.items()]


#: Every tag the repository is pretending to publish, oldest last, one of them
#: not a version at all. The order is GitHub's, not the list's: the check has to
#: sort it.
TAG_ROWS = [{"name": "berry1.6.1", "commit": {"sha": "aaa1111aaa"}},
            {"name": "berry1.6.0", "commit": {"sha": "bbb2222bbb"}},
            {"name": "berry1.5.9", "commit": {"sha": "ccc3333ccc"}},
            {"name": "v-older-thing", "commit": {"sha": ""}}]

#: The releases made for those tags. 1.6.1 has a file and a written account,
#: 1.6.0 has a file and no words, and 1.5.9 is a bare tag - the three states a
#: version can be in as far as installing it is concerned. The two that carry a
#: file carry it for both systems, because that is what a release holds and a
#: one-sided record would leave the other runner with nothing to find.
RELEASES: dict[str, dict] = {
    "berry1.6.1": {
        "tag_name": "berry1.6.1", "name": "Berry 1.6.1",
        "published_at": "2026-10-12T10:00:00Z",
        "body": "The store got a pane to read a listing in.",
        "author": {"login": "fligma"},
        "assets": offer("https://x/1.6.1"),
    },
    "berry1.6.0": {
        "tag_name": "berry1.6.0", "name": "berry1.6.0",
        "published_at": "2026-10-01T09:00:00Z", "body": "",
        "assets": offer("https://x/older"),
    },
}

#: The commit under every tag, and the file list between each version and the
#: one before it.
COMMIT = {
    "sha": "aaa1111aaa1111",
    "commit": {"message": "Berry Manager v1.6.1 - the store pane\n\n"
                          "second line of the commit",
               "author": {"name": "fligma", "date": "2026-10-12T09:55:00Z"}},
    "author": {"login": "fligma"},
}
COMPARISON = {"status": "ahead", "files": [
    {"filename": "utils/update util/addons/addon_store.py", "status": "changed",
     "additions": 40, "deletions": 3},
    {"filename": "utils/update util/assets/storedefaultaddon.png",
     "status": "added", "additions": 1, "deletions": 0},
    {"filename": "utils/update util/old_thing.py", "status": "removed",
     "additions": 0, "deletions": 12},
]}


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


@contextmanager
def patched(obj: Any, **values: Any) -> Any:
    """Swap functions on a module for one test, and put them back after."""
    saved = {name: getattr(obj, name) for name in values}
    for name, value in values.items():
        setattr(obj, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(obj, name, value)


class FakeResp:
    """A ``http.client`` answer small enough to stand in for one."""

    def __init__(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = dict(headers or {})
        self.closed = False

    def getheader(self, name: str, default: str = "") -> str:
        return self.headers.get(name, default)

    def read(self, amount: int = -1) -> bytes:
        if amount is None or amount < 0:
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:amount], self._body[amount:]
        return out

    def close(self) -> None:
        self.closed = True


class FakeFetcher:
    """``_http`` answers with queued fakes; nothing ever leaves the machine."""

    def __init__(self, responses: list[FakeResp]) -> None:
        self.responses = list(responses)
        self.opened: list[tuple[str, str]] = []

    def open(self, host: str, path: str) -> FakeResp:
        self.opened.append((host, path))
        return self.responses.pop(0)

    def close(self, host: str = "") -> None:
        pass

    def get(self, host, path, accept=""):       # not used by the job machine
        raise AssertionError("the download path must stream, not slurp")


#: Every address tail the patched GitHub reader has been asked for since the
#: last ``wire`` - what lets a test say "and it read nothing afterwards".
TAILS: list[str] = []


def wire(upd: Any) -> list[str]:
    """Patch the one function that touches GitHub; hand back what it was asked."""
    asked = TAILS
    del asked[:]

    def answering(values: dict, tail: str) -> Any:
        asked.append(tail)
        if tail.startswith("tags"):
            return TAG_ROWS
        if tail.startswith("releases?"):
            return list(RELEASES.values())
        if tail.startswith("releases/tags/"):
            return RELEASES.get(tail.split("/", 1)[1], {})
        if tail.startswith("commits/"):
            return COMMIT
        if tail.startswith("compare/"):
            return COMPARISON
        raise AssertionError(f"the addon asked GitHub for {tail!r} in a test")

    upd.get_json = answering
    return asked


# --- one: the pure helpers, with no api standing up ---

def test_the_helpers(upd: Any) -> None:
    print("\n1. versions, tags, the floor and addresses")
    check(upd.tag_version("berry1.5.9", "berry") == "1.5.9",
          "berry1.5.9 did not read as version 1.5.9")
    check(upd.tag_version("BERRY1.6", "berry") == "1.6",
          "the prefix compare was case-sensitive")
    check(upd.tag_version("berry", "berry") == "",
          "a prefix with no version after it read as a release anyway")
    check(upd.tag_version("v1.5.9", "berry") == "",
          "a tag outside the prefix counted as an update")
    check(upd.tag_version("berry-rc", "berry") == "",
          "a prefix plus letters with no digits counted as a version")

    check(upd.version_newer("1.10", "1.9"),
          "1.10 lost to 1.9 as text instead of winning as digits")
    check(not upd.version_newer("1.5.9", "1.5.9"), "an equal version counted as newer")
    check(not upd.version_newer("1.5.8", "1.5.9"), "an older version counted as newer")
    check(upd.version_newer("1.6", ""), "a remote version lost the empty compare")
    check(not upd.version_newer("", "1.6"), "nothing remote still counted as newer")

    check(upd.versions_equal("1.6", "1.6.0"),
          "1.6 and 1.6.0 were called different programs")
    check(not upd.versions_equal("1.6.1", "1.6.0"), "two versions one apart matched")
    check(not upd.versions_equal("", "1.6"), "nothing at all matched a version")

    ordered = sorted(["1.6.0", "1.10", "1.5.9", "1.6"], key=upd._sort_key)
    check(ordered[0] == "1.5.9" and ordered[-1] == "1.10"
          and upd.versions_equal(ordered[1], ordered[2])
          and upd.version_newer(ordered[1], ordered[0]),
          f"a list of versions did not sort: {ordered}")

    best = upd.pick_latest(["v2.0", "berry1.5.9", "berry1.6.0", "berry1.5.10"], "berry")
    check(best == ("berry1.6.0", "1.6.0"),
          f"the newest tag out of an unordered list is wrong: {best!r}")
    check(upd.pick_latest(["v2.0"], "berry") is None,
          "a list with no update tags produced one anyway")
    check(upd.pick_latest([], "berry") is None, "an empty tag list produced a winner")

    # Where one version stands, and how far back the swap is willing to go.
    check(upd.stance_of("1.6.1", "1.6.0", "1.6.0") == "newer",
          "a version ahead of this copy was not called an update")
    check(upd.stance_of("1.6.0", "1.6", "1.6.0") == "this",
          "this copy's own version, spelled shorter, was not recognised")
    check(upd.stance_of("1.6.0", "1.6.1", "1.6.0") == "older",
          "a version at the floor and behind was not a way back")
    check(upd.stance_of("1.5.9", "1.6.1", "1.6.0") == "too old",
          "a version below the floor was offered anyway")
    check(upd.stance_of("", "1.6.1", "1.6.0") == "unknown",
          "a version nothing reads was given a stance")
    check(upd.stance_of("1.5.9", "1.6.1", "1.5.0") == "older",
          "a moved floor did not move the line")
    check(upd._floor({}) == upd.OLDEST_ALLOWED,
          "the default floor is not the one named in the module")
    check(upd._floor({"min_version": "  "}) == upd.OLDEST_ALLOWED,
          "a blank floor left the default behind")
    check(upd._floor({"min_version": "1.4.2"}) == "1.4.2",
          "a named floor was not the one used")
    check(upd._prefix({}) == "berry" and upd._prefix({"tag_prefix": "fork"}) == "fork",
          "the tag prefix was not read, or not defaulted")

    # The major lines the left pane is built from: one row per line, newest line
    # first, each holding the place its versions have in the list it was made from.
    check(upd.major_of("1.6.1") == "1.6" and upd.major_of("1.6") == "1.6"
          and upd.major_of("2") == "2",
          f"a version's line is not its first two numbers: "
          f"{[upd.major_of(v) for v in ('1.6.1', '1.6', '2')]}")
    check(upd.major_of("nightly") == "" and upd.major_of("") == "",
          "a tag nothing reads as a version was given a line anyway")
    grouped = upd.majors([{"version": "2.0.1"}, {"version": "2.0.0"},
                          {"version": "1.9.4"}, {"version": "nightly"},
                          {"version": "1.6.1"}])
    check([line["major"] for line in grouped] == ["2.0", "1.9", "1.6"],
          f"the lines are not newest first, or a non-version joined them: "
          f"{[(line['major'], line['entries']) for line in grouped]}")
    check(grouped[0]["head"] == 0 and grouped[0]["entries"] == [0, 1]
          and grouped[2]["head"] == 4 and grouped[2]["entries"] == [4],
          f"a line does not name its own versions by their place in the list: "
          f"{grouped}")
    check(upd.majors([]) == [] and upd.majors(None) == [],
          "no published versions produced a line anyway")

    # With no theme to read, every colour is the one this tab has always worn.
    check(upd.stance_colour("newer") == upd.STANCE_COLOUR["newer"]
          and upd.stance_colour("too old") == upd.STANCE_COLOUR["too old"]
          and upd.file_colour("Added") == upd.FILE_COLOUR["added"],
          "a colour helper lost the sheet it falls back on")

    check(upd.offer_of({"stance": "newer", "download": ("a", "b", "c")})
          == "an update - file attached",
          f"an update with a file did not say so: {upd.offer_of({'stance': 'newer', 'download': 1})!r}")
    check(upd.offer_of({"stance": "older", "download": None}) == "a way back - no file",
          f"a way back with nothing attached did not say so: {upd.offer_of({'stance': 'older'})!r}")
    check(upd.offer_of({"stance": "this"}) == "this copy",
          "this copy's own row said something else")
    check(upd.offer_of({"stance": "too old"}) == "older than the floor",
          "a version below the floor was not called that")

    check(upd.one_line("  a\nb ") == "a", "one_line did not take the first clean line")
    check(upd.safe_segment("../../wi rd!") == "wi rd",
          f"traversal and shell junk survived safe_segment: {upd.safe_segment('../../wi rd!')!r}")

    host, path = upd.split_url("https://objects.example/a/b.exe?token=x%201")
    check(host == "objects.example" and path == "/a/b.exe?token=x%201",
          f"the download address lost its query, and with it its signature: {path!r}")
    for bad_url in ("http://example/a", "ftp://example/a", "not a url at all"):
        try:
            upd.split_url(bad_url)
            check(False, f"{bad_url} was accepted as a download address")
        except upd.AddonError:
            check(True, f"{bad_url!r} was refused as a download address")
    check(upd._size(2 * 1024 * 1024) == "2.0 MB" and upd._size(4096) == "4 KB"
          and upd._size(9) == "9 B", "a byte count did not come out in words")
    check(upd.local_version() == "", "local_version spoke without an api")
    check(upd.system_name() in ("windows", "linux"),
          f"system_name does not know this machine: {upd.system_name()!r}")


# --- two: what a release offers this machine ---

def test_the_release(upd: Any) -> None:
    print("\n2. assets, description links, refusals")
    release = {
        "assets": [
            {"name": "berry_manager-linux", "browser_download_url": "https://x/lin"},
            {"name": "berry_manager-windows.exe", "browser_download_url": "https://x/win"},
            {"name": "notes", "browser_download_url": ""},        # no url: never usable
        ],
        "body": "Windows: https://dl.example/berry_win.exe\n"
                "see also https://example.com/other.",
    }
    win = upd.pick_asset(release, "windows")
    check(win and win["name"] == SYSTEM_PROGRAM["windows"],
          f"windows did not pick its own asset: {win!r}")
    lin = upd.pick_asset(release, "linux")
    check(lin and lin["name"] == SYSTEM_PROGRAM["linux"],
          f"linux picked the wrong side's asset: {lin!r}")
    check(upd.pick_asset({"assets": [{"name": SYSTEM_PROGRAM["windows"],
                                      "browser_download_url": "https://x/win"}]},
                         "linux") is None,
          "linux was about to download a windows program")
    neutral = upd.pick_asset({"assets": [{"name": "berry_build",
                                          "browser_download_url": "https://x/one"}]},
                             "windows")
    check(neutral and neutral["name"] == "berry_build",
          "a neutral-named asset was not taken as the fallback")

    links = upd.body_links(release)
    check("https://dl.example/berry_win.exe" in links,
          f"a description link did not survive extraction: {links}")
    check(upd.pick_body_link(release, "windows") == "https://dl.example/berry_win.exe",
          "the windows link was not the one picked")
    check(upd.pick_body_link(release, "linux") == "",
          "linux accepted a link that names the other system")

    src = upd.download_source(release, "windows")
    check(src == (SYSTEM_PROGRAM["windows"], "https://x/win", "release asset"),
          f"an asset did not win over the description: {src!r}")
    only_link = {"body": "get https://dl.example/berry-linux.tar " }
    src = upd.download_source(only_link, "linux")
    check(src and src[1] == "https://dl.example/berry-linux.tar"
          and "description" in src[2],
          f"a link in the description was not used as the fallback: {src!r}")
    check(upd.download_source({}, "windows") is None,
          "an empty release offered a download anyway")

    # And the records the rest of this file is written from: each system has to
    # find a file of its own in them, or a runner on the other side reads a
    # release that offers it nothing - the mistake this guard is here to keep
    # from coming back.
    for tag, record in RELEASES.items():
        for side, name in SYSTEM_PROGRAM.items():
            got = upd.download_source(record, side)
            check(got and got[0] == name and got[2] == "release asset",
                  f"{tag} offers {side} {got!r} instead of its own {name!r}")


# --- three: the tab, the check and the two panes, on a stub api ---

def fresh(upd: Any, config: dict | None = None, version: str = "1.6.0",
          ask: bool = True, keep_timers: bool = False) -> Any:
    """The addon on a stub api: no start-up check, no network, a known version."""
    upd.api = None
    upd.working = False
    upd.job = None
    upd.found = None
    upd.published = []
    upd.picked = -1
    upd.ticker = None
    upd.lines = []
    upd.opened.clear()
    upd.notes.clear()
    upd.slot_names.clear()
    upd.filling = False
    upd.announced.clear()
    if ask:
        wire(upd)
    api = addon_client.StubAPI(addon_id="manager_update",
                               config={"check_startup": False,
                                       **dict(config or {})})
    api.answer("session.version", version)
    upd.init(api)
    if not keep_timers:
        api.timers.cancel_all()     # kill init's queued check; tests drive it
    return api


def last_set(api: Any, identifier: str, prop: str) -> Any:
    """The last thing the addon sent one widget, whatever it was."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == identifier and arguments[2] == prop:
            return arguments[3]
    return None


def last_status(api: Any) -> str:
    return str(last_set(api, "status", "text") or "")


def rows_for(api: Any, identifier: str) -> list:
    return list(last_set(api, identifier, "rows") or [])


def items_for(api: Any, identifier: str) -> list:
    """The words one dropdown was last handed, when it was ever handed any."""
    return list(last_set(api, identifier, "items") or [])


def sets_for(api: Any, identifier: str, prop: str) -> list:
    """Every value the addon sent one control for one property, oldest first."""
    return [arguments[3] for where, arguments, _named in api.calls
            if where == "tabs.set_widget" and len(arguments) >= 4
            and arguments[1] == identifier and arguments[2] == prop]


def cells(row: Any) -> list:
    return list(row.get("cells") or []) if isinstance(row, dict) else list(row)


def test_the_tab(upd: Any) -> None:
    print("\n3. init builds the tab and its parts")
    api = fresh(upd)
    asked = TAILS                 # wire() cleared it: anything in it was really read
    check(api.called("config.declare") and api.called("tabs.add_tab")
          and api.called("tabs.set_icon") and api.called("ui.menu_item"),
          "init did not declare its config, add and icon its tab and its menu entry")
    check(not asked, "a start-up check fired even though check_startup was off")
    check(str(last_set(api, "source", "text")) == "fligma/raspiOpenWrt @ berry*",
          f"the header chip does not name where tags come from: "
          f"{last_set(api, 'source', 'text')!r}")
    held = api.last("tabs.add_tab")
    check(held is not None and bool(held[2].get("pinned"))
          and bool(held[2].get("icon_only")),
          f"the update tab is not pinned and icon-only where furniture holds: "
          f"{None if held is None else held[2]}")

    print("\n4. the check reads the published versions, in two requests")
    del asked[:]
    upd.check_now()
    check(asked.count("tags?per_page=100") == 1
          and asked.count("releases?per_page=100") == 1,
          f"a check cost more than the tags and the releases: {asked}")
    # Only the version the pane opened on has its story read; the rest wait for
    # a pick, so a list of thirty costs two requests rather than ninety.
    check(sum(1 for tail in asked if tail.startswith("commits/")) == 1
          and sum(1 for tail in asked if tail.startswith("compare/")) == 1
          and "commits/aaa1111aaa" in asked,
          f"the check read the story of more versions than the one it opened "
          f"on: {asked}")
    versions = [str(entry["version"]) for entry in upd.published]
    check(versions == ["1.6.1", "1.6.0", "1.5.9"],
          f"the list is not the published versions newest first: {versions}")
    check([entry["tag"] for entry in upd.published][-1] == "berry1.5.9"
          and upd.published[0]["previous"] == "berry1.6.0"
          and upd.published[-1]["previous"] == "",
          f"a version is not chained to the one before it: "
          f"{[(e['tag'], e['previous']) for e in upd.published]}")
    check("berry1.6.1 is out" in last_status(api) and "1 update ahead" in last_status(api),
          f"an update out did not lead the status line: {last_status(api)!r}")
    check("floor for going back is 1.6.0" in " ".join(api.logs),
          f"the log does not account for the floor: {api.logs[-2:]}")


def cells(row: Any) -> list:
    return list(row.get("cells") or []) if isinstance(row, dict) else list(row)


def spec_words(node: Any) -> str:
    """Every word a spec holds, however deep it is nested."""
    if isinstance(node, dict):
        return " ".join(spec_words(value) for value in node.values())
    if isinstance(node, (list, tuple)):
        return " ".join(spec_words(value) for value in node)
    return str(node)


def spec_leaf(node: Any, identifier: str) -> Any:
    """The one control of a spec that carries this id, or nothing."""
    if isinstance(node, dict):
        if node.get("id") == identifier:
            return node
        for value in node.values():
            found = spec_leaf(value, identifier)
            if found is not None:
                return found
    elif isinstance(node, (list, tuple)):
        for value in node:
            found = spec_leaf(value, identifier)
            if found is not None:
                return found
    return None


def test_the_list_and_the_pane(upd: Any) -> None:
    print("\n5. the lines, and the pane beside them, say the same thing")
    api = fresh(upd)
    del TAILS[:]
    upd.check_now()
    # three published versions on two lines: 1.6 with two builds, 1.5 with one.
    check([line["major"] for line in upd.lines] == ["1.6", "1.5"],
          f"the versions are not grouped into their lines: "
          f"{[(line['major'], line['entries']) for line in upd.lines]}")
    check(upd.opened == ["1.6"],
          f"the pane did not open on the line the pick sits on: {upd.opened}")
    check(upd.slot_of("1.6") == 0 and upd.slot_of("1.5") == 1
          and upd.slot_of("9.9") == -1,
          "a line is not placed in the row of the pane it is drawn in")
    head = str(last_set(api, "mhead0", "text"))
    check("berry1.6.x" in head and "2 published build(s)" in head
          and "this copy is on it" in head,
          f"an open line does not say what it holds and where this copy is: "
          f"{head!r}")
    check(last_set(api, "mhead0", "visible") is True
          and last_set(api, "mpick0", "visible") is True
          and str(last_set(api, "mopen0", "text")) == "1.6  less",
          f"an open line is not drawn as its heading and its dropdown: "
          f"{last_set(api, 'mhead0', 'visible')!r}")
    check(last_set(api, "mpick1", "visible") is False
          and str(last_set(api, "mopen1", "text")) == "1.5  more"
          and last_set(api, "mpick1", "items") is None,
          "a line below this copy was opened before anybody asked for it")

    picks = items_for(api, "mpick0")
    check([str(text).split(" ")[0] for text in picks] == ["1.6.1", "1.6.0"],
          f"a line's dropdown is not its own versions, newest first: {picks}")
    check("an update - file attached" in str(picks[0])
          and "this copy" in str(picks[1]),
          f"a dropdown entry does not say where its version stands: {picks}")
    check(str(last_set(api, "mpick0", "value")) == str(picks[0]),
          f"the dropdown is not pointing at the version the pane shows: "
          f"{last_set(api, 'mpick0', 'value')!r}")
    check(len(sets_for(api, "mpick0", "items")) == 1,
          f"one check laid the same line down more than once: "
          f"{sets_for(api, 'mpick0', 'items')}")

    # The pane opens on the update, since that is what a person came for.
    check(upd.picked == 0, f"the check did not open on the update: {upd.picked}")
    check(str(last_set(api, "vtitle", "text")) == "berry1.6.1  \u2014  Berry 1.6.1",
          f"the pane's title is not the tag and the release's own name: "
          f"{last_set(api, 'vtitle', 'text')!r}")
    check(str(last_set(api, "vchip", "text")) == "an update - file attached"
          and upd.stance_colour("newer") in str(last_set(api, "vchip", "style"))
          and last_set(api, "vchip", "visible") is True,
          f"the version's own state is not worn as one chip: "
          f"{last_set(api, 'vchip', 'text')!r}")
    check(str(last_set(api, "vsha", "text")) == "commit aaa1111",
          f"the commit a version was cut from is not named in its own place: "
          f"{last_set(api, 'vsha', 'text')!r}")
    meta = str(last_set(api, "vmeta", "text"))
    check("fligma" in meta and "2026-10-12" in meta
          and f"release asset: {THE_PROGRAM}" in meta
          and "3 file(s) moved" in meta and "aaa1111" not in meta,
          f"the line of facts is not the facts: {meta!r}")
    state = str(last_set(api, "vstate", "text"))
    check("An update is ready" in state and "berry1.6.1" in state
          and "berry1.6.0" in state and "stay exactly where they are" in state,
          f"the state line does not say what installing would do: {state!r}")
    detail = str(last_set(api, "vdetail", "value"))
    check("The store got a pane" in detail,
          f"the release's own words did not reach the pane: {detail!r}")
    check("The commit it was made from" in detail and "second line" in detail,
          f"the commit under the release is not accounted for: {detail!r}")
    files = rows_for(api, "vfiles")
    check([cells(row)[1] for row in files]
          == ["changed +40 -3", "added +1 -0", "removed +0 -12"],
          f"the file list is not what GitHub compared: {[cells(row) for row in files]}")
    check([row.get("color") for row in files]
          == [upd.file_colour("changed"), upd.file_colour("added"),
              upd.file_colour("removed")],
          "the files are not tinted by what happened to them")

    print("\n6. a release with no words of its own, and one with no file")
    upd.pick_row(1)
    check(str(last_set(api, "vtitle", "text")) == "berry1.6.0",
          f"a release named for its own tag gained a title anyway: "
          f"{last_set(api, 'vtitle', 'text')!r}")
    check("Berry Manager v1.6.1 - the store pane" in str(last_set(api, "vdetail", "value")),
          "the commit's title did not stand in for a release that wrote nothing")
    upd.pick_row(2)
    check("no file attached" in str(last_set(api, "vmeta", "text")),
          f"a bare tag did not say it has nothing: {last_set(api, 'vmeta', 'text')!r}")
    check("is older than 1.6.0" in str(last_set(api, "vstate", "text")),
          f"the refusal did not reach the state line: {last_set(api, 'vstate', 'text')!r}")
    check("publish the tag as a release" not in str(last_set(api, "vdetail", "value")),
          "a version below the floor was told how to attach a file instead")
    check(upd.opened == ["1.6", "1.5"],
          f"picking a version did not pull its own line open: {upd.opened}")

    print("\n7. picking a version out of a line's dropdown")
    upd.pick_row(0)
    upd.on_widget(upd.TAB, "mpick1", "changed",
                  {"text": "1.5.9  \u2014  older than the floor"})
    check(upd.picked == 2, f"a line's dropdown did not move the pane: {upd.picked}")
    upd.on_widget(upd.TAB, "mpick0", "changed", {"text": "1.6.0  \u2014  this copy"})
    check(upd.picked == 1, f"a picked label did not move the pane: {upd.picked}")
    upd.on_widget(upd.TAB, "mpick0", "changed", {"text": "not-a-version"})
    check(upd.picked == 1, "an unknown label moved the pane off what it was showing")
    upd.on_widget("Other tab", "mpick0", "changed",
                  {"text": "1.6.1  \u2014  an update - file attached"})
    check(upd.picked == 1, "another tab's dropdown was read as this one's")
    upd.on_widget(upd.TAB, "mpick0", "clicked",
                  {"text": "1.6.1  \u2014  an update - file attached"})
    check(upd.picked == 1, f"a dropdown's click was taken as a pick: {upd.picked}")
    upd.pick_row(99)
    check(upd.picked == 1, "a row that does not exist moved the pane")
    check(upd.index_of("1.5", "1.6.1") == -1,
          "a version was read out of another line's dropdown")
    check(upd.index_of("1.6", "1.6.1") == 0,
          "a dropdown entry was not matched by the version it starts with")
    upd.filling = True
    upd.on_widget(upd.TAB, "mpick0", "changed",
                  {"text": "1.6.1  \u2014  an update - file attached"})
    upd.filling = False
    check(upd.picked == 1, "laying a line's versions down was taken as a pick")

    # The button of a line opens it and shuts it, and neither costs a request.
    print("\n7b. opening a line lays it down once, and reads nothing")
    del TAILS[:]
    del api.calls[:]
    upd.toggle_major(1)                      # 1.5 is open from the pick: shut it
    check("1.5" not in upd.opened
          and last_set(api, "mpick1", "visible") is False
          and str(last_set(api, "mopen1", "text")) == "1.5  more",
          f"shutting a line left its dropdown on the page: {upd.opened}")
    upd.toggle_major(1)                      # and open it again
    check("1.5" in upd.opened
          and last_set(api, "mpick1", "visible") is True
          and not sets_for(api, "mpick1", "items"),
          "opening a line a second time laid its versions down again")
    check(not TAILS, f"opening a line cost a request: {TAILS}")
    upd.on_widget(upd.TAB, "mopen1", "clicked", {})
    check("1.5" not in upd.opened,
          "the line's button did not shut its own line")
    upd.on_widget("Other tab", "mopen1", "clicked", {})
    upd.on_widget(upd.TAB, "mopen1", "changed", {})
    check("1.5" not in upd.opened,
          "another tab's button, or a report that is not a click, moved the pane")
    upd.on_widget(upd.TAB, "mopen9", "clicked", {})
    check(upd.opened == ["1.6"],
          "a button in a row the pane never filled opened something anyway")
    check(upd.slot_number("mpick3") == 3 and upd.slot_number("check") == -1
          and upd.slot_number("mopen99") == -1,
          "a control's row is not read off its own name")

    print("\n7c. a story is read when a version is picked, and only once")
    api = fresh(upd)
    del TAILS[:]
    upd.check_now()
    at_check = list(TAILS)
    values = upd.merge_config(upd.DEFAULTS, api.config.stored())
    upd.show_detail(values, upd.published[1], "1.6.0", "1.6.0", fetch=False)
    check("has not been read beyond the tag list" in
          str(last_set(api, "vdetail", "value")),
          f"a version nobody picked was given a story anyway: "
          f"{last_set(api, 'vdetail', 'value')!r}")
    check(TAILS == at_check, f"a redraw that was not a pick read GitHub again: "
                             f"{TAILS[len(at_check):]}")
    del TAILS[:]
    upd.pick_row(1)
    check(sum(1 for tail in TAILS if tail.startswith("commits/")) == 1
          and sum(1 for tail in TAILS if tail.startswith("compare/")) == 1
          and not any(tail.startswith("releases/tags/") for tail in TAILS),
          f"picking a version whose release came in the batch read it again: "
          f"{TAILS}")
    del TAILS[:]
    upd.pick_row(2)
    check("releases/tags/berry1.5.9" in TAILS,
          f"a tag outside the batch was not read for itself: {TAILS}")
    del TAILS[:]
    upd.pick_row(0)
    check(not TAILS, f"a version already read was fetched a second time: {TAILS}")
    upd.pick_row(1)
    check(not TAILS, "coming back to a read version cost a request")
    check(upd.read_already("berry1.6.1") and upd.read_already("berry1.6.0"),
          "a read version's story was not kept where the next pick can find it")

    print("\n7d. what the pane has no room for goes under Older, and is pickable")
    # The pane holds MAJOR_SLOTS lines and no more, so the versions past it are
    # not counted off in a sentence: they are laid under one dropdown of their
    # own. The fixtures only have two lines, so the pane's room is what is moved.
    spec = upd.tab_spec()
    head = spec_leaf(spec, upd.OLDER_HEAD) or {}
    pick = spec_leaf(spec, upd.OLDER_PICK) or {}
    check(head.get("text") == "Older lines" and head.get("visible") is False
          and pick.get("kind") == "combo" and pick.get("visible") is False
          and pick.get("on") == ["changed"],
          f"the overflow is not a folded-away dropdown that reports a pick: "
          f"{head} / {pick}")
    check("mrest" not in spec_words(spec).split(),
          "the pane still counts what it has no room for in a sentence")

    api = fresh(upd)
    del TAILS[:]
    with patched(upd, MAJOR_SLOTS=1):
        upd.check_now()
        check(upd.slot_of("1.6") == 0 and upd.slot_of("1.5") == -1
              and upd.older_indices() == [2],
              f"a line with no row is not the overflow's: "
              f"{[upd.slot_of(m) for m in ('1.6', '1.5')]} "
              f"{upd.older_indices()}")
        check(last_set(api, upd.OLDER_HEAD, "visible") is True
              and last_set(api, upd.OLDER_PICK, "visible") is True,
              "the overflow was painted with its dropdown still folded away")
        names = items_for(api, upd.OLDER_PICK)
        check(names == [upd.item_text(upd.published[2])]
              and str(names[0]).startswith("1.5.9"),
              f"the overflow's dropdown is not the versions with no row, in "
              f"the words every line uses: {names}")
        check(last_set(api, "major1", "visible") is None
              and (spec_leaf(spec, "major1") or {}).get("visible") is False,
              "a line the pane has no room for was put on the page anyway")
        check(upd.older_index(str(names[0])) == 2
              and upd.older_index("1.6.1  \u2014  this copy") == -1
              and upd.older_index("") == -1,
              "the overflow answers for a version that has a row of its own")
        upd.pick_row(2)
        check(str(last_set(api, upd.OLDER_PICK, "value")).startswith("1.5.9"),
              f"picking an overflow version did not point its dropdown at it: "
              f"{last_set(api, upd.OLDER_PICK, 'value')!r}")
        upd.pick_row(0)                        # come back off the overflow
        del api.calls[:]
        upd.on_widget(upd.TAB, upd.OLDER_PICK, "changed",
                      {"text": upd.item_text(upd.published[2])})
        check(upd.picked == 2,
              "the Older dropdown did not move the pane to its version")
        upd.on_widget(upd.TAB, upd.OLDER_PICK, "changed",
                      {"text": upd.item_text(upd.published[0])})
        check(upd.picked == 2,
              "the overflow's own dropdown picked a version off another line")
        upd.on_widget(upd.TAB, upd.OLDER_PICK, "changed",
                      {"text": upd.item_text(upd.published[1])})
        check(upd.picked == 2,
              "a version with a row of its own was picked from the overflow")
        upd.filling = True
        upd.on_widget(upd.TAB, upd.OLDER_PICK, "changed",
                      {"text": upd.item_text(upd.published[2])})
        upd.filling = False
        check(upd.picked == 2,
              "laying the overflow down was read as somebody picking it")
        upd.on_widget("Other tab", upd.OLDER_PICK, "changed",
                      {"text": upd.item_text(upd.published[2])})
        check(upd.picked == 2, "another tab's overflow dropdown moved this pane")

    # With room for every line the repository publishes, the overflow is not
    # merely empty: it is not on the page at all.
    del api.calls[:]
    upd.check_now()
    check(last_set(api, upd.OLDER_HEAD, "visible") is False
          and last_set(api, upd.OLDER_PICK, "visible") is False
          and not sets_for(api, upd.OLDER_PICK, "items"),
          "a repository that fits the pane still got an Older dropdown")
    check(upd.older_indices() == [],
          "every line has a row, so nothing should be overflowing")


def test_the_way_back(upd: Any) -> None:
    print("\n8. going back, and the floor that says how far")
    api = fresh(upd, version="1.6.1")
    upd.check_now()
    check([entry["stance"] for entry in upd.published]
          == ["this", "older", "too old"],
          f"against 1.6.1 the versions do not stand right: "
          f"{[(e['version'], e['stance']) for e in upd.published]}")
    check("newest published version" in last_status(api)
          and "1 older one(s) can still be gone back to" in last_status(api),
          f"an up-to-date copy was not told what it can still go back to: "
          f"{last_status(api)!r}")
    check(upd.picked == 0, f"with nothing ahead the pane did not open on this copy: "
                           f"{upd.picked}")
    values = upd.merge_config(upd.DEFAULTS, {})
    ok, why = upd.installable(upd.published[1], values, "1.6.1", "1.6.0")
    check(ok and why == "", f"a version at the floor was refused: {why!r}")
    ok, why = upd.installable(upd.published[2], values, "1.6.1", "1.6.0")
    check(not ok and "berry1.5.9 is older than 1.6.0" in why and "setting" in why,
          f"the floor refusal did not name itself: {why!r}")
    ok, why = upd.installable(upd.published[0], values, "1.6.1", "1.6.0")
    check(not ok and "already running here" in why,
          f"this copy's own version offered to replace itself: {why!r}")
    entry = dict(upd.published[1])
    entry["download"] = None
    ok, why = upd.installable(entry, values, "1.6.1", "1.6.0")
    check(not ok and "carries no download" in why,
          f"a way back with nothing attached was installable: {why!r}")
    upd.pick_row(1)
    check("A way back" in str(last_set(api, "vstate", "text")),
          f"the pane did not call a downgrade a way back: "
          f"{last_set(api, 'vstate', 'text')!r}")

    print("\n9. a moved floor moves the line, not the code")
    api = fresh(upd, version="1.6.1", config={"min_version": "1.5.0"})
    upd.check_now()
    check([entry["stance"] for entry in upd.published] == ["this", "older", "older"],
          f"a lowered floor did not let 1.5.9 back in: "
          f"{[(e['version'], e['stance']) for e in upd.published]}")
    check("2 older one(s)" in last_status(api),
          f"the count of ways back did not follow the floor: {last_status(api)!r}")


def test_the_notice(upd: Any) -> None:
    print("\n10. the notice a check sends up on its own")
    api = fresh(upd, version="1.6.0")
    upd.check_now()
    notices = [arguments for where, arguments, _n in api.calls
               if where == "ui.popup"]
    check(len(notices) == 1 and notices[0][0] == "Berry Manager update"
          and "berry1.6.1 is out" in str(notices[0][1])
          and "this copy is berry1.6.0" in str(notices[0][1])
          and "file is attached" in str(notices[0][1]),
          f"the version out was not announced once, in full: {notices}")
    upd.check_now()
    check(api.times("ui.popup") == 1,
          f"the same version was announced twice: {api.times('ui.popup')}")
    upd.announced.discard("1.6.1")
    upd.check_now()
    check(api.times("ui.popup") == 2,
          "a version the addon had stopped holding was not announced again")

    api = fresh(upd, config={"notify_found": False})
    upd.check_now()
    check(not api.called("ui.popup"),
          "the notice came through with the switch off")

    api = fresh(upd, version="1.6.1")
    upd.check_now()
    check(not api.called("ui.popup"),
          "a copy already newest was announced anyway")


def test_the_buttons(upd: Any) -> None:
    print("\n11. the buttons and the config change route through")
    fresh(upd)                    # the addon's api hand, for the clicks below
    seen: list[str] = []
    with patched(upd, check_now=lambda c=False: seen.append("check"),
                 update_selected=lambda c=False: seen.append("update"),
                 open_page=lambda c=False: seen.append("page")):
        upd.on_widget(upd.TAB, "check", "clicked", {})
        upd.on_widget(upd.TAB, "update", "clicked", {})
        upd.on_widget(upd.TAB, "page", "clicked", {})
        upd.on_widget("Other tab", "check", "clicked", {})
        upd.on_widget(upd.TAB, "check", "toggled", {})
        upd.on_widget(upd.TAB, "nonsense", "clicked", {})
    check(seen == ["check", "update", "page"],
          f"the buttons did not route by their ids: {seen}")
    seen.clear()
    with patched(upd, check_now=lambda c=False: seen.append("check")):
        upd.on_config({"addon": "manager_update"})
        check(seen == ["check"], f"a config change did not re-point and re-check: {seen}")
        upd.on_config({"addon": "something else"})
        check(seen == ["check"], "another addon's config change set this one off")
    upd.shutdown()
    check(upd.api is None and upd.found is None and not upd.working
          and not upd.published and upd.picked == -1,
          "shutdown left the addon holding an api, a job or a list")


def test_the_schedule(upd: Any) -> None:
    print("\n12. when it checks, and what changing that does")
    api = fresh(upd)
    check(upd.ticker is None, "a repeat check was set up with the switch off")
    api = fresh(upd, config={"check_again": True, "check_minutes": 90})
    check(upd.ticker is not None and upd.ticker.repeat
          and abs(float(upd.ticker.seconds) - 90 * 60.0) < 0.01,
          f"the repeat check is not on the interval asked for: {upd.ticker}")
    check("every 90 min" in " ".join(api.logs),
          f"the schedule was not written down: {api.logs[-2:]}")
    upd.apply_schedule(upd.merge_config(upd.DEFAULTS,
                                        {"check_again": True, "check_minutes": 5}))
    check(abs(float(upd.ticker.seconds) - 5 * 60.0) < 0.01,
          f"changing the interval left the old timer running: {upd.ticker}")
    upd.apply_schedule(upd.merge_config(upd.DEFAULTS, {"check_again": False}))
    check(upd.ticker is None, "switching the repeat off left a timer standing")
    api = fresh(upd, config={"check_minutes": "not a number"})
    upd.apply_schedule(upd.merge_config(upd.DEFAULTS,
                                        {"check_again": True,
                                         "check_minutes": "not a number"}))
    check(upd.ticker is not None
          and abs(float(upd.ticker.seconds) - upd.DEFAULTS["check_minutes"] * 60.0) < 0.01,
          f"a nonsense interval did not fall back: {upd.ticker}")
    upd.shutdown()
    check(upd.ticker is None, "shutdown left the repeat check running")

    print("\n13. the start-up check is the config's, not the addon's")
    original = upd.check_now
    fired: list[int] = []
    upd.check_now = lambda _checked=False: fired.append(1)   # type: ignore[assignment]
    try:
        api = fresh(upd, config={"check_startup": True},
                    ask=False, keep_timers=True)
        once = [timer for timer in api.timers.handles if not timer.repeat]
        check(upd.ticker is None, "a start-up check was put on the repeat timer")
        check(len(once) == 1 and abs(float(once[0].seconds) - 1.0) < 0.01,
              f"the start-up check is not queued once, a moment after the window: "
              f"{[timer.seconds for timer in once]}")
        for timer in once:
            timer.callback()
        check(fired == [1], "the queued start-up check did not reach check_now")
        api.timers.cancel_all()
        api = fresh(upd, config={"check_startup": False},
                    ask=False, keep_timers=True)
        check(not api.timers.handles,
              "a start-up check was queued with the switch off")
        api.timers.cancel_all()
    finally:
        upd.check_now = original


# --- four: what downloaded bytes really are ---

def test_the_bytes(upd: Any) -> None:
    print("\n14. a program, a zip, and a page dressed up as one")
    check(not upd.looks_like_program(b"MZ tiny", "windows"),
          "a stub too small to be the program passed as one")
    check(upd.looks_like_program(PROGRAM_BYTES, "windows"),
          "a proper windows program head was not recognised")
    check(upd.looks_like_program(ELF_BYTES, "linux"),
          "a proper linux program head was not recognised")

    data, how = upd.check_bytes(PROGRAM_BYTES, "windows")
    check(data == PROGRAM_BYTES and how == "as it came",
          "a raw program was not taken as it stood")

    # The release's file is a zip holding the program under a folder of its
    # own, and it is a zip for either system: both have to be unpacked.
    for side in ("windows", "linux"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"utils/update util/dist/{SYSTEM_FILE[side]}",
                             program_bytes(side))
            archive.writestr("utils/update util/dist/addons/README.txt",
                             b"note")
        data, how = upd.check_bytes(buffer.getvalue(), side)
        check(data == program_bytes(side) and "unpacked" in how,
              f"the {side} program inside an artifact-shaped zip was not "
              f"found: {how!r}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", b"only a readme")
    try:
        upd.check_bytes(buffer.getvalue(), "windows")
        check(False, "a zip whose only file is a readme was accepted as a program")
    except upd.AddonError as error:
        check("not a program" in str(error),
              f"a zip without a program said the wrong thing: {error}")

    try:
        upd.check_bytes(b"PK\x03\x04" + b"junk" * 100, "windows")
        check(False, "bytes that only start like a zip unpacked anyway")
    except upd.AddonError as error:
        check("not one" in str(error), f"a fake zip said the wrong thing: {error}")

    page = b"<!doctype html>\n<html><head><title>Sign in</title>" + b" " * 200_000
    try:
        upd.check_bytes(page, "windows")
        check(False, "an artifact sign-in page was accepted as a program")
    except upd.AddonError as error:
        check("sign-in" in str(error) and "release" in str(error),
              f"the artifact-page refusal did not explain itself: {error}")

    try:
        upd.check_bytes(b"small", "windows")
        check(False, "five bytes were a program")
    except upd.AddonError as error:
        check("too small" in str(error), f"a stub said the wrong thing: {error}")


# --- five: the swap, in a temporary folder ---

def test_the_swap(upd: Any) -> None:
    print("\n15. the new program takes the old one's place")
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        target = folder / THE_FILE
        target.write_bytes(b"the old program")
        staged = folder / f"{THE_FILE}.new"
        staged.write_bytes(THE_BYTES)
        words = upd.swap_program(target, staged)
        check(target.read_bytes() == THE_BYTES,
              "the new program did not land where the old one ran")
        check(not staged.exists(), "the staged download was left behind")
        check(not (folder / f"{THE_FILE}.old").exists(),
              f"the moved-aside leftover was not taken back ({words!r})")
        left = sorted(p.name for p in folder.iterdir())
        check(left == [THE_FILE],
              f"the swap moved more than the one program file: {left}")
        try:
            upd.swap_program(target, folder / "nowhere.new")
            check(False, "a swap with nothing staged went ahead anyway")
        except upd.AddonError as error:
            check("not where it was staged" in str(error),
                  f"a missing staged file said the wrong thing: {error}")


# --- six: what Install refuses, and the job all the way through ---

def test_the_job(upd: Any) -> None:
    print("\n16. Install refuses what it has no business doing")
    api = fresh(upd, version="1.6.0")
    upd.update_selected()
    check("no version picked yet" in last_status(api),
          f"Install with nothing picked did not say so: {last_status(api)!r}")

    upd.check_now()
    upd.published[2]["stance"] = "too old"
    upd.pick_row(2)
    upd.update_selected()
    check("older than 1.6.0" in last_status(api),
          f"a version below the floor was not refused before the download: "
          f"{last_status(api)!r}")

    bare = dict(upd.published[0])
    bare["download"] = None
    upd.published[0] = bare
    upd.pick_row(0)
    upd.update_selected()
    check("carries no download" in last_status(api),
          f"Install with no file attached did not say so: {last_status(api)!r}")

    # A re-check reads the release again and puts its file back, and this copy
    # runs from its source files, so the swap is turned away before anything
    # is fetched.
    upd.check_now()
    upd.pick_row(0)
    upd.update_selected()
    check("runs from its source files" in last_status(api),
          f"a copy running from source was not turned away kindly: "
          f"{last_status(api)!r}")

    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        target = folder / THE_FILE
        target.write_bytes(b"the old program")
        stage = folder / "stage"
        stage.mkdir()
        api.answer("ui.question", False)
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM):
            upd.update_selected()
        check("declined" in last_status(api),
              f"a declined update did not say so: {last_status(api)!r}")
        check(target.read_bytes() == b"the old program",
              "a declined update still touched the program")
        check(not upd.working and upd.found is None,
              "a declined update left a job or a picked version standing")

        print("\n17. the job: redirects, bytes, the swap, the offer to restart")
        api.answer("ui.question", True)
        body = program_bytes(THE_SYSTEM, b"\xAA")
        fetcher = FakeFetcher([
            FakeResp(302, b"", {"Location": "https://objects.example/f/berry.exe?sig=1"}),
            FakeResp(200, body, {"Content-Length": str(len(body))}),
        ])
        restarts: list[int] = []
        steps: list[Any] = []
        asked_before = len(api.calls)
        tails_before = len(TAILS)
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: fetcher,
                     _later=lambda seconds, callback: steps.append(callback),
                     restart_app=lambda: restarts.append(1)):
            upd.update_selected()
            check(upd.working and upd.job is not None,
                  "the update did not begin as a job")
            while steps:
                steps.pop(0)()          # drive the paced machine by hand
            check(not upd.working and upd.job is None,
                  "the finished job is still working or still holding itself")
            check(target.read_bytes() == body,
                  "the downloaded program is not what landed in its place")
            check(not (stage / f"{THE_FILE}.new").exists(),
                  "the staged file survived the swap")
            check(not (folder / f"{THE_FILE}.old").exists(),
                  "the moved-aside leftover survived the swap")
            check(upd.found is None, "a done update still holds the version it used")
            status = last_status(api)
            check("is in place" in status and "not touched" in status,
                  f"the finish line does not account for the config: {status!r}")
            check(restarts == [1],
                  f"the finished update did not offer a restart through the app: "
                  f"{restarts}")
            # The list is re-placed against the version that just landed, and
            # that re-place must not read GitHub again.
            check([entry["stance"] for entry in upd.published]
                  == ["this", "older", "too old"],
                  f"the list was not re-placed after the update: "
                  f"{[(e['version'], e['stance']) for e in upd.published]}")
            check(len([1 for where, arguments, _n in api.calls[asked_before:]
                       if where == "config.stored"]) >= 1
                  and not any(tail.startswith("tags")
                              for tail in TAILS[tails_before:]),
                  f"the list read the versions again after installing one: "
                  f"{TAILS[tails_before:]}")

        print("\n18. going back above the keep line asks three answers, and the one "
              "that keeps the setup lands")
        target.write_bytes(b"the old program")
        # The keep line is where this copy's setup may be left in place: the
        # published fixtures sit at 1.6.x, so it is moved down to them rather
        # than the versions being moved up to it. Its default is the clamp.
        api2 = fresh(upd, version="1.6.1",
                     config={"keep_min_version": "1.5.0"})
        api2.answer("ui.dialog", {"answer": upd.KEEP_ANSWER})
        api2.answer("ui.question", True)
        upd.check_now()
        upd.pick_row(1)
        body2 = program_bytes(THE_SYSTEM, b"\xBB")
        restarts2: list[int] = []
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, body2, {"Content-Length": str(len(body2))})]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: restarts2.append(1)):
            upd.update_selected()
        boxes = [(arguments, named) for where, arguments, named in api2.calls
                 if where == "ui.dialog"]
        check(len(boxes) == 1, f"a downgrade did not ask its own question once: {boxes}")
        words = spec_words(boxes[0][0]) if boxes else ""
        check(boxes and str(boxes[0][0][1]) == "Go back to berry1.6.0?"
              and "This copy is berry1.6.1" in words
              and "older program" in words
              and "hotfix on the same line" in words,
              f"the way back was not said as a way back: "
              f"{None if not boxes else boxes[0][0][1]!r}")
        check(boxes and all(str(risk).split(",")[0] in words
                            for risk in upd.DOWNGRADE_RISKS),
              f"the question does not name what may go wrong: {words!r}")
        check(boxes and list(boxes[0][1].get("answers") or [])
              == [upd.ERASE_ANSWER, upd.KEEP_ANSWER, upd.CANCEL_ANSWER],
              f"the three answers are not the three offered: "
              f"{None if not boxes else boxes[0][1]}")
        asked = [arguments for where, arguments, _n in api2.calls
                 if where == "ui.question"]
        check(not any(row[0] == "Update Berry Manager?" for row in asked),
              f"a downgrade asked the update's yes or no as well: {asked}")
        check(asked and asked[-1][0] == "Restart Berry Manager?"
              and "Go back to it now?" in str(asked[-1][1]),
              f"the finished downgrade did not ask to start the older copy: "
              f"{asked[-1] if asked else None}")
        check("berry1.6.0 is in place" in last_status(api2)
              and "were not touched" in last_status(api2),
              f"a downgrade did not say which older copy had landed: "
              f"{last_status(api2)!r}")
        check(target.read_bytes() == body2,
              "a downgrade did not land where the newer program ran")
        check([entry["stance"] for entry in upd.published][1] == "this",
              f"the list still calls the downgraded-to version a way back: "
              f"{[(e['version'], e['stance']) for e in upd.published]}")
        check(restarts2 == [1], "the downgrade did not offer to start the older copy")

        print("\n18a. below the keep line the middle answer is not on offer")
        target.write_bytes(b"the old program")
        api5 = fresh(upd, version="1.6.1")      # the shipped 2.0.0 clamp holds
        api5.answer("ui.dialog", {"answer": upd.KEEP_ANSWER})
        api5.answer("ui.question", True)
        upd.check_now()
        upd.pick_row(1)
        fetches5: list[int] = []
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: fetches5.append(1) or FakeFetcher([]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        boxes5 = [(arguments, named) for where, arguments, named in api5.calls
                  if where == "ui.dialog"]
        check(len(boxes5) == 1
              and list(boxes5[0][1].get("answers") or [])
              == [upd.ERASE_ANSWER, upd.CANCEL_ANSWER],
              f"a way back below the keep line was still asked with the answer "
              f"that keeps it: {None if not boxes5 else boxes5[0][1]}")
        words5 = spec_words(boxes5[0][0]) if boxes5 else ""
        check(f"further back than {upd.KEEP_ALLOWED}" in words5,
              f"the two answers did not say why the third is missing: {words5!r}")
        check("erase it and start clean" in last_status(api5)
              and f"berry1.6.0 is further back than {upd.KEEP_ALLOWED}"
              in last_status(api5),
              f"an answer that keeps the setup below the line was obeyed rather "
              f"than refused: {last_status(api5)!r}")
        check(not fetches5 and target.read_bytes() == b"the old program",
              "a refused keep still fetched and still swapped the program")
        check(not upd.working and upd.job is None,
              "a refused keep left the addon holding a job")
        # The clamp is a rule about the version, and the line it is measured on
        # is a setting like the floor: two dots and three read the same.
        moved = upd.merge_config(upd.DEFAULTS, {"keep_min_version": "2"})
        check(upd.keep_floor(moved) == "2"
              and upd.keep_allowed(moved, "2.0.0") is True
              and upd.keep_allowed(moved, "1.9.9") is False
              and upd.keep_allowed(moved, "") is False,
              f"the keep line does not read versions the way the list does: "
              f"{upd.keep_floor(moved)!r}")
        check(upd.keep_floor(upd.merge_config(upd.DEFAULTS, {}))
              == upd.KEEP_ALLOWED,
              "the default keep line is not the one the module names")
        check(upd.downgrade_answers(False) == [upd.ERASE_ANSWER, upd.CANCEL_ANSWER]
              and upd.downgrade_answers(True)
              == [upd.ERASE_ANSWER, upd.KEEP_ANSWER, upd.CANCEL_ANSWER],
              f"the answers under and above the line are not the two and three: "
              f"{upd.downgrade_answers(False)}")
        upd.pick_row(0)

        print("\n18b. the third answer leaves everything where it is")
        target.write_bytes(b"the old program")
        api3 = fresh(upd, version="1.6.1")
        api3.answer("ui.dialog", {"answer": upd.CANCEL_ANSWER})
        upd.check_now()
        upd.pick_row(1)
        tails_at_pick = len(TAILS)
        touched: list[int] = []
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: touched.append(1) or FakeFetcher([]),
                     restart_app=lambda: None):
            upd.update_selected()
            # a box closed without an answer is the same answer
            api3.answer("ui.dialog", None)
            upd.update_selected()
        check("turned down" in last_status(api3)
              and "nothing was fetched" in last_status(api3),
              f"a turned-down way back did not say so: {last_status(api3)!r}")
        check(not touched, "a cancelled way back opened its download anyway")
        check(TAILS[tails_at_pick:] == [],
              f"a cancelled way back read GitHub again: {TAILS[tails_at_pick:]}")
        check(not api3.called("ui.progress_notice") and not api3.called("ui.question"),
              "a cancelled way back started a job or asked another question")
        check(not upd.working and upd.job is None and upd.found is None,
              "a cancelled way back left the addon holding a job")
        check(target.read_bytes() == b"the old program",
              "a cancelled way back still touched the program")
        check("turned down" in last_status(api3),
              f"a closed box was not read as a refusal: {last_status(api3)!r}")

        print("\n18c. erase data and download takes the written things and the "
              "installed addons, and nothing else")
        base = folder / "erase"
        (base / "config").mkdir(parents=True)
        (base / "config" / "profiles.json").write_text("{}", encoding="utf-8")
        (base / "themes").mkdir()
        (base / "themes" / upd.SHIPPED_THEME).write_text("a = 1\n", encoding="utf-8")
        (base / "themes" / "mine.theme").write_text("a = 2\n", encoding="utf-8")
        (base / "addons").mkdir()
        data = base / "addons" / "_data"
        (data / "berry_web").mkdir(parents=True)
        (data / "some_addon").mkdir(parents=True)
        (data / "berry_web" / "config.json").write_text("{}", encoding="utf-8")
        (data / "some_addon" / "config.json").write_text("{}", encoding="utf-8")
        (base / "addons" / "berry_web.py").write_text("# shipped\n", encoding="utf-8")
        (base / "addons" / "some_addon.py").write_text("# installed\n", encoding="utf-8")
        (base / "addons" / f"{upd.OWN_ID}.py").write_text("# this addon\n",
                                                           encoding="utf-8")
        keep_folder = base / "addons" / "some_addon"
        keep_folder.mkdir()
        (keep_folder / "helper.py").write_text("# part of the addon\n",
                                                encoding="utf-8")
        target2 = base / THE_FILE
        target2.write_bytes(b"the old program")
        stage2 = base / "stage"
        stage2.mkdir()
        rows = [{"id": "berry_web", "bundled": True, "running": True, "enabled": True},
                {"id": "some_addon", "bundled": False, "running": True,
                 "enabled": True},
                {"id": upd.OWN_ID, "bundled": False, "running": True, "enabled": True}]
        api4 = fresh(upd, version="1.6.1")
        api4.answer("ui.dialog", {"answer": upd.ERASE_ANSWER})
        api4.answer("ui.question", False)      # no restart at the end of this one
        api4.answer("session.addons", rows)
        upd.check_now()
        upd.pick_row(1)
        body3 = program_bytes(THE_SYSTEM, b"\xCC")
        with patched(upd, program_path=lambda: target2,
                     staging_target=lambda name: stage2 / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, body3, {"Content-Length": str(len(body3))})]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        check(not (base / "config").exists(), "the config folder outlived an erase")
        check(not (base / "themes" / "mine.theme").exists(),
              "a theme somebody copied outlived an erase")
        check((base / "themes" / upd.SHIPPED_THEME).exists(),
              "an erase took the theme that came inside the program")
        check(not (base / "addons" / "some_addon.py").exists()
              and not keep_folder.exists() and not (data / "some_addon").exists(),
              "an erase left part of an installed addon behind")
        check((base / "addons" / "berry_web.py").exists()
              and (data / "berry_web").exists(),
              "an erase took an addon that came inside the program")
        check((base / "addons" / f"{upd.OWN_ID}.py").exists(),
              "an erase took the addon that was running it")
        stops = [arguments for where, arguments, _n in api4.calls
                 if where == "session.set_addon_running"]
        check(("some_addon", False) in stops
              and not any(str(row[0]) == "berry_web" for row in stops),
              f"an erase did not take the installed addons down first: {stops}")
        backup = base / "backup-before-1.6.0.zip"
        check(backup.is_file(),
              f"no backup landed beside the program: {sorted(p.name for p in base.iterdir())}")
        kept: list[str] = []
        if backup.is_file():
            with zipfile.ZipFile(backup) as archive:
                kept = list(archive.namelist())
        check(any(name.startswith("config/") for name in kept)
              and any(name.startswith("addons/_data/some_addon/") for name in kept)
              and any(name.startswith("addons/_data/berry_web/") for name in kept),
              f"the backup is not everything that was about to go: {kept}")
        check(any("backup-before-1.6.0.zip" in line for line in api4.logs),
              f"the backup was not said out loud where it was written: {api4.logs[-4:]}")
        check(any("some_addon" in line and "stopped and deleted" in line
                  for line in api4.logs),
              f"the erased addons were not accounted for: {api4.logs[-4:]}")
        check(not api4.called("session.rescan_addons"),
              "an erase rescanned while its own download was in the air")
        said = [str(text) for text in sets_for(api4, "status", "text")]
        check(any("written data is erased" in line for line in said),
              f"the erase did not say it had happened before fetching: {said}")
        check(any("is in place" in line and "backed up and erased" in line
                  for line in said),
              f"the finish line did not account for the erase: "
              f"{last_status(api4)!r}")
        check("were not touched" not in last_status(api4),
              f"an erased download still promised nothing had been touched: "
              f"{last_status(api4)!r}")
        check(target2.read_bytes() == body3,
              "the download after an erase did not land in the erased program's place")
        check(not upd.working and upd.job is None and upd.found is None,
              "the erase job left the addon working")

        print("\n19. a page on the wire stops the update without touching anything")
        target.write_bytes(b"the old program")
        api3 = fresh(upd, version="1.6.0")
        upd.check_now()
        upd.pick_row(0)
        page = b"<!doctype html>\n<html>" + b" " * 200_000
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher([FakeResp(200, page)]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()      # _later immediate runs the whole walk at once
        check("the update stopped" in last_status(api3),
              f"an artifact page did not stop the update: {last_status(api3)!r}")
        check("sign-in" in last_status(api3),
              f"the refusal did not name the sign-in problem: {last_status(api3)!r}")
        check(target.read_bytes() == b"the old program",
              "a refused download still replaced the program")
        check(not upd.working and upd.job is None and upd.found is not None,
              "the stopped job left the addon in a state the user cannot retry from")

        print("\n20. a short body is an early stop, not a program")
        api4 = fresh(upd, version="1.6.0")
        upd.check_now()
        upd.pick_row(0)
        short = SYSTEM_HEAD[THE_SYSTEM] + b"\x00" * 500
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, short, {"Content-Length": "999999"})]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        check("stopped early" in last_status(api4),
              f"a body shorter than promised passed as a program: "
              f"{last_status(api4)!r}")

        print("\n21. a redirect that never ends is given up on")
        api5 = fresh(upd, version="1.6.0")
        upd.check_now()
        upd.pick_row(0)
        hops = [FakeResp(302, b"", {"Location": f"https://x/{n}"})
                for n in range(upd.MAX_REDIRECTS + 2)]
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(hops),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        check("pointed somewhere else" in last_status(api5),
              f"a download that kept being redirected was not given up on: "
              f"{last_status(api5)!r}")
        check(target.read_bytes() == b"the old program",
              "a given-up download still touched the program")

    upd.found = None
    upd.working = False
    upd.job = None
    upd.shutdown()


# --- six b: the view the download puts over the tab ---

def test_the_job_view(upd: Any) -> None:
    """A download lays its console over the version list and counts its bytes on it.

    The pane is only there when the built tab says it is: ``tabs.set_widget``
    answers ``False`` for an id the layout does not hold. Both answers are driven
    here on the stub api, with the swap's own file in a temporary folder, so the
    card's name, the bar's share, the console's lines and the hold the job puts on
    the window's tab bar are all seen - and a tab with no pane is seen writing to
    no console and holding nothing.
    """
    print("\n22. the job's console, the card over it, and the tab bar it holds")
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        target = folder / THE_FILE
        target.write_bytes(b"the old program")
        stage = folder / "stage"
        stage.mkdir()
        body = program_bytes(THE_SYSTEM, b"\xAA")
        total = len(body)

        api = fresh(upd, version="1.6.0")
        api.answer("ui.question", True)
        api.answer("tabs.set_widget", True)
        upd.check_now()
        upd.pick_row(0)
        steps: list[Any] = []
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, body, {"Content-Length": str(total)})]),
                     _later=lambda seconds, callback: steps.append(callback),
                     restart_app=lambda: None):
            api.calls.clear()
            upd.update_selected()
            check(upd.job is not None and upd.job.get("pane") is True,
                  f"a tab holding the pane was never told to show it: {upd.job}")
            check(last_set(api, "jobpane", "visible") is True
                  and last_set(api, "browse", "visible") is False,
                  f"the job did not put its console over the list: "
                  f"{last_set(api, 'jobpane', 'visible')!r} / "
                  f"{last_set(api, 'browse', 'visible')!r}")
            check(last_set(api, "jobterm", "clear") is True,
                  "the console was started on a screen that still held the "
                  "last job's lines")
            # The job holds the window's tab bar for as long as its console is
            # up: the progress is the one thing on screen worth reading.
            check(api.asked("tabs.lock", "locked") is True,
                  "a running download did not hold the window's tab bar: "
                  f"{api.last('tabs.lock')}")
            check(str(last_set(api, "jobname", "text")) == "berry1.6.1",
                  f"the card does not name the version being fetched: "
                  f"{last_set(api, 'jobname', 'text')!r}")
            check(bool(last_set(api, "jobicon", "image")),
                  "the card wears no mark of its own")

            # the answer's head is the first line on the screen, in both places
            steps.pop(0)()                      # connect_step, by hand
            line = f"downloading... 4 KB of {upd._size(total)}"
            check(str(last_set(api, "jobstep", "text")) == line,
                  f"the card never said how much had come down: "
                  f"{last_set(api, 'jobstep', 'text')!r}")
            check(0 < int(last_set(api, "jobbar", "value") or 0) < 99,
                  f"a fifth of the way down read as nothing or as finished: "
                  f"{last_set(api, 'jobbar', 'value')!r}")
            check(not api.called("ui.progress_notice"),
                  "a step went out to the floating notice that was removed")
            written = [arguments[3] for where, arguments, _n in api.calls
                       if where == "tabs.set_widget" and len(arguments) >= 4
                       and arguments[1] == "jobterm" and arguments[2] == "write"]
            check(any(str(one).startswith("updating to berry1.6.1") for one in written),
                  f"the console never heard which version was being fetched: "
                  f"{written}")
            check(any(line in str(one) for one in written),
                  f"the console was not told the byte count the card was: "
                  f"{written}")

            while steps:
                steps.pop(0)()                  # the rest of the walk, to the swap
            check(str(last_set(api, "jobstep", "text")).endswith(
                  "; putting it in place"),
                  f"the card never said the bytes were in: "
                  f"{last_set(api, 'jobstep', 'text')!r}")
            check(last_set(api, "jobbar", "value") == 99,
                  f"the swap began with the bar not nearly full: "
                  f"{last_set(api, 'jobbar', 'value')!r}")

        check(last_set(api, "jobpane", "visible") is False
              and last_set(api, "browse", "visible") is True,
              "the job kept its console up after the program landed")
        check(api.asked("tabs.lock", "locked") is False,
              "the finished update still holds the window's tab bar: "
              f"{api.last('tabs.lock')}")
        check(last_set(api, "jobbar", "value") == 99,
              f"the finished update filled a card that is being taken down: "
              f"{last_set(api, 'jobbar', 'value')!r}")
        check(target.read_bytes() == body, "the job view's run did not land the file")

        # a job on a tab with no pane: nothing is written to a console that is
        # not on screen, and no tab bar is held for a job nobody can see
        target.write_bytes(b"the old program")
        api2 = fresh(upd, version="1.6.0")
        api2.answer("ui.question", True)
        upd.check_now()
        upd.pick_row(0)
        api2.calls.clear()
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: THE_SYSTEM,
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, body, {"Content-Length": str(total)})]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        check(any(where == "tabs.set_widget" and len(arguments) >= 3
                  and arguments[1] == "jobpane"
                  for where, arguments, _n in api2.calls),
              "a job never asked the tab whether it has the pane at all")
        check(not [arguments for where, arguments, _n in api2.calls
                   if where == "tabs.set_widget" and len(arguments) >= 3
                   and arguments[1] in ("jobterm", "jobcard", "jobicon",
                                        "jobname", "jobstep", "jobbar")],
              "a job with no pane wrote to the console and card that are not there")
        check(not api2.called("ui.progress_notice")
              and not api2.called("tabs.lock"),
              "a job with no pane showed a notice or held a bar it cannot be "
              "seen behind")
        check(not upd.working and upd.job is None,
              "a job on a paneless tab could not end")

    upd.found = None
    upd.working = False
    upd.job = None


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint (● ○ ✓),
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    upd = addon_client.load_addon(SOURCE)
    print(f"manager_update: the addon at {SOURCE.name}")
    print(f"       told from the {THE_SYSTEM} side: {THE_PROGRAM}, "
          f"{THE_FILE}, {len(THE_BYTES)} bytes")

    # These two ask about either system by name, so they read the machine as it
    # is; the records past them are one story about one system, and the addon
    # asks its own platform which file to take, so that answer is pinned here
    # rather than left to whichever runner got the job.
    test_the_helpers(upd)
    test_the_release(upd)
    with patched(upd, system_name=lambda: THE_SYSTEM):
        test_the_tab(upd)
        test_the_list_and_the_pane(upd)
        test_the_way_back(upd)
        test_the_notice(upd)
        test_the_buttons(upd)
        test_the_schedule(upd)
        test_the_bytes(upd)
        test_the_swap(upd)
        test_the_job(upd)
        test_the_job_view(upd)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())