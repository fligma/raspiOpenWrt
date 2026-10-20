#!/usr/bin/env python3
"""A test for the BerryVM addon - the XP-styled desktop in one tab.

BerryVM is a desktop, so the shapes worth checking without a window are the
ones the desktop itself is supposed to keep in step: an icon layer that is
born chromeless and stays at the back, a taskbar that follows what the
canvas is holding, a Start flyout that opens programs and a Log Off that
signs back out to the welcome screen, a local file picker and a separate
device browser that hand files
to each other, an editor that refuses what it cannot hold and can Save As its
way to a new file, a viewer that pulls a remote picture down before showing
it, a console and an ssh window that feed real shells, and a Control Panel
window whose sliders and toggles reach the config, the paint and
the icons - both on Apply and live as each dial moves.

Covered here:

* the manifest, and the picture the store is expected to show for it;
* the pure helpers - suffix to language, a byte count that reads as
  something, and the desktop's own scale;
* the VM's home folder: ``Desktop``, ``bin``, ``tmp``, ``config``, made
  once and not again;
* the ``config/settings.json`` mirror: every default key written, unknown
  keys surviving, no password in it;
* the theme sheets, the icon layer's shape, and the taskbar following the
  desktop's windows;
* the Start flyout, the desktop's right-click, and Log Off;
* the local-only My Computer and the separate berry sftp browser: opening
  across the file session, uploads, downloads, folders, renames and deletes;
* berry ssh: the connect prompt, the parsed settings, a password and a key
  file, and the shell's news landing on its window;
* files moved between the two windows by a drag-and-drop and by Copy/Paste;
* Notepad's refusals, its save, its Save As, and a scratch buffer finding
  its file;
* the Picture Viewer's remote pull and its window-replacing zoom;
* a local console driven through ``spawn``, with the aliases it carries;
* the Control Panel: the density sliders, the caption controls and their
  live preview, the wallpaper pickers, the size presets, and Defaults;
* Paint's canvas and Media Player's playlist, the theme card's repaint,
  the window-management layout, the XP cosplay - boot, login, accounts and
  power - and the logon bubble.

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


def collect_ids(node: Any) -> set:
    """Every ``id`` a spec tree carries, so a test can say a window holds one
    table and not another without knowing the shape around either."""
    found: set = set()
    if isinstance(node, dict):
        if node.get("id"):
            found.add(str(node["id"]))
        for value in node.values():
            found |= collect_ids(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= collect_ids(item)
    return found


def find_id(node: Any, want: str) -> dict:
    """The one node of a spec tree carrying id ``want`` - the way a test
    reaches a named control to read its ``expand`` or ``password`` flag
    without knowing where in the layout it sits."""
    if isinstance(node, dict):
        if str(node.get("id") or "") == want:
            return node
        for value in node.values():
            found = find_id(value, want)
            if found:
                return found
    elif isinstance(node, (list, tuple)):
        for item in node:
            found = find_id(item, want)
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
    check(str(addon.ADDON.get("version")) == "2.10",
          f"the shipped version is 2.10: {addon.ADDON.get('version')!r}")
    check(str(addon.ADDON.get("api")) == "1", "the api version is 1")
    check(sorted(addon.ADDON.get("requires") or []) == ["sftp", "ssh"],
          f"both transports are declared: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("connection")) == "app",
          "the manifest is honest that everything rides the app's own "
          "session - the shells are this machine's local processes")
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
                  addon.P_CONSOLE, addon.P_BROWSER, addon.P_SETTINGS,
                  addon.P_PAINT, addon.P_MEDIA, addon.P_SSH, addon.P_SFTP],
          f"the desktop ships ten programs in order: {ids}")
    check("Paint" in addon.CAPTIONS.values()
          and "Media Player" in addon.CAPTIONS.values(),
          "the new programs are captioned the way Start names them")
    check("berry ssh" in addon.CAPTIONS.values()
          and "berry sftp" in addon.CAPTIONS.values(),
          "the SSH terminal and the SFTP browser are captioned too")
    check(addon.CAPTIONS[addon.P_EXPLORER] == "My Computer",
          "the file manager wears the XP name")
    check(addon.CAPTIONS[addon.P_SETTINGS] == "Control Panel",
          "the settings window is named for the control panel it mocks")


def test_helpers(addon: Any, api: Api) -> None:
    print("\n2. the small pure helpers")
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
    # The desktop's own scale.
    api.config_values["ui_scale"] = 120
    check(addon.scale() == 120 and addon.scaled(100) == 120,
          "at 120% the sizes the desktop picks itself grow")
    api.config_values["ui_scale"] = 400
    check(addon.scale() == 300, "and the widened dial is clamped at its top")
    api.config_values["ui_scale"] = 30
    check(addon.scale() == 60, "and at its bottom")
    del api.config_values["ui_scale"]
    # The taskbar's own marks: read from config, clamped to their ranges.
    check(addon.task_icon() == addon.DEFAULTS["task_icon"]
          and addon.tray_icon() == addon.DEFAULTS["tray_icon"],
          "the taskbar and tray item sizes default to what the screen fit chose")
    api.config_values["task_icon"] = 64
    api.config_values["tray_icon"] = 4
    check(addon.task_icon() == 40 and addon.tray_icon() == 10,
          "and both dials clamp to their own ranges")
    del api.config_values["task_icon"]
    del api.config_values["tray_icon"]


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
    # The memo is per user, not per process: logging in under a new name
    # moves ``vm_home()``, and a home nobody built is the reason a real
    # machine's console once died of ENOENT at spawn. The new user's tree
    # has to be there before a shell, a pane or the desktop itself reaches
    # for it.
    api.config.set("default_user", "newmate")
    addon._ensure_home()
    check(addon.vm_home().is_dir()
          and all((addon.vm_home() / name).is_dir()
                  for name in addon.USER_SUBDIRS),
          "a login under a new name builds that user's home and its folders")
    check(marker.is_file() and marker.read_text() == "kept",
          "and the rebuild leaves everything that was already there alone")
    # The desktop's coming-up covers the login itself: a user the process
    # never built for gets a home before the icon layer is even raised.
    api.config.set("default_user", "logtest")
    addon._desktop_on()
    check(addon.vm_home().is_dir(),
          "coming up on the desktop lays down the logged-in user's home")
    # A console spawned under a name nobody built still starts: the spawn
    # guarantees its working folder rather than trusting the memo.
    api.config.set("default_user", "cmdtest")
    con = addon.open_console()
    spawn = api.last_set(f"{con}_term", "spawn") or {}
    check(Path(str(spawn.get("cwd") or "")).is_dir(),
          "the console's spawn folder really exists - no ENOENT at boot")
    # And a local pane walked into a missing folder inside the VM's own
    # tree lays it down rather than showing an unreadable error.
    api.config.set("default_user", "panetest")
    addon.pane_list(addon.PANE_LOCAL, str(addon.vm_home()))
    check(addon.vm_home().is_dir(),
          "a pane walked into a home that was not there builds it")
    api.config.set("default_user", "")


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
    # Readability: the app paints every widget dark through its own
    # window-wide ``QWidget`` rule, and a rule that matches a widget directly
    # is not inherited away by anything set on a parent. So the desktop's
    # sheet must carry a client-area base that pushes that dark back out -
    # and every rule that wants to keep its own colours, here and in the
    # cosplay and icon-layer sheets, has to outspecify it.
    check("#addonWindow QWidget { background: transparent; color: #000000; }"
          in sheet,
          "the client-area base takes the app's dark back off every window")
    for want in ("#addonWindow #addonWindowBar {",
                 "#addonWindow QGroupBox::title",
                 "#addonWindow QTabBar::tab",
                 "#addonWindow QCheckBox",
                 "#addonWindow QHeaderView::section",
                 "#addonWindow QComboBox QAbstractItemView",
                 "#addonWindow QListWidget::item:selected",
                 "#addonWindow #berryStartMenu",
                 "#addonWindow #berryStartRight QListWidget::item",
                 "Tahoma"):
        check(want in sheet,
              f"the desktop sheet spells the XP rule {want!r}")
    check("#addonWindow #berryDeskGrid" in icon_sheet,
          "the icon layer's grid rules outrank the client-area base")
    boot = addon._boot_sheet()
    login = addon._login_sheet()
    power = addon._power_sheet()
    check("#addonWindow #berryBootBrand" in boot
          and "#addonWindow #berryBootBar::chunk" in boot,
          "the boot screen's words outrank the base: white on black stays")
    check("#addonWindow #berryLoginTitle" in login
          and "#addonWindow #berryLoginList::item" in login,
          "the welcome screen's names stay light on the blue")
    check("#addonWindow #berryPowerText" in power,
          "and the shut-down line stays readable on the black")
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
          "Control Panel is one of them")
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
    addon.start_program(addon.P_PAINT)
    check(api.last_set(addon.TASK_LIST, "height")
          == addon.scaled(addon.task_height()),
          "the bar is pinned to one button's height, not Qt's tall default")
    check(api.last_set(addon.TASK_LIST, "icon_size")
          == addon.scaled(addon.task_icon()),
          "and its buttons wear the configured mark size that fits a slim bar")
    api.config_values["task_icon"] = 24
    addon_module._taskbar_shown = ()
    addon.refresh_taskbar()
    check(api.last_set(addon.TASK_LIST, "icon_size") == addon.scaled(24),
          "raising task_icon grows the buttons on the next rewrite")
    del api.config_values["task_icon"]
    # Set the bar back to the shipped mark and clear the skip-memo, so the
    # rewrite guard below starts from a bar that truly has nothing to redo.
    addon_module._taskbar_shown = ()
    addon.refresh_taskbar()
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    check(len(drawn) >= 2
          and all(str(row.get("icon") or "").startswith("<svg")
                  for row in drawn),
          "every taskbar button wears its window's icon")
    check(addon.icon_for(addon.P_EXPLORER) == addon.ICON_COMPUTER,
          "My Computer's button wears the computer mark")
    check(addon.icon_for(addon.P_PAINT) == addon.ICON_PAINT,
          "and Paint's wears its own")
    # The rewrite guard: dragging a window re-runs the taskbar refresh on
    # every mouse move, and none of it changes a button - so a refresh that
    # changes nothing must rewrite nothing, while a window coming and going
    # still moves the bar for real.
    writes = api.sets_count(addon.TASK_LIST, "items")
    addon.refresh_taskbar()
    addon.refresh_taskbar()
    check(api.sets_count(addon.TASK_LIST, "items") == writes,
          "a taskbar refresh with nothing changed rewrote the row anyway")
    api.windows.remove(addon.P_PAINT)
    addon.on_widget(addon.TAB, addon.P_PAINT, "closed", {"by": "user"})
    check(api.sets_count(addon.TASK_LIST, "items") == writes + 1,
          "closing a window did not land exactly one new row")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    texts = [row.get("text") for row in drawn]
    check(addon.caption_for(addon.P_PAINT) not in texts
          and addon.caption_for(addon.P_EXPLORER) in texts,
          f"the bar lost the closed window and kept the other: {texts}")
    addon.start_program(addon.P_PAINT)
    check(api.sets_count(addon.TASK_LIST, "items") == writes + 2,
          "reopening a program did not get the bar rewritten")
    drawn = api.last_set(addon.TASK_LIST, "items") or []
    check(addon.caption_for(addon.P_PAINT)
          in [row.get("text") for row in drawn],
          "and its button came back")


def test_menus(addon: Any, api: Api) -> None:
    print("\n6. Start, the desktop's right-click, and Log Off")
    fresh(api)
    addon.init(api)
    # A Start pick opens its program.
    api.picks.append("Paint")
    addon.start_menu()
    check(addon.P_PAINT in api.windows, "Start > Paint opened the window")
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
    # The flyout's own shape: a left row launches by caption, and the
    # right column's Control Panel row reaches the settings window - it is
    # the program's own caption now. These come before the footer's two
    # ways out, which ask first and then sign the desktop out.
    addon.on_widget(addon.TAB, f"{addon.P_START}_all", "activated",
                    {"text": "Media Player"})
    check(addon.P_MEDIA in api.windows,
          "the flyout's All Programs row opens the program it names")
    addon.on_widget(addon.TAB, f"{addon.P_START}_side", "activated",
                    {"text": "Control Panel"})
    check(addon.P_SETTINGS in api.windows,
          "the flyout's Control Panel row brings the settings window up")
    # About, and the Escape that means nothing happens - checked while the
    # Paint window from the top of this section is still open.
    api.picks.append("About BerryVM")
    addon.start_menu()
    check(any("XP" in line for line in api.informations()),
          "About says what the desktop is")
    api.picks.append(None)
    addon.start_menu()
    check(api.windows.count(addon.P_PAINT) == 1,
          "an Escape on the menu changes nothing")
    # The footer's two ways out both open XP's three-button ask. Log Off
    # closes every program window and walks back to the welcome screen -
    # a no-op on this headless run - so the desktop comes straight back up
    # with only its icon layer standing.
    api.picks.append("Log Off")
    addon.on_widget(addon.TAB, f"{addon.P_START}_logoff", "clicked", {})
    check(api.windows == [addon.ICONS],
          f"Log Off left only the icon layer: {api.windows}")
    check(list(addon.opened) == [addon.ICONS],
          "and the addon's registry agrees")
    # Cancel - the third button - leaves everything standing: nothing here
    # has been opened or closed since the log-off put the layer back.
    addon.start_program(addon.P_PAINT)
    api.picks.append("Cancel")
    addon.on_widget(addon.TAB, f"{addon.P_START}_shutdown", "clicked", {})
    check(addon.P_PAINT in api.windows,
          "Cancel on the footer ask changes nothing")
    # Turn Off Computer runs the whole desktop down: every window goes,
    # the icon layer with it, and only the power button would be left - a
    # headless run keeps even that to itself.
    api.picks.append("Turn Off Computer")
    addon.on_widget(addon.TAB, f"{addon.P_START}_shutdown", "clicked", {})
    check(api.windows == [],
          f"Turn Off Computer took the whole desktop: {api.windows}")
    check(not addon.opened, "and the registry is emptied with it")


def test_explorer(addon: Any, api: Api) -> None:
    print("\n7. My Computer (local) and berry sftp (device): two windows")
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
    # My Computer is local-only: it holds the machine's own pane and none of
    # the device's, which now lives in berry sftp.
    addon.start_program(addon.P_EXPLORER)
    check(addon.P_EXPLORER in api.windows, "My Computer is on the desktop")
    ex_ids = collect_ids(api.open_entry(addon.P_EXPLORER).get("spec") or {})
    check(f"{addon.PANE_LOCAL}_table" in ex_ids,
          "My Computer holds the machine's own pane")
    check(f"{addon.PANE_REMOTE}_table" not in ex_ids,
          "and it no longer hosts the device's pane")
    # berry sftp holds the device's pane, and its table opts into a transfer.
    addon.start_program(addon.P_SFTP)
    check(addon.P_SFTP in api.windows, "berry sftp is on the desktop")
    remote_table = find_kind(
        api.open_entry(addon.P_SFTP).get("spec") or {}, "table")
    check(str(remote_table.get("id")) == f"{addon.PANE_REMOTE}_table",
          "berry sftp holds the device pane")
    check("drop" in (remote_table.get("on") or [])
          and remote_table.get("draggable") is True,
          "its table can be dropped on and dragged from")
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
    # A closed connection is said, on the window that owns the device pane.
    api.sftp_connected = False
    addon.pane_list(addon.PANE_REMOTE, "")
    note = str(api.last_set(f"{addon.P_SFTP}_note", "text")).lower()
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
    print("\n10. Command Prompt: a real terminal, driven by spawn")
    # The console no longer runs a child of its own: it points a terminal
    # screen at ``spawn`` and the app's worker owns the shell. So the test
    # reads what the addon asked the widget to do, not a fake process.
    fresh(api)
    addon.init(api)
    # A profile alias, so the spawn carries the expansion table with it.
    addon.write_profile({"aliases": {"ll": "dir /al"},
                         "path_extra": [str(Path(api.paths.data) / "bin")],
                         "prompt": "u@berryvm:~$ "})
    api.run_clear()
    win_id = addon.open_console()
    check(win_id in api.windows, f"a console window stands up: {win_id}")
    leaf = find_kind(api.open_entry(win_id).get("spec"), "terminal")
    check(leaf is not None and leaf.get("id") == f"{win_id}_term",
          "the window carries a terminal leaf, not a read-only log")
    spawn = api.last_set(f"{win_id}_term", "spawn") or {}
    check(bool(spawn.get("cmd")), "the shell to run is named")
    check(spawn.get("cwd") == str(addon.vm_home()),
          "the shell is spawned in the Linux-shaped home")
    check(Path(str(spawn.get("cwd") or "")).is_dir(),
          "and that home is a real folder the worker can start in")
    check(spawn.get("aliases") == {"ll": "dir /al"},
          "and the profile's aliases ride into the worker")
    env = spawn.get("env") or {}
    check(env.get("HOME") == str(addon.vm_home()),
          "the shell environment names that same home")
    check(str(Path(api.paths.data) / "bin") in env.get("PATH", ""),
          "and the profile's extra PATH is in front")
    check(api.last_set(f"{win_id}_term", "write") is not None,
          "the banner is printed on the terminal")
    check(addon.terminals.get(f"{win_id}_term") == win_id,
          "the terminal is filed under its window")
    # The worker's news lands on the window's path line, not a state label.
    addon.on_widget(addon.TAB, f"{win_id}_term", "closed", {})
    check(api.last_set(f"{win_id}_path", "text") is not None,
          "a closed shell reports on the window's path line")
    # Closing the frame forgets the terminal id (the app stopped the worker).
    addon.on_widget(addon.TAB, win_id, "closed", {})
    check(f"{win_id}_term" not in addon.terminals,
          "and the addon drops the shell's terminal id")
    # The home tree is the Linux shape, not just a Desktop folder.
    root = Path(api.paths.data)
    check(all((root / name).is_dir() for name in addon.LINUX_DIRS),
          "the home root was built as a Linux tree")
    check(addon.vm_home().is_dir(),
          "and the per-user home sits inside it")


def test_ssh(addon: Any, api: Api) -> None:
    print("\n11. berry ssh: prompted, then connected through the terminal")
    fresh(api)
    addon.init(api)
    api.run_clear()
    # A password answer reaches the connect as a password; the window is a
    # fresh ``sh<n>`` with an ssh record and its terminal files under it.
    api.answers = ["root@10.0.0.5:22", "letmein"]
    win = addon.open_ssh()
    check(win == "sh1" and win in api.windows, f"an ssh window stood up: {win}")
    check((addon.opened.get(win) or {}).get("kind") == "ssh",
          "the window is recorded as an ssh session")
    connect = api.last_set(f"{win}_term", "connect") or {}
    check(connect.get("host") == "10.0.0.5"
          and int(connect.get("port") or 0) == 22
          and connect.get("username") == "root"
          and connect.get("password") == "letmein",
          f"the connect settings were parsed from the prompt: {connect}")
    leaf = find_kind(api.open_entry(win).get("spec"), "terminal")
    check(leaf.get("id") == f"{win}_term"
          and "ready" in (leaf.get("on") or []),
          "the window carries a terminal leaf that reports its news")
    check(addon.terminals.get(f"{win}_term") == win,
          "the terminal is filed under its window")
    # The shell's news lands on the window's own status line.
    addon.on_widget(addon.TAB, f"{win}_term", "ready", {})
    check("connected" in str(api.last_set(f"{win}_path", "text") or ""),
          "a ready shell reports on the window's status line")
    # A blank password means a key file instead; a non-default port parses too.
    api.answers = ["deploy@box.example:2222", ""]
    api.file_picks = ["/home/me/id_ed25519"]
    win2 = addon.open_ssh()
    check(win2 == "sh2", f"the second session is its own window: {win2}")
    connect2 = api.last_set(f"{win2}_term", "connect") or {}
    check(connect2.get("host") == "box.example"
          and int(connect2.get("port") or 0) == 2222
          and connect2.get("username") == "deploy"
          and connect2.get("key_file") == "/home/me/id_ed25519"
          and not connect2.get("password"),
          f"a blank password took the key file and the odd port: {connect2}")
    # Cancelling at the address box opens nothing.
    api.answers = [None]
    before = list(api.windows)
    check(addon.open_ssh() == "" and api.windows == before,
          "a cancelled prompt opens no window")


def test_transfer(addon: Any, api: Api) -> None:
    print("\n12. Files move between My Computer and berry sftp")
    fresh(api)
    addon.init(api)
    api.run_clear()
    home = Path(api.paths.data)
    api.sftp_listing = [
        {"name": "readme.txt", "path": "readme.txt", "directory": False,
         "size": 40},
    ]
    # Both windows open, so both panes are addressable and each knows where
    # it stands - the shared module state stays unambiguous.
    addon.start_program(addon.P_EXPLORER)
    addon.start_program(addon.P_SFTP)
    # --- drag a local file onto berry sftp: an upload ---
    addon.pane_rows[f"{addon.PANE_LOCAL}_table"] = {
        "a.txt": {"path": str(home / "a.txt"), "directory": False}}
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "drop",
                    {"source": f"{addon.PANE_LOCAL}_table",
                     "rows": [{"text": "a.txt"}]})
    check(api.last_upload == str(home / "a.txt"),
          "a dragged local row was uploaded")
    check(api.last_remote_target == "a.txt",
          f"into the folder berry sftp shows: {api.last_remote_target!r}")
    check(api.sets_count(f"{addon.PANE_REMOTE}_table", "rows") >= 2,
          "and the device pane was refreshed after the drop")
    # --- drag a device file onto My Computer: a download ---
    addon.pane_rows[f"{addon.PANE_REMOTE}_table"] = {
        "readme.txt": {"path": "readme.txt", "directory": False}}
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "drop",
                    {"source": f"{addon.PANE_REMOTE}_table",
                     "rows": [{"text": "readme.txt"}]})
    check(api.last_download == "readme.txt",
          "a dragged device row was downloaded")
    # --- a drop back onto its own window is ignored ---
    api.last_upload = ""
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_table", "drop",
                    {"source": f"{addon.PANE_REMOTE}_table",
                     "rows": [{"text": "readme.txt"}]})
    check(api.last_upload == "", "a drop onto its own window does nothing")
    # --- Copy in My Computer, Paste in berry sftp ---
    addon.pane_rows[f"{addon.PANE_LOCAL}_table"] = {
        "b.txt": {"path": str(home / "b.txt"), "directory": False}}
    api.picks.append("Copy")
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_table", "context",
                    {"text": "b.txt"})
    check(len(addon._xfer_clip) == 1
          and addon._xfer_clip[0]["pane"] == addon.PANE_LOCAL,
          "the row menu's Copy filled the transfer clipboard")
    api.last_upload = ""
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_paste", "clicked", {})
    check(api.last_upload == str(home / "b.txt"),
          "Paste in berry sftp uploaded the copied file")
    # --- Paste into the same window it was copied from: refused, said ---
    addon._xfer_clip = [{"pane": addon.PANE_REMOTE, "name": "c.txt",
                         "path": "c.txt", "directory": False}]
    api.last_download = ""
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_paste", "clicked", {})
    check(api.last_download == "",
          "a paste into the pane it came from transfers nothing")
    check("copy a file" in str(
        api.last_set(f"{addon.P_SFTP}_note", "text") or "").lower(),
          "and it says which window to copy from instead")
    # --- a folder is refused, files only ---
    addon._xfer_clip = [{"pane": addon.PANE_LOCAL, "name": "folder",
                         "path": str(home / "folder"), "directory": True}]
    api.last_upload = ""
    addon.on_widget(addon.TAB, f"{addon.PANE_REMOTE}_paste", "clicked", {})
    check(api.last_upload == "", "a pasted folder is refused, not uploaded")


def test_display_properties(addon: Any, api: Api) -> None:
    print("\n13. Display Properties: Apply reaches everything")
    fresh(api)
    addon.init(api)
    # One local console standing, so the terminal text size has somewhere to
    # land when Apply retypes the shells.
    addon.open_console()
    term_id = next(iter(addon.terminals), "")
    # The window's controls, answered the way the stub reads form values: the
    # size and density dials are sliders, read back through ``value``.
    api.form["set_wall:value"] = "Night"
    api.form["set_file:value"] = ""
    api.form["set_scale:value"] = 120
    api.form["set_font:value"] = 14
    api.form["set_width:value"] = 600
    api.form["set_height:value"] = 400
    api.form["set_icon:value"] = 48
    api.form["set_cap_h:value"] = 26
    api.form["set_cap_font:value"] = 11
    api.form["set_task_h:value"] = 34
    api.form["set_clock:value"] = False
    api.form["set_grad:value"] = True
    api.form["set_round:value"] = True
    api.form["set_small:value"] = False
    api.form["set_user:value"] = "admin"
    addon.on_widget(addon.TAB, "set_apply", "clicked", {})
    check(api.written.get("wallpaper") == "Night",
          "the named wallpaper reached the config")
    check(api.written.get("ui_scale") == 120
          and api.written.get("term_font") == 14,
          "the two size dials reached the config too")
    check(api.written.get("caption_height") == 26
          and api.written.get("task_height") == 34,
          "the title-bar and taskbar dials reached the config as well")
    check(api.written.get("clock_on") is False, "the clock was switched off")
    check(api.written.get("bar_gradient") is True,
          "the gradient-caption toggle was honoured")
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
    check(bool(term_id) and api.last_set(term_id, "font_size") == 14,
          "the shell on the desktop was handed the new text size")
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
    # Defaults means it: everything back, a person's other keys untouched.
    addon.on_widget(addon.TAB, "set_defaults", "clicked", {})
    check(api.written.get("ui_scale") == 100
          and api.written.get("wallpaper") == "Bliss",
          "Defaults put the shipped look back")
    check("machines" not in api.written,
          "Defaults never touches a key the desktop no longer owns")
    # A slider previews itself the moment it moves: the dial writes its key,
    # keeps its read-out in step, and repaints - no Apply between the two.
    addon.on_widget(addon.TAB, "set_icon", "changed", {"value": 64})
    check(api.written.get("icon_px") == 64,
          "dragging the icon-size slider writes the key straight away")
    check(api.last_set("set_icon_val", "text") == "64",
          "and its read-out keeps step with the knob")
    # The repaint itself is coalesced: a slider tick queues the rebuild rather
    # than tearing the icon layer down on every pixel of a drag. Nothing has
    # landed by the time the key is written; a flush pours it exactly once.
    opens = api.opens_for(addon.ICONS)
    addon.on_widget(addon.TAB, "set_icon", "changed", {"value": 72})
    check(api.opens_for(addon.ICONS) == opens,
          "a slider move queues the rebuild instead of running it at once")
    addon.flush_preview()
    check(api.opens_for(addon.ICONS) == opens + 1,
          "and the flush pours the coalesced rebuild once")
    # Apply drains anything still queued, so a commit never waits on the timer.
    addon.on_widget(addon.TAB, "set_scale", "changed", {"value": 120})
    opens = api.opens_for(addon.ICONS)
    addon.settings_apply()
    check(api.opens_for(addon.ICONS) > opens,
          "Apply flushes a preview still waiting on the coalescer")
    # A caption colour reaches the config through the same live door.
    addon.on_widget(addon.TAB, "set_active", "changed", {"text": "#123456"})
    check(api.written.get("active_caption") == "#123456",
          "a caption colour writes on changed, ahead of any Apply")
    # A wallpaper combo's changed event paints without waiting for Apply.
    addon.on_widget(addon.TAB, "set_wall", "changed", {"text": "Aqua"})
    check("063a4e" in str(api.last_set(addon.DESKTOP, "style") or ""),
          "naming a gradient in the combo repaints the canvas at once")
    # The Control Panel home's category buttons turn to the card they name.
    api.run_clear()
    addon.on_widget(addon.TAB, "set_nav_appear", "clicked", {})
    check(api.last_set("set_tabs", "current") == 4,
          "the Appearance card's home button turns the tabs to it")
    # A size preset seats both window dials and writes them.
    addon.on_widget(addon.TAB, "set_preset_1024", "clicked", {})
    check(api.written.get("window_width") == 1024
          and api.written.get("window_height") == 768,
          "the 1024x768 preset wrote both window sizes")


def test_extras(addon: Any, api: Api) -> None:
    print("\n14. the logon bubble")
    fresh(api)
    addon.init(api)
    check(any("Desktop is up" in line for line in api.toasts()),
          "the desktop says hello when it goes up")


# --- the harness ---

def fresh(api: Api) -> None:
    """Start a section clean: no state on the addon's side, nothing recorded.

    The addon keeps its window registry, its counters for ``bp<n>``, ``pv<n>``
    and ``cs<n>``, and its pane cursors on module globals; a test that wants
    to open something has to see a desktop that starts empty, so every bit of
    that state is reset here before :func:`init` runs.
    """
    addon_module.api = api
    addon_module.opened = {}
    addon_module.berrypads = {}
    addon_module.viewers = {}
    addon_module.terminals = {}
    addon_module.pane_pick = {}
    addon_module.pane_rows = {}
    addon_module.pane_path = {}
    addon_module.berry_seq = 0
    addon_module.view_seq = 0
    addon_module.console_seq = 0
    addon_module.ssh_seq = 0
    addon_module._xfer_clip = []
    addon_module.paint_state = {}
    addon_module.media_state = {}
    addon_module.home_ready = ""
    # The taskbar's skip-when-unchanged signature and the tray clock's last
    # written text are module state too; a fresh section has to be able to
    # paint its first row, not be told it already showed it.
    addon_module._taskbar_shown = ()
    addon_module._clock_shown = None
    # apply_theme now skips a re-pour identical to the last one, so a fresh
    # section has to forget that sheet memo too - otherwise init's first paint
    # is told it already showed the default look and writes nothing.
    addon_module._theme_sheets_shown = {}
    # The live-preview backlog and its debounce timer are process state as
    # well: a new section starts with no queued repaint and no timer running.
    addon_module._preview_pending = set()
    addon_module._preview_timer = None
    addon_module._desk_positions = {}
    addon_module._desk_renames = {}
    addon_module.minimized = {}
    addon_module.maximized = set()
    addon_module.attention = {}
    addon_module._flash_on = False
    # The XP cosplay keeps a boot-progress counter, a Konami key buffer and a
    # dict of live timers on module globals too; a fresh section must not start
    # with a half-filled buffer or a lingering timer from the last one.
    addon_module._splash_step = 0
    addon_module._konami = []
    addon_module._SCHED.clear()
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

    Two things a person sees daily and cannot be proven from the file-session
    switch alone: the network mark must say *connected* whenever the machine
    can reach the net (a browser working is exactly that), whether or not the
    app has opened a device session; and the desktop must not be a black box -
    the chromeless icon layer spans the whole desktop, so every widget it
    wraps has to be transparent for the wallpaper to show."""
    print("\n15. the WiFi mark reads the link, and the wallpaper shows through")
    fresh(api)
    addon.init(api)
    api.run_clear()
    real_link = addon._link_up
    try:
        # No file session, but the machine is online: WiFi reads connected.
        api.sftp_connected = False
        addon._link_up = lambda: True
        summary = addon._net_summary()
        check("WiFi: connected" in summary,
              f"an online machine with no file session said offline: "
              f"{summary!r}")
        check("Files: offline" in summary,
              f"the file session was reported up while it was down: "
              f"{summary!r}")
        # Offline link, no session: honest offline.
        addon._link_up = lambda: False
        check("WiFi: offline" in addon._net_summary(),
              "no link and no session still showed connected")
        # A live file session is proof of a network even with the probe out.
        api.sftp_connected = True
        check("WiFi: connected" in addon._net_summary(),
              "an open file session did not put the mark to connected")
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


def test_paint_media(addon: Any, api: Api) -> None:
    """The two media programs and the api kinds they ride on.

    Paint drives a ``canvas`` leaf through tool / colour / width / undo /
    clear / save, and Media Player drives a ``player`` leaf over a playlist.
    None of it needs Qt: the checks read the commands the addon hands its
    widgets and the specs it opens."""
    print("\n16. Paint and Media Player")
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
    addon.start_program(addon.P_PAINT)
    ids = addon.program_window_ids()
    check(addon.P_EXPLORER in ids and addon.P_PAINT in ids,
          "two program windows stand on the desktop")

    def prop_values(prop: str) -> list:
        return [a[3] for _w, a, _k in api.calls
                if _w == "tabs.set_widget" and len(a) >= 4 and a[2] == prop]

    api.run_clear()
    addon.show_desktop()
    mins = prop_values("minimize_window")
    check(set(mins) >= {addon.P_EXPLORER, addon.P_PAINT},
          f"Show desktop hides every program window: {mins}")
    check(addon.minimized.get(addon.P_EXPLORER)
          and addon.minimized.get(addon.P_PAINT),
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
    addon.taskbar_menu(addon.CAPTIONS[addon.P_PAINT])
    check(addon.P_PAINT not in addon.program_window_ids(),
          "closing a taskbar button takes its window off the desktop")


def test_upgrade_cosplay(addon: Any, api: Api) -> None:
    """The app's per-program touches (Explorer, Notepad, Media), the theme
    card, and the XP cosplay, all read through the same fake desktop.

    Every check reads the commands the addon hands its widgets and the pure
    helpers it factors out, so none of it needs Qt: the seek slider, the
    dirty ``*`` title, the four-column listing, a theme repaint, the
    boot/logon no-ops on a headless run, the account round-trip behind the
    welcome screen and the Konami code that flips the wallpaper."""
    print("\n18. the app bumps, the theme and the cosplay")

    # --- Media Player: reorder the playlist, drive and seat the seek ---
    fresh(api)
    addon.init(api)
    addon.start_program(addon.P_MEDIA)
    track_a = str(Path(api.paths.data) / "a.mp3")
    track_b = str(Path(api.paths.data) / "b.mp3")
    addon.media_add(track_a)
    addon.media_add(track_b)
    addon.media_play_index(0)
    state = addon._media_state()
    check(state["tracks"] == [track_a, track_b] and state["index"] == 0,
          "two tracks queued, the first loaded")
    # Highlight the second row and slide it up past the first.
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_list", "selected",
                    {"row": 1})
    api.run_clear()
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_up", "clicked", {})
    state = addon._media_state()
    check(state["tracks"] == [track_b, track_a],
          f"the selected row moved up the playlist: {state['tracks']}")
    check(state["index"] == 1,
          "and the play-mark rode with the track it was on")
    # Dragging the seek slider reaches the player's ``seek`` prop.
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_seek", "changed",
                    {"value": 42000})
    check(api.last_set(f"{addon.P_MEDIA}_player", "seek") == 42000,
          "the seek slider drives the leaf")
    # A state change seats the slider at the loaded track's length + clock.
    api.form[f"{addon.P_MEDIA}_player:duration"] = 60000
    api.form[f"{addon.P_MEDIA}_player:position"] = 30000
    addon.on_widget(addon.TAB, f"{addon.P_MEDIA}_player", "state",
                    {"state": "playing"})
    check(api.last_set(f"{addon.P_MEDIA}_seek", "maximum") == 60000,
          "the seek slider's reach follows the track length")
    check(api.last_set(f"{addon.P_MEDIA}_seek", "value") == 30000,
          "and its handle sits at the current position")
    check(api.last_set(f"{addon.P_MEDIA}_seeklabel", "text") == "0:30",
          "the clock reads the position as m:ss")
    check(addon._media_time(65000) == "1:05"
          and addon._media_time(0) == "0:00",
          "the clock formats milliseconds, and zero is 0:00")

    # --- Notepad: a dirty ``*`` title, Find, and the Recent shelf ---
    fresh(api)
    addon.init(api)
    note = Path(api.paths.data) / "Desktop" / "kp.txt"
    note.write_text("echo hi there\n")
    win = addon.open_berrypad(str(note), "local", "kp.txt")
    check(win in api.windows, "a text file opens in the editor")
    # Typing words the file has not been given puts a ``*`` on the caption.
    addon.on_widget(addon.TAB, f"{win}_edit", "changed",
                    {"text": "echo hi there, edited\n"})
    check("*" in api.titles.get(win, ""),
          f"a dirty buffer wears a * : {api.titles.get(win)!r}")
    # Find says how many matches and where the first is.
    api.answers.append("there")
    addon.on_widget(addon.TAB, f"{win}_find", "clicked", {})
    found_note = str(api.last_set(f"{win}_note", "text") or "")
    check("match" in found_note and "line 1" in found_note,
          f"Find reports the count and first line: {found_note!r}")
    # Opening a local file pushes it onto the persisted Recent shelf.
    check(str(note) in (api.written.get("recent") or []),
          "the opened file reached the Recent list in the config")

    # --- Explorer: the mode/stamp helpers and the four-column listing ---
    check(addon._mode_text(0o755, False) == "-rwxr-xr-x",
          "a 0755 file reads as -rwxr-xr-x")
    check(addon._mode_text(0o644, True) == "drw-r--r--",
          "a directory leads with d")
    check(addon._mode_text(0, True) == "d---------",
          "an unreadable mode is dashes, not a lie")
    check(addon._file_stamp(0) == "" and addon._file_stamp(-1) == "",
          "no mtime is a blank cell")
    check(bool(addon._file_stamp(1_700_000_000)),
          "a real mtime reads as a date")
    fresh(api)
    addon.init(api)
    addon.start_program(addon.P_EXPLORER)
    desk = Path(api.paths.data) / "Desktop"
    (desk / "listing.txt").write_text("one\n")
    addon.pane_list(addon.PANE_LOCAL, str(desk))
    rows = api.last_set(f"{addon.PANE_LOCAL}_table", "rows") or []
    check(rows and all(len(r.get("cells") or []) == 4 for r in rows),
          "each local row is Name, Size, Mode, Modified")
    check(any(r["cells"][0] == "listing.txt" for r in rows),
          "and the file we wrote is listed")
    # Enter on the address bar walks the pane there; Root jumps to the top.
    addon.on_widget(addon.TAB, f"{addon.PANE_LOCAL}_path", "returned",
                    {"text": str(desk)})
    check(addon.pane_path.get(addon.PANE_LOCAL) == str(desk),
          "typing an address and Enter walks the pane")
    addon.pane_root(addon.PANE_LOCAL)
    check(addon.pane_path.get(addon.PANE_LOCAL) == str(addon.home_root()),
          "Root jumps to the top of the Linux tree")

    # --- Display Properties: the Themes card repaints from the palette ---
    fresh(api)
    addon.init(api)
    api.form["set_theme:value"] = "Silver"
    addon.settings_apply()
    check(api.written.get("theme") == "Silver",
          "the theme pick reached the config")
    sheet = str(api.last_set(addon.DESKTOP, "style") or "")
    check("#6a6a72" in sheet,
          "Silver's frame colour repainted the desktop sheet")

    # --- The cosplay: boot, welcome, setup and power all no-op headless ---
    fresh(api)
    addon.init(api)
    for ghost in (addon.SPLASH, addon.LOGON, addon.ACC, addon.POWER, "sfx"):
        check(ghost not in api.windows,
              f"{ghost!r} is never left on a headless desktop")
    # Driving them directly still finds nothing to host them, so nothing
    # lands: the boot bar hands off, the screens decline, the chime is mute.
    addon.boot_splash()
    addon.login_screen()
    addon.account_create_screen()
    addon.startup_sound()
    check(all(ghost not in api.windows for ghost in
              (addon.SPLASH, addon.LOGON, addon.ACC, addon.POWER, "sfx")),
          "the cosplay is a no-op when there is no real window to show it on")

    # --- The cosplay screens' shapes, and the flyout's overflow fix ---
    boot_ids = collect_ids(addon._splash_spec())
    check(f"{addon.SPLASH}_brand" in boot_ids
          and f"{addon.SPLASH}_bar" in boot_ids
          and f"{addon.SPLASH}_tip" in boot_ids,
          f"the boot screen carries its brand, bar and tip: {boot_ids}")
    logon_ids = collect_ids(addon.login_spec())
    check(f"{addon.LOGON}_who" in logon_ids
          and f"{addon.LOGON}_pw" in logon_ids
          and f"{addon.LOGON}_ok" in logon_ids
          and f"{addon.LOGON}_switch" in logon_ids,
          f"the welcome screen carries names, a password and two buttons: "
          f"{logon_ids}")
    pw = find_id(addon.login_spec(), f"{addon.LOGON}_pw")
    check(pw.get("password") is True and pw.get("visible") is False,
          "the password line masks its typing and waits hidden for a name")
    acc_ids = collect_ids(addon.account_spec())
    check(f"{addon.ACC}_name" in acc_ids and f"{addon.ACC}_pw" in acc_ids
          and f"{addon.ACC}_pw2" in acc_ids and f"{addon.ACC}_ok" in acc_ids
          and f"{addon.ACC}_skip" in acc_ids,
          f"the setup screen asks a name, a password twice, and a skip: "
          f"{acc_ids}")
    check(f"{addon.POWER}_btn" in collect_ids(addon.power_spec()),
          "the shut-down screen is one power button on black")
    # The welcome list shows the accounts the VM knows, each with a mark.
    api.config_values["accounts"] = [{"name": "keigh", "pwd": "deadbeef"}]
    listing = find_kind(addon.login_spec(), "list")
    rows = listing.get("items") or []
    check([str(r.get("text")) for r in rows] == ["keigh"]
          and all(str(r.get("icon") or "").startswith("<svg") for r in rows),
          "the account tiles read the config and wear the user mark")
    del api.config_values["accounts"]
    # The Start flyout cannot overflow: All Programs expands and scrolls.
    flyout = addon.start_flyout_spec()
    check(find_id(flyout, f"{addon.P_START}_all").get("expand") is True,
          "the All Programs list expands to the room the flyout has")

    # --- The VM's accounts: stored as digests, kept out of the mirror ---
    fresh(api)
    addon.init(api)
    check(addon.vm_accounts() == [], "a clean VM knows no accounts yet")
    check(addon.add_account("keigh", "hunter2"), "an account can be made")
    check(addon.add_account("bob", ""),
          "and one can be made without a password")
    stored = addon.vm_accounts()
    check([one["name"] for one in stored] == ["keigh", "bob"],
          f"both accounts are stored in order: {stored}")
    check(all(one["pwd"] != "hunter2" for one in stored),
          "no account keeps its password in plain words")
    check(addon.account_password_ok("keigh", "hunter2"),
          "the right password opens")
    check(not addon.account_password_ok("keigh", "s3cret"),
          "the wrong one does not")
    check(addon.account_password_ok("bob", "anything"),
          "an account with no password lets anything through")
    check(not addon.account_password_ok("nobody", ""),
          "and an unknown name is refused outright")
    # The digests ride the config only: mirror_settings copies DEFAULTS keys,
    # and ``accounts`` names no default - so the VM's own settings.json - a
    # file a person can browse from the desktop - never sees them.
    addon.mirror_settings()
    mirrored = json.loads(addon.settings_file().read_text())
    check("accounts" not in mirrored,
          "the mirror keeps the account digests out of the VM's file")

    # --- The cosplay through the dispatcher: create, login, power cycle ---
    # Account creation: two passwords that disagree are said on the screen;
    # agreeing ones make the account, name the user and raise the desktop.
    api.form[f"{addon.ACC}_name:value"] = "keigh"
    api.form[f"{addon.ACC}_pw:value"] = "pw1"
    api.form[f"{addon.ACC}_pw2:value"] = "pw2"
    addon.on_widget(addon.TAB, f"{addon.ACC}_ok", "clicked", {})
    note = str(api.last_set(f"{addon.ACC}_note", "text") or "")
    check("not match" in note,
          f"mismatched passwords are said on the screen: {note!r}")
    api.form[f"{addon.ACC}_pw2:value"] = "pw1"
    addon.on_widget(addon.TAB, f"{addon.ACC}_ok", "clicked", {})
    check(bool(addon.find_account("keigh")),
          "the agreeing form created the account")
    check(api.written.get("default_user") == "keigh",
          "and the new account became the desktop's user")
    check(addon.ICONS in api.windows, "the desktop came up under it")
    # Login: picking a name lifts the password box; a wrong word is said
    # inline; the right one lands and names the user.
    addon.on_widget(addon.TAB, f"{addon.LOGON}_who", "selected",
                    {"text": "keigh"})
    check(api.last_set(f"{addon.LOGON}_pw", "visible") is True,
          "picking a name lifts the password box")
    addon.on_widget(addon.TAB, f"{addon.LOGON}_ok", "clicked",
                    {"text": "nope"})
    note = str(api.last_set(f"{addon.LOGON}_note", "text") or "")
    check("not correct" in note,
          f"a wrong password is said, never raised: {note!r}")
    addon.on_widget(addon.TAB, f"{addon.LOGON}_pw", "returned",
                    {"text": "pw1"})
    check(api.written.get("default_user") == "keigh",
          "the right password logs in, from the line itself")
    # Double-clicking a passwordless name logs straight in - the blank the
    # person would have typed is already correct.
    addon.add_account("bob", "")
    addon.on_widget(addon.TAB, f"{addon.LOGON}_who", "activated",
                    {"text": "bob"})
    check(api.written.get("default_user") == "bob",
          "a passwordless double-click logs straight in")
    # Power: off empties the desktop; on runs the whole turn-on again, which
    # headless lands straight back on the standing desktop.
    addon.start_program(addon.P_PAINT)
    addon.power_off()
    check(api.windows == [] and not addon.opened,
          "power off takes every window and the icon layer down")
    addon.power_on()
    check(addon.ICONS in api.windows,
          "pressing Power brings the desktop back up")

    # --- Konami code flips the wallpaper; Alt+Tab raises a window ---
    fresh(api)
    addon.init(api)
    for key in addon.KONAMI:
        addon.on_widget(addon.TAB, addon.DESKTOP, "shortcut",
                        {"keys": key})
    check(api.written.get("wallpaper") == "Night",
          "the Konami code switched the wallpaper to Night")
    addon._konami = []
    addon.konami_seen("left")
    addon.konami_seen("up")          # off the tail: the buffer resets
    addon.konami_seen("zzz")         # a stray key clears it outright
    check(addon._konami == [],
          "a key off the sequence drops the buffer rather than stacking a hit")
    addon.start_program(addon.P_PAINT)
    api.picks.append(addon.caption_for(addon.P_PAINT))
    addon.on_widget(addon.TAB, addon.DESKTOP, "shortcut",
                    {"keys": "Alt+Tab"})
    check(api.last_set(addon.DESKTOP, "raise_window") == addon.P_PAINT,
          "Alt+Tab raises the window named in the switcher")


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
    test_ssh(addon, api)
    test_transfer(addon, api)
    test_display_properties(addon, api)
    test_extras(addon, api)
    test_net_and_wallpaper(addon, api)
    test_paint_media(addon, api)
    test_window_management(addon, api)
    test_upgrade_cosplay(addon, api)

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
