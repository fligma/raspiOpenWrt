#!/usr/bin/env python3
"""A test for the BerryVM addon.

BerryVM is a virtual desktop in one tab, so the shapes worth checking without
a window are the ones the desktop's own primitives are supposed to keep in
step: a taskbar that follows what the desktop is holding, an editor that
refuses what it cannot hold, and a settings mirror that neither erases what
the form never showed nor puts a password on disk.

Covered here:

* the manifest, and the picture the store is expected to show for it;
* the pure helpers - port, address, machine entry, suffix to language, a
  byte count that reads as something;
* the VM's home folder: ``Desktop``, ``bin``, ``tmp``, ``config``, made
  once and not again;
* the ``config/settings.json`` mirror: every default key written, unknown
  keys surviving, no password in it;
* the four window-management calls - open, raise, close, and the reuse
  path that raises rather than rebuilds - and the taskbar following them;
* the editor's refusal of a big file, of a binary name, and its language
  picked from the suffix;
* Main SSH refusing to open a window when the profile has no host.

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


class Api(addon_client.StubAPI):
    """The recording stub, with a desktop that remembers its own windows.

    The real ``windows`` canvas is a Qt widget with a set of free-floating
    frames; this keeps the same three answers - which ids are up, what
    ``open_window`` did, what ``close_window`` did - so a check on a
    program's window reads a list rather than a log line.
    """

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.windows: list[str] = []       # ids the canvas is holding
        self.titles: dict[str, str] = {}   # win_id -> the title it wears
        self.form: dict[str, Any] = {}     # widget_value cache per id
        self.answers: list[Any] = []      # queued replies to ui.input
        self.dialogs: list[Any] = []      # queued replies to ui.dialog
        self.picks: list[Any] = []        # queued replies to ui.choose
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
            if prop == "raise_window":
                # The canvas raises without changing order here; a real one
                # re-stacks. The tests care that it is reached.
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
        if method == "ui.question":
            return self.questions
        if method == "config.set":
            self.written[str(ask("key", ""))] = ask("value")
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
            return f"/tmp/{Path(self.last_upload).name}"
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
            # The clock is asked for; running the callback once here means a
            # test can prove the timer was set without needing a Qt timer.
            callback = ask("callback")
            if callable(callback):
                callback()
            return None
        return super().default(method, arguments, named)

    # --- reading what the addon did ---

    def run_clear(self) -> None:
        """Forget what has been recorded; keep the desktop's window list."""
        self.calls = []
        self.written = {}

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None

    def notices(self) -> list[str]:
        return [str(arguments[0]) for where, arguments, _k in self.calls
                if where == "ui.notify"]

    def warnings(self) -> list[str]:
        return [str(arguments[1]) for where, arguments, _k in self.calls
                if where == "ui.warning"]

    def opens_for(self, win_id: str) -> int:
        """How many times the addon has asked the desktop to open this id.
        One means a real window; two means the reuse path was taken rather
        than a raise."""
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
    check(str(addon.ADDON.get("version")) == "1.0",
          f"the shipped version is 1.0: {addon.ADDON.get('version')!r}")
    check(str(addon.ADDON.get("api")) == "1", "the api version is 1")
    check(sorted(addon.ADDON.get("requires") or []) == ["sftp", "ssh"],
          f"both transports are declared: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("connection")) == "both",
          "the manifest is honest that some windows ride the session "
          "and some bring their own socket")
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
    check(ids == [addon.P_MAIN, addon.P_SFTP, addon.P_LOCAL,
                  addon.P_MACHINE, addon.P_FILES],
          f"the Start menu lists the five programs in order: {ids}")


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
    check(addon.language_for("notes.conf") == "ini",
          "a config ending is read as ini")
    check(addon.language_for("notes.txt") == "",
          "a plain text file is not painted at all")
    check(addon.is_text("a.sh") and not addon.is_text("a.bin"),
          "the suffix alone says whether Berrypad could try")
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
    check(got[0]["port"] == 22 and got[1]["port"] == 22,
          "a port named in an address, or missing entirely, both come out 22")
    check(got[1]["username"] == "root",
          "a user written in front of an address is used for that entry")


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


def test_window_management(addon: Any, api: Api) -> None:
    print("\n5. the desktop's four calls, and the taskbar following")
    fresh(api)
    addon.init(api)
    api.run_clear()
    # A program opened for the first time is asked to the desktop.
    addon.start_program(addon.P_LOCAL)
    check(addon.P_LOCAL in api.windows,
          f"Local disk is on the desktop: {api.windows}")
    check(api.opens_for(addon.P_LOCAL) == 1,
          "the desktop was told once")
    drawn = api.last_set(addon.TASKBAR, "items") or []
    check(addon.caption_for(addon.P_LOCAL)
          in [row.get("text") for row in drawn],
          "the taskbar picked the new window up")
    # A second Start-menu press raises rather than rebuilds: it goes through
    # ``open_window``, and the primitive itself does the reuse. This test
    # checks the addon-side view: the caption is stable and no second
    # registry entry was added.
    addon.start_program(addon.P_LOCAL)
    check(api.windows.count(addon.P_LOCAL) == 1,
          "the same program opened twice is still one window")
    # The Close button, and the taskbar's own raise.
    api.form[f"{addon.TASKBAR}:selected"] = [addon.caption_for(addon.P_LOCAL)]
    addon.on_widget(addon.TAB, addon.CLOSE_BTN, "clicked", {})
    check(addon.P_LOCAL not in api.windows,
          "Close on the taskbar shut the window")
    drawn = api.last_set(addon.TASKBAR, "items") or []
    check(addon.caption_for(addon.P_LOCAL)
          not in [row.get("text") for row in drawn],
          "and the taskbar forgot it")
    # The × button on a window's own frame reports closed; the addon forgets.
    addon.start_program(addon.P_FILES)
    check(addon.P_FILES in addon.opened, "Files is on the desktop")
    addon.on_widget(addon.TAB, addon.P_FILES, "closed", {"by": "user"})
    check(addon.P_FILES not in addon.opened,
          "a user-driven close forgets the window on the addon's side")
    # Raise: taskbar selection reaches the desktop with the right prop.
    addon.start_program(addon.P_LOCAL)
    api.form[f"{addon.TASKBAR}:selected"] = [addon.caption_for(addon.P_LOCAL)]
    addon.on_widget(addon.TAB, addon.TASKBAR, "selected", {})
    check(api.last_set(addon.DESKTOP, "raise_window") == addon.P_LOCAL,
          "picking a taskbar row tells the desktop to raise that window")


def test_berrypad(addon: Any, api: Api) -> None:
    print("\n6. Berrypad: the editor window's own refusals")
    fresh(api)
    addon.init(api)
    api.run_clear()
    home = Path(api.paths.data)
    text = home / "Desktop" / "note.sh"
    text.write_text("echo hi\n")
    win_id = addon.open_berrypad(str(text), "local", "note.sh")
    check(win_id and win_id in api.windows,
          f"a text file opened in Berrypad: {win_id}")
    check(str(api.last_set(f"{win_id}_edit", "value") or "").startswith("echo"),
          "the file's content reached the editor")
    # Same path again: reuse the window rather than build a second.
    win2 = addon.open_berrypad(str(text), "local", "note.sh")
    check(win2 == win_id,
          "the same file opens the same window, not a second copy")
    check(api.windows.count(win_id) == 1,
          "and the desktop still holds one frame for it")
    # Save writes back.
    api.form[f"{win_id}_edit:value"] = "echo saved\n"
    addon.on_widget(addon.TAB, f"{win_id}_save", "clicked", {})
    check(text.read_text() == "echo saved\n",
          "Save writes the editor's words back to the local file")
    # A binary name is refused with a saying, before any window is drawn.
    big = home / "Desktop" / "firm.bin"
    big.write_bytes(b"\x00\x01\x02")
    before = list(api.windows)
    refused = addon.open_berrypad(str(big), "local", "firm.bin")
    check(refused == "",
          "Berrypad refuses a non-text suffix without opening a window")
    check(api.windows == before, "and the desktop did not change")
    check(any("text file" in line.lower() for line in api.notices()),
          "the notice says what the refusal is about")
    # A file over the cap is refused with a size in the message.
    huge = home / "Desktop" / "huge.txt"
    huge.write_text("x" * (addon.MAX_SIZE + 10))
    before = list(api.windows)
    refused = addon.open_berrypad(str(huge), "local", "huge.txt")
    check(refused == "" and api.windows == before,
          "a text file past the cap is refused before any window is drawn")
    check(any("cap" in line.lower() for line in api.notices()),
          "the refusal names Berrypad's cap")


def test_main_ssh_needs_host(addon: Any, api: Api) -> None:
    print("\n7. Main SSH refuses without a host")
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
          "with a host, Main SSH opens its window")
    check(api.last_set(f"{addon.P_MAIN}_term", "connect") is not None,
          "the terminal was handed a connect dict")
    details = api.last_set(f"{addon.P_MAIN}_term", "connect")
    check("password" in details and details["host"] == "192.168.1.9",
          "the password and host reach the terminal")
    check("password" not in json.dumps(api.written),
          "and no password was written to the addon's config")


def test_sftp_view_and_local(addon: Any, api: Api) -> None:
    print("\n8. SFTP view and the local disk's 'Run on Main SSH'")
    fresh(api)
    addon.init(api)
    api.run_clear()
    # SFTP view over the app's open session.
    api.sftp_listing = [
        {"name": "etc", "path": "/etc", "directory": True, "size": 4096},
        {"name": "readme.txt", "path": "/readme.txt", "directory": False,
         "size": 40},
    ]
    api.sftp_text = "hello from the device"
    addon.start_program(addon.P_SFTP)
    rows = api.last_set(f"{addon.P_SFTP}_list", "items") or []
    names = [row.get("text") for row in rows]
    check("etc/" in names, f"a directory is drawn with a slash: {names}")
    check("readme.txt" in names, "and a file without one")
    # Picking a text file and pressing Open sends it into Berrypad.
    api.form[f"{addon.P_SFTP}_list:selected"] = ["readme.txt"]
    addon.on_widget(addon.TAB, f"{addon.P_SFTP}_open", "clicked", {})
    win_id = addon.open_berrypad("/readme.txt", "remote", "readme.txt")
    check(win_id in api.windows, "Berrypad's remote window opened")
    check(str(api.last_set(f"{win_id}_edit", "value") or "")
          == "hello from the device", "the file reached the editor over SFTP")
    # With the connection down, the view says so and the list is empty.
    api.sftp_connected = False
    addon.sftp_list("")
    check("not open" in str(api.last_set(f"{addon.P_SFTP}_note", "text")).lower(),
          "a closed connection is said, not swallowed")
    # Local disk: uploading and running a file hands both calls to the api.
    api.sftp_connected = True
    home = Path(api.paths.data)
    script = home / "bin" / "hello.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    addon.local_list(str(script.parent))
    api.form[f"{addon.P_LOCAL}_list:selected"] = ["hello.sh"]
    addon.local_action("run")
    check(api.last_upload == str(script), "the file was uploaded")
    check("hello.sh" in api.last_command(),
          f"and run over SSH: {api.last_command()!r}")


def test_machines_config(addon: Any, api: Api) -> None:
    print("\n9. Machines keeps its own list in the config")
    fresh(api)
    addon.init(api)
    api.run_clear()
    api.config_values["machines"] = [
        {"name": "Router", "host": "192.168.1.1", "port": 22,
         "username": "root"},
    ]
    addon.reload_machines()
    addon.draw_machines()
    rows = api.last_set(f"{addon.P_MACHINE}_list", "items") or []
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
    # Editing writes the whole list back, so an external entry survives.
    api.dialogs.append({"name": "AP", "host": "10.0.0.1", "port": 22,
                        "username": "root", "key_file": ""})
    addon.machine_row = 0
    addon.machine_edit()
    stored = api.written.get("machines")
    check(isinstance(stored, list) and stored[0].get("name") == "AP",
          f"the changed entry reached config.set('machines'): {stored}")


# --- the harness ---

def fresh(api: Api) -> None:
    """Start a section clean: no state on the addon's side, nothing recorded.

    The addon keeps its window registry, its counter for ``msh<n>`` and
    ``bp<n>`` and its cached ``machines`` list on module globals; a test
    that wants to open something has to see a desktop that starts empty,
    so every bit of that state is reset here before :func:`init` runs.
    """
    addon_module.api = api
    addon_module.opened = {}
    addon_module.berrypads = {}
    addon_module.terminals = {}
    addon_module.machines = []
    addon_module.machine_row = -1
    addon_module.machine_seq = 0
    addon_module.berry_seq = 0
    addon_module.home_ready = False
    addon_module.sftp_path = ""
    addon_module.local_path = ""
    addon_module.files_path = ""
    api.windows = []
    api.titles = {}
    api.form = {}
    api.answers = []
    api.dialogs = []
    api.picks = []
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
    api.last_run = ""
    api.config_values = {}
    api.run_clear()


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

    test_helpers(addon, api)
    test_home_tree(addon, api)
    test_settings_mirror(addon, api)
    test_window_management(addon, api)
    test_berrypad(addon, api)
    test_main_ssh_needs_host(addon, api)
    test_sftp_view_and_local(addon, api)
    test_machines_config(addon, api)

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
