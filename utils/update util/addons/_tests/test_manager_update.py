#!/usr/bin/env python3
"""A test for addons/manager_update.py.

The update addon's whole job is to read this program's own tags off GitHub,
work out whether one is ahead of the running copy, fetch the release made for
it and swap the downloaded program into place without touching the config or
the addons. None of that needs a device or the network, and nothing here
reaches either: the Qt-free half is driven directly - how tags read as
versions, what a release offers this machine, what downloaded bytes really
are - and the file work happens inside a temporary folder with dummy files
standing in for the program.

What gets looked at:

* the pure helpers: version compares, tag names, the address split that has
  to keep a download's one-time signature;
* what a release offers: assets named for one system or the other, the
  fallback to plain links in the description, and the refusal of an artifact
  page that would only answer with a sign-in;
* the tab on a stub api: init declares and builds it, and every check answer
  says the right thing - no tags yet (normal, not an error), a tag ahead with
  a download, a tag ahead without one, and a copy already newest;
* the download's byte sniffing: a real program, a zip like the workflow
  ships, and a page masquerading as one;
* the swap itself in a temporary folder: new bytes land where the old program
  ran, the leftover goes, nothing else moved;
* the job machine: what the buttons refuse, and the full redirect-to-swap
  walk driven through a patched scheduler with a fake answer on the wire.

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

#: A body big enough to pass for this program when bytes get sniffed.
PROGRAM_BYTES = b"MZ" + b"\x90" * (200_000)


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


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


# --- one: the pure helpers, with no api standing up ---

def test_the_helpers(upd: Any) -> None:
    print("\n1. versions, tags and addresses")
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

    best = upd.pick_latest(["v2.0", "berry1.5.9", "berry1.6.0", "berry1.5.10"], "berry")
    check(best == ("berry1.6.0", "1.6.0"),
          f"the newest tag out of an unordered list is wrong: {best!r}")
    check(upd.pick_latest(["v2.0"], "berry") is None,
          "a list with no update tags produced one anyway")
    check(upd.pick_latest([], "berry") is None, "an empty tag list produced a winner")

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
    check(win and win["name"] == "berry_manager-windows.exe",
          f"windows did not pick its own asset: {win!r}")
    lin = upd.pick_asset(release, "linux")
    check(lin and lin["name"] == "berry_manager-linux",
          f"linux picked the wrong side's asset: {lin!r}")
    check(upd.pick_asset({"assets": [{"name": "berry_manager-windows.exe",
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
    check(src == ("berry_manager-windows.exe", "https://x/win", "release asset"),
          f"an asset did not win over the description: {src!r}")
    only_link = {"body": "get https://dl.example/berry-linux.tar " }
    src = upd.download_source(only_link, "linux")
    check(src and src[1] == "https://dl.example/berry-linux.tar"
          and "description" in src[2],
          f"a link in the description was not used as the fallback: {src!r}")
    check(upd.download_source({}, "windows") is None,
          "an empty release offered a download anyway")


# --- three: the tab and the check, on a stub api ---

def fresh(upd: Any, config: dict | None = None, version: str = "1.5.8") -> Any:
    """The addon on a stub api: no startup check, no network, a known version."""
    upd.api = None
    upd.working = False
    upd.job = None
    upd.found = None
    api = addon_client.StubAPI(addon_id="manager_update",
                               config={"check_startup": False, **dict(config or {})})
    api.answer("session.version", version)
    upd.init(api)
    api.timers.cancel_all()
    return api


def last_status(api: Any) -> str:
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "status" and arguments[2] == "text":
            return str(arguments[3])
    return ""


def last_info(api: Any) -> str:
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "info" and arguments[2] == "value":
            return str(arguments[3])
    return ""


def test_the_tab(upd: Any) -> None:
    print("\n3. init builds the tab and its parts")
    calls: list[int] = []
    upd.list_tags = lambda values: calls.append(1) or []
    api = fresh(upd)
    check(api.called("config.declare") and api.called("tabs.add_tab")
          and api.called("tabs.set_icon") and api.called("ui.menu_item"),
          "init did not declare its config, add and icon its tab and its menu entry")
    check(not calls, "a start-up check fired even though check_startup was off")

    print("\n4. the check, in the four states it can end in")
    with patched(upd, system_name=lambda: "windows"):
        # no tags at all: the state of a program never published from here
        upd.list_tags = lambda values: []
        upd.release_for = lambda values, tag: {}
        upd.check_now()
        check("no berry<version> tag is published yet" in last_status(api),
              f"no tags yet was not said calmly: {last_status(api)!r}")
        check(upd.found is None, "the empty tag state still found an update")
        check("normal for a program that has never been released" in last_info(api),
              "the no-tags account did not explain that nothing is wrong yet")

        # a tag ahead, with a release asset both systems can accept
        upd.list_tags = lambda values: ["berry1.5.9", "v-old"]
        upd.release_for = lambda values, tag: {
            "assets": [{"name": "berry_build",
                        "browser_download_url": "https://x/one"}]}
        upd.check_now()
        check(upd.found and upd.found["tag"] == "berry1.5.9"
              and upd.found["version"] == "1.5.9"
              and upd.found["download"]
              and upd.found["download"][2] == "release asset",
              f"the newer tag did not come with its download: {upd.found!r}")
        check("Download and install will replace this copy" in last_status(api),
              f"an available update did not offer the button: {last_status(api)!r}")
        check("This copy is behind" in last_info(api),
              "the account did not say this copy is behind")

        # a tag ahead, but nothing downloadable attached to it
        upd.release_for = lambda values, tag: {}
        upd.check_now()
        check(upd.found and upd.found["download"] is None,
              f"a bare tag gained a download anyway: {upd.found!r}")
        check("nothing for this system is attached" in last_status(api),
              f"a tag without a download was not called out: {last_status(api)!r}")
        check("publish the tag as a release" in last_info(api).lower()
              and "artifact" in last_info(api).lower(),
              "the account did not tell the author how to fix a tag with no files")

        # up to date
        api2 = fresh(upd, version="1.5.9")
        with patched(upd, system_name=lambda: "windows"):
            upd.check_now()
            check("berry1.5.9 is the newest published tag" in last_status(api2),
                  f"being up to date said something else: {last_status(api2)!r}")
            check(upd.found is None, "an equal tag was still counted as newer")

    # a broken check is a status line, not a crash
    api3 = fresh(upd)

    def boom(values: dict) -> list[str]:
        raise upd.AddonError("GitHub refused the tags")

    upd.list_tags = boom
    upd.check_now()
    check("GitHub refused the tags" in last_status(api3),
          f"a failed check did not say why: {last_status(api3)!r}")
    upd.list_tags = lambda values: []

    print("\n5. the tab's buttons and the config change route through")
    api4 = fresh(upd)
    seen: list[str] = []
    with patched(upd, check_now=lambda c=False: seen.append("check"),
                 update_selected=lambda c=False: seen.append("update"),
                 open_page=lambda c=False: seen.append("page")):
        upd.on_widget(upd.TAB, "check", "clicked", {})
        upd.on_widget(upd.TAB, "update", "clicked", {})
        upd.on_widget(upd.TAB, "page", "clicked", {})
        upd.on_widget("Other tab", "check", "clicked", {})
        upd.on_widget(upd.TAB, "check", "toggled", {})
    check(seen == ["check", "update", "page"],
          f"the buttons did not route by their ids: {seen}")
    seen.clear()
    with patched(upd, check_now=lambda c=False: seen.append("check")):
        upd.on_config({"addon": "manager_update"})
        check(seen == ["check"], f"a config change did not re-point and re-check: {seen}")
        upd.on_config({"addon": "something else"})
        check(seen == ["check"], "another addon's config change set this one off")
    upd.shutdown()
    check(upd.api is None and upd.found is None and not upd.working,
          "shutdown left the addon holding an api or a job")


# --- four: what downloaded bytes really are ---

def test_the_bytes(upd: Any) -> None:
    print("\n6. a program, a zip, and a page dressed up as one")
    check(not upd.looks_like_program(b"MZ tiny", "windows"),
          "a stub too small to be the program passed as one")
    check(upd.looks_like_program(PROGRAM_BYTES, "windows"),
          "a proper windows program head was not recognised")
    check(upd.looks_like_program(b"\x7fELF" + b"\x00" * 200_000, "linux"),
          "a proper linux program head was not recognised")

    data, how = upd.check_bytes(PROGRAM_BYTES, "windows")
    check(data == PROGRAM_BYTES and how == "as it came",
          "a raw program was not taken as it stood")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("utils/update util/dist/berry_manager.exe", PROGRAM_BYTES)
        archive.writestr("utils/update util/dist/addons/README.txt", b"note")
    data, how = upd.check_bytes(buffer.getvalue(), "windows")
    check(data == PROGRAM_BYTES and "unpacked" in how,
          f"the program inside an artifact-shaped zip was not found: {how!r}")

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
    print("\n7. the new program takes the old one's place")
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        target = folder / "berry_manager.exe"
        target.write_bytes(b"the old program")
        staged = folder / "berry_manager.exe.new"
        staged.write_bytes(PROGRAM_BYTES)
        words = upd.swap_program(target, staged)
        check(target.read_bytes() == PROGRAM_BYTES,
              "the new program did not land where the old one ran")
        check(not staged.exists(), "the staged download was left behind")
        check(not (folder / "berry_manager.exe.old").exists(),
              f"the moved-aside leftover was not taken back ({words!r})")
        left = sorted(p.name for p in folder.iterdir())
        check(left == ["berry_manager.exe"],
              f"the swap moved more than the one program file: {left}")
        try:
            upd.swap_program(target, folder / "nowhere.new")
            check(False, "a swap with nothing staged went ahead anyway")
        except upd.AddonError as error:
            check("not where it was staged" in str(error),
                  f"a missing staged file said the wrong thing: {error}")


# --- six: what the buttons refuse, and the job all the way through ---

def test_the_job(upd: Any) -> None:
    print("\n8. the update refuses what it has no business doing")
    api = fresh(upd)
    upd.found = None
    upd.update_selected()
    check("press Check first" in last_status(api),
          f"Update with nothing found did not say so: {last_status(api)!r}")

    upd.found = {"tag": "berry1.6.0", "version": "1.6.0", "installed": "1.5.8",
                 "download": None}
    upd.update_selected()
    check("no download for this system" in last_status(api),
          f"Update with no download did not say so: {last_status(api)!r}")

    upd.found = {"tag": "berry1.6.0", "version": "1.6.0", "installed": "1.5.8",
                 "download": ("build", "https://x/one", "release asset")}
    upd.update_selected()               # not frozen here: the checkout refusal
    check("runs from its source files" in last_status(api),
          f"a copy running from source was not turned away kindly: {last_status(api)!r}")

    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        target = folder / "berry_manager.exe"
        target.write_bytes(b"the old program")
        stage = folder / "stage"
        stage.mkdir()
        api.answer("ui.question", False)
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: "windows"):
            upd.update_selected()
        check("declined" in last_status(api),
              f"a declined update did not say so: {last_status(api)!r}")
        check(target.read_bytes() == b"the old program",
              "a declined update still touched the program")

        print("\n9. the job: redirects, bytes, the swap, the offer to restart")
        api.answer("ui.question", True)
        body = b"MZ" + b"\xAA" * 200_000
        fetcher = FakeFetcher([
            FakeResp(302, b"", {"Location": "https://objects.example/f/berry.exe?sig=1"}),
            FakeResp(200, body, {"Content-Length": str(len(body))}),
        ])
        restarts: list[int] = []
        steps: list[Any] = []
        upd.found = {"tag": "berry1.6.0", "version": "1.6.0", "installed": "1.5.8",
                     "download": ("berry-windows.exe",
                                  "https://github.example/dl/berry.exe",
                                  "release asset")}
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: "windows",
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
            check(not (stage / "berry_manager.exe.new").exists(),
                  "the staged file survived the swap")
            check(not (folder / "berry_manager.exe.old").exists(),
                  "the moved-aside leftover survived the swap")
            check(upd.found is None, "a done update still holds the tag it used")
            status = last_status(api)
            check("is in place" in status and "not touched" in status,
                  f"the finish line does not account for the config: {status!r}")
            check(restarts == [1],
                  f"the finished update did not offer a restart through the app: {restarts}")

        print("\n10. a page on the wire stops the update without touching anything")
        target.write_bytes(b"the old program")
        page = b"<!doctype html>\n<html>" + b" " * 200_000
        upd.found = {"tag": "berry1.6.0", "version": "1.6.0", "installed": "1.5.8",
                     "download": ("art", "https://x/artifact", "release asset")}
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: "windows",
                     _http=lambda values: FakeFetcher([FakeResp(200, page)]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()      # _later immediate runs the whole walk at once
        check("the update stopped" in last_status(api),
              f"an artifact page did not stop the update: {last_status(api)!r}")
        check("sign-in" in last_status(api),
              f"the refusal did not name the sign-in problem: {last_status(api)!r}")
        check(target.read_bytes() == b"the old program",
              "a refused download still replaced the program")
        check(not upd.working and upd.job is None and upd.found is not None,
              "the stopped job left the addon in a state the user cannot retry from")

        print("\n11. a short body is an early stop, not a program")
        upd.found = {"tag": "berry1.6.0", "version": "1.6.0", "installed": "1.5.8",
                     "download": ("x", "https://x/one", "release asset")}
        short = b"MZ" + b"\x00" * 500
        with patched(upd, program_path=lambda: target,
                     staging_target=lambda name: stage / name,
                     system_name=lambda: "windows",
                     _http=lambda values: FakeFetcher(
                         [FakeResp(200, short, {"Content-Length": "999999"})]),
                     _later=lambda seconds, callback: callback(),
                     restart_app=lambda: None):
            upd.update_selected()
        check("stopped early" in last_status(api),
              f"a body shorter than promised passed as a program: {last_status(api)!r}")

    upd.found = None
    upd.working = False
    upd.job = None
    upd.shutdown()


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

    test_the_helpers(upd)
    test_the_release(upd)
    test_the_tab(upd)
    test_the_bytes(upd)
    test_the_swap(upd)
    test_the_job(upd)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
