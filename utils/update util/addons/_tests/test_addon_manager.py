#!/usr/bin/env python3
"""A test for the Addon manager - the store's second page, and the app behind it.

The page is two things at once: a tab spec the store draws, and a set of records
the application owns. Nothing on the page is the store's own opinion about what
is installed - every row, every switch and every button is one call to the app,
and the app writes the same profile record that Settings and the tab bar read. So
this test drives both halves for real: the store's module on a recording stub api,
and the app's classes - ``AddonManager``, ``Addon``, ``AddonAPI`` - against a small
fake window, offscreen, with no device and no Desktop.

What is looked at:

* the spec: the store's tab is now two pages under one sub-bar, the first page
  still carries every widget id it always had, and the second is the manager's
  table, its six switches, its seven buttons and its Addon Dev check;
* what the page shows: the addons that did not come inside the program and
  nothing else, one row each, in the order the app named them, with a tick that
  is that addon's own enable switch and a State cell that says where its tabs
  are;
* what a picked row fills in: every switch read from the row, the force-popout
  one greyed - never hidden - for an addon whose manifest spoke or which came with
  the program, a transport switch hidden when the manifest never named that
  transport, and the own-connection one greyed for an addon that has no choice to
  make;
* what a press does: each of the six switches, the row's tick and the dev-tab
  check go through the app's api by name, a refusal is said on the status line
  rather than swallowed, and the table is painted again either way;
* the settings button: the form is built from the keys the app says the addon
  declared, the dialog's answers are converted back to the shapes the file keeps,
  a dialog closed with Cancel writes nothing, and an addon with no keys and no
  file is told there is nothing to edit rather than shown an empty box;
* the app's own answers: ``api.session.addons()`` and ``addon_card()`` shape,
  the record the switches write, ``set_app_setting`` only moving the key on its
  whitelist, ``api.session.git_token()`` following the per-addon switch, the two
  transport gates and the own-connection choice reaching the records they own,
  ``addon_config`` reading what an addon declared, Settings' **Open addon manager**
  button starting the store and asking for its second page; and
* the trip a tab makes: one page out of the bar through the mark it wears, the
  way back through the window's own button, and the two names on the real window
  that let either happen.

Run it::

    python addons/_tests/test_addon_manager.py
"""

from __future__ import annotations

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
from core.addon import Addon, AddonManager, SHIPPED_ADDON_IDS  # noqa: E402
from addon_api import AddonAPI                               # noqa: E402

SOURCE = ROOT / "addons" / "addon_store.py"
FAILURES: list[str] = []
_QT_APP: Any = None

#: One addon that came inside the program, one dropped in by hand, one the store
#: installed and popped out, one that failed to start, and one that left the
#: connection to be chosen - the five shapes the page has to draw, in the shape
#: ``api.session.addons()`` answers them.
ROWS = [
    {"id": "berry", "name": "Berry", "version": "2.0.0", "author": "the program",
     "kind": "python", "installed": True, "enabled": True, "running": True,
     "bundled": True, "error": "", "requires": ["ssh", "sftp"],
     "connection": "app", "own_connection": False, "ssh_allowed": True,
     "sftp_allowed": True, "own_switchable": False, "popout_declared": "",
     "popout_switch": False, "popout_switchable": False, "popout": False,
     "git_token": False, "tabs": ["Terminal"], "window_open": False,
     "shortcut": "", "settings_page": True},
    {"id": "hello", "name": "Hello", "version": "1.0", "author": "fligma",
     "kind": "python", "installed": True, "enabled": True, "running": True,
     "bundled": False, "error": "", "requires": ["sftp"], "connection": "own",
     "own_connection": True, "ssh_allowed": True, "sftp_allowed": True,
     "own_switchable": False, "popout_declared": "", "popout_switch": False,
     "popout_switchable": True, "popout": False, "git_token": True,
     "tabs": ["Home"], "window_open": False, "shortcut": "",
     "settings_page": False},
    {"id": "floaty", "name": "Floaty", "version": "0.2", "author": "someone",
     "kind": "process", "installed": True, "enabled": True, "running": True,
     "bundled": False, "error": "", "requires": [], "connection": "none",
     "own_connection": False, "ssh_allowed": True, "sftp_allowed": True,
     "own_switchable": False, "popout_declared": "", "popout_switch": True,
     "popout_switchable": True, "popout": True, "git_token": False,
     "tabs": ["Home", "Notes"], "window_open": True,
     "shortcut": r"C:\Users\me\Desktop\Berry - floaty.lnk",
     "settings_page": False},
    {"id": "broken", "name": "Broken", "version": "", "author": "",
     "kind": "python", "installed": True, "enabled": True, "running": False,
     "bundled": False, "error": "no module named paramiko", "requires": ["ssh"],
     "connection": "app", "own_connection": False, "ssh_allowed": True,
     "sftp_allowed": True, "own_switchable": False, "popout_declared": "always",
     "popout_switch": False, "popout_switchable": False, "popout": True,
     "git_token": False, "tabs": [], "window_open": False, "shortcut": "",
     "settings_page": False},
    {"id": "choicey", "name": "Choicey", "version": "1.1", "author": "both",
     "kind": "python", "installed": True, "enabled": True, "running": False,
     "bundled": False, "error": "", "requires": ["ssh", "sftp"],
     "connection": "both", "own_connection": False, "ssh_allowed": True,
     "sftp_allowed": False, "own_switchable": True, "popout_declared": "",
     "popout_switch": False, "popout_switchable": True, "popout": False,
     "git_token": False, "tabs": ["Home"], "window_open": False, "shortcut": "",
     "settings_page": False},
]

#: What ``api.session.addon_config`` answers for an addon that declared keys of its
#: own: all six kinds a ``fields.json`` may name, the values the file holds now, and
#: the defaults behind them.
CONFIG_CARD = {
    "id": "hello", "title": "Hello settings",
    "path": r"C:\app\addons\_data\hello\config.json", "exists": True,
    "defaults": {"greeting": "Hi", "port": 22, "loud": False,
                 "mode": "loud", "paths": [], "notes": []},
    "fields": [
        {"key": "greeting", "label": "Greeting", "kind": "text"},
        {"key": "port", "label": "Port", "kind": "number",
         "minimum": 1, "maximum": 65535},
        {"key": "loud", "label": "Be loud", "kind": "bool"},
        {"key": "mode", "label": "Mode", "kind": "choice",
         "choices": ["loud", "quiet"]},
        {"key": "paths", "label": "Paths", "kind": "list"},
        {"key": "notes", "label": "Notes", "kind": "multiline",
         "hint": "one per line"}],
    "values": {"greeting": "Hi", "port": 22, "loud": True, "mode": "loud",
               "paths": ["a", "b"], "notes": ["one", "two"]},
}

#: What the settings dialog is answered with - the text each control holds.
CONFIG_REPLY = {"greeting": "  Hey  ", "port": 8022, "loud": False,
               "mode": "quiet", "paths": "a, b, c",
               "notes": "one\ntwo\nthree\n"}

#: The same answers back in the shapes the config file keeps.
CONFIG_WRITTEN = {"greeting": "Hey", "port": 8022, "loud": False,
                  "mode": "quiet", "paths": ["a", "b", "c"],
                  "notes": ["one", "two", "three"]}

#: Every widget id the store's own page has always carried. The subtab wrapper is
#: new; none of these is allowed to have moved, or the jobs, the pane and the log
#: the store's other tests drive would be reaching at nothing.
PAGE_ONE_IDS = ("status", "source", "delcfg", "showlog", "filter", "count",
                "logpane", "list", "browse", "dicon", "dtitle", "dby", "dstate",
                "dabout", "dfacts", "jobpane", "jobterm", "jobcard", "jobicon",
                "jobname", "jobstep", "jobbar", "log",
                "refresh", "install", "update", "delete", "sources")

MANAGER_IDS = ("managerstatus", "managercount", "devtab", "managed",
               "managerpane", "mg_enable", "mg_popout", "mg_token",
               "mg_ssh", "mg_sftp", "mg_own", "mg_note",
               "mg_window", "mg_restart", "mg_folder", "mg_data",
               "mg_config", "mg_shortcut", "mg_delete")


def check(condition: object, text: str) -> None:
    """One thing the page has to do; all of them are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


def ids_in(spec: Any) -> list[str]:
    """Every widget id one page of the spec carries, in the order it is drawn."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("id"):
                found.append(str(node["id"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(spec)
    return found


def spec_of(spec: Any, ident: str) -> Any:
    """The one leaf of a tab spec that carries this widget id, or nothing."""
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


# --- the store, on a stub api ---

def fresh(store: Any, rows: list[dict] | None = None,
          answers: dict[str, Any] | None = None) -> Any:
    """The addon on a stub api, its catalog patched so nothing reaches GitHub."""
    store.load_catalog = lambda values: []
    api = addon_client.StubAPI(addon_id="addon_store")
    api.answer("session.addons", list(rows if rows is not None else ROWS))
    for method, value in (answers or {}).items():
        api.answer(method, value)
    store.init(api)
    api.timers.cancel_all()          # kill init's scheduled refresh; tests drive it
    api.calls.clear()                # read only what the page itself asks for
    return api


def prop(api: Any, ident: str, name: str) -> Any:
    """The last value the addon sent to one widget's one property."""
    for where, arguments, _named in reversed(api.calls):
        if where == "tabs.set_widget" and len(arguments) >= 4 \
                and arguments[1] == ident and arguments[2] == name:
            return arguments[3]
    return None


def manager_status(api: Any) -> str:
    return str(prop(api, "managerstatus", "text") or "")


def asked(api: Any, method: str, key: str) -> Any:
    return api.asked(method, key)


def dialog_spec(api: Any) -> dict:
    """The spec the addon last handed to ``api.ui.dialog``, or nothing."""
    entry = api.last("ui.dialog") or (None, ())
    arguments = entry[1] if len(entry) > 1 else ()
    return arguments[0] if arguments and isinstance(arguments[0], dict) else {}


def spec_leaves(spec: dict) -> dict:
    """The leaves of a dialog spec, by the id each one carries."""
    return {str(leaf.get("id")): leaf for leaf in (spec.get("children") or [])
            if isinstance(leaf, dict) and leaf.get("id")}


# --- one: the spec ---

def test_the_spec(store: Any) -> None:
    print("\n1. the store's tab is two pages under one sub-bar")
    spec = store.tab_spec()
    check(spec.get("kind") == "tabs" and spec.get("id") == store.SUBTABS,
          f"the tab is no longer one sub-bar of its own: {spec.get('kind')!r} / "
          f"{spec.get('id')!r}")
    pages = spec.get("children") or []
    check(len(pages) == 2, f"the tab wraps {len(pages)} pages instead of two")
    check([page.get("label") for page in pages]
          == [store.PAGE_ADDONS, store.PAGE_MANAGER],
          f"the two pages are not named for what they are: "
          f"{[page.get('label') for page in pages]}")
    check(ids_in(pages[1]) == ids_in(store.manager_spec()),
          "the second page is not the Addon manager this module builds")
    check(store.MANAGER_PAGE == 1,
          "the page the app is told to open is not the manager's index")

    print("\n2. the store's own page kept every id it ever had")
    first = ids_in(pages[0])
    missing = [one for one in PAGE_ONE_IDS if one not in first]
    check(not missing, f"the store's page lost widgets out from under its jobs: "
                       f"{missing}")
    check(first.count("list") == 1 and first.count("log") == 1,
          f"the list or the log is drawn twice: {first}")
    check("managed" not in first,
          "the manager's table is on the store's page, where the ids would clash")

    print("\n3. the manager's page is a table, six switches and seven buttons")
    second = ids_in(pages[1])
    missing = [one for one in MANAGER_IDS if one not in second]
    check(not missing, f"the Addon manager page is missing its own controls: "
                       f"{missing}")
    table = spec_of(pages[1], "managed") or {}
    check(table.get("kind") == "table"
          and list(table.get("columns") or []) == list(store.MANAGER_COLUMNS)
          and table.get("checkbox") == 0 and table.get("multi") is True
          and table.get("on") == ["selected", "toggled"],
          f"the manager's table is not the picked-and-ticked one it promised: "
          f"{table}")
    for ident, label in (("mg_enable", "Run it"), ("mg_popout", "Force popout"),
                         ("mg_token", "Use the global git token"),
                         ("mg_ssh", "Use SSH"), ("mg_sftp", "Use SFTP"),
                         ("mg_own", "Open a connection of its own")):
        leaf = spec_of(pages[1], ident) or {}
        check(leaf.get("kind") == "check" and leaf.get("label") == label
              and leaf.get("on") == ["toggled"],
              f"the {ident} switch is not a check that reports being moved: {leaf}")
    for ident in ("mg_ssh", "mg_sftp"):
        check((spec_of(pages[1], ident) or {}).get("visible") is False,
              f"{ident} is drawn before a row says the addon asked for that "
              f"transport")
    check((spec_of(pages[1], "mg_own") or {}).get("visible") is None,
          "the own-connection switch is hidden rather than greyed when it is not "
          "this addon's to move")
    buttons = spec_of(pages[1], "mg_window")
    check(isinstance(buttons, dict), "the manager's buttons are not in its spec")
    for ident, _text in store.MANAGER_BUTTONS:
        check(spec_of(pages[1], ident) is not None,
              f"the {ident} button was promised and never drawn")
    check((spec_of(pages[1], "devtab") or {}).get("on") == ["toggled"],
          "the Addon Dev check reports nothing when it is moved")
    pane = spec_of(pages[1], store.MANAGER_PANE) or {}
    check(pane.get("kind") == "splitter" and pane.get("vertical") is True
          and list(pane.get("sizes") or []) == list(store.MANAGER_SHARE),
          f"the table and the switches under it are not in a draggable split: "
          f"{pane}")

    print("\n4. and Qt really builds both pages out of that spec")
    try:
        import addon_api
        from PyQt6.QtWidgets import (QApplication, QCheckBox, QPlainTextEdit,
                                     QTabWidget, QTreeWidget)

        global _QT_APP
        _QT_APP = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the pages are only read as text: {error}")
        return
    body, registry = addon_api.build_spec(spec)
    bar = registry.get(store.SUBTABS)
    check(isinstance(bar, QTabWidget),
          f"the sub-bar the spec names is not a tab widget: {bar!r}")
    if isinstance(bar, QTabWidget):
        check(bar.count() == 2
              and [bar.tabText(index) for index in range(bar.count())]
              == [store.PAGE_ADDONS, store.PAGE_MANAGER],
              f"the sub-bar does not carry the two pages: {bar.count()}")
    for ident in ("list", "managed"):
        check(isinstance(registry.get(ident), QTreeWidget),
              f"the page built no table called {ident}: {registry.get(ident)!r}")
    check(isinstance(registry.get("log"), QPlainTextEdit),
          f"the job's log is not the folded box it was said to be: "
          f"{registry.get('log')!r}")
    for ident in store.MANAGER_SWITCHES + ("devtab",):
        check(isinstance(registry.get(ident), QCheckBox),
              f"{ident} is not a check box a switch can be drawn on: "
              f"{registry.get(ident)!r}")
    managed = registry.get("managed")
    if isinstance(managed, QTreeWidget):
        labels = [managed.headerItem().text(column)
                  for column in range(managed.columnCount())]
        check(labels == list(store.MANAGER_COLUMNS),
              f"the manager's columns are not in the promised order: {labels}")


# --- two: what the page lists ---

def test_the_list(store: Any) -> None:
    print("\n5. the page lists what did not come inside the program, and only that")
    api = fresh(store)
    store.refresh_manager()
    rows = prop(api, "managed", "rows") or []
    captions = [row["cells"][0] for row in rows]
    check(captions == ["Hello", "Floaty", "Broken", "Choicey"],
          f"the table is not the addons the program did not ship: {captions}")
    check(all(not row.get("settings_page") for row in store.managed),
          "a shipped addon was taken into the list the switches are for")
    check(store.manager_caption({"id": "berry", "name": "Berry"}) == "Berry",
          "a caption was built for an addon the page does not carry")
    check(str(prop(api, "managercount", "text")) == "4 managed",
          f"the count chip says something else: {prop(api, 'managercount', 'text')!r}")

    print("\n6. a row says what it is, where its tabs are, and whether it is on")
    states = [row["cells"][3] for row in rows]
    check(states[0] == "running",
          f"a running addon is not said to be running: {states[0]!r}")
    check("\u25b8 in its window" in states[1] and "running" in states[1],
          f"an addon in a window of its own is not said to be: {states[1]!r}")
    check("failed to start" in states[2],
          f"an addon that would not start is not said so: {states[2]!r}")
    check(states[3] == "not running",
          f"an installed addon that is switched on but idle is not said so: "
          f"{states[3]!r}")
    check([row["check"] for row in rows] == [True, True, True, True],
          f"a row's tick is not its addon's enable switch: "
          f"{[row['check'] for row in rows]}")
    check(rows[2]["cells"][2] == "-" and rows[1]["cells"][2] == "someone",
          f"an author was dropped or invented: {[row['cells'][2] for row in rows]}")
    check(all(row.get("color") for row in rows),
          "a row was painted with no colour of its own")

    print("\n7. with nothing installed the page says so, and asks for nothing")
    api = fresh(store, rows=[ROWS[0]])
    store.refresh_manager()
    check(not (prop(api, "managed", "rows") or []),
          "a profile that ships everything still drew a row")
    check(str(prop(api, "managercount", "text")) == "nothing installed",
          f"an empty list was counted as something: "
          f"{prop(api, 'managercount', 'text')!r}")
    api.calls.clear()
    store.on_widget(store.TAB, "mg_restart", "clicked", {})
    check("pick an addon" in manager_status(api),
          f"a button with no row picked did not say so: {manager_status(api)!r}")

    print("\n8. the Addon Dev check in the header reads the app's own answer")
    api = fresh(store, answers={"session.app_setting": False})
    store.refresh_manager()
    check(prop(api, "devtab", "checked") is False,
          "the profile says the Addon Dev tab is hidden and the check says otherwise")
    api = fresh(store)
    store.refresh_manager()
    check(prop(api, "devtab", "checked") is True,
          "an app that names no answer leaves a profile that has never set one, "
          "which is shown")


# --- three: what a press does ---

def test_the_presses(store: Any) -> None:
    print("\n9. picking a row fills in its six switches and its buttons")
    api = fresh(store)
    store.refresh_manager()
    api.calls.clear()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Floaty"})
    check(prop(api, "mg_enable", "checked") is True
          and prop(api, "mg_popout", "checked") is True
          and prop(api, "mg_token", "checked") is False,
          f"the switches do not read the row: {prop(api, 'mg_enable', 'checked')} / "
          f"{prop(api, 'mg_popout', 'checked')} / {prop(api, 'mg_token', 'checked')}")
    check(prop(api, "mg_popout", "enabled") is True,
          "a silent addon was offered no force-popout switch")
    check(prop(api, "mg_window", "text") == "Raise window"
          and prop(api, "mg_shortcut", "text") == "Remove shortcut",
          f"the buttons are not named for what they would do: "
          f"{prop(api, 'mg_window', 'text')!r} / {prop(api, 'mg_shortcut', 'text')!r}")
    check("window of their own" in str(prop(api, "mg_note", "text")),
          f"the note does not say where its tabs are: {prop(api, 'mg_note', 'text')!r}")
    check("Berry - floaty.lnk" in str(prop(api, "mg_note", "text")),
          "the note says nothing about the Desktop link that is already there")
    check(prop(api, "mg_ssh", "visible") is False
          and prop(api, "mg_sftp", "visible") is False,
          "a gate was shown for an addon whose manifest asked for no transport")
    check(prop(api, "mg_own", "enabled") is False,
          "an addon that declared no choice of connection was offered the switch")

    print("\n10. a switch the addon cannot be given is greyed, and said why")
    api.calls.clear()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Broken"})
    check(prop(api, "mg_popout", "enabled") is False,
          "an addon whose manifest spoke was offered the app's switch anyway")
    check("manifest" in str(prop(api, "mg_note", "text")),
          f"the greyed switch was not explained: {prop(api, 'mg_note', 'text')!r}")
    check(prop(api, "mg_ssh", "visible") is True
          and prop(api, "mg_ssh", "enabled") is True,
          "the one transport this addon did ask for was left without a gate")
    check(prop(api, "mg_sftp", "visible") is False
          and prop(api, "mg_sftp", "enabled") is False,
          "a transport the manifest never named was drawn as a switch anyway")
    check(prop(api, "mg_own", "enabled") is False,
          'an addon that said "connection": "app" was offered the own-connection '
          "switch")
    check("over SSH" in str(prop(api, "mg_note", "text"))
          and "not here to choose" in str(prop(api, "mg_note", "text")),
          f"the note does not say what the gates gate: "
          f"{prop(api, 'mg_note', 'text')!r}")

    print("\n11. each of the app's own three switches goes through the app, by name")
    api = fresh(store)
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Hello"})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_enable", "toggled", {"checked": False})
    check(asked(api, "session.set_addon_running", "addon_id") == "hello"
          and asked(api, "session.set_addon_running", "running") is False,
          f"the enable switch did not reach the app for this addon: "
          f"{api.last('session.set_addon_running')}")
    store.on_widget(store.TAB, "mg_popout", "toggled", {"checked": True})
    check(asked(api, "session.set_addon_popout", "addon_id") == "hello"
          and asked(api, "session.set_addon_popout", "on") is True,
          "the force-popout switch never reached the app")
    store.on_widget(store.TAB, "mg_token", "toggled", {"checked": False})
    check(asked(api, "session.set_addon_git_token", "addon_id") == "hello"
          and asked(api, "session.set_addon_git_token", "on") is False,
          "the token switch never reached the app")
    check(api.called("tabs.activate"),
          "an addon woken up from this page was left holding the screen")

    print("\n12. the transport gates are the addon's own, named by their transport")
    api = fresh(store, answers={"session.set_addon_transport": True})
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Choicey"})
    check(prop(api, "mg_ssh", "visible") is True
          and prop(api, "mg_sftp", "visible") is True,
          "an addon that asked for both transports was shown neither gate")
    check(prop(api, "mg_ssh", "checked") is True
          and prop(api, "mg_sftp", "checked") is False,
          f"a gate the record holds shut is drawn open: "
          f"{prop(api, 'mg_ssh', 'checked')} / {prop(api, 'mg_sftp', 'checked')}")
    api.calls.clear()
    store.on_widget(store.TAB, "mg_sftp", "toggled", {"checked": True})
    check(asked(api, "session.set_addon_transport", "addon_id") == "choicey"
          and asked(api, "session.set_addon_transport", "transport") == "sftp"
          and asked(api, "session.set_addon_transport", "on") is True,
          f"the SFTP gate reached nothing, or the wrong addon: "
          f"{api.last('session.set_addon_transport')}")
    check("may use SFTP again" in manager_status(api),
          f"an opened gate was not said: {manager_status(api)!r}")
    store.on_widget(store.TAB, "mg_ssh", "toggled", {"checked": False})
    check(asked(api, "session.set_addon_transport", "transport") == "ssh"
          and asked(api, "session.set_addon_transport", "on") is False,
          "the SSH gate went somewhere other than the app's record")
    check("is refused SSH" in manager_status(api),
          f"a shut gate was not said: {manager_status(api)!r}")

    print("\n13. a gate the app will not move is said, and the table painted again")
    api.answer("session.set_addon_transport", False)
    api.calls.clear()
    store.on_widget(store.TAB, "mg_sftp", "toggled", {"checked": True})
    check("has no SFTP switch to move" in manager_status(api),
          f"a refused gate went quietly: {manager_status(api)!r}")
    check(prop(api, "managed", "rows") is not None,
          "the table was left standing after a gate was refused")

    print("\n14. the own-connection switch belongs to the addon that may ride either")
    api = fresh(store, answers={"session.set_addon_own_connection": True})
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Choicey"})
    check(prop(api, "mg_own", "enabled") is True
          and prop(api, "mg_own", "checked") is False,
          f"the choice a 'both' addon has not taken is not drawn as its own: "
          f"{prop(api, 'mg_own', 'enabled')} / {prop(api, 'mg_own', 'checked')}")
    check("either connection" in str(prop(api, "mg_note", "text")),
          f"the switch that is the addon's was not explained: "
          f"{prop(api, 'mg_note', 'text')!r}")
    api.calls.clear()
    store.on_widget(store.TAB, "mg_own", "toggled", {"checked": True})
    check(asked(api, "session.set_addon_own_connection", "addon_id") == "choicey"
          and asked(api, "session.set_addon_own_connection", "on") is True,
          f"the own-connection switch never reached the app: "
          f"{api.last('session.set_addon_own_connection')}")
    check("connection of its own" in manager_status(api),
          f"a fresh connection was not said: {manager_status(api)!r}")
    api.answer("session.set_addon_own_connection", False)
    api.calls.clear()
    store.on_widget(store.TAB, "mg_own", "toggled", {"checked": False})
    check("no own-connection switch" in manager_status(api),
          f"a refused connection went quietly: {manager_status(api)!r}")
    check(prop(api, "managed", "rows") is not None,
          "the table was not painted again after the refusal")

    print("\n15. the row's tick is the same switch as the one under the table")
    api = fresh(store)
    store.refresh_manager()
    api.calls.clear()
    store.on_widget(store.TAB, "managed", "toggled", {"text": "Hello",
                                                      "checked": False})
    check(asked(api, "session.set_addon_running", "addon_id") == "hello"
          and asked(api, "session.set_addon_running", "running") is False,
          "the row's tick went somewhere other than the app's enable switch")
    check(prop(api, "mg_popout", "checked") is not None,
          "ticking a row left the switches under it standing for nothing")

    print("\n16. a switch the app refuses is said, and the table is painted again")
    api = fresh(store)
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Floaty"})
    api.answer("session.set_addon_popout", False)
    api.calls.clear()
    store.on_widget(store.TAB, "mg_popout", "toggled", {"checked": False})
    check("no force-popout switch" in manager_status(api),
          f"a refused switch went quietly: {manager_status(api)!r}")
    check(prop(api, "managed", "rows") is not None,
          "the table was not painted again after the refusal")

    print("\n17. Edit settings opens the form the addon declared, and writes what "
          "is typed into it")
    api = fresh(store, answers={"session.addon_config": CONFIG_CARD,
                                "ui.dialog": CONFIG_REPLY,
                                "session.set_addon_config": CONFIG_WRITTEN})
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Hello"})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    check(asked(api, "session.addon_config", "addon_id") == "hello",
          "the settings were asked for somebody else than the picked addon")
    spec = dialog_spec(api)
    check(spec.get("kind") == "form",
          f"a declared form was not asked for as one: {spec.get('kind')!r}")
    leaves = spec_leaves(spec)
    check(list(leaves) == [str(field["key"]) for field in CONFIG_CARD["fields"]],
          f"the form is not the addon's own keys, in its own order: {list(leaves)}")
    check(api.asked("ui.dialog", "title") == "Hello settings",
          f"the dialog is not titled what the app called it: "
          f"{api.asked('ui.dialog', 'title')!r}")
    for key, want in (("greeting", "line"), ("port", "spin"), ("loud", "check"),
                      ("mode", "combo"), ("paths", "line"), ("notes", "multi")):
        check((leaves.get(key) or {}).get("kind") == want,
              f"a {key} is not edited with a {want}: {(leaves.get(key) or {})}")
    check((leaves["port"]).get("minimum") == 1
          and leaves["port"].get("maximum") == 65535
          and leaves["port"].get("value") == 22,
          f"the number keeps no range or no starting value: {leaves['port']}")
    check(leaves["loud"].get("checked") is True and "value" not in leaves["loud"],
          f"the tick is not drawn from the file: {leaves['loud']}")
    check(leaves["mode"].get("items") == ["loud", "quiet"]
          and leaves["mode"].get("value") == "loud",
          f"the choice was not given the entries it declared: {leaves['mode']}")
    check(leaves["paths"].get("placeholder") == "comma separated"
          and leaves["paths"].get("value") == "a, b",
          f"the list is not shown as the words it keeps: {leaves['paths']}")
    check(leaves["notes"].get("value") == "one\ntwo"
          and leaves["notes"].get("tooltip") == "one per line",
          f"the multiline lost its lines or its hint: {leaves['notes']}")
    check(asked(api, "session.set_addon_config", "addon_id") == "hello"
          and asked(api, "session.set_addon_config", "values") == CONFIG_WRITTEN,
          f"what was typed did not come back as the file's own shapes: "
          f"{api.last('session.set_addon_config')}")
    check("wrote hello's settings" in manager_status(api)
          and "hello\\config.json" in manager_status(api),
          f"a save was not said where it went: {manager_status(api)!r}")

    print("\n18. a dialog nobody answers writes nothing, and an addon with "
          "nothing set is told so")
    api = fresh(store, answers={"session.addon_config": CONFIG_CARD})
    store.refresh_manager()
    store.on_widget(store.TAB, "managed", "selected", {"text": "Hello"})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    check(api.called("ui.dialog") and not api.called("session.set_addon_config"),
          "a dialog closed with Cancel was treated as an answer")
    check("left as they were" in manager_status(api),
          f"a cancelled save said nothing: {manager_status(api)!r}")
    api.answer("session.addon_config", {**CONFIG_CARD, "fields": [],
                                        "exists": False, "values": {}})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    check(not api.called("ui.dialog"),
          "an empty box was shown for an addon that declared no keys and keeps "
          "no file")
    check("nothing to edit" in manager_status(api),
          f"the addon with nothing to set was not told so: "
          f"{manager_status(api)!r}")
    api.answer("session.addon_config", {**CONFIG_CARD, "fields": [],
                                        "exists": True, "values": {"keep": 1}})
    api.answer("ui.dialog", {store.CONFIG_RAW: '{"keep": 2}'})
    api.answer("session.set_addon_config", {"keep": 2})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    spec = dialog_spec(api)
    leaves = spec_leaves(spec)
    check(spec.get("kind") == "column" and store.CONFIG_RAW in leaves
          and leaves[store.CONFIG_RAW].get("kind") == "multi"
          and '"keep": 1' in str(leaves[store.CONFIG_RAW].get("value")),
          f"an undeclared file was not offered as its own JSON: {spec}")
    check(asked(api, "session.set_addon_config", "values") == {"keep": 2},
          f"the JSON was not written back as it was read: "
          f"{api.last('session.set_addon_config')}")
    api.answer("ui.dialog", {store.CONFIG_RAW: "not json at all"})
    api.calls.clear()
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    check(not api.called("session.set_addon_config"),
          "a text that is not JSON was written anyway")
    check("not one JSON object" in manager_status(api),
          f"bad JSON was refused quietly: {manager_status(api)!r}")
    api.answer("ui.dialog", {store.CONFIG_RAW: "[1, 2]"})
    store.on_widget(store.TAB, "mg_config", "clicked", {})
    check(not api.called("session.set_addon_config"),
          "a JSON list was taken for a settings file")
    check("one JSON object" in manager_status(api),
          f"the wrong shape was not said: {manager_status(api)!r}")

    print("\n19. the dev-tab check writes the one app setting it may")
    api = fresh(store)
    store.refresh_manager()
    api.calls.clear()
    store.on_widget(store.TAB, "devtab", "toggled", {"checked": False})
    check(asked(api, "session.set_app_setting", "key") == "addon_dev_enabled"
          and asked(api, "session.set_app_setting", "value") is False,
          f"the Addon Dev check did not write its own key: "
          f"{api.last('session.set_app_setting')}")
    check(prop(api, "devtab", "checked") is not None,
          "the check was left standing where it was clicked rather than as the "
          "app answers")

    print("\n20. the app that will not answer is reported, not guessed past")
    api = fresh(store)

    def boom(**_kwargs: Any) -> None:
        raise RuntimeError("the app is not there")

    api.session.addons = boom                       # a real failure, not a stub
    store.refresh_manager()
    check("would not say" in manager_status(api),
          f"a table that could not be read said nothing: {manager_status(api)!r}")


# --- four: the app behind the page ---

class FakeWindow:
    """The main window as the api sees it: a bar, a config and two counters."""

    is_main_window = True
    can_dock_tabs = True

    def __init__(self, **settings: Any) -> None:
        from PyQt6.QtWidgets import QTabWidget

        self.app_config: dict = {"addons": {}, "git_token": "", **settings}
        self.tabs = QTabWidget()
        self.saved = 0
        self.logs: list[str] = []
        self.dev_set: list[Any] = []
        self._pinned_pages: list[Any] = []
        self._pinned_icon_only: dict[Any, bool] = {}

    def log(self, text: Any) -> None:
        self.logs.append(str(text))

    def save_app_config(self, _value: Any = None) -> None:
        self.saved += 1

    def sync_tab_transport_dots(self) -> None:
        pass

    def pin_tab(self, page: Any, label: str = "", icon_only: bool = True) -> bool:
        return True

    def unpin_tab(self, page: Any) -> None:
        pass

    def set_addon_dev_enabled(self, on: Any) -> None:
        """The window's own answer: write it and show or hide the tab at once."""
        self.dev_set.append(on)
        self.app_config["addon_dev_enabled"] = bool(on)
        self.save_app_config()

    def add(self, name: str) -> Any:
        from PyQt6.QtWidgets import QWidget

        page = QWidget()
        self.tabs.addTab(page, name)
        return page

    def names(self) -> list[str]:
        return [str(self.tabs.tabText(index)) for index in range(self.tabs.count())]


def make_addon(name: str, *, builtin: bool = False) -> Addon:
    """One discovered addon, in the machine's temp folder so nothing of the app is read."""
    home = Path(tempfile.gettempdir())
    addon = Addon(name, home / f"{name}.py", home, "python", builtin=builtin)
    addon.origin = ""
    return addon


def world(*, settings: dict | None = None,
          names: tuple[str, ...] = ("hello", "berry")) -> tuple[Any, Any, dict]:
    """A manager holding one drop-in and one shipped addon, with their tabs in the bar."""
    win = FakeWindow(**(settings or {}))
    manager = AddonManager(win)
    addons: dict[str, Addon] = {}
    for name in names:
        addon = make_addon(name, builtin=(name in SHIPPED_ADDON_IDS or name == "berry"))
        addon.enabled = True
        addon.loaded = True
        addon.tabs["Home"] = {"page": win.add(f"{name} Home"), "popped": False}
        addons[name] = addon
    manager.addons = list(addons.values())
    for addon in addons.values():
        manager.apply_record(addon)
    return win, manager, addons


def test_the_rows_from_the_app() -> None:
    print("\n21. what the app answers for the page, and where each key comes from")
    desktop = tempfile.mkdtemp()
    os.environ["BERRY_DESKTOP_DIR"] = desktop
    try:
        win, manager, addons = world()
        api = AddonAPI(manager, addons["hello"])
        rows = api.session.addons()
        by_id = {row["id"]: row for row in rows}
        check(set(by_id) == {"hello", "berry"},
              f"the app did not answer for both addons: {sorted(by_id)}")
        check(by_id["berry"]["settings_page"] is True
              and by_id["hello"]["settings_page"] is False,
              "the shipped addon is not the one Settings keeps, or the drop-in is")
        check(by_id["hello"]["popout_switchable"] is True
              and by_id["berry"]["popout_switchable"] is False,
              f"the switch is offered to the wrong addon: "
              f"{by_id['hello']['popout_switchable']} / "
              f"{by_id['berry']['popout_switchable']}")
        check(by_id["hello"]["tabs"] == ["Home"]
              and by_id["hello"]["window_open"] is False
              and by_id["hello"]["shortcut"] == "",
              f"a row cannot see its own tabs, window or link: {by_id['hello']}")

        print("\n22. the switches the page moves write the profile's record")
        check(api.session.set_addon_popout("hello", True) is True,
              "the app refused the switch it owns")
        record = manager.record_for("hello")
        check(record["popout"] is True and addons["hello"].popout_record is True,
              f"the switch moved without writing the record: {record}")
        check(api.session.addon_popout("hello") is True,
              "the app will not say what it just wrote")
        check(api.session.addons()[0]["popout"] is True,
              "the row the table is drawn from still says the tabs are in the bar")
        check(win.names() == ["berry Home"],
              f"the switch was written and the tabs were left where they were: "
              f"{win.names()}")
        check(manager.windows["hello"].has_page("Home"),
              "the record was written and no window holds the page")
        check(api.session.set_addon_popout("berry", True) is False,
              "a shipped addon was offered the app's switch through the api")
        check(api.session.addon_card("no such addon")["installed"] is False,
              "a card for an id the folder does not hold was not refused plainly")

        print("\n23. the token switch decides what that addon is handed")
        win.app_config["git_token"] = "  secret-token  "
        check(api.session.git_token() == "",
              "an addon with no switch up was handed the profile's token")
        check(api.session.github_headers() == {},
              "an anonymous addon was given Authorization anyway")
        check(api.session.set_addon_git_token("hello", True) is True,
              "the token switch would not be written")
        check(manager.record_for("hello")["git_token"] is True
              and addons["hello"].git_token is True,
              "the token switch moved the addon but not the record")
        check(api.session.git_token() == "secret-token",
              f"the switch was up and the addon still got: {api.session.git_token()!r}")
        check(api.session.github_headers() == {
            "Authorization": "token secret-token",
            "X-GitHub-Api-Version": "2022-11-28"},
            f"the headers are not the two GitHub reads: "
            f"{api.session.github_headers()}")
        other = AddonAPI(manager, addons["berry"])
        check(other.session.git_token() == "",
              "a second addon got the token its own switch never gave it")
    finally:
        os.environ.pop("BERRY_DESKTOP_DIR", None)

    print("\n24. an app setting is written for one key only")
    win, manager, addons = world()
    api = AddonAPI(manager, addons["hello"])
    check(api.session.app_setting("git_token") == "",
          "a key the profile holds was not read back")
    check(api.session.app_setting("never_written") is None,
          "a key nobody ever set answered something")
    check(api.session.set_app_setting("addon_dev_enabled", False) is True,
          "the one writable key was refused")
    check(win.dev_set == [False] and win.app_config["addon_dev_enabled"] is False,
          f"the key was written without the window being told: {win.dev_set}")
    for key, value in (("host", "evil.example"), ("password", "hunter2"),
                       ("git_token", "stolen"), ("addons", {})):
        check(api.session.set_app_setting(key, value) is False,
              f"an addon wrote somebody's {key}")
    check(any("not one an addon may write" in line for line in win.logs),
          f"the refusal was silent: {win.logs}")
    check(win.app_config.get("host") is None and "addons" in win.app_config,
          "a refused key still reached the profile")

    print("\n25. the two transport gates reach the record, for a declared transport")
    win, manager, addons = world()
    addons["hello"].requires = ("ssh", "sftp")
    api = AddonAPI(manager, addons["hello"])
    row = {one["id"]: one for one in api.session.addons()}
    check(row["hello"]["ssh_allowed"] is True
          and row["hello"]["sftp_allowed"] is True,
          f"a record nobody has touched does not open both gates: {row['hello']}")
    check(api.session.set_addon_transport("hello", "sftp", False) is True,
          "the app refused a gate over a transport the manifest named")
    record = manager.record_for("hello")
    check(record["sftp_enabled"] is False
          and addons["hello"].transport_allowed("sftp") is False,
          f"the gate moved the answer but not the record: {record}")
    check({one["id"]: one for one in api.session.addons()}
          ["hello"]["sftp_allowed"] is False,
          "the row the table is drawn from still says the gate is open")
    check(addons["hello"].ssh_enabled is True,
          "shutting one transport shut the other with it")
    check(api.session.set_addon_transport("hello", "telnet", False) is False,
          "a switch was offered for a transport that is not SSH or SFTP")
    check(api.session.set_addon_transport("berry", "ssh", False) is False,
          "an addon that named no transport was given one through the api")
    check(any("berry has no SSH switch to move" in line for line in win.logs),
          f"the refused gate was not said in the log: {win.logs}")

    print("\n26. the own-connection choice belongs to a 'both' addon alone")
    win, manager, addons = world()
    api = AddonAPI(manager, addons["hello"])
    check(api.session.set_addon_own_connection("hello", True) is False,
          'an addon that declared "connection": "app" was put on one of its own')
    check(any("has no own-connection switch" in line for line in win.logs),
          f"the refused connection was not said in the log: {win.logs}")
    addons["hello"].connection = "both"
    row = {one["id"]: one for one in api.session.addons()}
    check(row["hello"]["own_switchable"] is True
          and row["hello"]["own_connection"] is False,
          f"a 'both' addon is not offered the choice: {row['hello']}")
    check(api.session.set_addon_own_connection("hello", True) is True,
          "the app refused the choice its own row says is the addon's")
    record = manager.record_for("hello")
    check(record["own_connection"] is True
          and addons["hello"].own_connection is True
          and addons["hello"].uses_own is True,
          f"the choice was taken without the record: {record}")
    check({one["id"]: one for one in api.session.addons()}["hello"]["own_connection"]
          is True,
          "the row still says the addon rides the app's session")
    addons["berry"].connection = "both"
    check(api.session.set_addon_own_connection("berry", True) is False,
          "a shipped addon was given a connection of its own")
    check(manager.record_for("berry")["own_connection"] is False,
          "the refusal still wrote the record")

    print("\n27. addon_config reads what the addon declared, and writes nothing")
    win, manager, addons = world()
    api = AddonAPI(manager, addons["hello"])
    addons["hello"].config_title = "Hello settings"
    addons["hello"].config_fields = [dict(one) for one in CONFIG_CARD["fields"]]
    addons["hello"].config_defaults = dict(CONFIG_CARD["defaults"])
    card = api.session.addon_config("hello")
    check(card["title"] == "Hello settings" and card["exists"] is False,
          f"the card is not the addon's own title or an honest answer about its "
          f"file: {card['title']!r} / {card['exists']}")
    check([one["key"] for one in card["fields"]] == [one["key"] for one in
                                                     CONFIG_CARD["fields"]],
          "the declared keys did not come back in the order the addon wrote them")
    check(card["values"] == CONFIG_CARD["defaults"],
          f"an addon with no file yet reads back as nothing but its defaults: "
          f"{card['values']}")
    check(Path(card["path"]).name == "config.json",
          f"the card does not name the file it speaks for: {card['path']!r}")
    check(not Path(card["path"]).exists(),
          "reading another addon's settings made a file that was not there")

    print("\n28. a save writes the file over the keys it showed, and keeps the rest")
    import addon_api as api_module
    import core.addon as core_addon

    folder = Path(tempfile.mkdtemp())
    file = folder / "config.json"
    file.write_text(json.dumps({"keepme": 1, "greeting": "Hi"}), encoding="utf-8")
    held = (core_addon.addon_config_path, api_module.addon_config_path)
    core_addon.addon_config_path = lambda _id: file
    api_module.addon_config_path = lambda _id: file
    heard: list[dict] = []
    detach = manager.add_listener("addon_config_changed", "hello",
                                 heard.append)
    try:
        card = api.session.addon_config("hello")
        check(card["exists"] is True and card["values"]["keepme"] == 1,
              f"the file that is there was not read: {card['values']}")
        saved = api.session.set_addon_config(
            "hello", {"greeting": "Hey", "port": 8022, "tunnel.from": "2222"})
        check(saved["keepme"] == 1 and saved["greeting"] == "Hey",
              f"the save did not start from the file: {saved}")
        on_disk = json.loads(file.read_text(encoding="utf-8"))
        check(on_disk["keepme"] == 1 and on_disk["greeting"] == "Hey"
              and on_disk["port"] == 8022,
              f"what was written is not what the file holds: {on_disk}")
        check(on_disk.get("tunnel") == {"from": "2222"},
              f"a dotted key did not land inside its section: {on_disk}")
        check(any("config saved (addon manager)" in line for line in win.logs),
              f"the write was not said in the log: {win.logs}")
        check(len(heard) == 1 and heard[0]["addon"] == "hello"
              and heard[0]["source"] == "addon manager",
              f"the addon was never told its settings changed: {heard}")
    finally:
        detach()
        core_addon.addon_config_path = held[0]
        api_module.addon_config_path = held[1]


def test_the_button_in_settings() -> None:
    print("\n29. Settings' Open addon manager starts the store and asks for page two")
    win, manager, _addons = world(names=("hello",))
    stub = addon_client.StubAPI(addon_id="addon_store")
    check(manager.open_addon_manager() is False,
          "the button claimed to open a manager with no store in the folder")
    check(any("no Addon manager" in line for line in win.logs),
          f"the missing store went unmentioned: {win.logs}")

    store_addon = make_addon("addon_store")
    store_addon.loaded = True
    store_addon.api = stub
    manager.addons.append(store_addon)
    check(manager.open_addon_manager() is True,
          "the button refused a store that is already running")
    entry = stub.last("tabs.set_widget") or (None, ())
    check(list(entry[1]) == [manager.STORE_TAB, manager.STORE_SUBTABS,
                             "current", manager.STORE_MANAGER_PAGE],
          f"the store was not asked for its second page: {list(entry[1])}")
    check(stub.asked("tabs.activate", "name") == manager.STORE_TAB,
          "the store's tab was not brought to the front")

    print("\n30. a store that will not be shown is said so")
    def refuse(*_arguments: Any, **_named: Any) -> None:
        raise RuntimeError("the tab is gone")

    stub.tabs.activate = refuse                    # type: ignore[method-assign]
    check(manager.open_addon_manager() is False,
          "a store that would not answer was reported as opened")
    check(any("could not be opened" in line for line in win.logs),
          f"the failure was silent: {win.logs}")


def test_the_window_moves() -> None:
    print("\n31. one tab out of the bar, and the window's own button on the way back")
    win, manager, addons = world(names=("hello",))
    addon = addons["hello"]
    notes = win.add("hello Notes")
    addon.tabs["Notes"] = {"page": notes, "popped": False}
    home = addon.tabs["Home"]["page"]
    check(win.tabs.indexOf(home) == 0 and win.tabs.indexOf(notes) == 1,
          f"the two tabs did not start side by side in the bar: {win.names()}")
    check(manager.pop_tab("hello", "Home") is True,
          "the mark on a tab could not send that one page to its window")
    check(win.tabs.indexOf(home) < 0 and win.tabs.indexOf(notes) == 0,
          f"popping one page took the addon's other one with it: {win.names()}")
    window = manager.windows["hello"]
    check(window.has_page("Home"),
          "the page left the bar and no window is holding it")
    window.dock_button.click()
    check(win.tabs.indexOf(home) == 0 and not window.has_page("Home"),
          "the window's Dock back left its page afloat instead of the bar's")
    check(addon.tabs["Home"].get("popped") is False,
          "the page came home and the addon still thinks it is away")
    check(manager.slot_for("hello", "Home").get("slot") == 0,
          f"the tab did not come back to the slot it left: "
          f"{manager.slot_for('hello', 'Home')}")
    check(manager.pop_tab("hello", "no such tab") is False,
          "a page the addon does not own was reported as moved")

    print("\n32. the real window says the two things core.popout asks, and the "
          "bar settles when a tab leaves it")
    import core.window as core_window
    import core.popout as core_popout
    from core.ui import TabPopoutButton

    real = core_window.BerryManager
    check(getattr(real, "is_main_window", False) is True,
          "the application window never says it is the one with a bar, so "
          "dock_back refuses every page and the window's button does nothing")
    check(getattr(real, "can_dock_tabs", False) is True,
          "the window does not carry the second name a popout asks for")
    for name in ("settle_tab_bar", "sync_tab_popout_buttons", "pop_tab_out"):
        check(callable(getattr(real, name, None)),
              f"the window no longer carries {name}, which core.popout and the "
              f"mark on a tab both reach for")
    heard: list[str] = []

    class Settling:
        win = type("Bar", (), {"settle_tab_bar":
                               lambda _self: heard.append("now")})()

    core_popout.settle_bar(Settling())
    check(heard == ["now"],
          "a popout moved tabs without settling the bar it just changed")
    core_popout.settle_bar(type("NoWin", (), {})())      # a host with no bar

    print("\n33. the mark a tab wears, and what its press does")
    from PyQt6.QtWidgets import QTabBar

    class TabPass:
        """The window's tab passes, on a stand-in holding a bar and a manager."""

        _popout_tab_pages = real._popout_tab_pages
        sync_tab_popout_buttons = real.sync_tab_popout_buttons
        pop_tab_out = real.pop_tab_out

        def __init__(self, window: Any, held: Any) -> None:
            self.tabs = window.tabs
            self.addons = held

    win, manager, addons = world(names=("hello",))
    addon = addons["hello"]
    painter = TabPass(win, manager)
    bar = win.tabs.tabBar()
    page = addon.tabs["Home"]["page"]
    index = win.tabs.indexOf(page)
    painter.sync_tab_popout_buttons()
    check(bar.tabButton(index, QTabBar.ButtonPosition.LeftSide) is None,
          "a tab whose addon has no window on was given a way out anyway")
    addon.popout_record = True
    painter.sync_tab_popout_buttons()
    mark = bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
    check(mark is not None and mark.objectName() == TabPopoutButton.NAME,
          "a tab of an addon with its window switched on wears no pop-out mark")
    mark.click()
    check(win.tabs.indexOf(page) < 0 and manager.windows["hello"].has_page("Home"),
          "pressing the mark did not send that tab to its window")
    addon.popout_record = False
    win.tabs.addTab(page, "Home")
    painter.sync_tab_popout_buttons()
    check(bar.tabButton(win.tabs.indexOf(page),
                        QTabBar.ButtonPosition.LeftSide) is None,
          "the switch went off and the tab still offers to leave the bar")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks this page paints (▸ ✓), so
        # anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    store = addon_client.load_addon(SOURCE)
    print(f"addon_store: the Addon manager page of {SOURCE.name}")

    test_the_spec(store)
    test_the_list(store)
    test_the_presses(store)
    test_the_rows_from_the_app()
    test_the_button_in_settings()
    test_the_window_moves()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
