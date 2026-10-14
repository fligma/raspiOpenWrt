#!/usr/bin/env python3
"""A test for addons/addon_store.py.

The store's whole job is to read a catalog from GitHub and turn one entry of it
into a compiled program in the addons folder. None of that needs a device, and
none of it is looked at here by reaching the network or running PyInstaller: this
drives the Qt-free half directly - how ``index.json`` is read, how an id and a
raw address are cleaned, how the table is filled, and how a button click is
routed - and checks the install guard rails that fire before any real work.

Three things get looked at:

* the pure helpers: catalog parsing, id sanitising, and the raw path that the
  store folder's space has to survive;
* the tab on a stub api: that init declares and adds it, that Refresh fills the
  table, and that Install/Delete say the right thing when nothing sensible is
  selected - and, with Qt standing up, that the status line fits into the header
  and the table and the log really share one horizontal splitter;
* what counts as installed, against a temporary addons folder, and how a compiled
  addon finds its own bundled assets;
* how the store works out that the catalog is ahead of what is installed: the
  version kept beside an install, the way two dotted versions are compared, and
  what Update refuses, asks and then does when it goes ahead;
* the catalog generator that rebuilds ``index.json`` from the store folder, the
  store's rule that an addon's test is run before it is compiled, which Python
  it is allowed to build with, and how a refused pip install is retried;
* where the two modules an addon imports come from - the app's own folder, a
  copy the build carries, or GitHub - and what happens to an install when a test
  stops because this machine lacks them rather than because the addon failed;
* what an install leaves where: the build staged and then cleared inside the
  store's own ``_data`` folder, the new addon written into the profile as
  switched off, and the rescan that shuts the store down done last of all;
* the environment a built app hands to anything it starts: a restarted copy of
  itself, and everything it compiles, has to be an instance of its own rather
  than a worker left holding the dying copy's unpacked folder;
* what an install says about itself afterwards: the manifest beside the program
  that marks it as installed and names the transports it may use, the settings
  form left in its ``_data`` folder for Settings to show, and the two files
  Delete is allowed to take back.

No internet, no compiler and no device are needed. Run it from the Test panel,
or::

    python addons/_tests/test_addon_store.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

SOURCE = ROOT / "addons" / "addon_store.py"
FAILURES: list[str] = []

#: Held so the Qt widgets below outlive the function that first asks for them.
_QT_APP: Any = None

#: A catalog as the store would read it off GitHub, one installed elsewhere.
CATALOG = {
    "addons": [
        {"id": "hello_store", "name": "Hello from the store", "version": "1.0",
         "author": "fligma", "filename": "hello_store.py",
         "assets": ["helloicon.png"], "icon": "hello_store.ico",
         "test": "test_hello_store.py", "status": "new"},
        {"id": "odd-id", "name": "Odd"},                     # no filename: falls back
    ]
}


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- one: the pure helpers, with no api standing up ---

def test_the_helpers(store: Any) -> None:
    print("\n1. reading the catalog, cleaning an id, building the address")
    entries = store.parse_catalog(CATALOG)
    check(len(entries) == 2, f"two rows in, {len(entries)} rows out")
    check(entries[0]["id"] == "hello_store" and entries[0]["filename"] == "hello_store.py",
          f"the named entry did not come through: {entries[0]}")
    check(entries[1]["id"] == "odd_id" and entries[1]["filename"] == "odd_id.py",
          f"a dashed id with no filename was not sanitised and completed: {entries[1]}")
    check(entries[0]["assets"] == ["helloicon.png"]
          and entries[0]["icon"] == "hello_store.ico"
          and entries[0]["test"] == "test_hello_store.py"
          and entries[0]["status"] == "new",
          f"assets, icon, test and status did not come through the catalog: {entries[0]}")
    check(entries[1]["assets"] == [] and entries[1]["icon"] == ""
          and entries[1]["test"] == "" and entries[1]["status"] == "",
          f"an entry with nothing to carry gained something: {entries[1]}")

    check(store.parse_catalog([{"id": "only", "name": "Only"}])[0]["id"] == "only",
          "a bare list was not read as the catalog")
    check(not store.parse_catalog({"nope": 1}),
          "an object with no addons list came back with rows anyway")
    check(not store.parse_catalog("not a list"),
          "a string was read as a catalog")

    check(store.raw_address(dict(store.DEFAULTS), "hello_store.py")
          == "/fligma/raspiOpenWrt/main/utils/update%20util/addon.store/hello_store.py",
          f"the store folder's space did not survive the address: "
          f"{store.raw_address(dict(store.DEFAULTS), 'hello_store.py')!r}")

    quoted = store.raw_address({"owner": "a b", "repo": "c/d", "ref": "v 1",
                                "store_path": "u/ld"}, "x.py")
    check(quoted.startswith("/a%20b/c/d/v%201/u/ld/x.py") and ".." not in quoted,
          f"a messy owner, ref or path was not encoded before use: {quoted!r}")

    asset = store.raw_address(dict(store.DEFAULTS), "assets/hello_store/icon.png")
    check(asset == "/fligma/raspiOpenWrt/main/utils/update%20util/"
                    "addon.store/assets/hello_store/icon.png",
          f"an asset in the store folder did not get an address: {asset!r}")


# --- two: the tab on a stub api ---

def fresh(store: Any, config: dict | None = None, catalog: list | None = None) -> Any:
    """The addon on a stub api, its catalog patched so nothing reaches GitHub."""
    store.load_catalog = lambda values: list(catalog if catalog is not None
                                             else store.parse_catalog(CATALOG))
    api = addon_client.StubAPI(addon_id="addon_store", config=dict(config or {}))
    store.init(api)
    api.timers.cancel_all()          # kill init's scheduled refresh; tests drive it
    return api


def last_row_call(api: Any) -> Any:
    """The rows the addon last sent to its table, or nothing."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "list" and arguments[2] == "rows":
            return arguments[3]
    return None


def last_status(api: Any) -> str:
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "status" and arguments[2] == "text":
            return str(arguments[3])
    return ""


def test_the_tab(store: Any) -> None:
    print("\n2. init builds the tab and reads the catalog")
    api = fresh(store)
    check(api.called("config.declare") and api.called("tabs.add_tab")
          and api.called("ui.menu_item"),
          "init did not declare its config, add its tab and add its menu entry")
    icon_call = [arguments for where, arguments, _n in api.calls
                 if where == "tabs.set_icon"]
    check(bool(icon_call) and icon_call[0][0] == store.TAB,
          "the tab never asked to wear its own mark")
    check(any(where == "tabs.use_app_icon" for where, _a, _n in api.calls) is False,
          "the tab still wears the program's icon instead of its own")

    print("\n3. Refresh fills the table, installed or not")
    store.entries = []
    store.on_widget(store.TAB, "refresh", "clicked", {})
    rows = last_row_call(api) or []
    check(len(rows) == 2, f"two catalog rows did not reach the table: {len(rows)}")
    ids = [row["cells"][0] for row in rows]
    check(ids == ["hello_store", "odd_id"], f"the table is not the catalog: {ids}")
    check(all("cells" in row for row in rows), "a table row holds no cells")
    check(store.installed("hello_store") in (False, True),
          "installed() answered something unusable")

    print("\n4. the buttons say the right thing with nothing sensible selected")
    api.calls.clear()
    store.on_widget(store.TAB, "install", "clicked", {})
    check("pick an addon" in last_status(api),
          f"Install with no row picked did not say so: {last_status(api)!r}")
    api.calls.clear()
    store.on_widget(store.TAB, "update", "clicked", {})
    check("pick an installed addon" in last_status(api),
          f"Update with no row picked did not say so: {last_status(api)!r}")
    api.calls.clear()
    store.on_widget(store.TAB, "delete", "clicked", {})
    check("pick an installed addon" in last_status(api),
          f"Delete with no row picked did not say so: {last_status(api)!r}")
    store.shutdown()


# --- three: the header and the two panes really are put where they are said ---

def test_the_layout(store: Any) -> None:
    """Qt builds the header line and the catalog with its log in one splitter."""
    print("\n3b. the status line, the table and the log are laid out as promised")
    global _QT_APP
    try:
        import addon_api
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (QApplication, QLabel, QPlainTextEdit,
                                     QSplitter, QTreeWidget)

        _QT_APP = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the layout itself is not built: {error}")
        return
    _body, registry = addon_api.build_spec(store.tab_spec())
    table = registry.get("list")
    log = registry.get("log")
    check(isinstance(table, QTreeWidget) and isinstance(log, QPlainTextEdit),
          f"the tab built no table and log: {table!r} / {log!r}")
    if not isinstance(table, QTreeWidget) or not isinstance(log, QPlainTextEdit):
        return
    bar = table.parentWidget()
    check(isinstance(bar, QSplitter),
          f"the table does not sit in a splitter of its own: {bar!r}")
    check(bar is not None and bar is log.parentWidget() and bar.count() == 2
          and bar.orientation() == Qt.Orientation.Horizontal,
          "the table and the log are not the two sides of one horizontal splitter")
    # The status has no row of its own any more: it shares the header line with
    # the title and the repository chip, so all the height goes to the table.
    status = registry.get("status")
    source = registry.get("source")
    header = status.parentWidget() if status is not None else None
    check(isinstance(status, QLabel) and header is not None
          and header is source.parentWidget() and header is not bar
          and not isinstance(header, QSplitter),
          f"the status line is not in the header with the chip: {header!r}")
    check(header is not None and len(header.findChildren(QLabel)) == 3,
          f"the header is not just title, status and chip: "
          f"{header.findChildren(QLabel) if header is not None else None}")
    # It has to claim the width the other two leave over, or a spacer would take
    # the slack and a long report would be squeezed into a narrow column.
    check(status is not None and status.sizePolicy().horizontalPolicy()
          == status.sizePolicy().Policy.Expanding,
          "the status line does not stretch across the room the header leaves it")
    labels = [table.headerItem().text(column) for column in range(table.columnCount())]
    check(labels == ["ID", "Version", "Name", "Author", "Installed"],
          f"the columns are not in the promised order: {labels}")
    widths = [table.columnWidth(column) for column in range(5)]
    check(widths[0] >= 120 and widths[1] >= 60 and widths[4] >= 100,
          f"the fixed columns were not given room to fit their text: {widths}")


# --- three: what counts as installed, against a temporary addons folder ---

def test_installed(store: Any) -> None:
    print("\n5. installed is read off the addons folder")
    real = store.addons_dir
    with tempfile.TemporaryDirectory() as where:
        folder = Path(where)
        store.addons_dir = lambda: folder
        try:
            check(not store.installed("hello_store"), "a fresh folder said it was installed")
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")
            check(store.installed("hello_store"), "a loose .exe was not noticed")
            (folder / "hello_store.exe").unlink()
            (folder / "hello_store").mkdir()
            check(store.installed("hello_store"), "an addon folder was not noticed")
            check(not store.installed(""), "an empty id was taken for installed")
        finally:
            store.addons_dir = real


# --- three b: the store works out that the catalog is ahead of what is here ---

def test_the_updates(store: Any) -> None:
    """Versions, the version kept beside an install, and what Update does.

    An update is only ever worth offering when the catalog names a version ahead
    of the one written down next to the installed program, so this reads the two
    numbers, marks the behind addon in the table, and drives Update through every
    thing it refuses before it touches the disk and the one job it starts once
    you say yes - with nothing really compiled.
    """
    print("\n5b. how the store spots a newer version, and what Update does about it")

    # the numbers, before anything reaches the disk
    check(store._version_parts("1.2.3") == (1, 2, 3),
          f"the digits did not come out of a dotted version: "
          f"{store._version_parts('1.2.3')}")
    check(store.version_newer("1.1", "1.0") is True,
          "a plainly newer catalog version was not taken for ahead")
    check(store.version_newer("1.0", "1.0") is False,
          "the same version on both sides read as an update")
    check(store.version_newer("1.10", "1.9") is True,
          "1.10 lost to 1.9 as text instead of winning as digits")
    check(store.version_newer("1.0", "") is True,
          "an install with no recorded version was not taken for behind")
    check(store.version_newer("", "1.0") is False,
          "a catalog that names no version promised an update anyway")
    check(store.version_newer("beta", "alpha") is True
          and store.version_newer("beta", "beta") is False,
          "a version with no digits was not compared as plain text")

    api = fresh(store)
    real_dir = store.addons_dir
    real_entries, real_updates = store.entries, store.updates
    real_begin, real_python = store._begin, store._ensure_python
    real_dev, real_compile = store._dev, store._compile
    try:
        with tempfile.TemporaryDirectory() as where:
            folder = Path(where)
            store.addons_dir = lambda: folder
            store.entries = store.parse_catalog(CATALOG)
            store.updates = {}

            print("\n5c. the version beside an install, and the list it makes")
            check(store.installed_version("hello_store") == "",
                  "an addon that is not there reported a version")
            check(not store.check_updates(),
                  "nothing installed still came back with an update waiting")
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")
            (folder / "hello_store.json").write_text('{"version": "1.0"}',
                                                     encoding="utf-8")
            check(store.installed_version("hello_store") == "1.0",
                  f"the version beside an install was not read: "
                  f"{store.installed_version('hello_store')!r}")
            check(not store.check_updates(),
                  "an install already at the catalog's version was said to be behind")
            (folder / "hello_store.json").write_text('{"version": "0.9"}',
                                                     encoding="utf-8")
            found = store.check_updates()
            check(list(found) == ["hello_store"],
                  f"a stale install was not the only one noticed: {list(found)}")
            check(found.get("hello_store")
                  == {"installed": "0.9", "available": "1.0"},
                  f"the update did not say from and to: {found.get('hello_store')}")

            # the table marks the behind addon, and only it, as an update waiting
            store.updates = found
            api.calls.clear()
            store.show_entries()
            rows = last_row_call(api) or []
            byid = {row["cells"][0]: row for row in rows}
            check(byid["hello_store"]["cells"][4] == "update available",
                  f"the behind addon was not marked for update: {byid['hello_store']}")
            check(byid["hello_store"].get("color") == "#ffb020",
                  "the update row did not wear the amber mark")
            check(byid["odd_id"]["cells"][4] == "not installed",
                  f"a thing not installed was described as an update: "
                  f"{byid['odd_id']}")

            print("\n5d. Update refuses, asks, and only then starts the job")
            api.answer("tabs.widget_value", None)          # nothing picked
            api.calls.clear()
            store.update_selected()
            check("pick an installed addon" in last_status(api),
                  f"Update with nothing picked did not say so: {last_status(api)!r}")

            api.answer("tabs.widget_value", ["odd_id"])    # picked, not installed
            api.calls.clear()
            store.update_selected()
            check("not installed yet" in last_status(api),
                  f"Update on an addon not installed did not send you to Install: "
                  f"{last_status(api)!r}")

            api.answer("tabs.widget_value", ["hello_store"])   # installed, current
            (folder / "hello_store.json").write_text('{"version": "1.0"}',
                                                     encoding="utf-8")
            api.calls.clear()
            store.update_selected()
            check("already up to date" in last_status(api),
                  f"Update on a current addon was not turned away: "
                  f"{last_status(api)!r}")

            (folder / "hello_store.json").write_text('{"version": "0.9"}',
                                                     encoding="utf-8")
            api.answer("tabs.widget_value", ["hello_store"])
            api.answer("ui.question", False)                # you say no
            api.calls.clear()
            store.update_selected()
            check(api.called("ui.question"),
                  "the update never asked before it touched anything")
            check("declined" in last_status(api),
                  f"a refused update still went ahead: {last_status(api)!r}")

            # say yes, and the job that starts is an update remembering its version
            api.answer("ui.question", True)
            began: list = []

            def fake_begin(verb):
                began.append(verb)
                store.job = {"verb": verb}
                return store.job
            store._begin = fake_begin
            store._ensure_python = lambda: "python"

            class _Ready:
                @staticmethod
                def pyinstaller_ready():
                    return True, ""
            store._dev = lambda: _Ready()
            compiled: list = []
            store._compile = lambda ident: compiled.append(ident)
            api.calls.clear()
            store.update_selected()
            check(began == ["update"],
                  f"a going update did not begin as an update job: {began}")
            check(compiled == ["hello_store"],
                  f"the accepted update did not reach the compile: {compiled}")
            check(store.job.get("id") == "hello_store"
                  and store.job.get("from_version") == "0.9",
                  f"the job did not remember what it was updating from: {store.job}")
    finally:
        store.addons_dir = real_dir
        store.entries, store.updates = real_entries, real_updates
        store._begin, store._ensure_python = real_begin, real_python
        store._dev, store._compile = real_dev, real_compile
        store.job = None
        store.working = False
        store.shutdown()


# --- four: a compiled addon finds its own bundled assets ---

def test_bundled_assets(store: Any) -> None:
    """api.paths.assets prefers the folder packed inside the program."""
    print("\n6. an embedded assets folder wins over the app's")
    api = addon_client.StubAPI(addon_id="addon_store")
    saved_env = os.environ.pop("RASCONF_ASSETS", None)
    saved_mei = sys.__dict__.pop("_MEIPASS", None)
    try:
        with tempfile.TemporaryDirectory() as where:
            bundle = Path(where) / "_MEI"
            (bundle / "assets").mkdir(parents=True)
            (bundle / "assets" / "x.png").write_text("x", encoding="utf-8")
            check(api.paths.assets != bundle / "assets",
                  "an addon with no bundle saw a bundle it does not have")
            sys._MEIPASS = str(bundle)
            check(api.paths.assets == bundle / "assets",
                  f"the bundled assets folder was not preferred: {api.paths.assets}")
    finally:
        if saved_mei is not None:
            sys._MEIPASS = saved_mei
        if saved_env is not None:
            os.environ["RASCONF_ASSETS"] = saved_env


# --- five: the catalog generator ---

def load_generator() -> Any:
    """Import tools/gen_addon_store_index.py from a folder whose name has a space."""
    path = ROOT / "tools" / "gen_addon_store_index.py"
    spec = importlib.util.spec_from_file_location("gen_addon_store_index", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator(store: Any) -> None:
    """index.json is rebuilt from the store folder: manifest, assets, test, marks."""
    print("\n7. the catalog generator reads the store folder")
    gen = load_generator()
    with tempfile.TemporaryDirectory() as where:
        root = Path(where)
        (root / "widget.py").write_text(
            'ADDON = {"name": "Widget", "version": "1.1", "author": "me",\n'
            '           "description": "does things"}\n', encoding="utf-8")
        (root / "bare.py").write_text(
            'ADDON = {"name": "Bare", "version": "1.0", "author": "me",\n'
            '           "description": "ships no test"}\n', encoding="utf-8")
        assets = root / "assets" / "widget"
        assets.mkdir(parents=True)
        (assets / "widget.ico").write_bytes(b"II")
        (assets / "pic.png").write_bytes(b"P")
        tests = root / "_tests"
        tests.mkdir(parents=True)
        (tests / "test_widget.py").write_text("x", encoding="utf-8")
        first = gen.build_index(root, None)
        by_id = {entry["id"]: entry for entry in first["addons"]}
        check({"widget", "bare"} <= set(by_id),
              f"the generator missed an addon: {sorted(by_id)}")
        widget = by_id["widget"]
        check(sorted(widget.get("assets", [])) == ["pic.png", "widget.ico"],
              f"the assets were not listed: {widget.get('assets')}")
        check(widget.get("icon") == "widget.ico",
              f"the icon was not picked: {widget.get('icon')}")
        check(widget.get("test") == "test_widget.py",
              f"the shipped test was not listed: {widget.get('test')}")
        check(not by_id["bare"].get("test"),
              "an addon with no test gained one anyway")
        check(widget.get("status") == "new",
              "a first-time addon was not marked new")
        second = {entry["id"]: entry for entry in gen.build_index(root, first)["addons"]}
        check(second["widget"].get("status", "") == "",
              "an unchanged addon was marked a second time")
        bumped = gen.build_index(root, {"addons": [{"id": "widget", "version": "1.0"}]})
        moved = [e for e in bumped["addons"] if e["id"] == "widget"][0]
        check(moved.get("status") == "updated",
              "a changed version was not marked updated")
        check(gen.render(gen.build_index(root, first))
              == gen.render(gen.build_index(root, first)),
              "the same store rendered differently twice")

    real = gen.STORE_DIR
    if (real / "hello_store.py").is_file():
        by_id = {entry["id"]: entry for entry in gen.build_index(real, None)["addons"]}
        check({"hello_store", "cert_manager", "openwrt_logs"} <= set(by_id),
              f"the real store folder did not list every addon: {sorted(by_id)}")
        check(all(by_id[i].get("test") for i in
                  ("hello_store", "cert_manager", "openwrt_logs") if i in by_id),
              f"a store addon brought no test into the catalog: {by_id}")


# --- six: the compile gate: an addon's test runs before its build ---

class _FakeDev:
    """Just enough of manager_addondev for the gate, with no real interpreter."""

    BASE_DIR = ROOT

    @staticmethod
    def build_interpreter() -> tuple[str, str]:
        return str(sys.executable), ""

    @staticmethod
    def harness_dir() -> Path:
        """A checkout: what an addon imports lies in the app's own folder."""
        return ROOT


class _FrozenDev(_FakeDev):
    """A program that kept those modules inside its archive and none on disk."""

    @staticmethod
    def harness_dir():
        return None


def test_the_gate(store: Any) -> None:
    """The store runs an addon's test before compiling, and only compiles a pass."""
    print("\n8. an addon's test is run before it is compiled")
    fresh(store)                        # stands up the stub api the gate talks to
    real_build, real_finish = store._build, store._finish
    real_stream = store._stream
    real_dev = store._dev
    real_job = store.job
    built: list = []
    ended: list = []
    streamed: dict = {}
    try:
        store._dev = lambda: _FakeDev()
        store.job = {"values": {}, "harness": ROOT}
        store._build = lambda id_value, source: built.append((id_value, str(source)))
        store._finish = lambda text, bad=False: ended.append((str(text), bool(bad)))

        with tempfile.TemporaryDirectory() as box:
            src = Path(box) / "widget.py"
            src.write_text("x", encoding="utf-8")
            test = Path(box) / "test_widget.py"
            test.write_text("x", encoding="utf-8")

            # no test published: straight to the compile, nothing streamed
            built.clear()
            store._maybe_test("widget", src, None)
            check(built == [("widget", str(src))],
                  f"with no test the addon did not compile straight away: {built}")

            # a test exists: it is run first, handed the source and the modules an
            # addon imports to reach the app; when it passes the build goes ahead
            def passing(command, cwd, on_ok, env=None, fail="", on_bad=None):
                streamed["command"] = command
                streamed["cwd"] = cwd
                streamed["env"] = env or {}
                streamed["fail"] = fail
                on_ok()                     # pretend the test came back clean
            store._stream = passing
            built.clear()
            streamed.clear()
            store._maybe_test("widget", src, test)
            check(streamed.get("command", [None, None])[1] == str(test),
                  f"the thing run was not the test: {streamed.get('command')}")
            check(streamed["env"].get("RASCONF_ADDON_SOURCE") == str(src),
                  "the downloaded addon was not handed to the test")
            check(str(ROOT) in streamed["env"].get("PYTHONPATH", ""),
                  f"the modules an addon imports were not on the test's path: "
                  f"{streamed['env'].get('PYTHONPATH')!r}")
            check(str(streamed.get("cwd")) == str(ROOT),
                  f"the test did not run beside the modules it imports: "
                  f"{streamed.get('cwd')!r}")
            check("test" in streamed["fail"].lower(),
                  f"a failed test would not have said so: {streamed['fail']!r}")
            check(built == [("widget", str(src))],
                  f"a passing test did not go on to compile: {built}")

            # a test that does not pass never reaches the compile
            def failing(command, cwd, on_ok, env=None, fail="", on_bad=None):
                pass                        # the real one would _finish; on_ok unused
            store._stream = failing
            built.clear()
            store._maybe_test("widget", src, test)
            check(not built, "a test that did not pass still compiled the addon")

            # with nothing to run it against, the install is not held up for it
            store.job = {"values": {}, "harness": None}
            built.clear()
            streamed.clear()
            store._maybe_test("widget", src, test)
            check(built == [("widget", str(src))],
                  f"a test that cannot be run here stopped the install: {built}")
            check(not streamed, f"something was run with no harness: {streamed}")

            # a run that stops is sorted by what it stopped on: this machine's
            # missing modules prove nothing about the addon, and must not refuse
            # an install; everything else, including a real FAIL, does
            def stopping(command, cwd, on_ok, env=None, fail="", on_bad=None):
                if callable(on_bad):
                    on_bad()
            store._stream = stopping
            for label, tail, may_build in (
                    ("a missing Qt", ["Traceback (most recent call last):",
                                      "ModuleNotFoundError: No module named 'PyQt6'"],
                     True),
                    ("a missing app module",
                     ["ModuleNotFoundError: No module named 'addon_client'"], True),
                    # a fetched harness is the contract's Qt-free half, so a test
                    # that wants the app's widgets never gets to the addon either
                    ("the app's Qt half",
                     ["ModuleNotFoundError: No module named 'addon_api'"], True),
                    ("a missing module of its own",
                     ["ModuleNotFoundError: No module named 'widget_thing'"], False),
                    ("a failing check", ["FAIL: the tab did not appear"], False),
                    ("a crash", ["ZeroDivisionError: division by zero"], False)):
                built.clear()
                ended.clear()
                store.job = {"values": {}, "harness": ROOT, "lines": list(tail)}
                store._maybe_test("widget", src, test)
                check(bool(built) == may_build,
                      f"a test that stopped for {label} should have "
                      f"{'compiled anyway' if may_build else 'been refused'}: "
                      f"built={built} ended={ended}")
                check(may_build or (ended and ended[-1][1]),
                      f"a test that stopped for {label} was not reported as a stop: "
                      f"{ended}")

            # the reading of a traceback's tail, on its own
            for lines, want in ((["nothing here"], ""),
                                (["ModuleNotFoundError: No module named 'paramiko'"],
                                 "paramiko"),
                                (["ModuleNotFoundError: No module named 'PyQt6.QtWidgets'"],
                                 "PyQt6"),
                                (["ModuleNotFoundError: No module named 'widget'"], "")):
                store.job = {"values": {}, "lines": list(lines)}
                check(store._machine_missing() == want,
                      f"{lines} was read as {store._machine_missing()!r}, not {want!r}")
            # what the harness folder holds counts too, whatever it is called
            other = Path(box) / "harnesscopy"
            other.mkdir()
            (other / "myappthing.py").write_text("x", encoding="utf-8")
            for harness, name, want in ((other, "myappthing", "myappthing"),
                                        (other, "requests", ""),
                                        (Path(box) / "nowhere", "myappthing", "")):
                store.job = {"values": {}, "harness": harness,
                             "lines": [f"ModuleNotFoundError: No module named '{name}'"]}
                check(store._machine_missing() == want,
                      f"{name} was read as {store._machine_missing()!r} with "
                      f"{harness.name} as the harness, not {want!r}")
            store.job = None
            check(store._machine_missing() == "",
                  "a missing module was named with no job in hand")
    finally:
        store._build = real_build
        store._finish = real_finish
        store._stream = real_stream
        store._dev = real_dev
        store.job = real_job


def test_the_harness(store: Any) -> None:
    """Where the two modules an addon imports come from, and what is built with them.

    A test and a compiled launcher both ``import addon_client``, and the Python
    doing that work only reads what lies on disk: a checkout has the two in its
    own folder, a build made by the current workflow carries a copy, and a program
    built before that has to fetch them from the repository the addons come from -
    from above the store folder, where the app's own files sit. Those two are all
    a build needs and less than a test wants, so a run that stops on the app's
    other modules asks for them and tries the test once more.
    """
    print("\n8b. the modules an addon imports are found, or fetched")
    fresh(store)
    real_dev, real_fetch = store._dev, store._fetch_file
    real_job, real_finish = store.job, store._finish
    real_build, real_stream = store._build, store._stream
    saved_data = os.environ.get("RASCONF_ADDON_DATA")
    fetched: list = []
    try:
        with tempfile.TemporaryDirectory() as box:
            os.environ["RASCONF_ADDON_DATA"] = str(Path(box) / "data")
            store.job = {"values": dict(store.DEFAULTS)}

            def write(values: dict, remote: str, target: Path,
                      folder: str | None = None) -> str:
                fetched.append((remote, folder))
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text("# fetched\n", encoding="utf-8")
                return ""
            store._fetch_file = write

            check(store.app_folder(dict(store.DEFAULTS)) == "utils/update util",
                  f"the app's folder was not read off the store path: "
                  f"{store.app_folder(dict(store.DEFAULTS))!r}")
            check(store.app_folder({"store_path": "addons"}) == "",
                  "a store folder at the repository root made a folder out of nothing")
            check(store.raw_address(dict(store.DEFAULTS), "addon_client.py",
                                    store.app_folder(dict(store.DEFAULTS)))
                  == "/fligma/raspiOpenWrt/main/utils/update%20util/addon_client.py",
                  f"the app's own folder did not survive the address: "
                  f"{store.raw_address(dict(store.DEFAULTS), 'addon_client.py', 'utils/update util')!r}")

            # a checkout answers with its own folder and downloads nothing
            store._dev = lambda: _FakeDev()
            check(store.harness_folder(dict(store.DEFAULTS)) == ROOT,
                  "a checkout's own folder was not taken for the modules an addon "
                  "imports")
            check(not fetched, f"a checkout still fetched: {fetched}")

            # a program with nothing on disk asks for both, from the app's folder
            store._dev = lambda: _FrozenDev()
            found = store.harness_folder(dict(store.DEFAULTS))
            check([name for name, _folder in fetched] == list(store.HARNESS_FILES),
                  f"both modules were not fetched: {fetched}")
            check({folder for _name, folder in fetched} == {store.app_folder(store.DEFAULTS)},
                  f"the modules were looked for in the store folder, not the app's: "
                  f"{fetched}")
            check(found == store.staging() / "harness",
                  f"the fetched modules were put somewhere odd: {found!r}")

            # and when the fetch stops, so does the answer: nothing is half-there
            fetched.clear()
            store._fetch_file = lambda values, remote, target, folder=None: (
                "GitHub answered 404" if remote == store.HARNESS_FILES[1] else "")
            check(store.harness_folder(dict(store.DEFAULTS)) is None,
                  "a harness that could not be fetched was still handed to a build")

            # a fetched pair is what a build needs, not what a test wants: a run
            # that stops on one of the app's other modules gets the rest fetched
            # and is run again over the wider folder
            narrow = store.staging() / "harness"
            narrow.mkdir(parents=True, exist_ok=True)
            src = Path(box) / "widget.py"
            src.write_text("x", encoding="utf-8")
            test = Path(box) / "test_widget.py"
            test.write_text("x", encoding="utf-8")
            runs: list = []
            built: list = []
            stopped: list = []

            def twice(command, cwd, on_ok, env=None, fail="", on_bad=None):
                runs.append(str((env or {}).get("PYTHONPATH", "")))
                if len(runs) == 1:
                    store.job["lines"] = ["ModuleNotFoundError: "
                                          "No module named 'addon_api'"]
                    if callable(on_bad):
                        on_bad()
                else:
                    on_ok()
            store._fetch_file = write
            store._stream = twice
            store._build = lambda id_value, source: built.append(str(source))
            store._finish = lambda text, bad=False: stopped.append((str(text), bad))
            fetched.clear()
            store.job = {"values": dict(store.DEFAULTS), "harness": narrow,
                         "lines": [], "widened": False}
            store._maybe_test("widget", src, test)
            check(len(runs) == 2, f"the test was not run a second time: {runs}")
            check(all(run == str(narrow) for run in runs),
                  f"a retry did not use the fetched harness: {runs}")
            check("addon_api.py" in [name for name, _folder in fetched],
                  f"the app's other modules were not asked for: "
                  f"{[name for name, _f in fetched]}")
            check((narrow / "addon_api.py").is_file(),
                  "the widened harness holds no addon_api")
            check(built == [str(src)] and not stopped,
                  f"a test that passed after the widening did not compile: "
                  f"{built} {stopped}")

            # a repository with nothing more to give is asked once: the install
            # then goes ahead on what the run could not disprove
            runs.clear()
            built.clear()
            stopped.clear()
            store._fetch_file = lambda values, remote, target, folder=None: "404"

            def again_bad(command, cwd, on_ok, env=None, fail="", on_bad=None):
                runs.append(1)
                store.job["lines"] = ["ModuleNotFoundError: "
                                      "No module named 'addon_api'"]
                if callable(on_bad):
                    on_bad()
            store._stream = again_bad
            store.job = {"values": dict(store.DEFAULTS), "harness": narrow,
                         "lines": [], "widened": False}
            store._maybe_test("widget", src, test)
            check(len(runs) == 1,
                  f"a widening that fetched nothing ran the test again: {runs}")
            check(built == [str(src)] and not stopped,
                  f"a test that cannot be run stopped the install: {built} {stopped}")

            # a build is only worth starting when those modules can be read: with
            # no harness in the job it is refused before PyInstaller is asked
            def no_build(command, cwd, on_ok, env=None, fail="", on_bad=None):
                raise AssertionError("a build was started with nothing to import")
            store._stream = no_build
            store._build = real_build            # the store's own refusal, not a fake
            store._fetch_file = real_fetch
            store._dev = lambda: _FakeDev()
            ended: list = []
            store._finish = lambda text, bad=False: ended.append((str(text), bool(bad)))
            store.job = {"values": {}, "harness": None}
            store._build("widget", Path(box) / "widget.py")
            check(ended and ended[-1][1] and "talk to the app" in ended[-1][0],
                  f"a build with no modules to import was started anyway: {ended}")
    finally:
        store._dev = real_dev
        store._fetch_file = real_fetch
        store._finish = real_finish
        store._build = real_build
        store._stream = real_stream
        store.job = real_job
        if saved_data is not None:
            os.environ["RASCONF_ADDON_DATA"] = saved_data
        else:
            os.environ.pop("RASCONF_ADDON_DATA", None)

    try:
        import manager_addondev as dev
    except Exception as error:                      # noqa: BLE001 - app modules
        look(f"manager_addondev is not importable here: {error}")
        return
    ready = dev.harness_dir()
    check(ready is not None and all((ready / name).is_file()
                                    for name in store.HARNESS_FILES),
          f"this checkout's own modules were not found on disk: {ready!r}")
    ok, why = dev.app_modules_ready()
    check(ok and not why, f"a checkout was told it cannot build an addon: {why!r}")
    saved_harness = dev.harness_dir
    try:
        dev.harness_dir = lambda: None
        with tempfile.TemporaryDirectory() as box:
            other = Path(box) / "fetched"
            command = dev.build_command(Path(box) / "launcher.py", "widget",
                                        {"onefile": True, "console": True,
                                         "icon": False, "hidden": ""},
                                        root=Path(box), paths=[other])
            paths = [command[index + 1] for index, part in enumerate(command)
                     if part == "--paths"]
            check(paths and not (set(paths) & {str(Path(box))}),
                  f"the modules an addon imports are not on the build's path: {paths}")
            check(str(other) in paths and str(dev.BASE_DIR) in paths,
                  f"neither the fetched folder nor the app's own is on the path: {paths}")
            twice = dev.build_command(Path(box) / "launcher.py", "widget",
                                      {"onefile": True, "console": True,
                                       "icon": False, "hidden": ""},
                                      root=Path(box),
                                      paths=[other, other, dev.BASE_DIR])
            counts = [twice[index + 1] for index, part in enumerate(twice)
                      if part == "--paths"]
            check(len(counts) == len(set(counts)),
                  f"a folder named twice went on the path twice: {counts}")
    finally:
        dev.harness_dir = saved_harness


# --- seven: the interpreter it builds with, and the pip that fills it ---

def test_the_interpreter(store: Any) -> None:
    """A python3 that is the Windows Store stand-in is passed over for a real one.

    The store asks :mod:`manager_addondev` which Python to build with, and on
    Windows the first name on the path is often a zero-length alias that answers
    "Python was not found" and stops with code 9009 - which reads as a broken
    build unless the search refuses it. Needs the app's own modules, so it is
    quiet when they are not there.
    """
    print("\n9. the interpreter search takes a Python that actually starts")
    try:
        import manager_addondev as dev
    except Exception as error:                      # noqa: BLE001 - app modules
        look(f"manager_addondev is not importable here: {error}")
        return
    check("python3" in dev.PYTHON_NAMES and "python" in dev.PYTHON_NAMES,
          f"both usual names are not tried: {dev.PYTHON_NAMES}")
    check(dev._not_a_python(str(sys.executable)) == "",
          f"this interpreter was not taken for a Python: "
          f"{dev._not_a_python(str(sys.executable))!r}")
    saved = (dev.shutil.which, dev.IS_COMPILED, dev._INTERPRETER)
    try:
        with tempfile.TemporaryDirectory() as where:
            stub = Path(where) / ("python3.exe" if os.name == "nt" else "python3")
            stub.write_bytes(b"")            # exactly what the alias is: nothing
            reason = dev._not_a_python(str(stub))
            check("alias" in reason.lower() or "empty" in reason.lower(),
                  f"a zero-length stand-in was not refused: {reason!r}")

            dev.shutil.which = lambda name: str(stub) if name == "python3" else None
            dev.IS_COMPILED, dev._INTERPRETER = True, None
            gone, why = dev.build_interpreter()
            check(gone is None and "python" in why.lower(),
                  f"a path with only a stand-in produced {gone!r}: {why!r}")

            dev._INTERPRETER = None
            dev.shutil.which = lambda name: (
                str(stub) if name == "python3"
                else str(sys.executable) if name == "python" else None)
            picked, why = dev.build_interpreter()
            check(picked == str(sys.executable),
                  f"the stand-in was not passed over for the python behind it: "
                  f"{picked!r} / {why!r}")

            # the answer is kept, and forgetting asks the path again
            dev.shutil.which = lambda name: None
            check(dev.build_interpreter()[0] == str(sys.executable),
                  "the second ask did not use the kept answer")
            dev.forget_interpreter()
            check(dev.build_interpreter()[0] is None,
                  "forgetting the answer did not put the path back to asking")
    finally:
        dev.shutil.which, dev.IS_COMPILED, dev._INTERPRETER = saved


def test_the_pip_fallback(store: Any) -> None:
    """A refused pip install is tried again for this user, then reported."""
    print("\n10. PyInstaller is installed for this user when the plain way fails")
    real_stream, real_finish, real_job = store._stream, store._finish, store.job
    asked: list[dict] = []
    try:
        store.job = {"values": {}}
        store._finish = lambda text, bad=False: asked.append({"ended": text})

        def refused(command, cwd, on_ok, env=None, fail="", on_bad=None):
            asked.append({"command": command, "fail": fail, "on_bad": on_bad})
            if callable(on_bad):
                on_bad()                     # pretend pip said no
        store._stream = refused
        store._pip_install("python")
        check(len(asked) == 3, f"the two tries and the ending are not there: {asked}")
        first, second = asked[0], asked[1]
        check("--user" not in first["command"],
              f"the first try was not the plain one: {first['command']}")
        check("--user" in second["command"],
              f"the second try did not go for this user: {second['command']}")
        check(first["command"][:2] == ["python", "-m"],
              f"pip was not run through the interpreter that builds: {first['command']}")
        check("ended" in asked[-1] and "pip" in asked[-1]["ended"].lower(),
              f"giving up said nothing about pip: {asked[-1]}")

        # a pip that goes through simply carries on to the compile
        asked.clear()
        def clean(command, cwd, on_ok, env=None, fail="", on_bad=None):
            asked.append({"command": command})
            on_ok()
        class Ready:
            """A dev module whose interpreter now has PyInstaller in it."""

            @staticmethod
            def pyinstaller_ready() -> tuple[bool, str]:
                return True, ""

            @staticmethod
            def forget_interpreter() -> None:
                pass
        real_dev = store._dev
        real_compile = store._compile
        store._stream = clean
        store._dev = lambda: Ready
        store._compile = lambda id_value: asked.append({"compiled": id_value})
        try:
            store.job["id"] = "widget"
            store._pip_install("python")
            check(any("compiled" in item for item in asked),
                  f"a clean install did not carry on to the compile: {asked}")
        finally:
            store._compile = real_compile
            store._dev = real_dev
    finally:
        store._stream = real_stream
        store._finish = real_finish
        store.job = real_job


# --- eight: where an install leaves its work, and what it must not touch after ---

class _BuildDev:
    """The dev machinery as the store sees it, with nothing really compiled."""

    BASE_DIR = ROOT

    class _Launcher:
        @staticmethod
        def substitute(**values: Any) -> str:
            return " ".join(f"{key}={value}" for key, value in values.items())

    _LAUNCHER = _Launcher()

    def __init__(self, dist: Path, installs: Path | None = None) -> None:
        self.dist = dist                # where the finished program is said to be
        self.installs = Path(installs) if installs is not None \
            else dist / "installed"    # where it is said to be put
        self.seen: dict = {}

    @staticmethod
    def build_interpreter() -> tuple[str, str]:
        return str(sys.executable), ""

    @staticmethod
    def harness_dir() -> Path:
        return ROOT

    @staticmethod
    def module_name(source: Path) -> str:
        return source.stem

    @staticmethod
    def _write_text(target: Path, text: str) -> None:
        Path(target).write_text(text, encoding="utf-8")

    def build_command(self, launcher: Path, addon_id: str, options: dict,
                      source: Path | None = None,
                      root: Path | None = None,
                      paths: list | tuple = ()) -> list[str]:
        self.seen["root"] = root
        self.seen["launcher"] = Path(launcher)
        self.seen["paths"] = [str(Path(folder)) for folder in paths]
        return ["py", "-m", "PyInstaller", str(launcher)]

    def staged_name(self, addon_id: str, onefile: bool,
                    root: Path | None = None) -> Path:
        self.seen["staged root"] = root
        return self.dist / f"{addon_id}.exe"

    def install_build(self, staged: Path, addon_id: str, onefile: bool) -> Path:
        """Pretend the program was copied, and say where it now sits.

        The real one writes into ``addons``, and the store speaks about that copy
        afterwards - so the stand-in puts its pretend file in the folder the test
        is using, never somewhere relative to whoever runs it.
        """
        self.seen["copied"] = Path(staged)
        self.installs.mkdir(parents=True, exist_ok=True)
        target = self.installs / f"{addon_id}.exe"
        target.write_text("x", encoding="utf-8")
        return target


class _FakeApp:
    """The two app calls an install ends with, recorded instead of carried out.

    ``session`` is the one namespace both kinds of addon reach the app through,
    so the store's last two moves are watched here rather than on a manager it
    no longer touches directly.
    """

    def __init__(self, on_rescan: Any = None) -> None:
        self.on_rescan = on_rescan
        self.written: list = []
        self.rescans = 0

    def set_addon_enabled(self, addon_id: str, enabled: bool) -> dict:
        self.written.append((str(addon_id), bool(enabled)))
        return {"enabled": bool(enabled)}

    def rescan_addons(self) -> bool:
        self.rescans += 1
        if callable(self.on_rescan):
            self.on_rescan()
        return True


def test_the_install(store: Any) -> None:
    """The build stays in the store's own folder, and the rescan comes last.

    A rescan shuts the store down and starts it again from disk, which takes the
    api out from under the code that asked for it - the crash this guards against
    - so the install has to finish everything that speaks to the tab first.
    """
    print("\n11. an install stages its work at home, clears it, and reloads last")
    api = fresh(store)
    saved_env = os.environ.pop("RASCONF_ADDON_DATA", None)
    real_dev, real_stream = store._dev, store._stream
    real_job, real_addons = store.job, store.addons_dir
    try:
        with tempfile.TemporaryDirectory() as where:
            data = Path(where)
            os.environ["RASCONF_ADDON_DATA"] = str(data)
            build = data / "build"
            stage = data / "staging"
            addons = data / "addons"
            store.addons_dir = lambda: addons

            # the build is asked for inside the store's own data folder
            streamed: dict = {}

            def watch(command, cwd, on_ok, env=None, fail="", on_bad=None):
                streamed["command"] = command
                streamed["cwd"] = cwd
            store._stream = watch
            dist = build / "widget" / "dist"
            dev = _BuildDev(dist, addons)
            store._dev = lambda: dev
            store.job = {"values": {}, "harness": ROOT}
            source = data / "widget.py"
            source.write_text("x", encoding="utf-8")
            store._build("widget", source)
            check(dev.seen.get("root") == build,
                  f"the build was not staged in the store's own folder: "
                  f"{dev.seen.get('root')!r} != {build}")
            check(dev.seen.get("launcher") == build / "widget" / "launcher.py",
                  f"the launcher was written somewhere else: {dev.seen.get('launcher')!r}")
            check(dev.seen.get("paths") == [str(ROOT)],
                  f"the build was not told where the modules a launcher imports are: "
                  f"{dev.seen.get('paths')!r}")
            check(Path(streamed.get("cwd", "/")) == build / "widget",
                  f"PyInstaller ran from elsewhere: {streamed.get('cwd')!r}")

            # the finished program: copied, cleared, marked off, and only then
            # the rescan, which takes the api away from the running code
            dist.mkdir(parents=True)
            (stage / "assets").mkdir(parents=True)
            (stage / "assets" / "pic.png").write_text("x", encoding="utf-8")
            (dist / "widget.exe").write_text("x", encoding="utf-8")
            order: list[str] = []
            seen_calls: dict = {}

            def reload():
                seen_calls["calls"] = len(api.calls)
                order.append("reload")
                store.shutdown()            # exactly what the app does to us here

            manager = _FakeApp(reload)
            api.session = manager
            store._dev = lambda: dev
            store.job = {"values": {}, "id": "widget", "harness": ROOT,
                         "details": {"manifest": {"name": "Widget",
                                                  "requires": ["ssh", "serial"]},
                                     "fields": [{"key": "size", "label": "Size"},
                                                {"label": "keyless"}, "junk"],
                                     "defaults": {"size": 10}}}
            api.calls.clear()
            broken: list[str] = []
            try:
                store._install_step("widget")
            except Exception as error:                  # noqa: BLE001 - the crash itself
                broken.append(f"{type(error).__name__}: {error}")
            check(not broken, f"the install could not finish itself: {broken}")
            check(dev.seen.get("staged root") == build
                  and dev.seen.get("copied") == dist / "widget.exe",
                  f"the program was looked for outside the store's build: {dev.seen}")
            check(order == ["reload"] and manager.rescans == 1,
                  f"the app was not asked to rescan, or was twice: {order} / "
                  f"{manager.rescans}")
            paints = [index for index, entry in enumerate(api.calls)
                      if entry[0] == "tabs.set_widget" and len(entry[1]) >= 4
                      and entry[1][1] == "list" and entry[1][2] == "rows"]
            check(paints and paints[0] < seen_calls.get("calls", 0),
                  f"the table was not repainted before the rescan: {paints} of "
                  f"{seen_calls.get('calls')}")
            check(any(entry[0] == "tabs.set_widget" and len(entry[1]) >= 4
                      and entry[1][1] == "status" and "off" in str(entry[1][3])
                      for entry in api.calls),
                  "the status line never said the addon is switched off")
            check(manager.written == [("widget", False)],
                  f"the profile was not told to keep it off: {manager.written}")
            check(not (build / "widget").exists(),
                  "the build folder was left behind next to the installed program")
            check(stage.is_dir() and not any(stage.iterdir()),
                  f"the downloads were left in staging: {list(stage.iterdir())}")

            # and it said what it had installed, in the two places the app looks
            keeps = store.addon_data_folder("widget")
            check(keeps == addons / "_data" / "widget",
                  f"the settings were not prepared in the addon's own folder: "
                  f"{keeps}")
            sidecar = json.loads((addons / "widget.json").read_text(encoding="utf-8"))
            check(sidecar.get(store.ORIGIN_KEY) == store.STORE_ORIGIN,
                  f"the manifest did not say where the addon came from: {sidecar}")
            check(sidecar.get("requires") == ["ssh"],
                  f"the transports were not kept to the ones the app knows: "
                  f"{sidecar.get('requires')}")
            check(sidecar.get("name") == "Widget" and sidecar.get("id") == "widget"
                  and sidecar.get("api") == 1,
                  f"the addon did not describe itself as its source does: {sidecar}")
            schema = json.loads((keeps / "fields.json").read_text(encoding="utf-8"))
            check(schema.get("fields") == [{"key": "size", "label": "Size"}],
                  f"the settings form kept more or less than the addon declared: "
                  f"{schema.get('fields')}")
            check(schema.get("defaults") == {"size": 10},
                  f"the form came without the values to start from: {schema}")
            check(str(schema.get("title", "")).endswith(
                  "addons/_data/widget/config.json"),
                  f"the form's heading does not name the file it writes: {schema}")
            check(json.loads((keeps / "config.json").read_text(encoding="utf-8"))
                  == {"size": 10},
                  "the addon's config file was not started from its defaults")
            gone = sorted(path.name for path in store._delete_paths("widget"))
            check(gone == ["widget.exe", "widget.json"],
                  f"delete does not take an install back whole: {gone}")
            (addons / "hand.exe").write_text("x", encoding="utf-8")
            (addons / "hand.json").write_text('{"name": "Hand"}', encoding="utf-8")
            check([path.name for path in store._delete_paths("hand")] == ["hand.exe"],
                  "a manifest the store never wrote was taken back with it")

        # a compiled store asks the same two things over its own pipe
        check("session.rescan_addons" in addon_client.ProcessAPI._POSITIONAL
              and "session.set_addon_enabled" in addon_client.ProcessAPI._POSITIONAL,
              "the app calls an install ends with are not passable by a process addon")
    finally:
        if saved_env is not None:
            os.environ["RASCONF_ADDON_DATA"] = saved_env
        else:
            os.environ.pop("RASCONF_ADDON_DATA", None)
        store._dev = real_dev
        store._stream = real_stream
        store.job = real_job
        store.addons_dir = real_addons


# --- nine: what an install says about an addon it cannot ask ---

def test_the_manifest(store: Any) -> None:
    """The manifest written beside a fresh program, from the source and the catalog.

    A compiled addon only reports itself once it is running, and the app has to
    know before then what the addon is called and whether it may connect at all,
    so what the source declared is taken first and the catalog fills the gaps.
    """
    print("\n12. an install's own manifest, from the source and the catalog")
    details = {"manifest": {"name": "Widget",
                            "requires": ["ssh", "SFTP", "serial", "ssh"],
                            "description": ""},
               "fields": [{"key": "a"}], "defaults": {"a": 1}}
    entry = {"name": "From the catalog", "version": "2.1", "author": "fligma",
             "description": "The catalog's words"}
    made = store._manifest_values("widget", details, entry)
    check(made["requires"] == ["ssh", "sftp"],
          f"the transports were not cleaned, made new and kept to the known ones: "
          f"{made.get('requires')}")
    check(made["name"] == "Widget" and made["version"] == "2.1"
          and made["author"] == "fligma"
          and made["description"] == "The catalog's words",
          f"the catalog did not fill what the source left blank: {made}")
    check(made["id"] == "widget" and made["api"] == 1
          and made[store.ORIGIN_KEY] == store.STORE_ORIGIN,
          f"an install was not marked as one: {made}")
    quiet = store._manifest_values("odd-id", {}, {})
    check(quiet == {"requires": [], "api": 1, "id": "odd_id",
                    store.ORIGIN_KEY: store.STORE_ORIGIN},
          f"an addon that declares nothing gained inventions: {quiet}")


# --- ten: what a built app hands to a child ---

def test_the_child_env() -> None:
    """The environment the app gives to everything it starts, itself included.

    A PyInstaller onefile tells a process spawned from its own executable that
    the folder it unpacked into is there for the taking, which is right for a
    worker and wrong for a restart: that folder is deleted as the dying copy
    exits, so the fresh one is left running with its code - which comes out of
    the executable - and none of the loose files beside it. The app has to turn
    the handoff down for every child, and say so in the way the bootloader of
    whatever version built it understands.
    """
    print("\n13. a child of a built app unpacks itself instead of reusing another's")
    import addon_common
    import manager_browser

    started: list = []
    real_popen = addon_common.subprocess.Popen
    devnull = addon_common.subprocess.DEVNULL

    class _Started:
        """A child that was only ever written down."""

        def __init__(self, command: Any, **kwargs: Any) -> None:
            self.command, self.kwargs = list(command), kwargs
            self.pid = 0

    bundle = os.path.join(os.environ.get("TEMP", "."), "_MEIfake")
    handed_on = {"PATH": os.pathsep.join([bundle, r"C:\Windows"]),
                 "LD_LIBRARY_PATH": bundle,
                 "_PYI_APPLICATION_HOME_DIR": bundle,
                 "_PYI_ARCHIVE_FILE": r"C:\elsewhere\berry_manager.exe",
                 "_PYI_PARENT_PROCESS_LEVEL": "2",
                 "_PYI_SPLASH_IPC": "0", "MEIPASS2": bundle, "KEEP": "kept"}
    saved_mei = sys.__dict__.pop("_MEIPASS", None)
    saved_frozen = sys.__dict__.pop("frozen", None)
    try:
        sys._MEIPASS = bundle
        cleaned = addon_common.clean_child_env(handed_on)
        check(cleaned.get(addon_common.PYI_RESET) == "1",
              "a child was never told to start as an instance of its own")
        kept = [key for key in cleaned
                if key.startswith("_PYI_") or key == "MEIPASS2"]
        check(not kept, f"the dying copy's own folder was passed on anyway: {kept}")
        check(bundle not in cleaned.get("PATH", "")
              and "LD_LIBRARY_PATH" not in cleaned,
              f"the unpack folder stayed in the search path: {cleaned.get('PATH')!r}")
        check(cleaned.get("KEEP") == "kept",
              "the cleaning took something it had no business touching")

        sys.__dict__.pop("_MEIPASS", None)
        check(addon_common.clean_child_env(handed_on) == handed_on,
              "a program run from source had the environment of its children changed")
        sys._MEIPASS = bundle

        def fake(command: Any, **kwargs: Any) -> _Started:
            started.append(_Started(command, **kwargs))
            return started[-1]

        addon_common.subprocess.Popen = fake
        sys.frozen = True
        proc = addon_common.popen_tree(["anything"], env=dict(handed_on))
        given = proc.kwargs.get("env") or {}
        check(given.get(addon_common.PYI_RESET) == "1",
              f"a child started with popen_tree kept this app's state on {os.name}")
        addon_common.release_process_tree(proc)

        started.clear()
        check(manager_browser.relaunch_process(),
              "the restart gave up on starting a fresh copy")
        check(bool(started) and started[0].command[:1] == [sys.executable],
              f"the restart did not start this program again: {started[:1]}")
        if started:
            env = started[0].kwargs.get("env") or {}
            check(env.get(addon_common.PYI_RESET) == "1",
                  "the fresh copy was left to work out of the dying one's folder")
            check(not [key for key in env if key.startswith("_PYI_")],
                  "the fresh copy was handed the folders the dying one unpacked into")
            check(started[0].kwargs.get("stdout") == devnull,
                  "the fresh copy was started still speaking to the one that is going")
            if os.name == "nt":
                check(started[0].kwargs.get("creationflags", 0)
                      & addon_common.subprocess.DETACHED_PROCESS,
                      "the fresh copy was started as a child that dies with its parent")
            else:
                check(started[0].kwargs.get("start_new_session"),
                      "the fresh copy was started as a child that dies with its parent")
    finally:
        addon_common.subprocess.Popen = real_popen
        sys.__dict__.pop("_MEIPASS", None)
        sys.__dict__.pop("frozen", None)
        if saved_mei is not None:
            sys._MEIPASS = saved_mei
        if saved_frozen is not None:
            sys.frozen = saved_frozen


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
    store = addon_client.load_addon(SOURCE)
    print(f"addon_store: the addon at {SOURCE.name}")

    test_the_helpers(store)
    test_the_tab(store)
    test_the_layout(store)
    test_installed(store)
    test_the_updates(store)
    test_bundled_assets(store)
    test_the_generator(store)
    test_the_gate(store)
    test_the_harness(store)
    test_the_interpreter(store)
    test_the_pip_fallback(store)
    test_the_install(store)
    test_the_manifest(store)
    test_the_child_env()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
