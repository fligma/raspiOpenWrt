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
  list, and that Install/Delete say the right thing when nothing sensible is
  selected - and, with Qt standing up, that the status line fits into the header,
  that the list and its detail pane share one horizontal splitter and the log
  sits under them both; and what the pane, the filter, the pictures and the
  commit lookup do when a row is picked;
* what counts as installed, against a temporary addons folder, and how a compiled
  addon finds its own bundled assets;
* the list of repositories the catalog is read from: the order that list is a
  priority in, one id published by two sources, a source that will not answer,
  the config from before the list existed, and the Sources button adding, moving,
  changing and removing a source through its menus;
* how the store works out that the catalog is ahead of what is installed: the
  version kept beside an install, the way two dotted versions are compared, and
  what Update refuses, asks and then does when it goes ahead;
* the store's own switches and its batch: the row's check box that takes an
  installed addon on and off, the stop-and-switch-off every job starts with and
  the switch an update puts back, the live notice that counts the whole batch
  in percent, and the queue that keeps going addon after addon;
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
  form left in its ``_data`` folder for Settings to show, the picture left in the
  same folder for the tabs and rows to wear, and - when the two toggles say so -
  the settings folder that Delete is allowed to take back, and the two things it
  is not: an addon the store did not install, and this store's own folder.

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
         "author": "fligma", "description": "Says hello, over SFTP.",
         "filename": "hello_store.py", "requires": ["sftp"], "connection": "own",
         "assets": ["helloicon.png"], "icon": "hello_storeicon.png",
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
          and entries[0]["icon"] == "hello_storeicon.png"
          and entries[0]["test"] == "test_hello_store.py"
          and entries[0]["status"] == "new",
          f"assets, icon, test and status did not come through the catalog: {entries[0]}")
    check(entries[0]["requires"] == ["sftp"] and entries[0]["connection"] == "own",
          f"what the addon needs on the device did not come through: {entries[0]}")
    check(entries[1]["assets"] == [] and entries[1]["icon"] == ""
          and entries[1]["test"] == "" and entries[1]["status"] == ""
          and entries[1]["requires"] == [] and entries[1]["connection"] == "",
          f"an entry with nothing to carry gained something: {entries[1]}")

    check(store.parse_catalog([{"id": "only", "name": "Only"}])[0]["id"] == "only",
          "a bare list was not read as the catalog")
    check(not store.parse_catalog({"nope": 1}),
          "an object with no addons list came back with rows anyway")
    check(not store.parse_catalog("not a list"),
          "a string was read as a catalog")

    check(store.raw_address(store.source_values(store.MAIN_SOURCE),
                            "hello_store.py")
          == "/fligma/raspiOpenWrt/main/addon.store/hello_store.py",
          f"the store folder did not come out in the address: "
          f"{store.raw_address(store.source_values(store.MAIN_SOURCE), 'hello_store.py')!r}")

    quoted = store.raw_address({"owner": "a b", "repo": "c/d", "ref": "v 1",
                                "store_path": "u/ld"}, "x.py")
    check(quoted.startswith("/a%20b/c/d/v%201/u/ld/x.py") and ".." not in quoted,
          f"a messy owner, ref or path was not encoded before use: {quoted!r}")

    asset = store.raw_address(store.source_values(store.MAIN_SOURCE),
                              "assets/hello_store/icon.png")
    check(asset == "/fligma/raspiOpenWrt/main/addon.store"
                    "/assets/hello_store/icon.png",
          f"an asset in the store folder did not get an address: {asset!r}")

    # A source saved by an install predating the repository move still names
    # the retired folder; read, it must be folded onto the root layout, or a
    # refresh 404s and no addon can be installed. A third-party folder is left
    # exactly as its author wrote it.
    retired = store.normalize_source({"owner": "fligma", "repo": "raspiOpenWrt",
                                      "ref": "main",
                                      "store_path": "utils/update util/addon.store"})
    check(retired["store_path"] == "addon.store",
          f"a store folder saved under the retired layout was not folded onto the "
          f"current root: {retired['store_path']!r}")
    check(store.raw_address(store.source_values(retired), "hello_store.py")
          == "/fligma/raspiOpenWrt/main/addon.store/hello_store.py",
          "a retired store folder still decided the address the addons are read from")
    check(store.app_folder(retired) == "src/berry_manager",
          f"the modules an addon imports were still looked for in the retired "
          f"app folder: {store.app_folder(retired)!r}")
    other = store.normalize_source({"owner": "someone", "repo": "theirs",
                                    "ref": "main", "store_path": "their/store"})
    check(other["store_path"] == "their/store",
          f"a third-party store folder was rewritten when it should be left alone: "
          f"{other['store_path']!r}")


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
    return last_rows_for(api, "list")


def last_rows_for(api: Any, widget: str) -> Any:
    """The rows the addon last sent to one table, by its widget id."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == widget and arguments[2] == "rows":
            return arguments[3]
    return None


def last_set(api: Any, identifier: str, prop: str) -> Any:
    """The last thing the addon sent one widget, whatever it was."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == identifier and arguments[2] == prop:
            return arguments[3]
    return None


def last_text_for(api: Any, widget: str) -> str:
    """The line the addon last put on one label, by its widget id."""
    return str(last_set(api, widget, "text") or "")


def last_status(api: Any) -> str:
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "status" and arguments[2] == "text":
            return str(arguments[3])
    return ""


def spec_of(spec: Any, ident: str) -> Any:
    """The one leaf of a tab spec that carries this widget id, or nothing.

    A tab says how much room its own columns need in the spec it hands the app,
    which is text and can be read on any machine; what the built widget reports
    back is that number as the platform's style chose to wear it.
    """
    if isinstance(spec, dict):
        if spec.get("id") == ident:
            return spec
        for value in spec.values():
            found = spec_of(value, ident)
            if found is not None:
                return found
    elif isinstance(spec, (list, tuple)):
        for value in spec:
            found = spec_of(value, ident)
            if found is not None:
                return found
    return None


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

    print("\n3. Refresh fills the list, installed or not")
    store.entries = []
    store.on_widget(store.TAB, "refresh", "clicked", {})
    rows = last_row_call(api) or []
    check(len(rows) == 2, f"two catalog rows did not reach the list: {len(rows)}")
    names = [row["cells"][0] for row in rows]
    check(names == ["Hello from the store  \u25cf new", "Odd"],
          f"the list is not the catalog: {names}")
    check([store.find_entry(name)["id"] for name in names]
          == ["hello_store", "odd_id"],
          f"a row's own caption does not lead back to its addon: {names}")
    check(all("cells" in row for row in rows), "a table row holds no cells")
    check(rows[1]["cells"][2] == "needs download",
          f"a row nobody has taken yet was not offered plainly: {rows[1]}")
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
    """Qt builds the header, the filter line, the tile shelf with its pane, and the log."""
    print("\n3b. the status line, the shelf, its detail pane and the log are put as promised")
    global _QT_APP
    try:
        import addon_api
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (QApplication, QCheckBox, QLabel,
                                     QLineEdit, QListView, QListWidget,
                                     QPlainTextEdit, QProgressBar, QPushButton,
                                     QSplitter, QTreeWidget)

        _QT_APP = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the layout itself is not built: {error}")
        return
    _body, registry = addon_api.build_spec(store.tab_spec())
    shelf = registry.get("list")
    log = registry.get("log")
    check(isinstance(shelf, QListWidget) and isinstance(log, QPlainTextEdit),
          f"the tab built no list and no log: {shelf!r} / {log!r}")
    if not isinstance(shelf, QListWidget) or not isinstance(log, QPlainTextEdit):
        return
    bar = shelf.parentWidget()
    check(isinstance(bar, QSplitter)
          and bar.orientation() == Qt.Orientation.Horizontal and bar.count() == 2,
          f"the shelf is not one side of a horizontal splitter: {bar!r}")
    pane = bar.widget(1) if isinstance(bar, QSplitter) else None
    check(pane is not None and not isinstance(pane, QSplitter)
          and pane.findChild(QTreeWidget) is not None,
          f"the detail pane beside the shelf is not the pair it was said to be: {pane!r}")
    # The browsing panes are only half of what sits above the log: the job's own
    # console is laid over them, so the two are held in a column and that column
    # is what the vertical splitter carries.
    holder = bar.parentWidget() if bar is not None else None
    check(holder is not None and not isinstance(holder, QSplitter)
          and bar in holder.findChildren(QSplitter),
          f"the browsing panes are not held in a column of their own: {holder!r}")
    outer = holder.parentWidget() if holder is not None else None
    check(isinstance(outer, QSplitter)
          and outer.orientation() == Qt.Orientation.Vertical and outer.widget(1) is log,
          f"the two panes do not sit above the log in a splitter of their own: {outer!r}")
    # The job's console, then: the pane over the browsing, put away until a job
    # asks for it, with a terminal for a background and a card in the middle.
    shown = registry.get("jobpane")
    check(shown is not None and holder is not None
          and shown.parentWidget() is holder,
          f"the job's pane is not laid over the browsing column: {shown!r}")
    check((spec_of(store.tab_spec(), "jobpane") or {}).get("visible") is False,
          "the job's pane is promised up when the store is only being browsed")
    check(shown is not None and not shown.isVisibleTo(holder),
          "the job's pane is shown before any job has started")
    term = registry.get("jobterm")
    check(term is not None and getattr(term, "_addon_terminal", None) is not None,
          f"the job has no terminal to show its work in: {term!r}")
    # The console is there but folded: a job says only what its card wears
    # until somebody presses the card's own button.
    check((spec_of(store.tab_spec(), "jobterm") or {}).get("visible") is False,
          "the job's console is promised up before anyone asked for it")
    check(term is not None and not term.isVisible(),
          "the job's console is built open on a tab that is only being browsed")
    details = registry.get("jobdetails")
    check(isinstance(details, QPushButton)
          and str(details.text()) == "Show details",
          f"the card does not offer its console behind a plain word: {details!r}")
    check((spec_of(store.tab_spec(), "jobdetails") or {}).get("on") == ["clicked"],
          "the card's details button does not report being pressed")
    card = registry.get("jobcard")
    check(card is not None and shown is not None
          and card.parentWidget() is shown and card is not term,
          f"the job's card does not float over its own console: {card!r}")
    check((spec_of(store.tab_spec(), "jobcard") or {}).get("align") == "center",
          "the job's card is not promised the middle of the console")
    # What the card is made of: the addon's mark, its name, what it is doing,
    # and how far it has got.
    mark = registry.get("jobicon")
    check(isinstance(mark, QLabel) and getattr(mark, "_addon_picture_side", 0) >= 8,
          f"the card wears no picture of its own: {mark!r}")
    for ident, wanted in (("jobname", QLabel), ("jobstep", QLabel),
                          ("jobbar", QProgressBar)):
        check(isinstance(registry.get(ident), wanted),
              f"the card is missing its own {ident}: {registry.get(ident)!r}")
    # The status has no row of its own any more: it shares the header line with
    # the title and the repository chip, so all the height goes to the panes.
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
    # The delete-settings box has left the header: what Delete takes back is
    # set on the store's Settings page only, and the one switch left up there
    # is the box that pulls the log out of its fold - the only thing an
    # end user is asked to decide while they are browsing.
    check(registry.get("delcfg") is None,
          "the header still wears the delete-settings switch")
    box = registry.get("showlog")
    check(isinstance(box, QCheckBox) and header is not None
          and box is header.findChild(QCheckBox),
          f"the header carries no check box beside the source: {box!r}")
    check((spec_of(store.tab_spec(), "showlog") or {}).get("on") == ["toggled"],
          "the check box does not report the tick being moved")
    # It has to claim the width the other two leave over, or a spacer would take
    # the slack and a long report would be squeezed into a narrow column.
    check(status is not None and status.sizePolicy().horizontalPolicy()
          == status.sizePolicy().Policy.Expanding,
          "the status line does not stretch across the room the header leaves it")
    # The filter line is a real box, and it reports as it is typed.
    check(isinstance(registry.get("filter"), QLineEdit)
          and isinstance(registry.get("count"), QLabel),
          f"there is no filter box and no count: {registry.get('filter')!r}")
    # The pane's own widgets, so the addon has something to fill in.
    picture = registry.get("dicon")
    check(isinstance(picture, QLabel) and getattr(picture, "_addon_picture_side", 0) >= 8,
          f"the pane has no picture of its own to wear: {picture!r}")
    about = registry.get("dabout")
    check(isinstance(about, QPlainTextEdit) and about.isReadOnly(),
          f"the pane's description is not a read-only box: {about!r}")
    facts = registry.get("dfacts")
    check(isinstance(facts, QTreeWidget) and facts.columnCount() == 2,
          f"the pane's facts are not a label and an answer: {facts!r}")
    for ident in ("dtitle", "dby", "dstate"):
        check(isinstance(registry.get(ident), QLabel),
              f"the pane is missing its own {ident}")
    # The shelf lays its addons as tiles: a picture over the words, static,
    # re-flowing its columns with the pane - the way a store spreads its apps.
    check(shelf.viewMode() == QListView.ViewMode.IconMode
          and shelf.movement() == QListView.Movement.Static
          and shelf.resizeMode() == QListView.ResizeMode.Adjust
          and shelf.uniformItemSizes() and shelf.wordWrap(),
          f"the store list is not laid out as a tile shelf: "
          f"{shelf.viewMode()!r} / {shelf.movement()!r}")
    asked = spec_of(store.tab_spec(), "list") or {}
    check(asked.get("grid") is True
          and int(asked.get("icon_size") or 0) >= 32
          and int(asked.get("tile_width") or 0) >= 120,
          f"the shelf promises no tiles of a readable size: {asked}")
    side = shelf.iconSize().width()
    check(side >= 32, f"the tiles were drawn with no room for a picture: "
                      f"{shelf.iconSize()!r}")
    look(f"{sys.platform} laid the shelf out at {side}px pictures, "
         f"{shelf.gridSize().width()}px tiles")


# --- three a: the pane beside the shelf, the filter over it, the pictures ---

def test_the_pane(store: Any) -> None:
    """What picking a row shows, what the filter leaves, and where a picture comes from.

    Nothing reaches GitHub here: the commit's address and its reading are checked as
    pure text, a missing picture is only ever put on the queue, and the pane is
    driven through the same ``on_widget`` the app itself would use.
    """
    print("\n3c. the detail pane, the filter, the pictures and the commit's address")
    real_dir = store.addons_dir
    real_entries, real_updates = store.entries, store.updates
    real_picked, real_filter = store.picked, store.filter_text
    with tempfile.TemporaryDirectory() as where:
        folder = Path(where)
        store.addons_dir = lambda: folder
        api = fresh(store)
        source = store.normalize_source(store.MAIN_SOURCE)
        try:
            store.entries = store.parse_catalog(CATALOG)
            for entry in store.entries:        # as merge_catalogs leaves them
                entry["src"] = dict(source)
                entry["source"] = store.source_label(source)
            store.updates = {}
            store.show_entries()
            check(last_text_for(api, "count") == "2 listed",
                  f"the list did not count itself: {last_text_for(api, 'count')!r}")

            print("\n3d. the filter narrows the list without touching GitHub")
            store.on_widget(store.TAB, "filter", "changed", {"text": "odd"})
            rows = last_row_call(api) or []
            check(len(rows) == 1 and rows[0]["cells"][0] == "Odd",
                  f"one word of a filter left the whole store showing: {rows}")
            check(last_text_for(api, "count") == "1 of 2",
                  f"the count did not follow the filter: {last_text_for(api, 'count')!r}")
            store.on_widget(store.TAB, "filter", "changed", {"text": "its own"})
            rows = last_row_call(api) or []
            check(len(rows) == 1 and rows[0]["cells"][0].startswith("Hello"),
                  f"the filter cannot search how an addon reaches a device: {rows}")
            store.on_widget(store.TAB, "filter", "changed", {"text": "sftp"})
            check(len(last_row_call(api) or []) == 1,
                  "the filter does not search what an addon needs on the device")
            store.on_widget(store.TAB, "filter", "changed", {"text": ""})
            check(len(last_row_call(api) or []) == 2,
                  "clearing the filter left it applied")

            print("\n3e. picking a row fills in the pane beside it")
            api.calls.clear()
            store.on_widget(store.TAB, "list", "selected",
                            {"text": "Hello from the store  \u25cf new"})
            facts = {row["cells"][0]: row["cells"][1]
                     for row in (last_rows_for(api, "dfacts") or [])}

            def shown(ident: str, prop: str) -> str:
                for name, arguments, _named in reversed(api.calls):
                    if name == "tabs.set_widget" and len(arguments) >= 4 \
                            and arguments[1] == ident and arguments[2] == prop:
                        return str(arguments[3])
                return ""

            check(store.picked == "hello_store",
                  f"the pane forgot which addon it is describing: {store.picked!r}")
            check(facts.get("id") == "hello_store" and facts.get("Version") == "1.0",
                  f"the pane does not even name its own addon: {facts}")
            check(facts.get("Author") == "fligma"
                  and facts.get("File") == "hello_store.py"
                  and facts.get("Published by") == "fligma/raspiOpenWrt @ main",
                  f"who wrote it, what file it is and where from were not said: {facts}")
            check("sftp" in facts.get("Needs", "")
                  and "of its own" in facts.get("Connection", ""),
                  f"what the addon needs was not said in words a person can read: {facts}")
            check(facts.get("Its own test") == "test_hello_store.py"
                  and "helloicon.png" in facts.get("Its own assets", ""),
                  f"the files an addon carries were not listed: {facts}")
            check("Says hello" in shown("dabout", "value"),
                  f"the description did not reach the pane: {shown('dabout', 'value')!r}")
            # an addon not here at all is put forward as a download, whatever the
            # catalog's own new/updated note says beside its name
            check("download" in last_text_for(api, "dstate").lower(),
                  f"how the addon stands was not said: {last_text_for(api, 'dstate')!r}")

            # and once it is installed behind, the pane says what an update replaces
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")
            (folder / "hello_store.json").write_text('{"version": "0.9"}',
                                                     encoding="utf-8")
            store.updates = store.check_updates()
            api.calls.clear()
            store.show_detail(store.entries[0])
            words = last_text_for(api, "dstate")
            check("Update available" in words and "0.9" in words and "1.0" in words,
                  f"the pane did not say what the update would replace: {words!r}")
            check(store.selected_id() == "hello_store",
                  f"the buttons are not working on the addon being shown: "
                  f"{store.selected_id()!r}")
            store.show_detail(None)
            check(store.picked == "" and last_text_for(api, "dtitle") == "Pick an addon",
                  "the pane was not put back empty when nothing is picked")

            print("\n3f. a right-click offers only what that row could still do")
            api.calls.clear()
            api.answer("ui.choose", None)
            store.on_widget(store.TAB, "list", "context", {"text": "Odd"})
            menus = [arguments[0] for name, arguments, _n in api.calls
                     if name == "ui.choose" and arguments]
            check(menus and list(menus[-1]) == ["Install it", "Refresh the catalog",
                                                "Nothing"],
                  f"a row that is not installed was offered to be updated: {menus}")
            api.answer("ui.choose", "Refresh the catalog")
            store.on_widget(store.TAB, "list", "context", {"text": "Odd"})
            check("catalog loaded" in last_status(api),
                  f"the menu's Refresh did not run it: {last_status(api)!r}")

            print("\n3g. an addon's picture, and which commit its file came out of")
            icons = Path(api.paths.data) / "icons"
            icons.mkdir(parents=True, exist_ok=True)
            (icons / "hello_store.png").write_bytes(b"png")
            check(store.cached_icon("hello_store") == str(icons / "hello_store.png"),
                  f"the picture on disk was not found from its id: "
                  f"{store.cached_icon('hello_store')!r}")
            check(store.icon_of(store.entries[0]).endswith("hello_store.png"),
                  "a row was not drawn with the picture it has")
            check(not store.cached_icon("odd_id"),
                  "an addon with no picture on disk reported one")
            default = store.fallback_icon()
            check(bool(default), "there is no default picture for an addon without one")
            check(store.icon_of(store.entries[1]) == default,
                  "an addon with no picture of its own was not shown with the default")
            del store.icon_queue[:]
            store.queue_icons()
            check(not store.icon_queue,
                  f"a picture already on disk was queued to be fetched again: "
                  f"{store.icon_queue}")
            (icons / "hello_store.png").unlink()
            store.queue_icons()
            check(store.icon_queue == ["hello_store"],
                  f"an addon whose picture is missing was not queued for it: "
                  f"{store.icon_queue}")
            api.timers.cancel_all()        # nothing above should have reached the net

            address = store.commit_address(store.source_values(store.MAIN_SOURCE),
                                           "hello_store.py")
            check(address.startswith("/repos/fligma/raspiOpenWrt/commits?sha=main&path=")
                  and "addon.store/hello_store.py" in address
                  and address.endswith("&per_page=1"),
                  f"the commit query is not an address GitHub answers: {address!r}")
            words = store._commit_words({
                "sha": "abcdef1234567890",
                "commit": {"message": "Title line\n\nBody line.",
                           "author": {"date": "2026-10-13T09:00:00Z"}},
                "author": {"login": "fligma"}})
            check(words["title"] == "Title line" and words["body"] == "Body line."
                  and words["who"] == "fligma" and words["date"] == "2026-10-13",
                  f"a commit was not read down to what a listing shows: {words}")
            store.entries[0]["commit"] = words
            api.calls.clear()
            store.show_detail(store.entries[0])
            facts = {row["cells"][0]: row["cells"][1]
                     for row in (last_rows_for(api, "dfacts") or [])}
            check(facts.get("Commit", "").startswith("abcdef1")
                  and "Title line" in facts.get("Commit", ""),
                  f"the pane does not say which commit the addon came out of: {facts}")
            store.entries[0].pop("asking", None)
            api.calls.clear()
            store.show_detail(store.entries[0])
            check(not [call for call in api.calls if call[0] == "schedule.after"],
                  "an addon whose commit is already known was asked for it again")
        finally:
            api.timers.cancel_all()
            store.addons_dir = real_dir
            store.entries, store.updates = real_entries, real_updates
            store.picked, store.filter_text = real_picked, real_filter
            del store.icon_queue[:]
            store.shutdown()


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
    real_begin, real_python = store._begin_job, store._ensure_python
    real_dev, real_download = store._dev, store._download_all
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

            # the newer shape: a folder program carrying its own manifest
            (folder / "hello_folder").mkdir()
            (folder / "hello_folder" / "hello_folder.json").write_text(
                '{"version": "0.9"}', encoding="utf-8")
            check(store.installed("hello_folder"),
                  "a folder install was not noticed")
            check(store.installed_version("hello_folder") == "0.9",
                  f"the version inside a folder install was not read: "
                  f"{store.installed_version('hello_folder')!r}")

            # the table marks the behind addon, and only it, as an update waiting
            store.updates = found
            api.calls.clear()
            store.show_entries()
            rows = last_row_call(api) or []
            byid = {store.find_entry(row["cells"][0])["id"]: row for row in rows}
            check(byid["hello_store"]["cells"][2] == "update available",
                  f"the behind addon was not marked for update: {byid['hello_store']}")
            check(byid["hello_store"].get("color") == "#ffb020",
                  "the update row did not wear the amber mark")
            check(byid["odd_id"]["cells"][2] == "needs download",
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

            # say yes, and the job that starts is an update remembering its
            # versions, opening on the phase that downloads the whole batch
            api.answer("ui.question", True)
            began: list = []

            def fake_begin(verb, items):
                began.append((verb, (items[0] or {}).get("id")))
                return real_begin(verb, items)
            store._begin_job = fake_begin
            store._ensure_python = lambda: "python"

            class _Ready:
                @staticmethod
                def pyinstaller_ready():
                    return True, ""
            store._dev = lambda: _Ready()
            downloaded: list = []
            store._download_all = lambda: downloaded.append(
                [entry["id"] for entry in (store.job or {}).get("queue") or []])
            api.calls.clear()
            store.update_selected()
            check(began == [("update", "hello_store")],
                  f"a going update did not begin as an update job: {began}")
            check(downloaded == [["hello_store"]],
                  f"the accepted update did not open on its batch downloads: "
                  f"{downloaded}")
            check(store.job.get("total") == 1
                  and store.job.get("from_versions", {}).get("hello_store") == "0.9",
                  f"the job did not remember what it was updating from: {store.job}")
            # the switch is only stopped when the app says the addon is really
            # running here: the stub answers that nothing is installed, so the
            # job must not have written any switch on its way in
            check(not api.called("session.set_addon_running"),
                  "a job for an addon the app does not have still tried to stop it")
    finally:
        store.addons_dir = real_dir
        store.entries, store.updates = real_entries, real_updates
        store._begin_job, store._ensure_python = real_begin, real_python
        store._dev, store._download_all = real_dev, real_download
        store.job = None
        store.working = False
        store.shutdown()


# --- three b2: the row's switch, the batch, and the live notice ---

def test_the_switches(store: Any) -> None:
    """The list's check boxes, a batch that works through its queue, the notice.

    Nothing here reaches the network or a compiler: the job machinery is driven
    on the stub api with the compile and the rescan stood in for, so what gets
    read is the store's own doing - which switch it remembers, which it stops,
    when it puts one back, how far the percentage has come, and the order a
    batch keeps its addons in.
    """
    print("\n5f. the row's switch, the batch, and the live notice")
    api = fresh(store)
    real_dir = store.addons_dir
    real_entries, real_updates = store.entries, store.updates
    real_begin_job, real_python = store._begin_job, store._ensure_python
    real_dev, real_download = store._dev, store._download_all
    real_tidy, real_reload = store._tidy, store._reload
    real_start, real_show = store._start_job, store.show_entries
    try:
        with tempfile.TemporaryDirectory() as where:
            folder = Path(where)
            store.addons_dir = lambda: folder
            store.entries = store.parse_catalog(CATALOG)
            store.updates = {}
            odd = store.entry_for("odd_id")
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")

            # an installed row wears the app's switch; an uninstalled one has none
            api.answer("session.addon_state", {"installed": True, "enabled": True,
                                               "running": True})
            api.calls.clear()
            store.show_entries()
            byid = {store.find_entry(row["cells"][0])["id"]: row
                    for row in (last_row_call(api) or [])}
            check(byid["hello_store"].get("check") is True,
                  f"the enabled addon's row wore no tick: {byid['hello_store']}")
            check("check" not in byid["odd_id"],
                  "a row nobody has installed gained a switch anyway")
            api.answer("session.addon_state", {"installed": True, "enabled": False,
                                               "running": False})
            api.calls.clear()
            store.show_entries()
            byid = {store.find_entry(row["cells"][0])["id"]: row
                    for row in (last_row_call(api) or [])}
            check(byid["hello_store"].get("check") is False,
                  "the switched-off addon's row was ticked as if it were on")

            # the tick itself: the app's switch is written and the tab kept
            api.answer("session.set_addon_running", {"installed": True,
                                                     "enabled": True,
                                                     "running": True})
            api.calls.clear()
            store.on_widget(store.TAB, "list", "toggled",
                            {"text": "Hello from the store  \u25cf new",
                             "checked": True, "cells": []})
            check(api.asked("session.set_addon_running", "addon_id")
                  == "hello_store"
                  and api.asked("session.set_addon_running", "running") is True,
                  f"a tick did not reach the app as a switch-on: "
                  f"{api.last('session.set_addon_running')}")
            check("switched hello_store on" in last_status(api),
                  f"the switch did not say what it did: {last_status(api)!r}")
            check(any(where == "tabs.activate" for where, _a, _n in api.calls),
                  "an addon woken from the store stole the store's own tab")

            # a tick during a job is the job's business, not the tick's
            store.working = True
            api.calls.clear()
            store.on_widget(store.TAB, "list", "toggled",
                            {"text": "Hello from the store", "checked": False})
            check(not api.called("session.set_addon_running"),
                  "a tick during a job moved a switch the job was holding")
            store.working = False

            # a job stops the addon it is about to replace, and remembers how
            # the switch stood; an addon the app does not have is left alone
            api.answer("session.addon_state", {"installed": True, "enabled": True,
                                               "running": True})
            store.job = {"verb": "update", "restore": {}, "total": 1, "done": 0}
            api.calls.clear()
            store._prep_one("hello_store")
            check(store.job["restore"] == {"hello_store": True},
                  f"the job did not remember the switch it took over: "
                  f"{store.job['restore']}")
            check(api.asked("session.set_addon_running", "running") is False,
                  "the addon was left running while its file is replaced")
            api.calls.clear()
            api.answer("session.addon_state", {"installed": False,
                                               "enabled": False, "running": False})
            store._prep_one("odd_id")
            check("odd_id" not in store.job["restore"],
                  "an addon the app does not have was remembered anyway")
            check(not api.called("session.set_addon_running"),
                  "a job tried to stop an addon that is not running here")
            api.calls.clear()
            store._restore_enabled("hello_store")
            check(api.asked("session.set_addon_enabled", "addon_id")
                  == "hello_store"
                  and api.asked("session.set_addon_enabled", "enabled") is True,
                  "the update did not put the switch back where it was found")
            api.calls.clear()
            store._restore_enabled("odd_id")
            check(not api.called("session.set_addon_enabled"),
                  "a fresh install's switch was written as if it were an update")
            store.job = None

            # the card: the percentage is the whole batch's and the card alone
            # wears the steps - the page's status line is not rewritten mid-job -
            # and only the ending leaves the bar where it got to, since the card
            # goes with the job instead of fading over the window
            store.job = {"total": 2, "done": 1, "pane": True}
            api.calls.clear()
            store._progress("compiling widget...", 0.5)
            check(last_set(api, "jobbar", "value") == 75,
                  f"the batch's half-way did not read 75 percent: "
                  f"{last_set(api, 'jobbar', 'value')!r}")
            check(last_set(api, "jobstep", "text")
                  == "2 of 2: compiling widget...",
                  f"the card did not count the batch: "
                  f"{last_set(api, 'jobstep', 'text')!r}")
            check(not api.called("ui.progress_notice"),
                  "a step was still sent to the floating notice this app has no "
                  "longer")
            check(last_status(api) == "",
                  f"a live step rewrote the page's status line: "
                  f"{last_status(api)!r}")
            # A job whose tab has no pane has nowhere to print, and no card to
            # be drawn on: the steps simply are not sent anywhere.
            store.job = {"total": 2, "done": 1, "pane": False}
            api.calls.clear()
            store._progress("compiling widget...", 0.5)
            check(last_set(api, "jobstep", "text") is None
                  and last_set(api, "jobbar", "value") is None,
                  "a job with no pane wrote on the card it never asked for")
            store.job = {"total": 3, "done": 1, "pane": True}
            api.calls.clear()
            store._finish("the download stopped", bad=True)
            check(api.asked("tabs.lock", "locked") is False,
                  "a job that ended bad left the window's tab bar held: "
                  f"{api.last('tabs.lock')}")
            check(last_set(api, store.SUBTABS, "locked") is False,
                  "a job that ended left the store's own sub-bar held")
            check(last_set(api, "jobpane", "visible") is False,
                  "a job that ended bad left its console over the browsing panes")
            check("stopped after 1 of 3" in last_status(api),
                  f"the failed batch did not say how far it got: "
                  f"{last_status(api)!r}")
            store.job = {"total": 1, "done": 1, "pane": True}
            api.calls.clear()
            store._finish("installed addons/widget")
            check(last_set(api, "jobbar", "value") is None,
                  "a finished job filled a card that is already being taken down")
            check(not api.called("ui.progress_notice_close"),
                  "an ending still closed a notice that is not shown")
            store.job, store.working = None, False

            # the batch install: two rows picked, one question, the queue held
            (folder / "hello_store.exe").unlink()

            # the old loose shapes go before the folder takes their name: on a
            # Unix machine the one-file program is the bare addons/<id>, the
            # very name this build wants for its folder, and only a manifest
            # the store wrote says the store may clear it
            (folder / "hello_store.json").write_text(
                json.dumps({store.ORIGIN_KEY: store.STORE_ORIGIN}),
                encoding="utf-8")
            (folder / "hello_store").write_text("x", encoding="utf-8")
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")
            store._clear_old_flat("hello_store")
            check(not (folder / "hello_store").is_file()
                  and not (folder / "hello_store.exe").exists(),
                  "a loose store program of the addon's own name was left "
                  "to block the folder")
            (folder / "odd_id").write_text("x", encoding="utf-8")
            store._clear_old_flat("odd_id")
            check((folder / "odd_id").is_file(),
                  "a program the store never installed was cleared anyway")
            (folder / "odd_id").unlink()

            api.answer("session.addon_state", {"installed": False,
                                               "enabled": False, "running": False})
            api.answer("tabs.widget_value",
                       ["Hello from the store  \u25cf new", "Odd"])
            api.answer("ui.question", True)
            phases: list = []
            store._ensure_python = lambda: "python"

            class _Ready:
                @staticmethod
                def pyinstaller_ready():
                    return True, ""
            store._dev = lambda: _Ready()
            store._download_all = lambda: phases.append(
                [entry["id"] for entry in (store.job or {}).get("queue") or []])
            api.calls.clear()
            store.install_selected()
            check(store.job is not None and store.job.get("total") == 2
                  and [entry["id"] for entry in store.job["queue"]]
                  == ["hello_store", "odd_id"],
                  f"the batch did not hold the whole pick for its downloads: "
                  f"{store.job}")
            check("install starting" in last_status(api),
                  f"the page never heard the job begin: {last_status(api)!r}")
            check(phases == [["hello_store", "odd_id"]],
                  f"the batch did not open on the whole queue's downloads: "
                  f"{phases}")
            check([arguments for where, arguments, _n in api.calls
                   if where == "ui.question"]
                  and api.calls and "Install 2 addons" in
                  str([a for _w, a, _n in api.calls if _w == "ui.question"][-1][1]),
                  "a two-addon install asked per addon, or not at all")

            # and the batch lands addon by addon as its builds finish: each
            # install step places one program on its own, and only the batch's
            # own ending closes the job and asks for the rescan
            reloads: list = []
            store._tidy = lambda ident: None
            store._reload = lambda: reloads.append("reload")

            class _Step:
                def staged_name(self, ident, onefile, root=None):
                    made = folder / f"{ident}.staged.exe"
                    made.write_text("x", encoding="utf-8")
                    return made

                def install_build(self, staged, ident, onefile):
                    target = folder / ident / f"{ident}.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("x", encoding="utf-8")
                    return target
            store._dev = lambda: _Step()
            api.calls.clear()
            store._install_step("hello_store")
            check(store.job is not None and store.job["done"] == 1
                  and store.job["did"] == ["hello_store"],
                  f"the batch's first addon did not land on its own step: "
                  f"{store.job}")
            check(not reloads,
                  "the rescan came before the batch's last addon")
            store._install_step("odd_id")
            check(store.job is not None
                  and store.job["did"] == ["hello_store", "odd_id"],
                  f"the batch's last addon did not land on its own step: "
                  f"{store.job}")
            api.calls.clear()
            store._finish_all()
            check("installed 2 addons - hello_store, odd_id" in last_status(api),
                  f"the batch did not end by naming what it installed: "
                  f"{last_status(api)!r}")
            check(reloads == ["reload"] and store.job is None
                  and not store.working,
                  f"the batch's end did not close itself and rescan: {reloads} "
                  f"/ {store.job}")

            # the batch update: only what is behind is taken, in one job, and
            # it is asked for once
            store.entries = store.parse_catalog({"addons": [
                {"id": "hello_store", "name": "Hello", "version": "1.1"},
                {"id": "odd_id", "name": "Odd", "version": "0.2"}]})
            (folder / "hello_store.json").write_text('{"version": "1.0"}',
                                                     encoding="utf-8")
            (folder / "odd_id" / "odd_id.json").parent.mkdir(parents=True,
                                                              exist_ok=True)
            (folder / "odd_id" / "odd_id.json").write_text('{"version": "0.1"}',
                                                           encoding="utf-8")
            api.answer("tabs.widget_value", ["Hello", "Odd"])
            api.answer("ui.question", True)
            began: list = []
            store._begin_job = lambda verb, items: (
                began.append((verb, [entry["id"] for entry in items])), None)[1]
            store._start_job = lambda: None
            api.calls.clear()
            store.update_selected()
            check(began == [("update", ["hello_store", "odd_id"])],
                  f"the picked update did not take both behind addons at once: "
                  f"{began}")
            asked = [arguments for where, arguments, _n in api.calls
                     if where == "ui.question"]
            check(asked and str(asked[-1][0]) == "Update addons",
                  "a two-addon update asked per addon, or not at all")
    finally:
        store.addons_dir = real_dir
        store.entries, store.updates = real_entries, real_updates
        store._begin_job, store._ensure_python = real_begin_job, real_python
        store._dev, store._download_all = real_dev, real_download
        store._tidy, store._reload = real_tidy, real_reload
        store._start_job, store.show_entries = real_start, real_show
        store.job = None
        store.working = False
        store.shutdown()


def test_a_start_that_dies(store: Any) -> None:
    """A job that dies before its first scheduled step has to die visibly.

    A wedged ``working`` flag is indistinguishable from a button that does
    nothing: once a job is promised, every later press answers the guard at
    the top of the handler, so an exception loose in the synchronous part of
    the start - an ``import core.addon`` that a compiled store cannot reach,
    say - keeps every button dead for the rest of the session. Both buttons,
    and the step a finished process hands the job on to, must end the promised
    job with the reason on the screen instead.
    """
    print("\n5i. a start that cannot start ends as a failure it says")
    api = fresh(store)
    real_dir = store.addons_dir
    real_entries, real_updates = store.entries, store.updates
    real_start, real_dev = store._start_job, store._dev
    try:
        with tempfile.TemporaryDirectory() as where:
            folder = Path(where)
            store.addons_dir = lambda: folder
            store.entries = store.parse_catalog(CATALOG)
            store.updates = {}
            api.answer("session.addon_state", {"installed": False,
                                               "enabled": False,
                                               "running": False})
            api.answer("tabs.widget_value", ["Hello from the store  \u25cf new"])
            api.answer("ui.question", True)

            def explode() -> None:
                raise ModuleNotFoundError("No module named 'core'")

            store._start_job = explode
            api.calls.clear()
            store.install_selected()
            check(not store.working and store.job is None,
                  f"a start that raised wedged the tab: working={store.working}, "
                  f"job={store.job is not None}")
            check("install could not start" in last_status(api)
                  and "core" in last_status(api),
                  f"a failed install did not say why: {last_status(api)!r}")

            # the same promise on the update button
            (folder / "hello_store.exe").write_text("x", encoding="utf-8")
            (folder / "hello_store.json").write_text('{"version": "1.0"}',
                                                     encoding="utf-8")
            store.entries = store.parse_catalog({"addons": [
                {"id": "hello_store", "name": "Hello", "version": "1.1",
                 "filename": "hello_store.py"}]})
            api.answer("session.addon_state", {"installed": True,
                                               "enabled": False,
                                               "running": False})
            api.answer("tabs.widget_value", ["Hello"])
            api.calls.clear()
            store.update_selected()
            check(not store.working and store.job is None,
                  "a start of an update that raised wedged the tab")
            check("update could not start" in last_status(api),
                  f"a failed update did not say why: {last_status(api)!r}")

            # and a step that raises on the tick after a finished process is
            # a failed job too - not a dead program, and not a held button
            class _DoneStream:
                finished = True
                code = 0

                def drain(self, _n: int) -> list:
                    return []
            store.job = {"verb": "install", "total": 1, "done": 0,
                         "pane": False, "stream": _DoneStream(),
                         "on_ok": explode, "on_bad": None, "fail": "",
                         "building": {}, "waiting": [], "restore": {}}
            store.working = True
            api.calls.clear()
            store._poll_stream()
            check(not store.working and store.job is None,
                  "an on_ok that raised left the job holding the buttons")
            check("could not go on" in last_status(api),
                  f"a failed continuation did not say why: {last_status(api)!r}")
    finally:
        store.addons_dir = real_dir
        store.entries, store.updates = real_entries, real_updates
        store._start_job, store._dev = real_start, real_dev
        store.job = None
        store.working = False
        store.shutdown()


def test_the_batch_order(store: Any) -> None:
    """The batch's own order: every download, then the gates, then threes.

    The install used to walk the picked addons one at a time - one addon's
    whole download, test and compile before the next was even fetched. This
    drives the phases themselves: the whole queue staged to disk before the
    first gate opens, the builds kept to three at a time with a finished one
    handing its slot to the next waiting addon, and a build that dies taking
    its siblings down rather than leaving them printing to nobody.
    """
    print("\n5h. the batch's phases: every download, then gates, then threes")
    api = fresh(store)
    real = (store.job, store._stage_entry, store._start_tests,
            store._begin_build, store._install_step, store._finish_all,
            store.harness_folder, store._reset_staging)
    slots: dict = {}
    try:
        hello = {"id": "hello_store", "name": "Hello"}
        odd = {"id": "odd_id", "name": "Odd"}

        class _SlotStream:
            """A build process the test finishes by hand, slot by slot."""

            def __init__(self, ident: str) -> None:
                self.ident = ident
                self.finished = False
                self.code = 0
                self.killed = False

            def start(self) -> None:
                pass

            def drain(self, limit: int = 400) -> list:
                return []

            def kill(self) -> None:
                self.killed = True

        def batch(items: list) -> dict:
            """A job dict whole enough for every phase to walk over."""
            return {"verb": "install", "values": {}, "source": "",
                    "stream": None, "on_ok": None, "fail": "", "lines": [],
                    "harness": None, "widened": False, "details": {},
                    "console": False, "id": "", "queue": list(items),
                    "phase": "", "staged": {}, "waiting": [], "building": {},
                    "test_ids": [], "test_at": 0, "from_versions": {},
                    "total": len(items), "done": 0, "restore": {},
                    "did": [], "pane": False}

        # one: every file of the whole batch lands before the first gate opens
        staged: list = []
        gates: list = []
        store._reset_staging = lambda id_value="": None
        store.harness_folder = lambda values: ROOT
        store._stage_entry = lambda entry: (staged.append(entry["id"]), "")[1]
        store._start_tests = lambda: gates.append(list(staged))
        store.job = batch([hello, odd])
        store._download_all()
        check(staged == ["hello_store", "odd_id"],
              f"the download phase did not take the whole queue first: {staged}")
        check(gates == [["hello_store", "odd_id"]],
              f"the gates did not open after every download: {gates}")

        # two: the builds keep to three at a time, and a finished one hands
        # its slot to the next waiting addon rather than ending the batch
        started: list = []

        def fake_begin(ident: str) -> None:
            made = _SlotStream(ident)
            slots[ident] = made
            store.job["building"][ident] = {"stream": made, "lines": [],
                                            "fail": f"{ident} failed"}
            started.append(ident)
        landed: list = []
        store._begin_build = fake_begin
        store._install_step = lambda ident: landed.append(ident)
        store._finish_all = lambda: landed.append("end")
        ids = [f"a{n}" for n in range(5)]
        store.job = batch([{"id": one} for one in ids])
        store.job["test_ids"] = list(ids)
        store._start_builds()
        check(started == ["a0", "a1", "a2"],
              f"the batch did not open exactly three builds: {started}")
        slots["a0"].finished = True
        store._poll_builds()
        check(landed == ["a0"] and started == ["a0", "a1", "a2", "a3"],
              f"a finished build did not hand its slot on: {landed} / {started}")
        check(store.job is not None and "a4" in store.job["waiting"],
              "the batch ended while a build and a waiting addon remained")

        # three: a build that dies ends the whole job and takes its siblings
        # down with it - nobody is left to read what they go on printing
        slots["a1"].finished = True
        slots["a1"].code = 1
        api.calls.clear()
        store._poll_builds()
        check(store.job is None and not store.working,
              "a failed build left the batch running")
        check(slots["a2"].killed and slots["a3"].killed,
              "a failed build left its siblings compiling")
        check("a1 failed" in last_status(api),
              f"the failed build did not say so: {last_status(api)!r}")
    finally:
        (store.job, store._stage_entry, store._start_tests,
         store._begin_build, store._install_step, store._finish_all,
         store.harness_folder, store._reset_staging) = real
        store.job = None
        store.working = False
        store.shutdown()


# --- three c: several repositories, read in order and edited on the tab ---

class _Menus:
    """``api.ui`` for a run through the Sources menu: the answers, in order.

    The editor is a sequence of menus and single-line boxes, so a list drives it:
    a string is what was picked or typed, ``True``/``False`` a question's answer,
    and running out of answers is the user closing the menu without saying more.
    """

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.asked: list = []
        self.warned: list = []

    def _next(self) -> Any:
        return self.answers.pop(0) if self.answers else None

    def choose(self, items):
        self.asked.append(("choose", list(items)))
        return self._next()

    def input(self, title, label, default="", secret=False):
        self.asked.append(("input", str(label), str(default)))
        return self._next()

    def question(self, title, text, default=False):
        self.asked.append(("question", str(title)))
        answer = self._next()
        return bool(answer) if answer is not None else False

    def warning(self, title, text):
        self.warned.append((str(title), str(text)))


def last_chip(api: Any) -> str:
    """The source line the tab last showed in its header."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == "source" and arguments[2] == "text":
            return str(arguments[3])
    return ""


def chips_shown(api: Any) -> list:
    """Every source line the tab has shown, in the order it showed them.

    A stub api never keeps what it was asked to write, so a reload after an edit
    reads the sources the api was made with rather than the edited ones; the line
    the editor put up is still the one that was asked for.
    """
    return [str(arguments[3]) for where, arguments, _named in api.calls
            if where == "tabs.set_widget" and len(arguments) >= 4
            and arguments[1] == "source" and arguments[2] == "text"]


def sources_written(api: Any) -> Any:
    """The ``values`` the store last asked the config to be written with."""
    for where, arguments, named in reversed(api.calls):
        if where == "config.update":
            if named:
                return named.get("values")
            return arguments[0] if arguments else None
    return None


def keys_popped(api: Any) -> list:
    return sorted(arguments[0] for where, arguments, _named in api.calls
                  if where == "config.pop" and arguments)


def test_the_sources(store: Any) -> None:
    """What the config says about where addons come from, and what the tab does about it.

    Nothing here reaches GitHub: the catalog is answered per source, so the order
    of the list, the clash between two repositories publishing one id, a source
    that will not answer and the header chip are all read off what the store put
    together. The Sources button is driven through its menus by a scripted run of
    answers, and the config it writes is read back out of the recorded calls.
    """
    print("\n5e. the sources: which repositories the store reads, in what order")

    main = store.read_sources({"sources": [dict(store.MAIN_SOURCE)], "token": ""})
    check(len(main) == 1 and main[0]["owner"] == "fligma"
          and main[0]["store_path"] == "addon.store",
          f"the shipped default is not this program's own repository: {main}")
    legacy = store.read_sources({"owner": "someone", "repo": "other", "ref": "v1",
                                 "store_path": "util/store"})
    check([store.source_label(source) for source in legacy] == ["someone/other @ v1"],
          f"a single-source config was not migrated: {legacy}")
    check(store.read_sources({}) == main,
          f"a config naming nothing did not fall back to the main source: "
          f"{store.read_sources({})}")
    check(not store.read_sources({"sources": []}),
          "every source having been removed still left one behind")
    twice = store.read_sources({"sources": [dict(store.MAIN_SOURCE),
                                              dict(store.MAIN_SOURCE),
                                              {"owner": "a", "repo": "b"}]})
    check([store.source_label(source) for source in twice]
          == ["fligma/raspiOpenWrt @ main", "a/b @ main"],
          f"a source named twice was kept twice, or the wrong one dropped: {twice}")
    check(store.read_sources({"sources": [{"owner": "a"}, {"repo": "b"},
                                            {"owner": " c ", "repo": " d "}]})
          == [store.normalize_source({"owner": "c", "repo": "d"})],
          "a half-typed source was kept, or a whole one not cleaned")
    check(store.source_values(store.normalize_source({"owner": "a", "repo": "b"}))
          == {"owner": "a", "repo": "b", "ref": "main",
              "store_path": "addon.store"},
          "a source did not come out as the flat address the fetches read, or it "
          "still carried a token of its own")

    # one source, on the tab: the chip names it and the rows say where they came from
    api = fresh(store)
    check(last_chip(api) == "fligma/raspiOpenWrt @ main",
          f"the header does not name the source: {last_chip(api)!r}")
    store.entries = []
    store.on_widget(store.TAB, "refresh", "clicked", {})
    api.calls.clear()
    store.on_widget(store.TAB, "list", "selected", {"text": "hello_store"})
    facts = {row["cells"][0]: row["cells"][1]
             for row in (last_rows_for(api, "dfacts") or [])}
    check(facts.get("Published by") == "fligma/raspiOpenWrt @ main",
          f"the pane did not say which source published the addon: {facts}")

    # nothing configured at all is said, rather than reading the main source anyway
    empty = fresh(store, config={"sources": []})
    store.entries = store.parse_catalog(CATALOG)
    store.refresh()
    check("no source is configured" in last_status(empty),
          f"an empty list of sources still read a catalog: {last_status(empty)!r}")
    check(not store.entries, f"the table kept rows out of a source-less store: "
                             f"{store.entries}")

    # the list put together: order is priority, and a dead source costs only itself
    other = store.normalize_source({"owner": "someone", "repo": "other",
                                    "ref": "v1", "store_path": "store"})
    both = fresh(store, config={"sources": [dict(store.MAIN_SOURCE), other]})
    real_load = store.load_catalog
    check(last_chip(both) == "fligma/raspiOpenWrt @ main  +1",
          f"several sources were not counted in the header: {last_chip(both)!r}")

    def catalogue(values: dict) -> list:
        if values["owner"] == "fligma":
            return store.parse_catalog(CATALOG)
        return store.parse_catalog({"addons": [
            {"id": "hello_store", "name": "Other's copy", "version": "9.9"},
            {"id": "only_other", "name": "Only there", "version": "1.0"}]})
    try:
        store.load_catalog = catalogue
        found, failed = store.merge_catalogs([main[0], other])
        ids = [entry["id"] for entry in found]
        check(ids == ["hello_store", "odd_id", "only_other"],
              f"the catalogs did not come together in the order asked for: {ids}")
        check(found[0]["name"] == "Hello from the store"
              and found[0]["source"] == "fligma/raspiOpenWrt @ main",
              f"an id in two sources was not taken from the first: {found[0]}")
        check(found[2]["source"] == "someone/other @ v1"
              and found[2]["src"] == other,
              f"a row from the second source did not carry it: {found[2]}")
        check(not failed, f"two living sources reported a failure: {failed}")

        def refuses(values: dict) -> list:
            if values["owner"] != "fligma":
                raise store.AddonError("the catalog could not be reached")
            return store.parse_catalog(CATALOG)
        store.load_catalog = refuses
        found, failed = store.merge_catalogs([main[0], other])
        check(len(found) == 2 and len(failed) == 1
              and failed[0].startswith("someone/other @ v1:"),
              f"a source that would not answer took the rest of them with it: "
              f"{found} {failed}")
        both.calls.clear()
        store.refresh()
        check("could not be read" in last_status(both),
              f"a source that failed was not reported on the tab: "
              f"{last_status(both)!r}")
        check(len(store.entries) == 2,
              f"a source that failed emptied the table: {store.entries}")
    finally:
        store.load_catalog = real_load

    # a job follows the source that published the addon it is building
    store.entries = store.parse_catalog(CATALOG)
    store.entries[0]["src"] = other
    store.entries[0]["source"] = store.source_label(other)
    try:
        began = store._begin("install", store.entries[0])
        check(began["values"]["owner"] == "someone"
              and began["values"]["store_path"] == "store"
              and began["source"] == "someone/other @ v1",
              f"the job did not follow the source that published the addon: "
              f"{began['values']}")
        store.working = False
        began = store._begin("install")
        check(began["values"]["owner"] == "fligma",
              f"a job with no entry did not take the first source: {began['values']}")
    finally:
        store.job, store.working = None, False

    # the Sources button: one field changed, and the old flat keys cleared out
    # (the runs below all drive ``both``, the api holding the two sources, since
    # that is the one the addon is talking to: a stub never stores what was
    # written, so every run starts from those two and writes the edited list)
    both.calls.clear()
    both.ui = _Menus(["2. someone/other @ v1", "Change repository", "somerepo",
                      store._DONE])
    store.edit_sources()
    wrote = sources_written(both)
    check(wrote and [store.source_label(s) for s in wrote["sources"]]
          == ["fligma/raspiOpenWrt @ main", "someone/somerepo @ v1"],
          f"the edited source list is not what the menu was told: {wrote}")
    check(keys_popped(both) == sorted(store.SOURCE_KEYS),
          f"the keys a single-source config used were not cleared: {keys_popped(both)}")
    check(last_chip(both) == "fligma/raspiOpenWrt @ main  +1",
          f"the header was not pointed at the changed sources: {last_chip(both)!r}")

    # add one, refuse a duplicate, move one first, remove one - and close with nothing said
    both.calls.clear()
    both.ui = _Menus([store._ADD, "another", "repo2", "dev", "store/here",
                      store._DONE])
    store.edit_sources()
    wrote = sources_written(both)
    check(wrote and [store.source_label(s) for s in wrote["sources"]][-1]
          == "another/repo2 @ dev",
          f"a source added from the menu is not in the config: {wrote}")
    check(len(wrote["sources"]) == 3,
          f"adding a source lost one that was already there: {wrote}")

    both.calls.clear()
    both.ui = _Menus([store._ADD, "fligma", "raspiOpenWrt", "main",
                      "addon.store", store._DONE])
    store.edit_sources()
    check("already a source" in last_status(both),
          f"a source added twice was not turned away: {last_status(both)!r}")
    check(sources_written(both) is None,
          "the config was written for a source that was refused")

    both.calls.clear()
    both.ui = _Menus(["2. someone/other @ v1", "Move first",
                      "1. someone/other @ v1", "Remove", False,
                      "2. fligma/raspiOpenWrt @ main", "Remove", True, store._DONE])
    store.edit_sources()
    wrote = sources_written(both)
    check(wrote and [store.source_label(s) for s in wrote["sources"]]
          == ["someone/other @ v1"],
          f"a move and two removals did not come out as told: {wrote}")
    check("someone/other @ v1" in chips_shown(both),
          f"the header never showed the sources as they were left: "
          f"{chips_shown(both)}")

    both.calls.clear()
    both.ui = _Menus([])                    # closed the menu without a word
    store.edit_sources()
    check(sources_written(both) is None,
          "closing the Sources menu wrote the config anyway")
    check(not [asked for asked in both.ui.asked if asked[0] == "input"],
          "a menu closed at once went on asking for a repository")

    # a typed-in source with no owner or repo is refused where it was asked for
    both.calls.clear()
    menus = _Menus([store._ADD, "", "", "", "store", store._DONE])
    both.ui = menus
    store.edit_sources()
    check(bool(menus.warned),
          "a source with nothing in it was accepted without a word")
    check(sources_written(both) is None,
          "a refused source still reached the config")
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
    """Import buildtools.py from the top of the repository, beside ``src``."""
    path = ROOT.parents[1] / "buildtools.py"
    spec = importlib.util.spec_from_file_location("buildtools", str(path))
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
        first = gen.addon_index_build(root, None)
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
        second = {entry["id"]: entry
                  for entry in gen.addon_index_build(root, first)["addons"]}
        check(second["widget"].get("status", "") == "",
              "an unchanged addon was marked a second time")
        bumped = gen.addon_index_build(root, {"addons": [{"id": "widget",
                                                          "version": "1.0"}]})
        moved = [e for e in bumped["addons"] if e["id"] == "widget"][0]
        check(moved.get("status") == "updated",
              "a changed version was not marked updated")
        check(gen.render_index(gen.addon_index_build(root, first))
              == gen.render_index(gen.addon_index_build(root, first)),
              "the same store rendered differently twice")

    real = gen.ADDON_STORE
    if (real / "hello_store.py").is_file():
        by_id = {entry["id"]: entry
                 for entry in gen.addon_index_build(real, None)["addons"]}
        check({"hello_store", "cert_manager", "openwrt_logs"} <= set(by_id),
              f"the real store folder did not list every addon: {sorted(by_id)}")
        check(all(by_id[i].get("test") for i in
                  ("hello_store", "cert_manager", "openwrt_logs") if i in by_id),
              f"a store addon brought no test into the catalog: {by_id}")


# --- six: the compile gate: an addon's test runs before its build ---

class _FakeDev:
    """Just enough of core.addon for the gate, with no real interpreter."""

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
                                # a module of the app's package is named whole, and
                                # is fetched whole - not collapsed onto its folder
                                (["ModuleNotFoundError: No module named 'core.ui'"],
                                 "core.ui"),
                                (["ModuleNotFoundError: "
                                  "No module named 'core.ui.things'"], "core"),
                                (["ModuleNotFoundError: No module named 'widget'"], ""),
                                (["ModuleNotFoundError: "
                                  "No module named 'requests.packages'"], "")):
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
            main = store.source_values(store.MAIN_SOURCE)
            store.job = {"values": dict(main)}

            def write(values: dict, remote: str, target: Path,
                      folder: str | None = None) -> str:
                fetched.append((remote, folder))
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text("# fetched\n", encoding="utf-8")
                return ""
            store._fetch_file = write

            check(store.app_folder(dict(main)) == "src/berry_manager",
                  f"the app's folder was not read off the store path: "
                  f"{store.app_folder(dict(main))!r}")
            check(store.app_folder({"store_path": "deep/er/store"}) == "deep/er",
                  "a store folder with a parent above it did not name that parent")
            check(store.raw_address(dict(main), "addon_client.py",
                                    store.app_folder(dict(main)))
                  == "/fligma/raspiOpenWrt/main/src/berry_manager/addon_client.py",
                  f"the app's own folder did not survive the address: "
                  f"{store.raw_address(dict(main), 'addon_client.py', 'src/berry_manager')!r}")

            # a checkout answers with its own folder and downloads nothing
            store._dev = lambda: _FakeDev()
            check(store.harness_folder(dict(main)) == ROOT,
                  "a checkout's own folder was not taken for the modules an addon "
                  "imports")
            check(not fetched, f"a checkout still fetched: {fetched}")

            # a program with nothing on disk asks for both, from the app's folder
            store._dev = lambda: _FrozenDev()
            found = store.harness_folder(dict(main))
            # Both are asked for at once, so which lands first is the network's
            # business; what matters is that neither was left out.
            check({name for name, _folder in fetched} == set(store.HARNESS_FILES)
                  and len(fetched) == len(store.HARNESS_FILES),
                  f"both modules were not fetched: {fetched}")
            check({folder for _name, folder in fetched} == {store.app_folder(main)},
                  f"the modules were looked for in the store folder, not the app's: "
                  f"{fetched}")
            check(found == store.staging() / "harness",
                  f"the fetched modules were put somewhere odd: {found!r}")

            # and when the fetch stops, so does the answer: nothing is half-there
            fetched.clear()
            store._fetch_file = lambda values, remote, target, folder=None: (
                "GitHub answered 404" if remote == store.HARNESS_FILES[1] else "")
            check(store.harness_folder(dict(main)) is None,
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
            store.job = {"values": dict(main), "harness": narrow,
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
            # a module of a package is the file under that package's folder: the
            # widening asks for the path, and GitHub hands it back the same way
            check("core/ui.py" in [name for name, _folder in fetched],
                  f"a module of the app's package was not asked for by its path: "
                  f"{[name for name, _f in fetched]}")
            check((narrow / "core" / "ui.py").is_file(),
                  "the widened harness holds no core/ui.py in a core folder")
            check(not (narrow / "core.py").exists(),
                  "a package was fetched as one file of its own name")
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
            store.job = {"values": dict(main), "harness": narrow,
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
        import core.addon as dev
    except Exception as error:                      # noqa: BLE001 - app modules
        look(f"core.addon is not importable here: {error}")
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

    The store asks :mod:`core.addon` which Python to build with, and on
    Windows the first name on the path is often a zero-length alias that answers
    "Python was not found" and stops with code 9009 - which reads as a broken
    build unless the search refuses it. Needs the app's own modules, so it is
    quiet when they are not there.
    """
    print("\n9. the interpreter search takes a Python that actually starts")
    try:
        import core.addon as dev
    except Exception as error:                      # noqa: BLE001 - app modules
        look(f"core.addon is not importable here: {error}")
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

        # a pip that goes through simply carries on to the batch downloads
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
        real_download = store._download_all
        store._stream = clean
        store._dev = lambda: Ready
        store._download_all = lambda: asked.append({"downloaded": True})
        try:
            store.job["id"] = "widget"
            store._pip_install("python")
            check(any("downloaded" in item for item in asked),
                  f"a clean pip did not carry on to the batch downloads: {asked}")
        finally:
            store._download_all = real_download
            store._dev = real_dev
    finally:
        store._stream = real_stream
        store._finish = real_finish
        store.job = real_job


# --- eight: where an install leaves its work, and what it must not touch after ---

class _IdleStream:
    """A build process stood in for: it prints nothing and ends clean."""

    finished = True
    code = 0

    def start(self) -> None:
        pass

    def drain(self, limit: int = 400) -> list:
        return []

    def kill(self) -> None:
        pass


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
        if onefile:
            return self.dist / f"{addon_id}.exe"
        return self.dist / addon_id / f"{addon_id}.exe"

    def install_build(self, staged: Path, addon_id: str, onefile: bool) -> Path:
        """Pretend the program was copied, and say where it now sits.

        The real one writes into ``addons``, and the store speaks about that copy
        afterwards - so the stand-in puts its pretend file in the folder the test
        is using, never somewhere relative to whoever runs it. A one-file build
        lands loose in that folder; a folder build lands inside a folder of the
        addon's own name, the way the store installs programs now.
        """
        self.seen["copied"] = Path(staged)
        self.installs.mkdir(parents=True, exist_ok=True)
        if onefile:
            target = self.installs / f"{addon_id}.exe"
        else:
            target = self.installs / addon_id / staged.name
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        return target

    def _Stream(self, command, cwd=None, env=None):
        """The process a build is started as: watched, never really run."""
        self.seen["command"] = list(command)
        self.seen["cwd"] = cwd
        return _IdleStream()


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

            # the build is asked for inside the store's own data folder, and
            # taken up as a slot of its own - a batch may have three in flight
            dist = build / "widget" / "dist"
            dev = _BuildDev(dist, addons)
            store._dev = lambda: dev
            store.job = {"values": {}, "harness": ROOT, "building": {},
                         "total": 1, "done": 0}
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
            check(Path(dev.seen.get("cwd", "/")) == build / "widget",
                  f"PyInstaller ran from elsewhere: {dev.seen.get('cwd')!r}")
            slot = store.job["building"].get("widget") or {}
            check(slot.get("stream") is not None,
                  f"the build was not taken up as a slot the batch can drain: "
                  f"{store.job['building']}")

            # the finished program: copied, cleared, marked off, and only then
            # the rescan, which takes the api away from the running code
            dist.mkdir(parents=True)
            (stage / "widget" / "assets").mkdir(parents=True)
            (stage / "widget" / "assets" / "pic.png").write_text(
                "x", encoding="utf-8")
            (dist / "widget").mkdir()
            (dist / "widget" / "widget.exe").write_text("x", encoding="utf-8")
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
                # the build ended clean: the step places this addon's program,
                # and the batch's own ending closes the job and asks for the
                # rescan
                store._install_step("widget")
                store._finish_all()
            except Exception as error:                  # noqa: BLE001 - the crash itself
                broken.append(f"{type(error).__name__}: {error}")
            check(not broken, f"the install could not finish itself: {broken}")
            check(dev.seen.get("staged root") == build
                  and dev.seen.get("copied") == dist / "widget" / "widget.exe",
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
            left = sorted(p.name for p in (stage / "widget").iterdir())
            check(stage.is_dir() and not left,
                  f"the addon's downloads were left in its staging folder: {left}")

            # and it said what it had installed, in the two places the app looks
            keeps = store.addon_data_folder("widget")
            check(keeps == addons / "_data" / "widget",
                  f"the settings were not prepared in the addon's own folder: "
                  f"{keeps}")
            sidecar = json.loads((addons / "widget" / "widget.json")
                                 .read_text(encoding="utf-8"))
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
            gone = sorted(path.relative_to(addons).as_posix()
                          for path in store._delete_paths("widget"))
            check(gone == ["_data/widget", "widget"],
                  f"delete does not take an install back whole: {gone}")
            store.session_delete_config = False     # the box at the top, this session
            check(sorted(path.relative_to(addons).as_posix()
                         for path in store._delete_paths("widget"))
                  == ["widget"],
                  "the session's check box did not leave the settings where they are")
            store.session_delete_config = None
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


def test_the_parallel_download(store: Any) -> None:
    """An install's files come down side by side, and the widgets stay clear.

    ``_fetch_many`` overlaps the round-trips an addon needs - program, pictures,
    test - so an install pays for the slowest rather than the sum. That only
    holds if a side thread never touches what belongs to the main one: the log
    and the credential. This drives the batch with ``_fetch_file`` replaced (no
    network), and checks the four promises the callers lean on: the per-file
    reasons come back lined up with the files asked for, every file is fetched
    exactly once, a worker's log line is buffered rather than said on its own
    thread (and reaches the widgets only when the caller flushes it), and the
    token is read once on the main thread so no worker asks the session for it.
    """
    print("\n11p. an install fetches an addon's files at once, off the widgets")
    import threading as _th
    api = fresh(store)
    real_fetch, real_job = store._fetch_file, store.job
    try:
        store.job = None
        # one: the reasons line up with the specs and each file is fetched once
        seen: list[str] = []
        lock = _th.Lock()

        def fake_fetch(values, remote, target, folder=None):
            with lock:
                seen.append(remote)
            return "E" if remote.startswith("assets/") else ""
        store._fetch_file = fake_fetch
        specs = [("a.py", Path("/tmp/a")), ("assets/x/a.png", Path("/tmp/a.png")),
                 ("assets/x/b.png", Path("/tmp/b.png")), ("c.py", Path("/tmp/c"))]
        reasons = store._fetch_many({}, specs)
        check(reasons == ["", "E", "E", ""],
              f"the per-file reasons did not line up with the files asked for: {reasons}")
        check(sorted(seen) == sorted(r for r, _t in specs),
              f"a file was fetched more or less than once: {seen}")

        # two: the single-file batch still goes straight through _fetch_file
        seen.clear()
        check(store._fetch_many({}, [("only.py", Path("/tmp/o"))]) == [""],
              "one file was not fetched as one")
        check(seen == ["only.py"], f"a lone file was not handed to _fetch_file: {seen}")

        # three: note() on a worker thread is held off the widgets, then flushed
        api.logs.clear()
        held: list[str] = []
        store._LOCAL.buf = held
        try:
            store.note("a worker's line")
        finally:
            store._LOCAL.buf = None
        check(held == ["a worker's line"],
              f"a worker's log line was not buffered: {held}")
        check("a worker's line" not in api.logs,
              "a buffered line still reached the widgets on its own thread")
        store.note("a main-thread line")
        check("a main-thread line" in api.logs,
              "a line said on the main thread never reached the log")

        # four: the token is read once on the main thread, never by a worker
        store._fetch_file = fake_fetch
        before = api.times("session.git_token")
        store._fetch_many({}, [("p.py", Path("/tmp/p")), ("q.py", Path("/tmp/q")),
                               ("r.py", Path("/tmp/r"))])
        after = api.times("session.git_token")
        check(after - before == 1,
              f"the batch asked the session for a token on more than the main "
              f"thread: {after - before} times for three files")

        # five: a worker's connection is its own, and it is closed on the way out
        main_ident = _th.get_ident()
        ids: dict = {}

        def cap_fetch(values, remote, target, folder=None):
            ids[remote] = _th.get_ident()
            store._http(values)                  # the fetcher this thread will use
            return ""
        store._fetch_file = cap_fetch
        store._fetch_many({}, [("m.py", Path("/tmp/m")), ("n.py", Path("/tmp/n")),
                               ("o.py", Path("/tmp/o"))])
        check(ids and all(ident != main_ident for ident in ids.values()),
              f"a downloaded file was fetched on the main thread: {ids}")
        # The pool is keyed by (token, thread), so a worker never shares the main
        # thread's socket, and each worker drops its own when it finishes - so
        # nothing but main-thread entries (none made here) are left behind.
        left = [k for k in store._fetchers if k[1] != main_ident]
        check(not left,
              f"a worker left its connection in the pool: {left}")
        check(store._http({}) is store._http({}),
              "repeated fetches on one thread did not share a connection")
        store._drop_http()
    finally:
        store._fetch_file = real_fetch
        store.job = real_job
        store._drop_http()


def test_delete_toggle(store: Any) -> None:
    """What the two toggles do to a delete, and what a delete never takes.

    The switch on the store's Settings page is the answer kept for every start,
    the check box beside the source is the answer for this one, and a delete
    still asks before it erases the settings an addon cannot be un-asked for -
    while an addon the store did not install, and this store's own folder, are
    out of it either way.
    """
    print("\n11b. what Delete does with an addon's settings, and who decides")
    api = fresh(store)
    real_dir = store.addons_dir
    real_work = (store.selected_id, store._tidy, store.show_entries, store._reload)
    try:
        with tempfile.TemporaryDirectory() as where:
            folder = Path(where)
            store.addons_dir = lambda: folder
            store._tidy = lambda ident: None
            store.show_entries = lambda: None
            store._reload = lambda: None
            store.selected_id = lambda: "widget"

            def lay_out() -> None:
                """One store install on disk: program, manifest, settings."""
                for item in (folder / "_data" / "widget",
                             folder / "_data" / "addon_store",
                             folder / "_data" / "hand"):
                    item.mkdir(parents=True, exist_ok=True)
                (folder / "widget.exe").write_text("x", encoding="utf-8")
                (folder / "widget.json").write_text(json.dumps(
                    {"id": "widget", store.ORIGIN_KEY: store.STORE_ORIGIN}),
                    encoding="utf-8")
                (folder / "_data" / "widget" / "config.json").write_text(
                    '{"size": 10}', encoding="utf-8")
                (folder / "_data" / "addon_store" / "config.json").write_text(
                    '{"sources": []}', encoding="utf-8")
                (folder / "hand.exe").write_text("x", encoding="utf-8")
                (folder / "hand.json").write_text('{"name": "Hand"}',
                                                  encoding="utf-8")

            def gone(ident: str) -> list[str]:
                return sorted(path.relative_to(folder).as_posix()
                              for path in store._delete_paths(ident))

            # the shipped answer, and the box that overrides it for one session
            lay_out()
            check(store.delete_config_wanted() is True,
                  "Delete was not set to take an addon's settings out of the box")
            check(gone("widget"), "nothing was offered for deletion at all")
            api.calls.clear()
            store.delete_selected()
            asks = [arguments for name, arguments, _n in api.calls
                    if name == "ui.question"]
            check(bool(asks) and "addons/_data/widget/" in str(asks[-1][1]),
                  f"settings were taken without a word first: {asks}")
            check(not (folder / "_data" / "widget").exists()
                  and not (folder / "widget.exe").exists(),
                  "a default delete left the program or its settings behind")
            check("with its settings" in last_status(api),
                  f"the delete did not say what it took: {last_status(api)!r}")

            lay_out()
            store.on_widget(store.TAB, "delcfg", "toggled", {"checked": False})
            check(store.delete_config_wanted() is False,
                  "the check box did not change what Delete is set to do")
            api.calls.clear()
            store.delete_selected()
            check(not [name for name, _a, _n in api.calls if name == "ui.question"],
                  "a delete that touches no settings still asked")
            check((folder / "_data" / "widget" / "config.json").is_file()
                  and not (folder / "widget.exe").exists(),
                  "leaving the settings did not hold them, or the program went too")
            check("left in addons/_data/widget/" in last_status(api),
                  f"the delete did not say what it left: {last_status(api)!r}")

            # the Settings page's own switch, which holds until the box is touched
            lay_out()
            store.session_delete_config = None
            api = fresh(store, {"delete_config": False})
            check(store.delete_config_wanted() is False,
                  "the store's config file does not answer for Delete")
            store.on_widget(store.TAB, "delcfg", "toggled", {"checked": True})
            check(store.delete_config_wanted() is True,
                  "the check box did not overrule the file for this session")
            store.session_delete_config = None
            check(store.delete_config_wanted() is False,
                  "the file's answer did not come back when the box was left alone")
            store.on_config({"addon": "addon_store"})
            check(store.delete_config_wanted() is False,
                  "a config change did not leave the session's override behind")

            # and the two things a store delete has no business touching
            check(gone("addon_store") == [],
                  f"the store's own folder was offered for its own delete: "
                  f"{gone('addon_store')}")
            check(gone("hand") == ["hand.exe"],
                  f"an addon built elsewhere had more than its program offered: "
                  f"{gone('hand')}")
    finally:
        (store.selected_id, store._tidy, store.show_entries,
         store._reload) = real_work
        store.addons_dir = real_dir
        store.session_delete_config = None
        api.timers.cancel_all()
        store.shutdown()


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
    import core.views

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
        check(core.views.relaunch_process(),
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


# --- eleven: the pictures an install brings, and the ones a build can wear ---

def test_the_pictures(store: Any) -> None:
    """A picture that will not arrive is skipped, and a PNG is not handed over raw.

    An install's assets are decoration - every addon draws its own tab mark - so
    one that GitHub will not give out is said and left behind, never a refused
    install. And a store addon's ``<id>icon.png`` is a PNG, which Windows
    PyInstaller only converts when Pillow happens to be installed, so what
    reaches ``--icon`` has to be a file this platform can wear as it stands.
    """
    print("\n14. an asset that will not arrive, and an icon a build can use")
    fresh(store)
    real_fetch, real_job, real_entries = store._fetch_file, store.job, store.entries
    real_maybe, real_finish = store._maybe_test, store._finish
    real_say, real_note = store.say, store.note
    saved_data = os.environ.get("RASCONF_ADDON_DATA")
    words: list = []
    ended: list = []
    reached: list = []
    try:
        with tempfile.TemporaryDirectory() as box:
            data = Path(box) / "data"
            os.environ["RASCONF_ADDON_DATA"] = str(data)
            store.entries = store.parse_catalog(CATALOG)
            hello = store.entries[0]                     # assets and icon, as listed
            check(hello["assets"] and hello["icon"],
                  f"the catalog row used here carries no pictures to fetch: {hello}")
            store.say = lambda text, bad=False: words.append((str(text), bool(bad)))
            store.note = lambda text: words.append((str(text), False))
            store._finish = lambda text, bad=False: ended.append((str(text), bool(bad)))
            store._maybe_test = lambda id_value, source, test_path, then=None: (
                reached.append(
                    (id_value, str(test_path) if test_path else None)))

            def only_the_source(values: dict, remote: str, target: Path,
                                folder: str | None = None) -> str:
                if remote.startswith("assets/"):
                    return "GitHub answered 404"
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text("# addon\n", encoding="utf-8")
                return ""
            store._fetch_file = only_the_source
            store.job = {"verb": "install", "id": "", "source": "",
                         "values": store.source_values(store.MAIN_SOURCE),
                         "queue": [hello], "total": 1, "done": 0, "did": [],
                         "restore": {}, "staged": {}, "waiting": [],
                         "building": {}, "test_ids": [], "test_at": 0,
                         "from_versions": {}, "harness": None, "lines": [],
                         "widened": False, "details": {}}
            store._download_all()
            check(not [pair for pair in ended if pair[1]],
                  f"a picture that would not download refused the install: {ended}")
            check(len(reached) == 1 and reached[0][0] == "hello_store"
                  and str(reached[0][1] or "").endswith("test_hello_store.py"),
                  f"the download phase stopped before the addon's gate: {reached}")
            check((data / "staging" / "hello_store" / "hello_store.py").is_file(),
                  "the addon's own file did not make it into its staging folder")
            check(not (data / "staging" / "hello_store" / "assets").exists(),
                  "an asset that was not fetched was still left in the staging "
                  "folder")
            said = " ".join(text for text, _bad in words)
            check("helloicon.png could not be fetched" in said,
                  f"the missing picture was never said out loud: {said!r}")
            check("wears the mark it draws itself" in said,
                  f"nobody was told the addon still builds without it: {said!r}")

        for text, _bad in words:
            if "could not be fetched" in text or "mark it draws" in text:
                look(text)
    finally:
        store._fetch_file = real_fetch
        store._finish = real_finish
        store._maybe_test = real_maybe
        store.say, store.note = real_say, real_note
        store.job, store.entries = real_job, real_entries
        if saved_data is not None:
            os.environ["RASCONF_ADDON_DATA"] = saved_data
        else:
            os.environ.pop("RASCONF_ADDON_DATA", None)

    # the icon a build is handed, and the PNG -> ICO rewrap that keeps it
    try:
        import struct

        import core.addon as dev
    except Exception as error:                      # noqa: BLE001 - app modules
        look(f"core.addon is not importable here: {error}")
        return
    png_bytes = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                 + struct.pack(">II", 128, 128) + b"\x08\x02\x00\x00\x00"
                 + struct.pack(">I", 0))
    check(dev.png_size(png_bytes) == (128, 128),
          f"a PNG's own header was not read: {dev.png_size(png_bytes)!r}")
    check(dev.png_size(b"not a png at all") is None,
          "something that is not a PNG was measured as one")
    with tempfile.TemporaryDirectory() as box:
        pic = Path(box) / "widgeticon.png"
        pic.write_bytes(png_bytes)
        ico = dev.ico_from_png(pic, Path(box) / "made.ico")
        check(ico is not None and ico.is_file(),
              "one PNG was not rewrapped as an icon at all")
        if ico is not None:
            body = ico.read_bytes()
            head = struct.unpack("<HHH", body[:6])
            entry = struct.unpack("<BBBBHHII", body[6:22])
            check(head == (0, 1, 1),
                  f"the icon's header is not one picture of type icon: {head}")
            check(entry[:4] == (128, 128, 0, 0) and entry[4:6] == (1, 32),
                  f"the icon's directory entry is wrong: {entry}")
            check(entry[6] == len(png_bytes) and entry[7] == 22,
                  f"the icon does not point at the PNG's own bytes: {entry}")
            check(body[22:] == png_bytes, "the icon holds something other than the PNG")
        check(dev.ico_from_png(pic.with_suffix(".missing"),
                               Path(box) / "no.ico") is None,
              "a picture that is not there still made an icon")
        wide = Path(box) / "wide.png"
        wide.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                         + struct.pack(">II", 512, 512) + b"\x08\x02\x00\x00\x00"
                         + struct.pack(">I", 0))
        check(dev.ico_from_png(wide, Path(box) / "wide.ico") is None,
              "a side the icon directory cannot count was packed into one anyway")

        formats = dev.icon_formats()
        check(".ico" in formats if sys.platform == "win32" else True,
              f"Windows is not said to want an icon: {formats}")
        picked = dev.usable_icon(pic)
        check(picked is not None and Path(picked).suffix.lower() in formats,
              f"a build was handed a file this platform cannot wear: {picked!r}")
        if sys.platform == "win32":
            check(Path(picked) != pic and Path(picked).suffix.lower() == ".ico",
                  f"a PNG reached a Windows build as a PNG: {picked!r}")
        else:
            check(Path(picked) == pic,
                  f"a picture that is usable here was remade anyway: {picked!r}")
        check(dev.usable_icon(pic.with_suffix(".missing")) is None,
              "an icon named by the catalog but never downloaded came back as a file")
        check(dev.icon_choice(False) is None,
              "a build with no icon was given one anyway")
        app = dev.icon_choice(True)
        check(app is None or Path(str(app)).suffix.lower() in formats,
              f"the program's own mark was handed over in a shape to fail on: {app!r}")
        if app is not None:
            worn = Path(str(app)).read_bytes()
            if Path(str(app)).suffix.lower() == ".ico":
                # Only a rewrapped icon owes an ICO header; a platform that
                # reads a PNG or ICNS straight hands that over unchanged, and
                # the shape check above already said it is wearable here.
                check(struct.unpack("<HHH", worn[:6]) == (0, 1, 1),
                      f"what a build is told to wear is not an icon: {worn[:6]!r}")
            own = dev.find_asset(dev.ICON_FILES[0])
            look(f"{own.name if own is not None else 'no shipped mark'} "
                 f"{dev.png_size(own.read_bytes()) if own is not None else ''} "
                 f"reaches a build as a {len(worn)} byte {Path(str(app)).suffix}")
        wear = dev.icon_choice(str(pic))
        check(wear is not None and Path(str(wear)).suffix.lower() in formats,
              f"an addon's own PNG was handed over raw: {wear!r}")
        look(f"{sys.platform} wears {formats}; a PNG build icon arrives as "
             f"{Path(str(dev.icon_choice(str(pic)))).name}")


# --- twelve: the picture an install leaves where the app can read it ---

def test_the_kept_picture(store: Any) -> None:
    """The copy of an addon's picture an install keeps beside its settings.

    A program built here carries its assets inside itself and its staging folder
    is thrown away once it lands, so nothing on disk says what an installed addon
    looks like. One copy in the addon's own ``addons/_data/<id>`` answers for the
    app, which searches that folder for a row's mark, and for the addon, to which
    that very folder is handed as ``api.paths.data`` - and it has to be written
    even for an addon that declares no settings form at all, which is the case
    that made the copy's place in ``_describe`` worth checking.
    """
    print("\n15. the picture an install leaves in the addon's data folder")
    real_dir, real_entries, real_note = store.addons_dir, store.entries, store.note
    saved_data = os.environ.get("RASCONF_ADDON_DATA")
    said: list = []
    try:
        with tempfile.TemporaryDirectory() as box:
            os.environ["RASCONF_ADDON_DATA"] = str(Path(box) / "data")
            api = fresh(store)
            store.addons_dir = lambda: Path(box) / "addons"
            store.note = lambda text: said.append(str(text))
            store.entries = store.parse_catalog(CATALOG)
            hello = store.entries[0]
            check(hello["icon"] == store.icon_filename(hello["id"]),
                  f"the catalog row used here does not name the standard picture: "
                  f"{hello['icon']!r}")
            program = store.addons_dir() / hello["id"] / f"{hello['id']}.exe"
            program.parent.mkdir(parents=True)
            assets = store.staged_folder(hello["id"]) / "assets"
            assets.mkdir(parents=True, exist_ok=True)
            kept = store.addon_data_folder(hello["id"]) / hello["icon"]

            # nothing staged and nothing cached: an addon that brought no picture
            store._describe(hello["id"], program, {}, hello)
            check(not kept.exists(),
                  "an install with no picture anywhere wrote one anyway")
            check((program.parent / f"{hello['id']}.json").is_file(),
                  "the manifest beside the program was lost on the way")

            # the staged asset is kept, under the name the rule uses
            (assets / hello["icon"]).write_bytes(b"staged")
            store._describe(hello["id"], program, {}, hello)
            check(kept.is_file() and kept.read_bytes() == b"staged",
                  "the staged asset was not kept beside the addon's settings")

            # an asset the catalog names differently still lands under the rule
            kept.unlink()
            (assets / hello["icon"]).unlink()
            (assets / "odd_name.png").write_bytes(b"odd")
            store._describe(hello["id"], program, {}, {**hello, "icon": "odd_name.png"})
            check(kept.is_file() and kept.read_bytes() == b"odd",
                  "a picture the catalog named otherwise was not kept under the "
                  "standard name")
            (assets / "odd_name.png").unlink()

            # with no asset at all the store's own cached picture answers
            icons = Path(api.paths.data) / "icons"
            icons.mkdir(parents=True, exist_ok=True)
            (icons / f"{hello['id']}.png").write_bytes(b"cached")
            store._describe(hello["id"], program, {}, hello)
            check(kept.is_file() and kept.read_bytes() == b"cached",
                  "the picture the store fetched for its catalog was not kept either")

            # an id that names nothing is left alone, folder and all
            data = store.addons_dir() / "_data"
            before = sorted(one.name for one in data.iterdir())
            store._keep_picture("", hello)
            check(sorted(one.name for one in data.iterdir()) == before,
                  "an id that names nothing still made somewhere for a picture")

            # nothing about a picture stops an install: it is only said
            check(not [text for text in said if "could not be kept" in text],
                  f"a picture that was there was said to have failed: {said}")
    finally:
        store.addons_dir, store.entries, store.note = real_dir, real_entries, real_note
        if saved_data is not None:
            os.environ["RASCONF_ADDON_DATA"] = saved_data
        else:
            os.environ.pop("RASCONF_ADDON_DATA", None)


def test_the_job_view(store: Any) -> None:
    """A job puts its console over the browsing panes and prints its work on it.

    The pane is only there when the built tab says it is: ``tabs.set_widget``
    answers ``False`` for an id the layout does not hold, and a job started on
    such a tab keeps the browsing view up and writes to no console at all. Both
    answers are driven here on the stub api, so the swap, the console's lines,
    the card's name and the bar's share are all seen - and the tab that has no
    pane is seen putting up no card either.
    """
    print("\n5g. the job's console, the card over it, and a tab that has neither")
    real_entries, real_updates = store.entries, store.updates
    real_dir = store.addons_dir
    try:
        with tempfile.TemporaryDirectory() as where:
            folder = Path(where)
            store.addons_dir = lambda: folder
            store.entries = store.parse_catalog(CATALOG)
            store.updates = {}
            entry = store.entry_for("hello_store")

            api = fresh(store)
            api.answer("tabs.set_widget", True)
            api.calls.clear()
            store._begin("install", entry)

            def set_as(ident: str, prop: str) -> Any:
                """The last thing the job wrote to one widget's one property."""
                for name, arguments, _named in reversed(api.calls):
                    if name == "tabs.set_widget" and len(arguments) >= 4 \
                            and arguments[1] == ident and arguments[2] == prop:
                        return arguments[3]
                return None

            check(store.job is not None and store.job.get("pane") is True,
                  f"a tab holding the pane was never told to show it: "
                  f"{store.job}")
            check(set_as("jobpane", "visible") is True
                  and set_as("browse", "visible") is False,
                  f"the job did not put its console over the browsing: "
                  f"{set_as('jobpane', 'visible')!r} / "
                  f"{set_as('browse', 'visible')!r}")
            check(set_as("jobterm", "clear") is True,
                  "the console was started on a screen that still held the "
                  "last job's lines")
            # The job holds both bars while it runs: the window's tab bar, so a
            # build cannot be clicked away from, and this tab's own sub-bar, so
            # the Addon manager page cannot be opened over it.
            check(api.asked("tabs.lock", "locked") is True,
                  "a running job did not hold the window's tab bar: "
                  f"{api.last('tabs.lock')}")
            check(set_as(store.SUBTABS, "locked") is True,
                  "a running job let its own tab's sub-bar be moved")
            check(set_as("jobname", "text") == "Hello from the store",
                  f"the card does not name the addon being installed: "
                  f"{set_as('jobname', 'text')!r}")
            check(isinstance(set_as("jobicon", "image"), str)
                  and set_as("jobicon", "image") != "",
                  f"the card wears no picture of its own: {set_as('jobicon', 'image')!r}")

            # the console starts folded: the card alone is up, and only the
            # card's own button pulls the terminal out from behind it
            check(set_as("jobterm", "visible") is False
                  and set_as("jobdetails", "text") == "Show details",
                  f"the job put its console up unasked: "
                  f"{set_as('jobterm', 'visible')!r} / "
                  f"{set_as('jobdetails', 'text')!r}")
            api.calls.clear()
            store.on_widget(store.TAB, "jobdetails", "clicked", {})
            check(set_as("jobterm", "visible") is True
                  and set_as("jobdetails", "text") == "Hide details",
                  "pressing Show details did not open the console")
            api.calls.clear()
            store.on_widget(store.TAB, "jobdetails", "clicked", {})
            check(set_as("jobterm", "visible") is False
                  and set_as("jobdetails", "text") == "Show details",
                  "pressing Hide details did not fold the console back away")

            # every line the job logs goes onto the console as well, in its own
            # words and with the carriage a terminal needs
            api.calls.clear()
            store.note("building with python")
            check(set_as("jobterm", "write") == "building with python\r\n",
                  f"the console did not get the line the log got: "
                  f"{set_as('jobterm', 'write')!r}")

            # and a step is on the card and nowhere else: the status line keeps
            # the job's beginning and its ending, and nothing floats over the
            # window to be drawn on
            api.calls.clear()
            store._progress("compiling hello_store...", 0.5)
            check(set_as("jobstep", "text") == "compiling hello_store...",
                  f"the card never said what the job is doing: "
                  f"{set_as('jobstep', 'text')!r}")
            check(set_as("jobbar", "value") == 50,
                  f"the card's bar is not at the batch's half: "
                  f"{set_as('jobbar', 'value')!r}")
            check(not api.called("ui.progress_notice"),
                  "a step went out to the floating notice that was removed")
            check(last_status(api) == "",
                  f"a live step rewrote the page's status line: "
                  f"{last_status(api)!r}")

            # a long step is cut for the card, since all of it is behind it
            api.calls.clear()
            long_step = "running " + "a very long line " * 8
            store._progress(long_step, 0.5)
            step = str(set_as("jobstep", "text"))
            check(len(step) == store._JOB_STEP_LEN and step.endswith("\u2026"),
                  f"a long step was written to the card as it came: {step!r}")
            check(long_step not in api.logs,
                  "the cut step was also said in full somewhere the card is not")

            # the job ends the way it began: the browsing panes back, the log kept,
            # and both bars let go before anything else is touched
            api.calls.clear()
            store._finish("installed hello_store")
            check(set_as("jobpane", "visible") is False
                  and set_as("browse", "visible") is True,
                  "the job kept the console up after it ended")
            check(api.asked("tabs.lock", "locked") is False,
                  "a finished job still holds the window's tab bar")
            check(set_as(store.SUBTABS, "locked") is False,
                  "a finished job still holds this tab's own sub-bar")
            check(store.job is None and not store.working,
                  "the finished job is still holding the tab")

            # a tab built without the pane: no console, no card, no stopped job
            api2 = fresh(store)
            api2.calls.clear()
            store._begin("install", entry)
            check(store.job is not None and store.job.get("pane") is False,
                  f"a tab with no pane claimed it showed one: {store.job}")
            check(any(name == "tabs.set_widget" and len(arguments) >= 3
                      and arguments[1] == "jobpane"
                      for name, arguments, _n in api2.calls),
                  "a job never asked the tab whether it has the pane at all")
            store.note("building with python")
            store._progress("compiling hello_store...", 0.5)
            check(not [arguments for name, arguments, _n in api2.calls
                       if name == "tabs.set_widget" and len(arguments) >= 3
                       and arguments[1] in ("jobterm", "jobcard", "jobicon",
                                            "jobname", "jobstep", "jobbar",
                                            "jobdetails")],
                  "a job with no pane wrote to the console and card that are "
                  "not there")
            check(not api2.called("tabs.lock"),
                  "a job nobody can see held the window's tab bar anyway")
            check(not [arguments for name, arguments, _n in api2.calls
                       if name == "tabs.set_widget" and len(arguments) >= 3
                       and arguments[1] == store.SUBTABS
                       and arguments[2] == "locked"],
                  "a job with no pane locked the sub-bar it is not over")
            store._finish("installed hello_store")
            check(store.job is None and not store.working,
                  "a job on a paneless tab could not end")
    finally:
        store.entries, store.updates, store.addons_dir = (real_entries,
                                                          real_updates, real_dir)
        store.job, store.working = None, False


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
    test_the_pane(store)
    test_installed(store)
    test_the_updates(store)
    test_the_switches(store)
    test_a_start_that_dies(store)
    test_the_batch_order(store)
    test_the_job_view(store)
    test_the_sources(store)
    test_bundled_assets(store)
    test_the_generator(store)
    test_the_gate(store)
    test_the_harness(store)
    test_the_interpreter(store)
    test_the_pip_fallback(store)
    test_the_install(store)
    test_the_parallel_download(store)
    test_delete_toggle(store)
    test_the_manifest(store)
    test_the_child_env()
    test_the_pictures(store)
    test_the_kept_picture(store)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    STATUS = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The verdict is printed: go out now, without walking Qt through the
    # interpreter's shutdown. Its destructors run there in whatever order the
    # garbage collection picks, and an offscreen widget suite has been seen to
    # raise an access violation on a busy machine after all checks passed -
    # which is how a green run turns into a 0xC0000005.
    os._exit(STATUS)
