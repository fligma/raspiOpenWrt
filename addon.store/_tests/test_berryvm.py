#!/usr/bin/env python3
"""A test for the BerryVM addon - the XP-styled desktop in one tab.

BerryVM is a desktop, so the shapes worth checking without a window are the
ones the desktop itself is supposed to keep in step: an icon layer that is
born chromeless and stays at the back, a taskbar that follows what the
canvas is holding, a Start menu that opens programs and a Log Off that shuts
them, two file panes that read from this machine and from the main
connection, an editor that refuses what it cannot hold and can Save As its
way to a new file, a viewer that pulls a remote picture down before showing
it, a console that feeds a real shell and drains its answers, and a Display
Properties window whose Apply reaches the config, the paint and the icons.

Covered here:

* the manifest, and the picture the store is expected to show for it;
* the pure helpers - port, address, machine entry, suffix to language, a
  byte count that reads as something, the calculator's arithmetic;
* the VM's home folder: ``Desktop``, ``bin``, ``tmp``, ``config``, made
  once and not again;
* the ``config/settings.json`` mirror: every default key written, unknown
  keys surviving, no password in it;
* the theme sheets, the icon layer's shape, and the taskbar following the
  desktop's windows;
* the Start menu, the desktop's right-click, and Log Off;
* the Explorer: both panes, opening across the connection, uploads,
  downloads, folders, renames, deletes and Run on device;
* Notepad's refusals, its save, its Save As, and a scratch buffer finding
  its file;
* the Picture Viewer's remote pull and its window-replacing zoom;
* a local console kept by a fake process, with the pump in between;
* the Terminal's refusals and the connect dict it hands its shell;
* the Machines list kept in the config;
* Display Properties: Apply, the wallpaper pickers, and Defaults;
* the calculator's keys and the logon bubble.

Run from the Test panel, or::

    python addon.store/_tests/test_berryvm.py

The store also runs this before it compiles the addon, pointing
``RASCONF_ADDON_SOURCE`` at the freshly downloaded ``.py`` so the copy under
test is the one about to be built.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: The repository root; Berry Manager and addon_client live under ``src``.
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "src" / "berry_manager"
sys.path.insert(0, str(APP))

import addon_client                                          # noqa: E402

#: The addon to test: the store install source or local tree.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "berryvm.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = APP / "addons" / "berryvm.py"

FAILURES: list[str] = []
addon_module = None

#: Where the addon's data folder is pointed for the run, so it never writes
#: in the tree the test is read from.
WORK = Path(tempfile.mkdtemp(prefix="berryvm-test-"))


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def find_kind(node: Any, kind: str) -> dict:
    """The first child of a spec tree wearing ``kind`` - the way a test
    reaches a window's terminal or tile grid without knowing the shape
    around it."""
    if isinstance(node, dict):
        if str(node.get("kind") or "") == kind:
            return node
        for value in node.values():
            found = find_kind(value, kind)
            if found:
                return found
    elif isinstance(node, (list, tuple)):
        for item in node:
            found = find_kind(item, kind)
            if found:
                return found
    return {}


class Api(addon_client.StubAPI):
    """The recording stub, with a desktop that remembers its own windows.

    The real ``windows`` canvas is a Qt widget with a set of free-floating
    frames; this keeps the same answers - which ids are up, what each
    ``open_window`` descriptor said, what the panes and devices were handed -
    so a check on a program reads a list rather than a log line.
    """

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.windows: list[str] = []       # ids the canvas is holding
        self.titles: dict[str, str] = {}   # win_id -> the title it wears
        self.form: dict[str, Any] = {}     # widget_value cache per id
        self.answers: list[Any] = []      # queued replies to ui.input
        self.dialogs: list[Any] = []      # queued replies to ui.dialog
        self.picks: list[Any] = []        # queued replies to ui.choose
        self.file_picks: list[Any] = []   # queued replies to ui.pick_file
        self.colour_picks: list[Any] = []  # queued replies to ui.pick_color
        self.questions: bool = True       # what a yes/no box answers
        self.written: dict[str, Any] = {}  # what config.set was handed
        self.sftp_connected: bool = True
        self.ssh_connected: bool = True
        self.sftp_listing: list[dict] = []
        self.sftp_text: str = ""
        self.sftp_size: int = 0
        self.profile: dict = {"host": "192.168.1.1", "port": 22,
                              "username": "root", "key_file": ""}
        self.last_upload: str = ""
        self.last_remote_target: str = ""
        self.last_download: str = ""
        self.remote_ops: list[tuple] = []  # mkdir / remove / rename calls
        self.last_run: str = ""

    # --- the fake desktop: only what the addon actually calls ---

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments,
                                                       named, fallback)
        if method == "tabs.set_widget":
            identifier = str(ask("identifier", ""))
            prop = str(ask("prop", ""))
            value = ask("value")
            if prop == "open_window":
                entry = value if isinstance(value, dict) else {}
                win_id = str(entry.get("id") or "")
                if win_id:
                    if win_id not in self.windows:
                        self.windows.append(win_id)
                    self.titles[win_id] = str(entry.get("title") or win_id)
                return win_id
            if prop == "close_window":
                win_id = str(value or "")
                if win_id in self.windows:
                    self.windows.remove(win_id)
                self.titles.pop(win_id, None)
                return None
            if prop in ("raise_window", "lower_window"):
                # The canvas re-stacks for real; the tests only care which
                # window the call named, and ``last_set`` reads that back.
                return None
            # Text written into a form field so a check can read it back.
            if prop in ("text", "value", "items", "enabled", "visible"):
                self.form[f"{identifier}:{prop}"] = value
            return None
        if method == "tabs.widget_value":
            identifier = str(ask("identifier", ""))
            what = str(ask("what", "value"))
            if what == "windows" and identifier == addon_module.DESKTOP:
                return list(self.windows)
            if what == "selected":
                return self.form.get(f"{identifier}:selected", [])
            if what == "items":
                return self.form.get(f"{identifier}:items", [])
            if what == "value":
                return self.form.get(f"{identifier}:value", "")
            return self.form.get(f"{identifier}:{what}", "")
        if method == "ui.input":
            return self.answers.pop(0) if self.answers else None
        if method == "ui.dialog":
            return self.dialogs.pop(0) if self.dialogs else None
        if method == "ui.choose":
            return self.picks.pop(0) if self.picks else None
        if method == "ui.pick_file":
            return self.file_picks.pop(0) if self.file_picks else ""
        if method == "ui.pick_color":
            return self.colour_picks.pop(0) if self.colour_picks else ""
        if method == "ui.question":
            return self.questions
        if method == "config.set":
            # A write is also a read for the next call: the addon reads its
            # config straight back when it repaints, so the stub keeps the
            # value live in ``config_values`` exactly as the file would.
            self.written[str(ask("key", ""))] = ask("value")
            self.config_values[str(ask("key", ""))] = ask("value")
            return None
        if method == "config.get":
            key = str(ask("key", ""))
            stored = self.config_values.get(key)
            return stored if stored is not None else ask("default")
        # --- the small device stubs the addon actually reaches ---
        if method == "session.profile":
            return dict(self.profile)
        if method == "device.is_connected":
            kind = str(ask("protocol", "")).lower()
            return self.sftp_connected if kind == "sftp" else self.ssh_connected
        if method == "device.list":
            return list(self.sftp_listing)
        if method == "device.read_text":
            return self.sftp_text
        if method == "device.write_text":
            self.form[f"remote_written:{ask('path', '')}"] = ask("text", "")
            return True
        if method == "device.stat":
            return {"size": int(self.sftp_size), "directory": False}
        if method == "device.upload":
            self.last_upload = str(ask("local", ""))
            self.last_remote_target = str(ask("remote_path", ""))
            return self.last_remote_target or f"tmp/{Path(self.last_upload).name}"
        if method == "device.download":
            remote = str(ask("remote_path", ""))
            local = str(ask("local") or WORK / "downloads" / Path(remote).name)
            target = Path(local)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x89PNG fake picture bytes")
            self.last_download = remote
            return str(target)
        if method == "device.mkdir":
            self.remote_ops.append(("mkdir", str(ask("path", ""))))
            return True
        if method == "device.remove":
            self.remote_ops.append(("remove", str(ask("path", ""))))
            return True
        if method == "device.rename":
            self.remote_ops.append(("rename", str(ask("path", "")),
                                    str(ask("new_path", ""))))
            return True
        if method == "device.run_async":
            self.last_run = str(ask("command", ""))
            callback = ask("on_done")
            if callable(callback):
                callback({"code": 0, "out": "", "err": ""})
            return None
        if method == "device.run":
            self.last_run = str(ask("command", ""))
            return {"code": 0, "out": "", "err": ""}
        if method == "schedule.every":
            # The clock and the console pump are asked for; running the
            # callback once here means a test can prove the timer was set
            # without needing a Qt timer. Both are safe to fire early.
            callback = ask("callback")
            if callable(callback):
                callback()
            return None
        return super().default(method, arguments, named)

    # --- reading what the addon did ---

    def run_clear(self) -> None:
        """Forget what has been recorded; keep the desktop's window list."""
        self.calls = []

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None

    def sets_count(self, identifier: str, prop: str) -> int:
        """How many times this id was told this property since the last
        ``run_clear`` - what a skip-path check reads as a write count."""
        return sum(1 for where, arguments, _k in self.calls
                   if where == "tabs.set_widget" and len(arguments) >= 4
                   and arguments[1] == identifier and arguments[2] == prop)

    def open_entry(self, win_id: str) -> dict:
        """The last ``open_window`` descriptor this id was handed: the test
        reads its ``chrome``, ``lower``, ``min_size`` and ``spec``."""
        for where, arguments, _k in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[2] == "open_window"
                    and isinstance(arguments[3], dict)
                    and str(arguments[3].get("id") or "") == win_id):
                return arguments[3]
        return {}

    def notices(self) -> list[str]:
        return [str(arguments[0]) for where, arguments, _k in self.calls
                if where == "ui.notify"]

    def warnings(self) -> list[str]:
        return [str(arguments[1]) for where, arguments, _k in self.calls
                if where == "ui.warning"]

    def toasts(self) -> list[str]:
        return [str(arguments[1]) for where, arguments, _k in self.calls
                if where == "ui.popup"]

    def informations(self) -> list[str]:
        return [str(arguments[1]) for where, arguments, _k in self.calls
                if where == "ui.information"]

    def opens_for(self, win_id: str) -> int:
        """How many times the addon has asked the desktop to open this id.
        One means a real window; two means the layer was rebuilt or the
        reuse path was taken rather than a raise."""
        return sum(1 for where, arguments, _k in self.calls
                   if where == "tabs.set_widget" and len(arguments) >= 4
                   and arguments[2] == "open_window"
                   and isinstance(arguments[3], dict)
                   and str(arguments[3].get("id") or "") == win_id)

    def last_command(self) -> str:
        """The last string handed to ``device.run`` / ``run_async``."""
        for where, arguments, _k in reversed(self.calls):
            if where in ("device.run", "device.run_async"):
                return str(arguments[0] if arguments else "")
        return ""


# --- the sections ---

def test_manifest(addon: Any, api: Api) -> None:
    print("\n1. what the addon says it is")
    check(str(addon.ADDON.get("name")) == "BerryVM",
          f"the name the store shows: {addon.ADDON.get('name')!r}")
    check(str(addon.ADDON.get("version")) == "2.3",
          f"the shipped version is 2.3: {addon.ADDON.get('version')!r}")
    check(str(addon.ADDON.get("api")) == "1", "the api version is 1")
    check(sorted(addon.ADDON.get("requires") or []) == ["sftp", "ssh"],
          f"both transports are declared: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("connection")) == "both",
          "the manifest is honest that the panes ride the session and the "
          "terminals bring their own sockets")
    check("icon" not in addon.ADDON,
          "the manifest names no picture, so the store picks one by the rule")
    check(addon.TAB_ICON_FILE == "berryvmicon.png",
          f"the tab's mark follows {addon.ADDON.get('id', 'berryvm')}icon.png")
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / addon.TAB_ICON_FILE,
                  ROOT / "addon.store" / "assets" / "berryvm"
                  / addon.TAB_ICON_FILE]
    check(any(candidate.is_file() for candidate in candidates),
          f"the picture is where a shipped addon keeps it: {candidates}")
    ids = [pid for pid, _cap in addon.PROGRAMS]
    check(ids == [addon.P_EXPLORER, addon.P_NOTEPAD, addon.P_VIEWER,
                  addon.P_CONSOLE, addon.P_BROWSER, addon.P_MAIN,
                  addon.P_MACHINES, addon.P_SETTINGS, addon.P_CALC,
                  addon.P_PAINT, addon.P_MINE, addon.P_MEDIA,
                  addon.P_RECYCLE],
          f"the desktop ships thirteen programs in order: {ids}")
    check("Paint" in addon.CAPTIONS.values()
          and "Minesweeper" in addon.CAPTIONS.values()
          and "Media Player" in addon.CAPTIONS.values(),
          "the three new programs are captioned the way Start names them")
    check(addon.CAPTIONS[addon.P_EXPLORER] == "My Computer",
          "the file manager wears the XP name")


def test_helpers(addon: Any, api: Api) -> None:
    print("\n2. the small pure helpers")
    check(addon.port_of("22") == 22, "a port kept in a string is a port")
    check(addon.port_of("") == 22, "a blank falls back to the default")
    check(addon.port_of(0) == 22, "zero is not a port")
    check(addon.port_of(70000, 22) == 22,
          "a number out of range falls back rather than connecting nowhere")
    split = addon.split_address("root@192.168.1.1:2222")
    check(split == {"username": "root", "host": "192.168.1.1",
                    "port": "2222"},
          f"an address written as one string splits into parts: {split}")
    v6 = addon.split_address("fe80::1")
    check(v6["host"] == "fe80::1" and v6["port"] == "",
          "an IPv6 literal keeps its colons")
    check(addon.language_for("/tmp/thing.sh") == "bash",
          "a shell script gets its own colour scheme")
    check(addon.language_for("config.json") == "json",
          "a settings file is highlighted as JSON")
    check(addon.language_for("notes.txt") == "",
          "a plain text file is not painted at all")
    check(addon.is_text("a.sh") and not addon.is_text("a.bin"),
          "the suffix alone says whether the editor could try")
    check(addon.is_picture("a.png") and not addon.is_picture("a.txt"),
          "and the same rule says what the viewer could show")
    check(addon.short_name("/var/log/x.conf") == "x.conf",
          "a path's last piece is what a title has room for")
    check(addon.describe_size(2048) == "2.0 KiB",
          f"the size a refusal talks about is human-readable: "
          f"{addon.describe_size(2048)}")
    check(addon.describe_size(3 * 1024 * 1024) == "3.0 MiB",
          "the same rule reaches into MiB")
    got = addon.machines_from([{"host": "a"}, "root@b:22",
                                "nonsense", {"nope": 1}])
    check([m.get("host") for m in got] == ["a", "b", "nonsense"],
          f"a bare address and an entry with a broken host come out equal, "
          f"and the entry with no host at all is dropped: {got}")
    check(got[1]["username"] == "root",
          "a user written in front of an address is used for that entry")
    # The calculator's arithmetic: a real read of the line, no eval.
    check(addon.calc_eval("2+3*4") == 14, "the four operations bind right")
    check(addon.calc_eval("(2+3)*4") == 20, "brackets say otherwise")
    check(addon.calc_eval("10/4") == 2.5, "a fraction comes out a fraction")
    check(addon.calc_eval("7%3") == 1, "the modulo mark is known too")
    check(addon.calc_eval("-2+5") == 3, "a minus at the front is a sign")
    for bad in ("1/0", "abc", "2+", "", "1..2"):
        try:
            addon.calc_eval(bad)
            ok = False
        except ValueError:
            ok = True
        check(ok, f"{bad!r} is refused as a line, not a crash")
    # The desktop's own scale.
    api.config_values["ui_scale"] = 120
    check(addon.scale() == 120 and addon.scaled(100) == 120,
          "at 120% the sizes the desktop picks itself grow")
    api.config_values["ui_scale"] = 300
    check(addon.scale() == 160, "and the dial is clamped at its ceiling")
    api.config_values["ui_scale"] = 100


def test_home_tree(addon: Any, api: Api) -> None:
    print("\n3. the VM's home folder")
    root = Path(api.paths.data)
    for name in ("Desktop", "bin", "tmp", "config"):
        check((root / name).is_dir(), f"{name} is there after init")
    # Calling again must not re-mkdir nor wipe anything: touch a file inside
    # ``Desktop`` and check the call left it alone.
    marker = root / "Desktop" / "hello.txt"
    marker.write_text("kept")
    addon._ensure_home()
    addon._ensure_home()
    check(marker.is_file() and marker.read_text() == "kept",
          "a second _ensure_home is a no-op, not a rebuild")


def test_settings_mirror(addon: Any, api: Api) -> None:
    print("\n4. the settings mirror in config/settings.json")
    target = addon.settings_file()
    check(target.is_file(), "the mirror file exists after init")
    saved = json.loads(target.read_text())
    for key, fallback in addon.DEFAULTS.items():
        check(saved.get(key) == fallback,
              f"the file mirrors default {key!r}: {saved.get(key)!r}")
    check("wallpaper" in saved and "ui_scale" in saved,
          "the XP keys the settings window owns are mirrored too")
    # An unknown key that a hand-edit put in the file survives a re-mirror;
    # this is the same bargain Multi SSH makes with the ``quickstart`` list.
    saved["a_key_the_form_never_showed"] = "kept"
    target.write_text(json.dumps(saved))
    api.config_values["default_user"] = "admin"
    after = addon.mirror_settings()
    check(after.get("default_user") == "admin",
          "a config change reaches the mirror")
    check(after.get("a_key_the_form_never_showed") == "kept",
          "a key the form never showed survives the rewrite")
    check("password" not in after,
          "the mirror never picks up a password - none is in DEFAULTS")


def test_desktop_and_taskbar(addon: Any, api: Api) -> None:
    print("\n5. the desktop, its paint, the icon layer and the taskbar")
    fresh(api)
    addon.init(api)
    # The icon layer's shape: no bar, no grip, lowered, floor pinned.
    check(addon.ICONS in api.windows, "the icon layer stands on the desktop")
    entry = api.open_entry(addon.ICONS)
    check(entry.get("chrome") is False,
          "the layer is drawn with no title bar at all")
    check(entry.get("lower") is True,
          "and it is put behind every frame from its first showing")
    check(entry.get("min_size"), "and a hand cannot drag it under its floor")
    # The theme: three sheets, each on its own object.
    sheet = str(api.last_set(addon.DESKTOP, "style") or "")
    check("berryDesktop" in sheet and "#addonWindow" in sheet,
          "the canvas wears the wallpaper and the Luna frame")
    check("addonWindowBar" in sheet, "the title bars are painted blue")
    bar = str(api.last_set(addon.TASKBAR, "style") or "")
    check("berryStart" in bar and "berryClock" in bar,
          "the taskbar sheet paints the Start button and the tray")
    icon_sheet = str(api.last_set(addon.ICONS, "style") or "")
    check("berryDeskGrid" in icon_sheet,
          "the icon layer's own sheet makes the wallpaper show through")
    # The tiles: one per program, each with a drawn mark.
    tiles = api.last_set(addon.ICON_GRID, "rows") or []
    check(len(tiles) == len(addon.PROGRAMS),
          f"every program has its desktop mark: {len(tiles)} tiles")
    check(all(str(t.get("icon") or "").startswith("<svg") for t in tiles),
          "and each tile wears an SVG of its own")
    grid = find_kind(entry.get("spec"), "list")
    check(grid.get("icon_size") == addon.scaled(48),
          "the tiles were built at the desktop's own scale")
    check(addon.CAPTIONS[addon.P_SETTINGS]
          in [t["cells"][0] for t in tiles],
          "Display Properties is one of them")
    # Window management, and the taskbar following it.
    addon.start_program(addon.P_EXPLORER)
    check(addon.P_EXPLORER in api.windows, "My Computer is on the desktop")
    check(api.opens_for(addon.P_EXPLORER) == 1, "the desktop was told once")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    texts = [row.get("text") for row in drawn]
    check(addon.caption_for(addon.P_EXPLORER) in texts,
          "the taskbar picked the new window up")
    check(addon.caption_for(addon.ICONS) not in texts
          and "icons" not in texts,
          "and the icon layer is never offered as a button")
    # A second Start press reuses the frame rather than rebuilding it.
    addon.start_program(addon.P_EXPLORER)
    check(api.windows.count(addon.P_EXPLORER) == 1,
          "the same program opened twice is still one window")
    # The taskbar's raise, and the icons ducking back under.
    api.form[f"{addon.TASK_LIST}:selected"] = [
        addon.caption_for(addon.P_EXPLORER)]
    addon.on_widget(addon.TAB, addon.TASK_LIST, "selected", {})
    check(api.last_set(addon.DESKTOP, "raise_window") == addon.P_EXPLORER,
          "picking a taskbar row tells the desktop to raise that window")
    check(api.last_set(addon.DESKTOP, "lower_window") == addon.ICONS,
          "and every raise puts the icon layer back at the floor")
    # The × on a frame reports closed; the addon forgets the window. The
    # canvas has already taken the frame off its own list before it speaks,
    # so the fake desktop does the same and then delivers the news.
    api.windows.remove(addon.P_EXPLORER)
    addon.on_widget(addon.TAB, addon.P_EXPLORER, "closed", {"by": "user"})
    check(addon.P_EXPLORER not in addon.opened,
          "a user-driven close forgets the window on the addon's side")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    check(addon.caption_for(addon.P_EXPLORER)
          not in [row.get("text") for row in drawn],
          "and its button is gone")
    # The wallpaper shows through: the icon layer's own sheet makes the
    # frame's client body transparent, so the window-wide dark ``QWidget``
    # cannot paint a black floor over the desktop (the old bug: an always
    # black wall, because the full-size icon layer hid it).
    check("#addonWindowBody { background: transparent" in icon_sheet,
          "the icon layer's body is transparent so the wallpaper is not black")
    # The taskbar is a slim horizontal strip, not a tall column: the buttons
    # run sideways, and the list is pinned to one button's height rather than
    # Qt's 192px default that used to swallow the desktop.
    task_list = find_kind(addon.TAB_SPEC, "list")
    check(str(task_list.get("id")) == addon.TASK_LIST
          and str(task_list.get("flow") or "").lower() == "horizontal",
          "the task buttons run sideways along the bar")
    addon.start_program(addon.P_EXPLORER)
    addon.start_program(addon.P_CALC)
    check(api.last_set(addon.TASK_LIST, "height")
          == addon.scaled(addon.TASKBAR_H),
          "the bar is pinned to one button's height, not Qt's tall default")
    check(api.last_set(addon.TASK_LIST, "icon_size")
          == addon.scaled(addon.TASKBAR_ICON),
          "and its buttons wear small marks that fit a slim bar")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    check(len(drawn) >= 2
          and all(str(row.get("icon") or "").startswith("<svg")
                  for row in drawn),
          "every taskbar button wears its window's icon")
    check(addon.icon_for(addon.P_EXPLORER) == addon.ICON_COMPUTER,
          "My Computer's button wears the computer mark")
    check(addon.icon_for(addon.P_CALC) == addon.ICON_CALC,
          "and the calculator's wears its own")
    # The rewrite guard: dragging a window re-runs the taskbar refresh on
    # every mouse move, and none of it changes a button - so a refresh that
    # changes nothing must rewrite nothing, while a window coming and going
    # still moves the bar for real.
    writes = api.sets_count(addon.TASK_LIST, "items")
    addon.refresh_taskbar()
    addon.refresh_taskbar()
    check(api.sets_count(addon.TASK_LIST, "items") == writes,
          "a taskbar refresh with nothing changed rewrote the row anyway")
    api.windows.remove(addon.P_CALC)
    addon.on_widget(addon.TAB, addon.P_CALC, "closed", {"by": "user"})
    check(api.sets_count(addon.TASK_LIST, "items") == writes + 1,
          "closing a window did not land exactly one new row")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    texts = [row.get("text") for row in drawn]
    check(addon.caption_for(addon.P_CALC) not in texts
          and addon.caption_for(addon.P_EXPLORER) in texts,
          f"the bar lost the closed window and kept the other: {texts}")
    addon.start_program(addon.P_CALC)
    check(api.sets_count(addon.TASK_LIST, "items") == writes + 2,
          "reopening a program did not get the bar rewritten")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    check(addon.caption_for(addon.P_CALC)
          in [row.get("text") for row in drawn],
          "and its button came back")


def test_menus(addon: Any, api: Api) -> None:
    print("\n6. Start, the desktop's right-click, and Log Off")
    fresh(api)
    addon.init(api)
    # A Start pick opens its program.
    api.picks.append("Calculator")
    addon.start_menu()
    check(addon.P_CALC in api.windows, "Start > Calculator opened the window")
    # A tile's double-click opens by its caption.
    addon.on_widget(addon.TAB, addon.ICON_GRID, "activated",
                    {"text": "Notepad"})
    check(any(win.startswith(addon.BERRY_PREFIX) for win in api.windows),
          "double-clicking the Notepad tile opened a scratch editor")
    # A tile's own menu: just Open.
    api.picks.append("Open")
    addon.on_widget(addon.TAB, addon.ICON_GRID, "context",
                    {"text": "My Computer"})
    check(addon.P_EXPLORER in api.windows,
          "the tile menu's Open brings its program up")
    # Bare wallpaper: the desktop's menu, and Refresh rebuilds the layer.
    check(api.opens_for(addon.ICONS) == 1, "one icon layer so far")
    api.picks.append("Refresh")
    addon.on_widget(addon.TAB, addon.ICON_GRID, "context", {"text": ""})
    check(api.opens_for(addon.ICONS) == 2,
          "Refresh shut the layer and stood it up again")
    # About, and the Escape that means nothing happens.
    api.picks.append("About BerryVM")
    addon.start_menu()
    check(any("XP" in line for line in api.informations()),
          "About says what the desktop is")
    api.picks.append(None)
    addon.start_menu()
    check(api.windows.count(addon.P_CALC) == 1,
          "an Escape on the menu changes nothing")
    # Log Off shuts every program window and keeps the desktop.
    api.picks.append("Log Off")
    addon.start_menu()
    check(api.windows == [addon.ICONS],
          f"Log Off left only the icon layer: {api.windows}")
    check(not addon.opened or list(addon.opened) == [addon.ICONS],
          "and the addon's registry agrees")


def test_explorer(addon: Any, api: Api) -> None:
    print("\n7. My Computer: two panes, one main connection")
    fresh(api)
    addon.init(api)
    api.run_clear()
    home = Path(api.paths.data)
    (home / "note.txt").write_text("pane text\n")
    (home / "sub").mkdir(exist_ok=True)
    api.sftp_listing = [
        {"name": "etc", "path": "etc", "directory": True, "size": 4096},
        {"name": "readme.txt", "path": "readme.txt", "directory": False,
         "size": 40},
    ]
    addon.start_program(addon.P_EXPLORER)
    check(addon.P_EXPLORER in api.windows, "the window is on the desktop")
    remote_rows = api.last_set(f"{addon.PANE_REMOTE}_table", "rows") or []
    names = [row["cells"][0] for row in remote_rows]
    check("etc" in names and "readme.txt" in names,
          f"the device pane read the main connection: {names}")
    check(all("icon" in row for row in remote_rows),
          "every row wears its file's mark")
    # Park the local pane on the VM's home so the checks see known words.
    addon.pane_list(addon.PANE_LOCAL, str(home))
    local_rows = api.last_set(f"{addon.PANE_LOCAL}_table", "rows") or []
    lnames = [row["cells"][0] for row in local_rows]
    check("note.txt" in lnames and "sub" in lnames,
          f"the local pane lists this machine: {lnames}")
    # A pick is remembered per pane.
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "selected",
                    {"text": "readme.txt"})
    check(addon.pane_picked(addon.PANE_REMOTE) == "readme.txt",
          "the device pane remembers its cursor")
    # Double-click walks into a folder.
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "activated",
                    {"text": "sub"})
    check(str(addon.pane_path[addon.PANE_LOCAL]).endswith("sub"),
          "a double-click walks the local pane into the folder")
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "activated",
                    {"text": "etc"})
    check(addon.pane_path[addon.PANE_REMOTE] == "etc",
          "and the device pane follows the same rule")
    addon.pane_up(addon.PANE_REMOTE)
    check(addon.pane_path[addon.PANE_REMOTE] == "",
          "Up from a top-level folder is the root again")
    # Double-click on a file opens it in the editor.
    addon.pane_list(addon.PANE_LOCAL, str(home))
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "activated",
                    {"text": "note.txt"})
    editor = next((win for win in addon.berrypads), "")
    check(editor in api.windows, "a text file's double-click opened it")
    # Upload: the picked local file goes to where the device pane stands.
    addon.pane_list(addon.PANE_REMOTE, "")
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "selected",
                    {"text": "note.txt"})
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_send", "clicked", {})
    check(api.last_upload == str(home / "note.txt"), "the file was uploaded")
    check(api.last_remote_target == "note.txt",
          f"into the folder the other pane shows: {api.last_remote_target!r}")
    # Download through the row's right-click menu.
    api.picks.append("Download to local pane")
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "context",
                    {"text": "readme.txt"})
    check(api.last_download == "readme.txt",
          "the menu's Download pulled the remote file")
    check((home / "readme.txt").is_file(),
          "and it landed in the folder the local pane shows")
    # Folder, rename and delete on the device go through the api.
    api.answers.append("inbox")
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_new", "clicked", {})
    check(("mkdir", "inbox") in api.remote_ops,
          f"a new folder reached device.mkdir: {api.remote_ops}")
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "selected",
                    {"text": "readme.txt"})
    api.answers.append("notes.txt")
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_rename", "clicked", {})
    check(("rename", "readme.txt", "notes.txt") in api.remote_ops,
          "the rename kept the folder it was in")
    api.questions = False
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_delete", "clicked", {})
    check(not any(op[0] == "remove" for op in api.remote_ops),
          "a refused question deletes nothing")
    api.questions = True
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_delete", "clicked", {})
    check(("remove", "readme.txt") in api.remote_ops,
          "and an accepted one reaches device.remove")
    # Run on device: upload then sh, both over the main connection.
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "selected",
                    {"text": "note.txt"})
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_run", "clicked", {})
    check("note.txt" in api.last_command()
          and api.last_command().startswith("sh"),
          f"Run on device asked the shell for it: {api.last_command()!r}")
    # A closed connection is said, not swallowed.
    api.sftp_connected = False
    addon.pane_list(addon.PANE_REMOTE, "")
    note = str(api.last_set(f"{addon.P_EXPLORER}_note", "text")).lower()
    check("not open" in note, f"the closed connection is on the note: {note}")


def test_berrypad(addon: Any, api: Api) -> None:
    print("\n8. Notepad: local, remote, scratch, and Save As")
    fresh(api)
    addon.init(api)
    api.run_clear()
    home = Path(api.paths.data)
    text = home / "Desktop" / "note.sh"
    text.write_text("echo hi\n")
    win_id = addon.open_berrypad(str(text), "local", "note.sh")
    check(win_id and win_id in api.windows,
          f"a text file opened in the editor: {win_id}")
    check(str(api.last_set(f"{win_id}_edit", "value") or "").startswith("echo"),
          "the file's content reached the editor")
    spec = api.open_entry(win_id).get("spec") or {}
    check(find_kind(spec, "multi").get("highlight") == "bash",
          "a .sh opens coloured as a shell script")
    # Same path again: reuse the window rather than build a second.
    win2 = addon.open_berrypad(str(text), "local", "note.sh")
    check(win2 == win_id and api.windows.count(win_id) == 1,
          "the same file opens the same window, not a second copy")
    # Save writes back.
    api.form[f"{win_id}_edit:value"] = "echo saved\n"
    addon.on_widget(addon.TAB, f"{win_id}_save", "clicked", {})
    check(text.read_text() == "echo saved\n",
          "Save writes the editor's words back to the local file")
    check("saved" in str(api.last_set(f"{win_id}_note", "text")),
          "and the note says when")
    # Save As copies it to a new name and says so.
    other = home / "Desktop" / "copy.sh"
    api.file_picks.append(str(other))
    addon.on_widget(addon.TAB, f"{win_id}_saveas", "clicked", {})
    check(other.is_file() and other.read_text() == "echo saved\n",
          "Save As wrote the copy")
    check(addon.berrypads[win_id]["path"] == str(text),
          "a file that had a name keeps it after Save As")
    # A scratch buffer: Save is Save As until it finds a file.
    scratch = addon.open_berrypad("", "scratch", "Untitled")
    check(scratch in api.windows and addon.berrypads[scratch]["origin"]
          == "scratch", "Start > Notepad makes an untitled buffer")
    api.form[f"{scratch}_edit:value"] = "found it\n"
    found = home / "Desktop" / "found.txt"
    api.file_picks.append(str(found))
    addon.on_widget(addon.TAB, f"{scratch}_save", "clicked", {})
    check(found.is_file(), "a scratch Save goes through Save As")
    record = addon.berrypads[scratch]
    check(record["path"] == str(found) and record["origin"] == "local",
          "and the buffer adopts the file it was saved to")
    # Remote editing over the main connection, both ways.
    api.sftp_text = "hello from the device"
    remote_win = addon.open_berrypad("readme.txt", "remote", "readme.txt")
    check(str(api.last_set(f"{remote_win}_edit", "value") or "")
          == "hello from the device", "the remote text reached the editor")
    api.form[f"{remote_win}_edit:value"] = "edited there\n"
    addon.on_widget(addon.TAB, f"{remote_win}_save", "clicked", {})
    check(api.form.get("remote_written:readme.txt") == "edited there\n",
          "Save on a remote file writes over the connection")
    # A binary name is refused with a saying, before any window is drawn.
    big = home / "Desktop" / "firm.bin"
    big.write_bytes(b"\x00\x01\x02")
    before = list(api.windows)
    refused = addon.open_berrypad(str(big), "local", "firm.bin")
    check(refused == "" and api.windows == before,
          "the editor refuses a non-text suffix without opening a window")
    check(any("text file" in line.lower() for line in api.notices()),
          "the notice says what the refusal is about")
    # A file over the cap is refused with a size in the message.
    huge = home / "Desktop" / "huge.txt"
    huge.write_text("x" * (addon.MAX_SIZE + 10))
    refused = addon.open_berrypad(str(huge), "local", "huge.txt")
    check(refused == "" and api.windows == before,
          "a text file past the cap is refused before any window is drawn")
    check(any("cap" in line.lower() for line in api.notices()),
          "the refusal names the editor's cap")


def test_viewer(addon: Any, api: Api) -> None:
    print("\n9. Picture Viewer: local, pulled, and zoomed")
    fresh(api)
    addon.init(api)
    api.run_clear()
    home = Path(api.paths.data)
    pic = home / "Desktop" / "view.png"
    pic.write_bytes(b"\x89PNG a picture")
    win = addon.open_viewer(str(pic), "local")
    check(win in api.windows, f"a local picture opened: {win}")
    check(api.last_set(f"{win}_pic", "value") == str(pic),
          "the viewer's leaf was handed the file")
    same = addon.open_viewer(str(pic), "local")
    check(same == win, "the same picture raises its viewer, not a second")
    # A remote picture is pulled down over the main connection first.
    rw = addon.open_viewer("wall.png", "remote")
    check(api.last_download == "wall.png",
          "the remote picture went through device.download")
    check(rw and rw != win and api.last_set(f"{rw}_pic", "value"),
          "and it landed in a viewer of its own")
    # Zoom: the leaf was built at one size, so the window is replaced.
    old_size = int(addon.viewers[win]["size"])
    addon.on_widget(addon.TAB, f"{win}_zin", "clicked", {})
    check(win not in addon.viewers, "zooming shut the old viewer window")
    zoomed = [rec for rec in addon.viewers.values()
              if rec.get("origin") == "local"]
    check(zoomed and int(zoomed[0]["size"]) == int(round(old_size * 1.5)),
          "and stood a bigger one up in its place")
    # The Open Local button puts a new picture in the same window.
    pic2 = home / "Desktop" / "second.png"
    pic2.write_bytes(b"\x89PNG another")
    stay = addon.open_viewer(str(pic2), "local")
    api.file_picks.append(str(pic))
    addon.on_widget(addon.TAB, f"{stay}_local", "clicked", {})
    check(api.last_set(f"{stay}_pic", "value") == str(pic),
          "Open Local re-shows in the window it came from")
    check(addon.viewers[stay]["path"] == str(pic),
          "and the window now knows which picture it holds")
    # With the connection down, the pull says so and opens nothing.
    api.sftp_connected = False
    before = list(api.windows)
    check(addon.open_viewer("nope.png", "remote") == ""
          and api.windows == before,
          "a remote picture with no connection is refused politely")


def test_console(addon: Any, api: Api) -> None:
    print("\n10. Command Prompt: a real shell in a window")

    class FakeStdin:
        def __init__(self) -> None:
            self.typed: list[str] = []

        def write(self, text: str) -> None:
            self.typed.append(str(text))

        def flush(self) -> None:
            pass

    class FakeStdout:
        """Yields the queued lines, then waits at a gate: the reader
        thread stays alive - so the shell has not 'ended' - while the
        test's answers never depend on timing."""

        def __init__(self, lines: list[str], gate: threading.Event) -> None:
            self._lines = list(lines)
            self._gate = gate

        def __iter__(self) -> Any:
            return self

        def __next__(self) -> str:
            if self._lines:
                return self._lines.pop(0)
            self._gate.wait(5.0)
            if self._lines:
                return self._lines.pop(0)
            raise StopIteration

    class FakeProc:
        def __init__(self, lines: list[str]) -> None:
            self.stdin = FakeStdin()
            self.gate = threading.Event()
            self.stdout = FakeStdout(lines, self.gate)
            self.terminated = False

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True

    fresh(api)
    addon.init(api)
    api.run_clear()
    real_shell = addon._start_shell
    spawned: list[Any] = []

    def fake_shell(cwd: str) -> Any:
        proc = FakeProc([f"hello from {Path(cwd).name}\n", "one more\n"])
        spawned.append(proc)
        return proc

    addon._start_shell = fake_shell
    try:
        win_id = addon.open_console(str(Path(api.paths.data)))
        check(win_id in api.windows, f"a console window stands up: {win_id}")
        check(spawned and spawned[0].stdin is not None,
              "a real shell process was asked for (here, a fake)")
        con = addon.consoles.get(win_id) or {}
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
                "hello" in line for line in list(con.get("pending", ()))):
            time.sleep(0.02)
        addon.pump_consoles()
        drawn = str(api.last_set(f"{win_id}_out", "append") or "")
        check("hello from" in drawn, "the shell's words reach the screen")
        # A typed line goes into the shell's keys, echoed on the screen.
        api.form[f"{win_id}_in:value"] = "echo hi"
        addon.on_widget(addon.TAB, f"{win_id}_in", "returned", {})
        check(spawned[0].stdin.typed == ["echo hi\n"],
              "the typed line is written and flushed to the shell")
        addon.pump_consoles()
        drawn = str(api.last_set(f"{win_id}_out", "append") or "")
        check("> echo hi" in drawn, "the window echoes what was typed")
        # An empty pump writes nothing at all - no spurious clears.
        api.run_clear()
        addon.pump_consoles()
        check(api.last_set(f"{win_id}_out", "append") is None,
              "an empty tray writes nothing")
        # Closing the window lets go of the child.
        addon.on_widget(addon.TAB, win_id, "closed", {})
        check(spawned[0].terminated, "shutting the window terminates it")
        check(win_id not in addon.consoles, "and the addon forgets the shell")
    finally:
        for proc in spawned:
            proc.gate.set()
        addon._start_shell = real_shell


def test_main_ssh(addon: Any, api: Api) -> None:
    print("\n11. Terminal: the profile's host, on its own socket")
    fresh(api)
    addon.init(api)
    api.run_clear()
    api.profile = {"host": "", "port": 22, "username": "root"}
    opened = addon.open_main_ssh()
    check(opened == "", "an empty host opens no window")
    check(addon.P_MAIN not in api.windows,
          "and the desktop was never asked for the frame")
    check(any("host" in line.lower() for line in api.warnings()),
          "a warning says why")
    # With a host, the same call opens the window and asks for the password.
    api.profile = {"host": "192.168.1.9", "port": 22, "username": "root"}
    api.answers.append("hunter2")
    opened = addon.open_main_ssh()
    check(opened == addon.P_MAIN and addon.P_MAIN in api.windows,
          "with a host, the Terminal opens its window")
    details = api.last_set(f"{addon.P_MAIN}_term", "connect") or {}
    check("password" in details and details.get("host") == "192.168.1.9",
          "the password and host reach the terminal's own socket")
    check("password" not in json.dumps(api.written),
          "and no password was written to the addon's config")
    leaf = find_kind(api.open_entry(addon.P_MAIN).get("spec"), "terminal")
    check(leaf.get("font_size") == addon.term_font(),
          "the shell is built at the configured text size")
    # The shell's news lands on the state line.
    addon.on_widget(addon.TAB, f"{addon.P_MAIN}_term", "ready", {})
    check("connected" in str(api.last_set(f"{addon.P_MAIN}_state", "text")),
          "a ready shell says connected on the window")
    addon.on_widget(addon.TAB, f"{addon.P_MAIN}_disc", "clicked", {})
    check(api.last_set(f"{addon.P_MAIN}_term", "disconnect") is not None
          or api.called("tabs.set_widget"),
          "the Disconnect button reaches the terminal")
    # A cancelled password leaves the window standing with a Connect.
    fresh(api)
    addon.init(api)
    api.run_clear()
    api.answers.append(None)
    addon.open_main_ssh()
    check(str(api.last_set(f"{addon.P_MAIN}_state", "text")) == "cancelled",
          "walking out of the password box is said, not guessed")
    addon.on_widget(addon.TAB, f"{addon.P_MAIN}_conn", "clicked", {})
    api.answers.append("second chance")
    addon.on_widget(addon.TAB, f"{addon.P_MAIN}_conn", "clicked", {})
    check(api.last_set(f"{addon.P_MAIN}_term", "connect") is not None,
          "and Connect retries the same window")


def test_machines(addon: Any, api: Api) -> None:
    print("\n12. Machines keeps its own list in the config")
    fresh(api)
    addon.init(api)
    api.run_clear()
    api.config_values["machines"] = [
        {"name": "Router", "host": "192.168.1.1", "port": 22,
         "username": "root"},
    ]
    addon.reload_machines()
    addon.draw_machines()
    rows = api.last_set(f"{addon.P_MACHINES}_list", "items") or []
    check(rows and rows[0]["text"].startswith("Router"),
          f"an entry reaches the list: {rows}")
    # Connect the row: the addon opens a fresh msh1 window and hands the
    # terminal its connection with a password that never touches the file.
    api.answers.append("pw")
    addon.machine_row = 0
    addon.machine_connect(addon.machines[0])
    check("msh1" in api.windows, "a machine window opened")
    check(api.last_set("msh1_term", "connect") is not None,
          "the terminal was handed a connect dict")
    check("password" not in json.dumps(api.written),
          "no password reached config.set")
    # A machine's news lands on its own window's state line.
    addon.on_widget(addon.TAB, "msh1_term", "failed", {"error": "refused"})
    check("refused" in str(api.last_set("msh1_state", "text")),
          "the failure is said on the machine's window")
    # Editing writes the whole list back, so an external entry survives.
    api.dialogs.append({"name": "AP", "host": "10.0.0.1", "port": 22,
                        "username": "root", "key_file": ""})
    addon.machine_row = 0
    addon.machine_edit()
    stored = api.written.get("machines")
    check(isinstance(stored, list) and stored[0].get("name") == "AP",
          f"the changed entry reached config.set('machines'): {stored}")


def test_display_properties(addon: Any, api: Api) -> None:
    print("\n13. Display Properties: Apply reaches everything")
    fresh(api)
    addon.init(api)
    # The window's controls, answered the way the stub reads form values.
    api.form["set_wall:value"] = "Night"
    api.form["set_file:value"] = ""
    api.form["set_scale:value"] = 120
    api.form["set_font:value"] = 14
    api.form["set_width:value"] = 600
    api.form["set_height:value"] = 400
    api.form["set_clock:value"] = False
    api.form["set_user:value"] = "admin"
    api.form["set_port:value"] = 2222
    api.form["set_trust:value"] = True
    # One terminal standing, so the text size has somewhere to land.
    api.answers.append("pw")
    addon.open_main_ssh()
    addon.on_widget(addon.TAB, "set_apply", "clicked", {})
    check(api.written.get("wallpaper") == "Night",
          "the named wallpaper reached the config")
    check(api.written.get("ui_scale") == 120
          and api.written.get("term_font") == 14,
          "the two size dials reached the config too")
    check(api.written.get("clock_on") is False, "the clock was switched off")
    sheet = str(api.last_set(addon.DESKTOP, "style") or "")
    check("060a1c" in sheet, "the Night gradient is the canvas's background")
    check(api.last_set(addon.CLOCK, "text") == "",
          "a clock turned off empties the tray's text")
    check(api.opens_for(addon.ICONS) == 2,
          "a scale that moved rebuilt the icon layer")
    entry = api.open_entry(addon.ICONS)
    grid = find_kind(entry.get("spec"), "list")
    check(grid.get("icon_size") == addon.scaled(48),
          "and the new layer was built at the new scale")
    check(api.last_set(f"{addon.P_MAIN}_term", "font_size") == 14,
          "every shell on the desktop was handed the new text size")
    # The two picture doors: a solid colour, then a wallpaper file.
    api.colour_picks.append("#ff0000")
    addon.on_widget(addon.TAB, "set_colour", "clicked", {})
    check(api.written.get("wallpaper") == "Solid",
          "picking a colour switches the desktop to it")
    check("#ff0000" in str(api.last_set(addon.DESKTOP, "style") or ""),
          "and the solid fill is painted straight away")
    pic = Path(api.paths.data) / "Desktop" / "wall.png"
    pic.write_bytes(b"\x89PNG wall")
    api.file_picks.append(str(pic))
    addon.on_widget(addon.TAB, "set_picture", "clicked", {})
    check(api.written.get("wallpaper") == "Picture",
          "picking a picture switches the desktop to it")
    check("url(" in str(api.last_set(addon.DESKTOP, "style") or ""),
          "and the file is asked for as the background")
    # Defaults means it: everything back, bookmarks untouched.
    addon.on_widget(addon.TAB, "set_defaults", "clicked", {})
    check(api.written.get("ui_scale") == 100
          and api.written.get("wallpaper") == "Bliss",
          "Defaults put the shipped look back")
    check("machines" not in api.written,
          "Defaults never touches a person's bookmarks")
    # A wallpaper combo's changed event paints without waiting for Apply.
    addon.on_widget(addon.TAB, "set_wall", "changed", {"text": "Aqua"})
    check("063a4e" in str(api.last_set(addon.DESKTOP, "style") or ""),
          "naming a gradient in the combo repaints the canvas at once")


def test_extras(addon: Any, api: Api) -> None:
    print("\n14. the calculator and the logon bubble")
    fresh(api)
    addon.init(api)
    check(any("Desktop is up" in line for line in api.toasts()),
          "the desktop says hello when it goes up")
    api.run_clear()

    def key_id(key: str) -> str:
        for wid, k in addon.CALC_IDS.items():
            if k == key:
                return wid
        return ""

    addon.start_program(addon.P_CALC)
    for key in ("1", "2", "+", "8"):
        addon.on_widget(addon.TAB, key_id(key), "clicked", {})
    addon.on_widget(addon.TAB, key_id("="), "clicked", {})
    check(str(api.last_set("calc_disp", "value")) == "20",
          "the keys build a line and = works it out: 12+8 is 20")
    addon.on_widget(addon.TAB, key_id("C"), "clicked", {})
    addon.on_widget(addon.TAB, key_id("("), "clicked", {})
    addon.on_widget(addon.TAB, key_id("="), "clicked", {})
    check(str(api.last_set("calc_disp", "value")) == "Error",
          "a bad line shows Error, not a traceback")
    check(str(api.last_set("calc_disp", "value")) == "Error",
          "and it stays up until the next key")
    addon.on_widget(addon.TAB, key_id("7"), "clicked", {})
    check(str(api.last_set("calc_disp", "value")) == "7",
          "the next key clears the mistake away")


# --- the harness ---

def fresh(api: Api) -> None:
    """Start a section clean: no state on the addon's side, nothing recorded.

    The addon keeps its window registry, its counters for ``bp<n>``, ``pv<n>``
    and ``msh<n>``, its pane cursors and its calculator line on module
    globals; a test that wants to open something has to see a desktop that
    starts empty, so every bit of that state is reset here before
    :func:`init` runs.
    """
    addon_module.api = api
    addon_module.opened = {}
    addon_module.berrypads = {}
    addon_module.viewers = {}
    addon_module.consoles = {}
    addon_module.terminals = {}
    addon_module.pane_pick = {}
    addon_module.pane_rows = {}
    addon_module.pane_path = {}
    addon_module.machines = []
    addon_module.machine_row = -1
    addon_module.machine_seq = 0
    addon_module.berry_seq = 0
    addon_module.view_seq = 0
    addon_module.console_seq = 0
    addon_module.calc_expr = ""
    addon_module.paint_state = {}
    addon_module.media_state = {}
    addon_module.minesweeper = {}
    addon_module.home_ready = False
    # The taskbar's skip-when-unchanged signature and the tray clock's last
    # written text are module state too; a fresh section has to be able to
    # paint its first row, not be told it already showed it.
    addon_module._taskbar_shown = ()
    addon_module._clock_shown = None
    addon_module._desk_positions = {}
    addon_module._desk_renames = {}
    addon_module._desk_recycled = []
    addon_module.minimized = {}
    addon_module.attention = {}
    addon_module._flash_on = False
    api.windows = []
    api.titles = {}
    api.form = {}
    api.answers = []
    api.dialogs = []
    api.picks = []
    api.file_picks = []
    api.colour_picks = []
    api.questions = True
    api.written = {}
    api.sftp_connected = True
    api.ssh_connected = True
    api.sftp_listing = []
    api.sftp_text = ""
    api.sftp_size = 0
    api.profile = {"host": "192.168.1.1", "port": 22,
                   "username": "root", "key_file": ""}
    api.last_upload = ""
    api.last_remote_target = ""
    api.last_download = ""
    api.remote_ops = []
    api.last_run = ""
    api.config_values = {}
    api.run_clear()


def test_net_and_wallpaper(addon: Any, api: Api) -> None:
    """The tray's WiFi mark reads the machine's own link, and the icon layer
    lets the wallpaper through its whole subtree.

    Two things a person sees daily and cannot be proven from the SSH/SFTP
    switches alone: the network mark must say *connected* whenever the
    machine can reach the net (a browser working is exactly that), whether or
    not the app has opened a device session; and the desktop must not be a
    black box - the chromeless icon layer spans the whole desktop, so every
    widget it wraps has to be transparent for the wallpaper to show."""
    print("\n15. the WiFi mark reads the link, and the wallpaper shows through")
    fresh(api)
    addon.init(api)
    api.run_clear()
    real_link = addon._link_up
    try:
        # No SSH or SFTP, but the machine is online: WiFi reads connected.
        api.ssh_connected = api.sftp_connected = False
        addon._link_up = lambda: True
        summary = addon._net_summary()
        check("WiFi: connected" in summary,
              f"an online machine with no device session said offline: "
              f"{summary!r}")
        check("SSH:  offline" in summary and "SFTP: offline" in summary,
              f"the two sessions were reported up while they were down: "
              f"{summary!r}")
        # Offline link, no session: honest offline.
        addon._link_up = lambda: False
        check("WiFi: offline" in addon._net_summary(),
              "no link and no session still showed connected")
        # A live session is proof of a network even with the probe unavailable.
        api.ssh_connected = True
        check("WiFi: connected" in addon._net_summary(),
              "an open SSH session did not put the mark to connected")
    finally:
        addon._link_up = real_link
        api.run_clear()
    # The icon layer's sheet must make the frame's whole widget subtree
    # transparent, not just the frame and its body - the intermediate column
    # the grid is wrapped in was what painted a black box over the wallpaper.
    sheet = str(addon.ICON_FRAME_SHEET)
    check("#addonWindow QWidget { background: transparent" in sheet,
          "the icon layer does not clear its whole subtree, so a container "
          "widget paints a black box over the wallpaper")


def test_paint_mine_media(addon: Any, api: Api) -> None:
    """The three new programs and the api kinds they ride on.

    Paint drives a ``canvas`` leaf through tool / colour / width / undo /
    clear / save, Minesweeper's board logic is checked as pure functions and
    its field is built as a ``grid`` of small buttons, and Media Player drives
    a ``player`` leaf over a playlist. None of it needs Qt: the checks read
    the commands the addon hands its widgets and the specs it opens."""
    print("\n16. Paint, Minesweeper and Media Player")
    fresh(api)
    addon.init(api)

    # --- Paint: the canvas drawing program ---
    addon.start_program(addon.P_PAINT)
    check(addon.P_PAINT in api.windows, "Paint opened a window")
    entry = api.open_entry(addon.P_PAINT)
    surface = find_kind(entry.get("spec"), "canvas")
    check(surface.get("id") == f"{addon.P_PAINT}_surface",
          "Paint's window carries a canvas surface")
    api.run_clear()
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_pt_rect", "clicked", {})
    check(api.last_set(f"{addon.P_PAINT}_surface", "tool") == "rect",
          "a tool button names that tool on the canvas")
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_width", "changed",
                    {"value": 8})
    check(api.last_set(f"{addon.P_PAINT}_surface", "width") == 8,
          "the width slider's value is handed to the surface")
    api.colour_picks.append("#ff0000")
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_color", "clicked", {})
    check(api.last_set(f"{addon.P_PAINT}_surface", "color") == "#ff0000",
          "the colour button's pick reaches the surface")
    check(api.last_set(f"{addon.P_PAINT}_colorname", "text") == "#ff0000",
          "and the chosen hex is shown on the row")
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_undo", "clicked", {})
    check(api.last_set(f"{addon.P_PAINT}_surface", "undo") is True,
          "Undo drives the canvas's undo")
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_clear", "clicked", {})
    check(api.last_set(f"{addon.P_PAINT}_surface", "clear") == "",
          "Clear blanks the surface")
    out = str(Path(api.paths.data) / "draw.png")
    api.file_picks.append(out)
    api.questions = False
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_save", "clicked", {})
    check(api.last_set(f"{addon.P_PAINT}_surface", "save") == out,
          "Save As hands the surface a path to write")
    api.run_clear()
    api.file_picks.append(str(Path(api.paths.data) / "noext"))
    addon.on_widget(addon.TAB, f"{addon.P_PAINT}_save", "clicked", {})
    saved = str(api.last_set(f"{addon.P_PAINT}_surface", "save") or "")
    check(saved.endswith(".png"),
          f"a name with no picture suffix is saved as a PNG: {saved!r}")

    # --- Minesweeper: the pure board logic ---
    board = addon.mine_make_board(9, 9, 10)
    check(board["rows"] == 9 and board["cols"] == 9 and board["mines"] == 10,
          "a fresh Beginner board is 9x9 with ten mines")
    check(all(len(row) == 9 for row in board["cells"])
          and len(board["cells"]) == 9, "the grid holds 81 cells")
    check(board["state"] == "ready",
          "no mines are laid until the first press")
    addon.mine_place(board, 4, 4)
    placed = sum(cell["mine"] for row in board["cells"] for cell in row)
    check(placed == 10, f"ten mines were placed: {placed}")
    check(not board["cells"][4][4]["mine"],
          "the first-pressed square stayed clear")
    check(all(not board["cells"][r][c]["mine"]
              for r, c in addon._mine_neighbors(9, 9, 4, 4)),
          "and the squares around it were kept mine-free, so it opens blank")
    check(board["state"] == "playing", "placing the mines starts the game")
    addon.mine_toggle_flag(board, 0, 0)
    check(board["cells"][0][0]["flagged"] and board["flags"] == 1,
          "a flag can be planted on a covered square")
    addon.mine_toggle_flag(board, 0, 0)
    check(not board["cells"][0][0]["flagged"] and board["flags"] == 0,
          "pressing again takes the flag back off")
    addon.mine_reveal(board, 4, 4)
    check(board["cells"][4][4]["revealed"], "opening the safe centre works")
    check(board["state"] != "lost", "opening a safe square never loses")
    # A press on a known mine loses and bares every mine.
    mine_at = next((r, c) for r in range(9) for c in range(9)
                   if board["cells"][r][c]["mine"])
    addon.mine_reveal(board, mine_at[0], mine_at[1])
    check(board["state"] == "lost", "hitting a mine ends the game")
    check(all(cell["revealed"] for row in board["cells"] for cell in row
              if cell["mine"]), "and every mine is laid bare")

    # --- Minesweeper: the window it builds and drives ---
    addon.start_program(addon.P_MINE)
    check(addon.P_MINE in api.windows, "Minesweeper opened a window")
    entry = api.open_entry(addon.P_MINE)
    grid = find_kind(entry.get("spec"), "grid")
    cells = [c for c in (grid.get("children") or []) if isinstance(c, dict)]
    check(len(cells) == 9 * 9, f"Beginner lays 81 squares: {len(cells)}")
    check(cells[0].get("id") == "mc0c0" and cells[0].get("style"),
          "each square is named mc<r>c<c> and sized by its style")
    combo = find_kind(entry.get("spec"), "combo")
    check("Beginner" in (combo.get("items") or [])
          and "Expert" in (combo.get("items") or []),
          "the difficulty combo offers the levels")
    api.run_clear()
    addon.on_widget(addon.TAB, "mc0c0", "clicked", {})
    live = addon.minesweeper.get("board")
    check(live is not None and live["cells"][0][0]["revealed"],
          "a press lays the mines and opens the square")
    check(api.last_set("mine_count", "text") is not None,
          "and the readouts are redrawn")
    addon.on_widget(addon.TAB, "mine_level", "changed", {"text": "Expert"})
    entry = api.open_entry(addon.P_MINE)
    grid = find_kind(entry.get("spec"), "grid")
    cells = [c for c in (grid.get("children") or []) if isinstance(c, dict)]
    check(len(cells) == 16 * 30, f"Expert rebuilds the field to 16x30: {len(cells)}")
    check(addon.minesweeper.get("level") == "Expert",
          "a difficulty change is remembered")

    # --- Media Player: the player leaf over a playlist ---
    addon.start_program(addon.P_MEDIA)
    check(addon.P_MEDIA in api.windows, "Media Player opened a window")
    entry = api.open_entry(addon.P_MEDIA)
    player = find_kind(entry.get("spec"), "player")
    check(player.get("id") == f"{addon.P_MEDIA}_player",
          "the window carries an audio player leaf")
    check("state" in (player.get("on") or [])
          and "done" in (player.get("on") or []),
          "the leaf reports state and done")
    listing = find_kind(entry.get("spec"), "list")
    check("activated" in (listing.get("on") or []),
          "the playlist plays a row on activate")
    api.run_clear()
    track = str(Path(api.paths.data) / "song.mp3")
    Path(track).write_bytes(b"fake audio")
    api.file_picks.append(track)
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_open", "clicked", {})
    check(api.last_set(f"{addon.P_MEDIA}_player", "open") == track,
          "the picked track is loaded onto the player")
    check(api.last_set(f"{addon.P_MEDIA}_player", "play") is True,
          "and playback is started")
    rows = api.last_set(f"{addon.P_MEDIA}_list", "items") or []
    check(rows and "song.mp3" in str(rows[0]),
          f"the track lands in the playlist: {rows}")
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_volume", "changed",
                    {"value": 55})
    check(api.last_set(f"{addon.P_MEDIA}_player", "volume") == 55,
          "the volume slider drives the leaf")
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_player", "done", {})
    now = str(api.last_set(f"{addon.P_MEDIA}_now", "text") or "")
    check("Playlist finished" in now,
          f"the last track ending says so: {now!r}")


def test_window_management(addon: Any, api: Api) -> None:
    """Show desktop, the taskbar's right-click, and cascade / tile.

    These drive the ``windows`` canvas' window-management commands, so the
    check is which commands the addon issues against the fake desktop: every
    program window is minimised on Show desktop, and a cascade or a tile
    moves and sizes each window once."""
    print("\n17. Show desktop, the taskbar menu, cascade and tile")
    fresh(api)
    addon.init(api)
    addon.start_program(addon.P_EXPLORER)
    addon.start_program(addon.P_CALC)
    ids = addon.program_window_ids()
    check(addon.P_EXPLORER in ids and addon.P_CALC in ids,
          "two program windows stand on the desktop")

    def prop_values(prop: str) -> list:
        return [a[3] for _w, a, _k in api.calls
                if _w == "tabs.set_widget" and len(a) >= 4 and a[2] == prop]

    api.run_clear()
    addon.show_desktop()
    mins = prop_values("minimize_window")
    check(set(mins) >= {addon.P_EXPLORER, addon.P_CALC},
          f"Show desktop hides every program window: {mins}")
    check(addon.minimized.get(addon.P_EXPLORER)
          and addon.minimized.get(addon.P_CALC),
          "and each is marked hidden so the taskbar row shows it")

    addon.minimized.clear()
    api.run_clear()
    addon.cascade_windows()
    check(len(prop_values("move_window")) >= 2
          and len(prop_values("resize_window")) >= 2,
          "a cascade moves and sizes each window")
    api.run_clear()
    addon.tile_windows(vertical=True)
    check(len(prop_values("resize_window")) >= 2,
          "tiling sizes each window as well")

    # Over an empty bar the menu offers the layout list; picking a word runs it.
    api.picks.append("Cascade windows")
    addon.minimized.clear()
    api.run_clear()
    addon.taskbar_menu("")
    choice = [a for _w, a, _k in api.calls if _w == "ui.choose"]
    check(choice and "Cascade windows" in (choice[0][0] if choice[0] else []),
          "the empty-bar menu lists cascade, tile and Show desktop")
    check(len(prop_values("move_window")) >= 2,
          "and naming Cascade lays the windows out")

    # Over a window's button the menu is that window's own; Close shuts it.
    addon.minimized.clear()
    api.picks.append("Close")
    api.run_clear()
    addon.taskbar_menu(addon.CAPTIONS[addon.P_CALC])
    check(addon.P_CALC not in addon.program_window_ids(),
          "closing a taskbar button takes its window off the desktop")


def main() -> int:
    global addon_module
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    os.environ["RASCONF_ADDON_DATA"] = str(WORK)
    addon = addon_client.load_addon(SOURCE)
    addon_module = addon
    api = Api(addon_id="berryvm")
    print(f"berryvm: testing addon at {SOURCE.name}")

    test_manifest(addon, api)
    addon.init(api)
    check(api.called("config.declare"), "its config form is declared")
    check(api.called("tabs.add_tab") and api.called("tabs.set_icon"),
          "the tab is added, and wears its own mark")
    check(isinstance(api.declared.get("defaults"), dict)
          and "clock_on" in api.declared["defaults"],
          f"the defaults go with the fields: {list(api.declared)}")
    spec = api.asked("tabs.add_tab", "spec") or {}
    canvas = find_kind(spec, "windows")
    check(canvas.get("id") == addon.DESKTOP
          and "activated" in (canvas.get("on") or []),
          "the tab is born as a desktop that reports its windows")

    test_helpers(addon, api)
    test_home_tree(addon, api)
    test_settings_mirror(addon, api)
    test_desktop_and_taskbar(addon, api)
    test_menus(addon, api)
    test_explorer(addon, api)
    test_berrypad(addon, api)
    test_viewer(addon, api)
    test_console(addon, api)
    test_main_ssh(addon, api)
    test_machines(addon, api)
    test_display_properties(addon, api)
    test_extras(addon, api)
    test_net_and_wallpaper(addon, api)
    test_paint_mine_media(addon, api)
    test_window_management(addon, api)

    shutil.rmtree(WORK, ignore_errors=True)
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall berryvm checks passed")
    return 0


if __name__ == "__main__":
    STATUS = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The verdict is printed: go out now without walking Qt through the
    # interpreter's shutdown - same rule every sibling suite follows.
    os._exit(STATUS)
