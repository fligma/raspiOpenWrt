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
* the catalog generator that rebuilds ``index.json`` from the store folder, and
  the store's rule that an addon's test is run before it is compiled.

No internet, no compiler and no device are needed. Run it from the Test panel,
or::

    python addons/_tests/test_addon_store.py
"""

from __future__ import annotations

import importlib.util
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
    check((store.TAB,) in [tuple(arguments) for where, arguments, _n in api.calls
                           if where == "tabs.use_app_icon"],
          "the tab never asked to wear the program's own icon")

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


def test_the_gate(store: Any) -> None:
    """The store runs an addon's test before compiling, and only compiles a pass."""
    print("\n8. an addon's test is run before it is compiled")
    fresh(store)                        # stands up the stub api the gate talks to
    real_build = store._build
    real_stream = store._stream
    real_dev = store._dev
    real_job = store.job
    built: list = []
    streamed: dict = {}
    try:
        store._dev = lambda: _FakeDev()
        store.job = {"values": {}}
        store._build = lambda id_value, source: built.append((id_value, str(source)))

        # no test published: straight to the compile, nothing streamed
        built.clear()
        store._maybe_test("widget", Path("widget.py"), None)
        check(built == [("widget", "widget.py")],
              f"with no test the addon did not compile straight away: {built}")

        # a test exists: it is run first, handed the source and the app's modules;
        # when it passes the build goes ahead
        def passing(command, cwd, on_ok, env=None, fail=""):
            streamed["command"] = command
            streamed["env"] = env or {}
            streamed["fail"] = fail
            on_ok()                     # pretend the test came back clean
        store._stream = passing
        built.clear()
        streamed.clear()
        with tempfile.TemporaryDirectory() as where:
            src = Path(where) / "widget.py"
            src.write_text("x", encoding="utf-8")
            test = Path(where) / "test_widget.py"
            test.write_text("x", encoding="utf-8")
            store._maybe_test("widget", src, test)
        check(streamed.get("command", [None, None])[1] == str(test),
              f"the thing run was not the test: {streamed.get('command')}")
        check(streamed["env"].get("RASCONF_ADDON_SOURCE") == str(src),
              "the downloaded addon was not handed to the test")
        check(str(_FakeDev.BASE_DIR) in streamed["env"].get("PYTHONPATH", ""),
              "the app's modules were not on the test's path")
        check("test" in streamed["fail"].lower(),
              f"a failed test would not have said so: {streamed['fail']!r}")
        check(built == [("widget", str(src))],
              f"a passing test did not go on to compile: {built}")

        # a test that does not pass never reaches the compile
        def failing(command, cwd, on_ok, env=None, fail=""):
            pass                        # the real one would _finish; on_ok unused
        store._stream = failing
        built.clear()
        with tempfile.TemporaryDirectory() as where:
            src = Path(where) / "widget.py"
            src.write_text("x", encoding="utf-8")
            test = Path(where) / "test_widget.py"
            test.write_text("x", encoding="utf-8")
            store._maybe_test("widget", src, test)
        check(not built, "a test that did not pass still compiled the addon")
    finally:
        store._build = real_build
        store._stream = real_stream
        store._dev = real_dev
        store.job = real_job


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)    # the Test pane wants it live
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    store = addon_client.load_addon(SOURCE)
    print(f"addon_store: the addon at {SOURCE.name}")

    test_the_helpers(store)
    test_the_tab(store)
    test_the_layout(store)
    test_installed(store)
    test_bundled_assets(store)
    test_the_generator(store)
    test_the_gate(store)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
