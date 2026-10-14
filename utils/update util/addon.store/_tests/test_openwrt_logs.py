#!/usr/bin/env python3
"""A test for the openwrt_logs addon (in addon.store, or addons if re-shipped).

It runs the addon with the recording stub from :mod:`addon_client`, plus three
things of its own:

* a fake fleet of OpenWrt boxes - each answers ``logread`` with its own log,
  and one of them always fails;
* a real Qt build of the tab's spec - one tab whose two sub-pages carry every
  id the addon later sets or reads, and a sub-page bar that answers to
  ``current`` and ``index``;
* a hand-held scheduler, so the multi fetch is finished when the test says
  so instead of from a background thread;
* and one answer handed over the way the app's process bridge hands it, since
  the store compiles this addon and a job still running is not something a
  second process can hold - that is what ``test_the_pipe`` checks.

What gets looked at: the manifest declares its services; the addon opens the
one tab with a single and a multi sub-page and declares its config form; the
single page reads the main connection through ``run_async`` and honours the
config (search highlighting on by default, Filter mode, regex, the fetch
command and the row cap all come from it); the multi page reaches every
stored device with its own ``device=`` dict (never the main session), merges
the logs by time, colours each device's rows, filters by
interface/process/MAC/IP/device, and keeps passwords out of the settings.

Run it from the Test panel, or on the command line::

    python addon.store/_tests/test_openwrt_logs.py

The Berry addon store also runs this file before it compiles the addon, setting
``RASCONF_ADDON_SOURCE`` to the freshly downloaded ``.py`` so the copy under test
is the one that is about to be built.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402
import addon_common                                          # noqa: E402

#: The addon to test: the copy a store install handed over, else the one in the
#: tree - the store folder first, the addons folder if it was ever re-shipped.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "openwrt_logs.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = ROOT / "addons" / "openwrt_logs.py"
FAILURES: list[str] = []
#: The addon module, set once in main().
api_module = None


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


# --- the fleet: three boxes with their own logs, one unreachable ---

MAIN_LOG = "\n".join([
    "Oct 12 09:00:00 daemon.info dropbear[101]: Running on 'eth0': port 22",
    "Oct 12 09:05:12 kern.info kernel: [  123.456789] br-lan: port 1(eth0) "
    "entered blocking state",
    "Oct 12 09:10:00 daemon.info dnsmasq-dhcp[202]: DHCPACK(br-lan): "
    "192.168.1.50 aa:bb:cc:dd:ee:ff laptop",
])

LOGS = {
    "10.0.0.4": "\n".join([
        "Oct 12 09:07:00 daemon.info dropbear[11]: Password auth succeeded "
        "for 'root' from 10.0.0.1",
        "Oct 12 09:01:30 daemon.warn hostapd[55]: wlan0: STA "
        "cc:dd:ee:ff:00:11 IEEE 802.11: deauthenticated",
    ]),
    "10.0.0.5": "\n".join([
        "Oct 12 09:03:00 daemon.info dnsmasq[9]: DHCPACK(eth0) 192.168.1.50 "
        "cc:dd:ee:ff:00:11",
        "Oct 12 09:09:40 user.notice root: finished scheduled reboot check",
    ]),
    "10.0.0.7": "Oct 12 09:15:00 daemon.info dropbear[7]: Running on 'lan': port 22",
}

SEED = [
    {"label": "Attic", "host": "10.0.0.4", "port": 22, "username": "root",
     "key_file": "", "color": "#4da3ff"},
    {"label": "Router", "host": "10.0.0.5", "port": 2222, "username": "root",
     "key_file": "/root/.key", "color": "#ffb020"},
    {"label": "Broken", "host": "10.0.0.9", "port": 22, "username": "root",
     "key_file": "/root/.key", "color": "#32c5c5"},
]


class Done:
    """An already-finished stand-in for the Future ``run(block=False)`` gives."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def done(self) -> bool:
        return True

    def result(self, timeout: Any = None) -> Any:
        return self.value

    def cancel(self) -> bool:
        return False


class HandHeld:
    """``api.schedule`` the test drives itself: ``every`` just remembers."""

    def __init__(self) -> None:
        self.tick = None

    def after(self, seconds: float, callback: Any) -> HandHeld:
        return self                                 # one-shot runs through tick too

    def every(self, seconds: float, callback: Any) -> HandHeld:
        self.tick = callback
        return self

    def cancel(self) -> None:
        self.tick = None

    stop = cancel


class Api(addon_client.StubAPI):
    """The recording stub with the fleet, a form and a place to store settings."""

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.form: dict = {}
        self.dialog_answer: dict | None = None
        self.chosen: str | None = None
        self.schedule = HandHeld()
        self.runs: list[dict] = []                 # device dicts the calls used

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments,
                                                       named, fallback)
        if method == "device.run_async":
            result = {"code": 0, "out": MAIN_LOG, "err": ""}
            callback = ask("on_done")
            if callable(callback):
                callback(result)
            return result
        if method == "device.run":
            details = ask("device") or {}
            self.runs.append(dict(details))
            host = str(details.get("host", ""))
            if host in LOGS:
                return Done({"code": 0, "out": LOGS[host], "err": ""})
            return Done({"code": 1, "out": "", "err": "connection refused"})
        if method == "tabs.widget_value":
            identifier = ask("identifier", "")
            return self.form.get(identifier)
        if method == "settings.set":
            self.settings_values[str(ask("key"))] = ask("value")
            return None
        if method == "ui.input":
            return "pw"                            # the fake boxes take any password
        if method == "ui.dialog":
            return dict(self.dialog_answer) if self.dialog_answer else None
        if method == "ui.choose":
            return self.chosen
        return super().default(method, arguments, named)

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None


# --- the checks ---

def test_the_manifest(addon: Any) -> None:
    print("\n1. the manifest and the new api")
    check(addon.ADDON.get("requires") == ["ssh"],
          f"the addon must declare its services: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("api")) == "1", "the addon must stay on api version 1")
    check(addon.ADDON.get("connection") == "both",
          f"the api now refuses a device= run unless the manifest declares it: "
          f"{addon.ADDON.get('connection')!r}")
    positional = addon_client.ProcessAPI._POSITIONAL.get("device.run", ())
    check("device" in positional and "block" in positional,
          f"device.run's one-off device must be reachable by the pipe too: {positional}")


def test_the_pipe(addon: Any) -> None:
    """The multi view fetches with ``block=False`` and polls; a build must cope.

    The stub in this file answers with an already-finished stand-in, which is
    what an imported addon gets from the app too. Built as a program the addon
    only ever sees a handle and three questions it may ask about the job, so
    drive the real client over a pretend pipe and check the logs still arrive.
    """
    print("\n1b. a block=False fetch survives being compiled")
    client = addon_client.ProcessAPI("openwrt_logs", "OpenWrt Log Viewer")
    asked: list[str] = []

    def answer(identifier: int, **named: Any) -> None:
        box = client.pending.get(identifier)
        if box is not None:
            box.put(("error" if "error" in named else "ok",
                     named.get("error", named.get("result"))))

    def fake_send(payload: dict) -> bool:
        method = str(payload.get("api") or "")
        asked.append(method)
        if method == "device.run":
            # A job the app is running: the bridge answers with its handle.
            answer(payload["id"], result=addon_common.future_handle(1))
        elif method == "device.future_done":
            answer(payload["id"], result=True)
        elif method == "device.future_result":
            answer(payload["id"],
                   result={"code": 0, "out": LOGS["10.0.0.4"], "err": ""})
        elif method == "device.future_cancel":
            answer(payload["id"], result=False)
        else:
            answer(payload["id"], error=f"unknown method {method}")
        return True

    client.send = fake_send
    job = client.device.run("logread", timeout=5, block=False,
                           device={"host": "10.0.0.4"})
    check(isinstance(job, addon_client.RemoteFuture),
          f"the compiled addon got {type(job).__name__}, not a job it can poll")
    check(not isinstance(job, addon._Settled),
          "and it is not mistaken for an answer already in, so the fetch polls")
    check(job.done() is True, "done() reached the app as its own question")
    got = job.result()
    check(isinstance(got, dict) and got.get("out") == LOGS["10.0.0.4"],
          f"result() brought the device's log back: {str(got)[:60]!r}")
    check(job.cancel() is False, "a finished job says it could not be cancelled")
    check(asked[:2] == ["device.run", "device.future_done"],
          f"the pipe saw the handle and the questions, nothing else: {asked}")
    lines = addon.make_entries(str(got.get("out", "")).splitlines(),
                               device="Attic", color="#4da3ff")
    check(bool(lines),
          f"so the multi view has lines to merge, got {len(lines)}")


def test_the_tabs(addon: Any, api: Api) -> None:
    print("\n2. one tab with two sub-pages, and its spec really builds")
    addon.init(api)
    names = [api.argument("tabs.add_tab", "name", a, k)
             for w, a, k in api.calls if w == "tabs.add_tab"]
    check(names == [addon.ONE_TAB],
          f"the addon opened {names}; one tab is what it owes")
    check(api.times("ui.menu_item") == 2,
          "both modes should be reachable from the app's menu")
    check(api.called("tabs.set_icon"),
          "the tab should wear a picture of its own")
    mark = str(api.asked("tabs.set_icon", "icon") or "")
    check(mark == addon.TAB_ICON or mark.endswith(addon.TAB_ICON_FILE),
          f"the mark is the shipped picture, else the drawn fallback: {mark[:60]!r}")
    check(len(api.declared.get("fields", [])) == len(addon.CONFIG_FIELDS),
          "the addon should declare its whole config form to the app")
    check(api.declared.get("defaults", {}).get("search_mode") == "Highlight",
          "and its declared defaults should start the search in Highlight mode")
    addon._show_mode(addon.MULTI_MODE)
    check(api.last_set("mode_tabs", "current") == addon.MULTI_MODE,
          "the menu entry should page the tab over to the multi view")
    check(any(w == "tabs.activate" and a and a[0] == addon.ONE_TAB
              for w, a, k in api.calls),
          "switching mode should bring the tab forward as well")
    try:
        import addon_api
    except Exception as error:                     # noqa: BLE001 - Qt is optional
        look(f"no Qt here, spec build skipped: {error}")
        return
    for spec, ids in ((addon.SINGLE_SPEC,
                       ["s_search", "s_iface", "s_proc", "s_mac", "s_ip", "s_order",
                        "s_refresh", "s_status", "s_table"]),
                      (addon.MULTI_SPEC,
                       ["m_conns", "m_add", "m_edit", "m_colour", "m_remove",
                        "m_refresh", "m_status", "m_device", "m_search", "m_iface",
                        "m_proc", "m_mac", "m_ip", "m_order", "m_table"])):
        _widget, registry = addon_api.build_spec(spec)
        missing = [i for i in ids if i not in registry]
        check(not missing, f"a spec does not carry its ids: {missing}")
    from PyQt6.QtWidgets import QTabWidget
    page, whole = addon_api.build_spec(addon.TAB_SPEC)
    check(isinstance(page, QTabWidget) and page.count() == 2,
          "the tab's root should be a bar of the two sub-pages")
    check(all(i in whole for i in ("mode_tabs", "s_table", "m_table")),
          "the one spec should register every id of both pages")
    addon_api.apply_widget_property(page, "current", addon.MULTI_MODE)
    check(page.currentIndex() == addon.MULTI_MODE,
          "the sub-page bar should follow 'current'")
    check(addon_api.read_widget_value(page, "index") == addon.MULTI_MODE,
          "and say which page shows when asked for 'index'")
    from PyQt6.QtWidgets import QHeaderView
    s_table = whole["s_table"]
    check(s_table.header().sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
          and s_table.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive,
          "the Message column should take the slack the other columns leave")


def test_single_device(addon: Any, api: Api) -> None:
    print("\n3. single device reads the main connection")
    fresh_addon(api)
    addon.fetch_logs()
    check(api.called("device.run_async"),
          "the single tab should answer through run_async, not block the interface")
    rows = api.last_set("s_table", "rows")
    check(rows and len(rows) == 3, f"three lines were due, got {rows and len(rows)}")
    check(rows and str(rows[0][0]).startswith("Oct 12 09:10:00"),
          "newest first was the default: the freshest line should be on top")
    check(rows and not str(rows[0][2]).startswith("Oct 12"),
          f"the stamp the Time column shows should be trimmed off the message: {rows[0][2]!r}")
    check(rows and all(isinstance(r, list) for r in rows),
          "one device needs no colour rows; plain cells keep it readable")
    items = api.last_set("s_iface", "items")
    check(items and {"All", "eth0", "br-lan"} <= set(items),
          f"the interface combo should know the log's faces: {items}")
    for combo, want in (("s_mac", "aa:bb:cc:dd:ee:ff"), ("s_ip", "192.168.1.50"),
                        ("s_proc", "dnsmasq-dhcp")):
        got = api.last_set(combo, "items") or []
        check(want in got, f"{combo} never offered {want}: {got}")
    api.form["s_search"] = "dnsmasq"
    addon.render_single()
    rows = api.last_set("s_table", "rows")
    check(rows and len(rows) == 3,
          f"Highlight mode keeps every line in view, got {rows and len(rows)}")
    tinted = [r for r in rows or [] if isinstance(r, dict)
              and str(r.get("overlay", "")).startswith("#" + addon.HIGHLIGHT_ALPHA)]
    check(len(tinted) == 1 and "dnsmasq" in str(tinted[0]["cells"][2]),
          f"exactly the dnsmasq line should wear the white overlay: {rows}")
    status = str(api.last_set("s_status", "text") or "")
    check("matched" in status, f"the status line should count the matches: {status!r}")
    api.config_values["search_mode"] = "Filter"
    addon.render_single()
    rows = api.last_set("s_table", "rows")
    check(rows and len(rows) == 1 and "dnsmasq" in str(rows[0][2]),
          f"Filter mode should leave one line: {rows}")
    api.config_values.pop("search_mode", None)

    print("\n3b. the config steers the search and the fetch")
    api.form["s_search"] = "dropbear|dnsmasq"
    api.config_values["use_regex"] = True
    addon.render_single()
    rows = api.last_set("s_table", "rows")
    tinted = [r for r in rows or [] if isinstance(r, dict)]
    check(len(rows or []) == 3 and len(tinted) == 2,
          f"the regex should tint the dropbear and dnsmasq lines: {rows}")
    api.form["s_search"] = "[dnsmasq"          # a regex that does not compile
    addon.render_single()
    rows = api.last_set("s_table", "rows")
    check(rows and len(rows) == 3 and not any(isinstance(r, dict) for r in rows),
          f"a broken regex falls back to plain text without erroring: {rows}")
    api.config_values.pop("use_regex", None)
    api.form.pop("s_search", None)
    api.config_values["max_rows"] = 2
    addon.render_single()
    check(len(api.last_set("s_table", "rows") or []) == 2,
          "max_rows should cap what is drawn")
    api.config_values.pop("max_rows", None)
    api.config_values["log_command"] = "logread -e dnsmasq"
    api.config_values["log_timeout"] = 5
    addon.fetch_logs()
    check(api.asked("device.run_async", "command") == "logread -e dnsmasq",
          f"the fetch should use the configured command: "
          f"{api.asked('device.run_async', 'command')!r}")
    check(api.asked("device.run_async", "timeout") == 5,
          "and the configured timeout")
    api.config_values.pop("log_command", None)
    api.config_values.pop("log_timeout", None)
    api.config_values["repeat_time"] = True
    addon.render_single()
    raw = api.last_set("s_table", "rows")
    check(raw and str(raw[0][2]).startswith("Oct 12"),
          "repeat_time should put the raw stamp back into the message")
    api.config_values.pop("repeat_time", None)


def test_parsing(addon: Any) -> None:
    print("\n4. reading the stamp off a line")
    when = addon.parse_time("Oct 12 09:10:00 x")
    check(when and (when.month, when.day, when.hour, when.minute, when.second)
          == (10, 12, 9, 10, 0), f"busybox stamp read as {when}")
    when = addon.parse_time("Mon Oct 12 10:00:00 2024 x")
    check(when and (when.year, when.month, when.day, when.hour)
          == (2024, 10, 12, 10), f"syslog-with-year stamp read as {when}")
    when = addon.parse_time("2024-10-12T05:06:07 x")
    check(when and (when.year, when.month, when.day, when.hour, when.minute,
                    when.second) == (2024, 10, 12, 5, 6, 7), f"ISO stamp read as {when}")
    when = addon.parse_time("10:00:00 daemon.info x")
    check(when and (when.hour, when.minute, when.second) == (10, 0, 0),
          f"a time-only stamp read as {when}")
    check(addon.parse_time("kernel: no stamp here") is None,
          "a line without a stamp should stay unplaced, not be guessed")
    check(addon.line_proc("Oct 12 09:00:00 daemon.info dropbear[101]: hi")
          == "dropbear", "the writing process should come out of the line")
    check(addon.line_body("Oct 12 09:10:00 daemon.info x: y") == "daemon.info x: y",
          "the leading stamp should come off cleanly")
    check(addon.line_body("Mon Oct 12 10:00:00 2024 hi") == "hi",
          "including the weekday-and-year form")
    check(addon.line_body("no stamp here") == "no stamp here",
          "a line without a stamp is left exactly as it was")


def test_connections(addon: Any, api: Api) -> None:
    print("\n5. the connection list: add, recolour, remove - and no saved passwords")
    api.settings_values["connections"] = [dict(c) for c in SEED]
    fresh_addon(api)
    api.dialog_answer = {"label": "Shed", "host": "10.0.0.7", "port": 22,
                         "username": "root", "password": "toolongtostore",
                         "key_file": "", "color": "pink"}
    addon.add_connection()
    stored = api.settings_values.get("connections", [])
    check(len(stored) == 4 and str(stored[3].get("label")) == "Shed",
          f"the new device was not stored: {stored}")
    check("password" not in stored[3],
          "the settings must never carry a password")
    check(str(stored[3].get("color")) == "#f06bc0",
          f"a palette name typed in the colour box should still pass: {stored[3]}")
    api.dialog_answer = {"label": "Bad", "host": "10.0.0.8", "port": 22,
                         "username": "root", "password": "",
                         "key_file": "", "color": "reddish"}
    addon.add_connection()
    check(len(api.settings_values["connections"]) == 4,
          "a colour that is neither hex nor a palette name must be refused")
    api.answer("ui.pick_color", "#2ec27e")
    addon._remember_selection({"texts": [addon._conn_rows[0]]})
    addon.recolour_connection()
    stored = api.settings_values["connections"]
    check(stored[0]["color"] == "#2ec27e",
          f"the Attic should wear mint now: {stored[0]['color']}")
    broken_row = next(i for i, row in enumerate(addon._conn_rows)
                      if row.startswith("Broken"))
    addon._remember_selection({"texts": [addon._conn_rows[broken_row]]})
    addon.remove_connection()
    stored = api.settings_values["connections"]
    check([c["label"] for c in stored] == ["Attic", "Router", "Shed"],
          f"the unreachable box should be gone: {[c['label'] for c in stored]}")


def test_multi_device(addon: Any, api: Api) -> None:
    print("\n6. multi device reaches every box with its own device dict")
    api.settings_values["connections"] = [dict(c) for c in SEED[:2]] + [
        {"label": "Shed", "host": "10.0.0.7", "port": 22, "username": "root",
         "key_file": "", "color": "#f06bc0"},
        {"label": "Broken", "host": "10.0.0.9", "port": 22, "username": "root",
         "key_file": "/root/.key", "color": "#32c5c5"},
    ]
    fresh_addon(api)
    addon.fetch_all()
    hosts = sorted(run.get("host") for run in api.runs)
    check(hosts == ["10.0.0.4", "10.0.0.5", "10.0.0.7", "10.0.0.9"],
          f"every device should have been asked with its own dict: {hosts}")
    check(all(run.get("username") and "port" in run for run in api.runs),
          "each dict must carry at least host, port and username")
    check(api.schedule.tick is not None,
          "the fetch should pace itself on a timer instead of blocking")
    api.schedule.tick()                        # the futures are already Done
    rows = api.last_set("m_table", "rows")
    check(rows and len(rows) == 5, f"5 merged lines were due, got {rows and len(rows)}")
    if not rows:
        return
    order = [str(r["cells"][0]) for r in rows]
    check(order == sorted(order, reverse=True),
          f"newest first across devices, but the merge says: {order}")
    check(all(isinstance(r, dict) and r.get("background") for r in rows),
          "every merged row should sit on its device's colour wash")
    by_device = {str(r["cells"][1]): str(r["background"]) for r in rows}
    wash = "#" + addon.DEVICE_TINT_ALPHA
    check(by_device.get("Attic") == wash + "4da3ff"
          and by_device.get("Shed") == wash + "f06bc0",
          f"the colours came out wrong: {by_device}")
    status = str(api.last_set("m_status", "text") or "")
    check("Broken" in status and "connection refused" not in status,
          f"the failed device should be named in the status line: {status!r}")
    devices = api.last_set("m_device", "items")
    check(devices and set(devices) == {"All", "Attic", "Router", "Shed", "Broken"},
          f"the device filter should list each box: {devices}")

    print("\n7. filtering the merged log")
    api.form["m_ip"] = "192.168.1.50"
    addon.render_multi()
    rows = api.last_set("m_table", "rows")
    check(rows and len(rows) == 1 and rows[0]["cells"][1] == "Router",
          f"only the Router line has that IP: {rows}")
    api.form["m_ip"] = "All"
    api.form["m_device"] = "Attic"
    addon.render_multi()
    rows = api.last_set("m_table", "rows")
    check(rows and len(rows) == 2
          and all(r["cells"][1] == "Attic" for r in rows),
          f"the device filter left {rows}")
    api.form["m_device"] = "All"
    api.form["m_iface"] = "wlan0"
    addon.render_multi()
    rows = api.last_set("m_table", "rows")
    check(rows and len(rows) == 1 and "hostapd" in str(rows[0]["cells"][3]),
          f"the interface filter should leave the hostapd line: {rows}")
    api.form["m_iface"] = "All"
    api.form["m_mac"] = "aa:bb:cc:dd:ee:ff"
    addon.render_multi()
    check(not api.last_set("m_table", "rows"),
          "no merged line carries that MAC - the filter must not guess")
    api.form["m_mac"] = "All"
    api.form["m_proc"] = "dropbear"
    api.form["m_order"] = "Oldest first"
    addon.render_multi()
    rows = api.last_set("m_table", "rows")
    order = [str(r["cells"][0]) for r in rows or []]
    check(len(order) == 2 and order == sorted(order),
          f"the two dropbear lines, oldest first: {order}")


def test_rows_paint(app: Any) -> None:
    print("\n8. the app really paints coloured rows")
    if app is None:
        look("no Qt here; skipped")
        return
    import addon_api
    from PyQt6.QtWidgets import QTreeWidget

    tree = QTreeWidget()
    tree.setColumnCount(2)
    addon_api.apply_widget_property(tree, "rows", [
        {"cells": ["a", "b"], "color": "#ff0000", "overlay": "#33f5f5f5"},
        ["c", "d"],
        {"cells": ["e", "f"], "color": "not-a-color", "background": "#102030"},
    ])
    check(tree.topLevelItemCount() == 3, "all three rows should be there")
    check(tree.topLevelItem(0).foreground(0).color().name() == "#ff0000",
          "the first row should wear the colour it sent")
    check(str(tree.topLevelItem(1).foreground(0).color().name())
          != "#ff0000", "a plain row keeps the plain look")
    check(tree.topLevelItem(2).background(1).color().name() == "#102030",
          "the background colour should ride along too")
    brush = tree.topLevelItem(0).data(0, addon_api.ROW_OVERLAY_ROLE)
    check(isinstance(brush, addon_api.QBrush)
          and brush.color().name() == "#f5f5f5"
          and brush.color().alpha() == 0x33,
          f"the overlay should ride in its own role, faint white: {brush}")
    check(tree.topLevelItem(0).toolTip(1) == "b",
          "every cell should carry its own text as a tooltip, so an elided "
          "one can still be read")

    # The data checks above are exactly what fooled the last two versions:
    # the app's own stylesheet names QTreeWidget::item colours, which used to
    # swallow every per-row brush whole. Only the delegate paints past it, so
    # count real pixels with the real sheet on.
    import manager_const
    from PyQt6.QtGui import QColor, QImage, QPainter
    sheet = app.styleSheet()
    app.setStyleSheet(manager_const.APP_STYLESHEET)
    try:
        _root, registry = addon_api.build_spec(
            {"kind": "table", "id": "t", "columns": ["A", "B"]})
        painted = registry["t"]
        addon_api.apply_widget_property(painted, "rows", [
            {"cells": ["wash", "x"], "background": "#664da3ff"},
            ["plain", "x"],
        ])
        painted.resize(240, 96)
        painted.show()
        app.processEvents()
        img = QImage(240, 96, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("#000000"))
        painter = QPainter(img)
        painted.render(painter)
        painter.end()
        rect = painted.visualItemRect(painted.topLevelItem(0))
        lit = 0
        for y in range(max(0, rect.top()), min(96, rect.bottom() + 1)):
            for x in range(max(0, rect.left()), min(240, rect.right() + 1)):
                c = img.pixelColor(x, y)
                if c.blue() > 60 and c.blue() > c.green() + 20 and c.red() > 40:
                    lit += 1                    # the sky wash over the dark base
        check(lit > 150,
              f"the row colours must survive the app stylesheet (delegate); "
              f"only {lit} wash-coloured pixels showed")
        painted.deleteLater()
    finally:
        app.setStyleSheet(sheet)


def fresh_addon(api: Api) -> None:
    """Start the addon's own state over against this api object."""
    api_module.api = api
    api_module._single_entries = []
    api_module._multi_entries = []
    api_module._multi_failed = {}
    api_module._poll = None
    api_module._refresh_timer = None
    api_module._conn_rows = []
    api_module._conn_sel = None
    api_module._passwords.clear()


# --- run it ---

def main() -> int:
    global api_module, FAILURES
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint (● ○ ✓),
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    addon = addon_client.load_addon(SOURCE)
    api_module = addon
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here: {error}")

    print(f"openwrt_logs: the addon at {SOURCE.name}")
    test_the_manifest(addon)
    test_the_pipe(addon)

    api = Api(addon_id="openwrt_logs")
    addon.init(api)
    test_the_tabs(addon, Api(addon_id="openwrt_logs2"))
    test_single_device(addon, api)
    test_parsing(addon)
    test_connections(addon, Api(addon_id="openwrt_logs3", settings={}))
    test_multi_device(addon, Api(addon_id="openwrt_logs4", settings={}))
    test_rows_paint(app)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
