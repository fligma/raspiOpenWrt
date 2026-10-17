# -*- coding: utf-8 -*-
"""A test for addons/berry.py.

Berry is now ONE window-tab whose file view has two pages - **Device** (the
*current profile*'s own SSH/SFTP connection, ids prefixed ``d``) and **Local**
(this computer, ids prefixed ``l``). Editing a file opens a **notepad-like tab of
its own** (``Berry Editor``) whose text lives in memory until Save; the language
box re-shades it, and the highlighter itself lives in the app process. Copy on
one page and paste on the other moves bytes over SFTP.

The parts a bug would hide in are not the SSH calls (exercised elsewhere) but:
the routing that turns a prefixed id back into a side and a base name, the
sorting that keeps folders on top, the filter and show-hidden, the path
arithmetic (posix on the device, native on the local page) every mkdir / rename /
delete depends on, the shell quoting that lets a name with a space survive
``chmod``, the menu that only offers what makes sense for the row, the paste that
uploads a local file but downloads a device one, and the editor that must NOT
write until Save.

So this drives the addon through a stubbed device and dialogs that answer; the
local page is pointed at a real temporary folder so the genuine filesystem reads,
writes and deletes. The tab and the editor are also built by Qt for real - the
two-page frame, the headers, the right-click policy, and the syntax highlighter
attached to the editor's text area - to catch a spec typo before it ships.

No internet and no device are needed. Run it from the Test panel, or::

    python addons/_tests/test_berry.py
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

SOURCE = ROOT / "addons" / "berry.py"
FAILURES: list[str] = []

REMOTE_ROOT = "/www/rasconf"

#: A device listing of /etc: a folder, two files of different sizes and a
#: dotfile that only the show-hidden switch lets through.
LISTING = [
    {"name": "bin", "path": "/etc/bin", "directory": True, "link": False,
     "size": 0, "mtime": 1_700_000_000, "mode": 0o040755},
    {"name": "passwd", "path": "/etc/passwd", "directory": False, "link": False,
     "size": 500, "mtime": 1_700_000_100, "mode": 0o100644},
    {"name": "hosts", "path": "/etc/hosts", "directory": False, "link": False,
     "size": 100, "mtime": 1_700_000_050, "mode": 0o100644},
    {"name": ".profile", "path": "/etc/.profile", "directory": False, "link": False,
     "size": 42, "mtime": 1_699_999_000, "mode": 0o100600},
]


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


# --- the api object the addon is handed ---

class Api(addon_client.StubAPI):
    """A stub device that answers, a form that holds, and tabs that open/close.

    Two stores keep the pages apart: ``form`` holds the Berry tab's controls (by
    their prefixed id) and ``editor`` holds the editor tab's. A widget whose id
    starts with ``ed`` is read from ``editor``, so an edit made through
    ``set_widget`` (a typed body, a picked language) is there for the addon to
    read back. ``open_tabs`` makes ``tabs.exists`` truthful so the editor's
    open-then-close dance is exercised for real.
    """

    def __init__(self, *args: Any, remote_root: str = REMOTE_ROOT, **named: Any) -> None:
        super().__init__(*args, **named)
        self.remote_root = remote_root
        self.form: dict = {}
        self.editor: dict = {}
        self.editor_hl = None          # the last language the editor was shaded with
        self.open_tabs: set[str] = set()
        self.listing: list[dict] = []
        self.file_text = "root:x:0:0::/root:/bin/sh\n"
        self.run_out = "Filesystem  Size  Used  Avail  Use%  Mounted on\n/dev/mmcblk0p2  29G  1G  27G  4%  /"
        self.inputs: list[str] = []
        self.answer_yes = True
        self.exists_flag = False
        self.choose = None                 # what api.ui.choose is answered with
        self.dialog_result = None          # what api.ui.dialog is answered with

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments, named, fallback)
        if method == "device.list":
            return [dict(entry) for entry in self.listing]
        if method == "device.read_text":
            return self.file_text
        if method == "device.run":
            return {"code": 0, "out": self.run_out, "err": ""}
        if method == "device.write_text":
            return len(str(ask("text", "") or ""))
        if method == "device.exists":
            return bool(self.exists_flag)
        if method == "ui.input":
            return self.inputs.pop(0) if self.inputs else ""
        if method == "ui.question":
            return bool(self.answer_yes)
        if method == "ui.choose":
            return self.choose
        if method == "ui.dialog":
            return self.dialog_result

        if method == "tabs.add_tab":
            name = str(ask("name", "tab"))
            self.open_tabs.add(name)
            return name
        if method == "tabs.close":
            self.open_tabs.discard(str(ask("name", "")))
            return True
        if method == "tabs.exists":
            return str(ask("name", "")) in self.open_tabs
        if method == "tabs.set_widget":
            identifier = str(ask("identifier", ""))
            prop = str(ask("prop", ""))
            value = ask("value")
            if identifier.startswith("ed"):
                if prop in ("text", "value"):
                    self.editor[identifier] = value
                elif prop == "highlight":
                    self.editor_hl = value
            return True
        if method == "tabs.widget_value":
            identifier = str(ask("identifier", ""))
            what = str(ask("what", "value"))
            if what == "selected" and identifier.endswith("files"):
                return list(self.form.get("files") or [])
            store = self.editor if identifier.startswith("ed") else self.form
            if identifier not in store:
                return None
            value = store[identifier]
            if what == "checked":
                return bool(value)
            if isinstance(value, (list, tuple)):
                return list(value)
            return value
        if method == "session.status":
            return {"host": "192.168.1.1", "port": 22, "username": "root",
                    "remote_root": self.remote_root,
                    "sftp_connected": True, "ssh_connected": False, "busy": False}
        return super().default(method, arguments, named)

    def sets(self, identifier: str, prop: str | None = None) -> list:
        out = []
        for where, arguments, _named in self.calls:
            if where == "tabs.set_widget" and arguments[1:2] == (identifier,):
                if prop is None or arguments[2:3] == (prop,):
                    out.append(arguments[3] if len(arguments) > 3 else None)
        return out

    def last_set(self, identifier: str, prop: str) -> Any:
        values = self.sets(identifier, prop)
        return values[-1] if values else None

    def command(self, method: str = "device.run") -> str:
        entry = self.last(method)
        if entry is None:
            return ""
        return str(self.argument(method, "command", entry[1], entry[2], ""))


def fresh(saved: dict | None = None) -> Api:
    """The addon, loaded fresh and initialized on a stubbed device."""
    global berry
    berry = addon_client.load_addon(SOURCE)
    berry.clip = None
    berry.editing = None
    berry.working = False
    api = Api(addon_id="berry", config=dict(saved or {}))
    berry.init(api)
    return api


def rows(api: Api, side: Any = None) -> list:
    side = berry.DEVICE if side is None else side
    painted = api.last_set(berry.pid(side, "files"), "rows") or []
    return [[str(cell) for cell in row] for row in painted]


def names(api: Api, side: Any = None) -> list:
    return [row[0] for row in rows(api, side)]


def at(api: Api, path: str = "/etc", side: Any = None, **form_over: Any) -> None:
    """Put a page on a folder, with the given control states, and paint it."""
    side = berry.DEVICE if side is None else side
    api.listing = [dict(entry) for entry in LISTING]
    s = berry.st(side)
    s["entries"] = [dict(entry) for entry in LISTING]
    s["cwd"] = path
    api.form = {berry.pid(side, "path"): path, berry.pid(side, "filter"): "",
                berry.pid(side, "sort"): "Name", berry.pid(side, "hidden"): False,
                "files": []}
    api.form.update(form_over)
    berry.render(side)


def at_local(api: Api, root: Path, **form_over: Any) -> None:
    """Point the local page at a real folder, set the controls and paint it."""
    berry.st(berry.LOCAL)["cwd"] = str(root)
    berry.st(berry.LOCAL)["entries"] = berry._list_local(str(root))
    api.form = {berry.pid(berry.LOCAL, "path"): str(root),
                berry.pid(berry.LOCAL, "filter"): "",
                berry.pid(berry.LOCAL, "sort"): "Name",
                berry.pid(berry.LOCAL, "hidden"): False, "files": []}
    api.form.update(form_over)
    berry.render(berry.LOCAL)


# --- one: the little formatters ---

def test_formatting() -> None:
    print("\n1. how a listing is painted")
    check(berry._sz(500) == "500B" and berry._sz(1024) == "1.0K"
          and berry._sz(5 * 1024 * 1024) == "5.0M",
          f"sizes are not readable: {berry._sz(500)}, {berry._sz(1024)}, "
          f"{berry._sz(5 * 1024 * 1024)}")
    kind = {berry._kind(entry) for entry in LISTING}
    check(kind == {"DIR", "FILE"}, f"a folder and files do not read as DIR/FILE: {kind}")
    mode = berry._mode(LISTING[0])          # /etc/bin, 0755 directory
    check(mode.startswith("0755 d"), f"a 0755 directory is not shown so: {mode!r}")
    filemode = berry._mode(LISTING[1])      # passwd, 0644 file
    check(filemode.startswith("0644 -"), f"a 0644 file is not shown so: {filemode!r}")
    check("rw-r--r--" in filemode, f"the permission letters are missing: {filemode!r}")
    check(berry._t(0) == "", "an mtime of 0 should read as blank")
    check(berry.language_for("/etc/config/nginx.conf") == "ini"
          and berry.language_for("a.py") == "python"
          and berry.language_for("a.json") == "json"
          and berry.language_for("run.sh") == "bash"
          and berry.language_for("no-extension") == "",
          "the extension -> language map is wrong")


# --- two: id routing (one tab, two pages) ---

def test_routing() -> None:
    print("\n2. a prefixed id maps back to a page and a control")
    check(berry.pid(berry.DEVICE, "files") == "dfiles"
          and berry.pid(berry.LOCAL, "files") == "lfiles",
          "the pages do not prefix their ids with d/l")
    check(berry.side_of_id("dfiles") == berry.DEVICE
          and berry.side_of_id("lfiles") == berry.LOCAL,
          "the side cannot be read back from an id")
    check(berry.base_of_id("dupload") == "upload"
          and berry.base_of_id("lpath") == "path",
          "the base control name is not the id minus its prefix")
    check(berry.berry_spec().get("kind") == "tabs"
          and [c.get("label") for c in berry.berry_spec()["children"]] == ["Device", "Local"],
          "the Berry tab is not a Device/Local page frame")


# --- three: sorting, filtering, hidden files ---

def test_render() -> None:
    print("\n3. sorting, filtering and hidden files")
    api = fresh()
    at(api, "/etc")
    check(names(api) == ["bin", "hosts", "passwd"],
          f"the default view is not folders-then-name: {names(api)}")
    check(".profile" not in names(api), "a dotfile showed with show-hidden off")

    print("\n3b. show hidden")
    api.form["dhidden"] = True
    berry.render(berry.DEVICE)
    check(".profile" in names(api), "show-hidden did not reveal the dotfile")

    print("\n3c. the filter")
    api.form["dhidden"] = False
    api.form["dfilter"] = "as"
    berry.render(berry.DEVICE)
    check(names(api) == ["passwd"], f"filter 'as' should leave only passwd: {names(api)}")

    print("\n3d. sort by size, then descending")
    api.form["dfilter"] = ""
    api.form["dsort"] = "Size"
    berry.render(berry.DEVICE)
    check(names(api) == ["bin", "hosts", "passwd"],
          f"size ascending lost the order or the folder-first rule: {names(api)}")
    api.config_values["sort_desc"] = True        # a config edit, without rewriting the file
    berry.render(berry.DEVICE)
    check(names(api) == ["bin", "passwd", "hosts"],
          f"size descending is wrong: {names(api)}")
    api.config_values["sort_desc"] = False

    print("\n3e. the device row holds size, type, modified, mode and path")
    api.form["dsort"] = "Name"
    berry.render(berry.DEVICE)
    passwd_row = next(row for row in rows(api) if row[0] == "passwd")
    check(len(passwd_row) == 6 and passwd_row[1] == "500B" and passwd_row[2] == "FILE"
          and passwd_row[5] == "/etc/passwd", f"a file row is not the whole row: {passwd_row}")


# --- four: navigation on the device ---

def test_navigation() -> None:
    print("\n4. navigating the device")
    api = fresh()
    at(api, "/etc")
    berry.navigate(berry.DEVICE, "/var/log")
    asked = api.last("device.list")
    check(asked is not None, "navigating did not ask the device to list anything")
    check(api.argument("device.list", "path", asked[1], asked[2], "") == "/var/log",
          "the folder asked for is not the one navigated to")
    check(api.argument("device.list", "outside_root", asked[1], asked[2], False) is True,
          "a file manager must browse outside the deploy folder (outside_root=True)")

    print("\n4b. Up")
    berry.st(berry.DEVICE)["cwd"] = "/etc/network"
    berry.go_up(berry.DEVICE)
    check(berry.st(berry.DEVICE)["cwd"] == "/etc",
          f"Up from /etc/network should land on /etc, got {berry.st(berry.DEVICE)['cwd']}")

    print("\n4c. double-click a folder walks in, a file opens the editor tab")
    at(api, "/etc")
    berry.on_widget(berry.TAB, "dfiles", "selected", {"text": "bin"})
    check(api.argument("device.list", "path", *api.last("device.list")[1:], "") == "/etc/bin",
          "double-clicking a folder did not open it")
    berry.on_widget(berry.TAB, "dfiles", "selected", {"text": "passwd"})
    read = api.last("device.read_text")
    check(read is not None and api.argument("device.read_text", "path", read[1], read[2], "")
          == "/etc/passwd", "double-clicking a file did not read it from the device")
    check("Berry Editor" in api.open_tabs, "the editor tab did not open")
    check(berry.editing and berry.editing["side"] == berry.DEVICE
          and berry.editing["path"] == "/etc/passwd",
          f"the editor is not tracking the opened file: {berry.editing}")


# --- five: the in-memory editor (Save writes) ---

def test_editor() -> None:
    print("\n5. the notepad editor: in memory until Save")
    api = fresh()
    at(api, "/etc")

    entry = berry.st(berry.DEVICE)["by_name"]["passwd"]
    berry.open_editor(berry.DEVICE, entry)
    check(berry._editor_open(), "opening a file did not add the editor tab")
    check(api.editor.get("ed-body") == api.file_text,
          "the editor body was not filled with the file read from the device")
    before = api.times("device.write_text")
    check(before == 0, "opening a file already wrote something")

    print("\n5b. the language box re-shades without touching the file")
    berry.set_editor_language("JSON")
    check(api.editor_hl == "json", f"the language box did not reach the highlighter: {api.editor_hl}")
    berry.set_editor_language("auto")
    check(api.editor_hl == "", f"'auto' on a no-extension device file should mean none, got {api.editor_hl!r}")

    print("\n5c. Save writes the typed text, then closes the tab and re-lists")
    api.editor["ed-body"] = "changed contents\n"
    berry.save_editor()
    wrote = api.last("device.write_text")
    check(wrote is not None
          and api.argument("device.write_text", "path", wrote[1], wrote[2], "") == "/etc/passwd"
          and api.argument("device.write_text", "text", wrote[1], wrote[2], "")
          == "changed contents\n", "the typed text did not reach write_text")
    check(not berry._editor_open() and berry.editing is None,
          "Save left the editor tab open")
    check(api.called("device.list"), "saving did not re-list the folder")

    print("\n5d. Close on a clean body discards silently")
    api = fresh()
    at(api, "/etc")
    berry.open_editor(berry.DEVICE, berry.st(berry.DEVICE)["by_name"]["hosts"])
    asked_before = api.times("device.write_text")
    berry.close_editor()
    check(api.times("device.write_text") == asked_before, "Close wrote the file")
    check(not berry._editor_open(), "Close left the editor open")

    print("\n5e. Close on a dirty body asks first, and a refusal keeps it open")
    api = fresh()
    at(api, "/etc")
    berry.open_editor(berry.DEVICE, berry.st(berry.DEVICE)["by_name"]["hosts"])
    api.editor["ed-body"] = "unsaved edits"
    api.answer_yes = False
    berry.close_editor()
    check(berry._editor_open() and berry.editing is not None,
          "refusing to discard closed the editor anyway")
    api.answer_yes = True
    berry.close_editor()
    check(not berry._editor_open(), "agreeing to discard did not close the editor")

    print("\n5f. Reload re-reads and drops the edits")
    api = fresh()
    at(api, "/etc")
    berry.open_editor(berry.DEVICE, berry.st(berry.DEVICE)["by_name"]["passwd"])
    api.editor["ed-body"] = "typo"
    api.answer_yes = True                    # accept the discard Reload asks about
    api.file_text = "fresh read\n"
    berry.reload_editor()
    check(api.editor.get("ed-body") == "fresh read\n",
          f"Reload did not re-read the file: {api.editor.get('ed-body')!r}")
    check(berry.editing["text"] == "fresh read\n", "Reload did not update the tracked text")


# --- six: the actions and the paths they address ---

def test_actions() -> None:
    print("\n6. acting on a selection (device)")
    api = fresh()
    at(api, "/etc")

    # New folder.
    api.inputs = ["conf"]
    berry.new_folder(berry.DEVICE)
    mk = api.last("device.mkdir")
    check(mk is not None and api.argument("device.mkdir", "path", mk[1], mk[2], "")
          == "/etc/conf", f"new folder went to the wrong path: {mk}")

    # Rename / move.
    at(api, "/etc", files=["passwd"])
    api.inputs = ["passwd.bak"]
    berry.do_rename(berry.DEVICE, None)
    rn = api.last("device.rename")
    check(rn is not None
          and api.argument("device.rename", "path", rn[1], rn[2], "") == "/etc/passwd"
          and api.argument("device.rename", "new_path", rn[1], rn[2], "") == "/etc/passwd.bak",
          f"rename did not move the selected file: {rn}")

    # Delete a folder asks for recursion.
    at(api, "/etc", files=["bin"])
    api.answer_yes = True
    berry.do_delete(berry.DEVICE, None)
    rm = api.last("device.remove")
    check(rm is not None
          and api.argument("device.remove", "path", rm[1], rm[2], "") == "/etc/bin"
          and api.argument("device.remove", "recursive", rm[1], rm[2], False) is True
          and api.argument("device.remove", "outside_root", rm[1], rm[2], False) is True,
          f"deleting a folder was not recursive-and-outside-root: {rm}")

    # A refused confirmation deletes nothing.
    before = api.times("device.remove")
    at(api, "/etc", files=["hosts"])
    api.answer_yes = False
    berry.do_delete(berry.DEVICE, None)
    check(api.times("device.remove") == before, "delete ran even though it was refused")
    api.answer_yes = True

    # Chmod quotes the path.
    at(api, "/etc", files=["passwd"])
    api.inputs = ["600"]
    berry.do_chmod(berry.DEVICE, None)
    command = api.command()
    check("chmod 600 " in command and shlex.quote("/etc/passwd") in command,
          f"chmod command is wrong: {command!r}")

    # A bad octal never reaches the device.
    before = api.times("device.run")
    api.inputs = ["999"]
    berry.do_chmod(berry.DEVICE, None)
    check(api.times("device.run") == before, "a bad octal mode still ran a chmod")


# --- seven: transfers and read-only tools ---

def test_transfers_and_tools() -> None:
    print("\n7. uploads, downloads and the read-only tools")
    api = fresh()
    at(api, "/etc")

    # Upload from a path that is not there stops before touching the device.
    api.inputs = ["/no/such/here/file.txt"]
    before = api.times("device.upload")
    berry.upload_typed(berry.DEVICE)
    check(api.times("device.upload") == before, "a missing local file was still uploaded")

    # Downloading a folder is refused; a file goes to the chosen local path.
    at(api, "/etc", files=["bin"])
    berry.download_typed(berry.DEVICE, None)
    check(not api.called("device.download"), "a folder was offered for download")

    at(api, "/etc", files=["passwd"])
    dest = str(Path(api.paths.data) / "dl-passwd")
    api.inputs = [dest]
    berry.download_typed(berry.DEVICE, None)
    dl = api.last("device.download")
    check(dl is not None
          and api.argument("device.download", "remote_path", dl[1], dl[2], "") == "/etc/passwd"
          and api.argument("device.download", "outside_root", dl[1], dl[2], False) is True,
          f"download did not address the device path: {dl}")

    # Disk usage runs df and shows it in a dialog.
    berry.do_disk(berry.DEVICE)
    check("df -h" in api.command(), "Disk did not ask for df -h")
    check(api.called("ui.dialog"), "df output was not shown in a dialog")

    # Find builds a quoted, depth-and-count-limited search.
    api.inputs = ["host s"]
    berry.do_find(berry.DEVICE)
    find_command = api.command()
    check("find /etc -maxdepth 4 -iname" in find_command,
          f"find is not the shape we want: {find_command!r}")
    check(shlex.quote("*host s*") in find_command,
          f"the search pattern was not quoted: {find_command!r}")
    check("head -n 300" in find_command, f"find ignored the result limit: {find_command!r}")

    # Info reads ls -ld and stat for the selection.
    at(api, "/etc", files=["hosts"])
    berry.do_info(berry.DEVICE, None)
    info_command = api.command()
    check("ls -ld" in info_command and "stat " in info_command,
          f"info did not ask ls -ld and stat: {info_command!r}")


# --- eight: the right-click / Actions menu ---

def test_menus() -> None:
    print("\n8. what the right-click menu offers, and what a pick runs")
    api = fresh()
    at(api, "/etc")

    entry_file = berry.st(berry.DEVICE)["by_name"]["passwd"]
    labels = [label for label, _ident in berry.menu_for(berry.DEVICE, entry_file)]
    check("Edit" in labels and "Download..." in labels and "Chmod" in labels
          and "Delete" in labels, f"a file menu is incomplete: {labels}")
    check("New folder" in labels and "Upload..." in labels,
          "the folder-level actions are missing from a row menu")

    entry_dir = berry.st(berry.DEVICE)["by_name"]["bin"]
    dir_labels = [label for label, _ident in berry.menu_for(berry.DEVICE, entry_dir)]
    check("Open" in dir_labels and "Edit" not in dir_labels and "Download..." not in dir_labels,
          f"a folder menu should not offer Edit/Download: {dir_labels}")

    none_labels = [label for label, _ident in berry.menu_for(berry.DEVICE, None)]
    check("New folder" in none_labels and "Disk usage" in none_labels
          and "Delete" not in none_labels, f"an empty-area menu is wrong: {none_labels}")

    # A local file menu offers Upload to device instead of Download / Chmod.
    local_menu = berry.menu_for(berry.LOCAL, {
        "name": "a.txt", "path": "/x/a.txt", "directory": False})
    check(any(ident == "up2dev" for _lab, ident in local_menu)
          and not any(ident == "chmod" for _lab, ident in local_menu)
          and not any(ident == "download" for _lab, ident in local_menu),
          "the local menu must upload to the device, not chmod/download")

    # Right-clicking a row and picking Delete acts on THAT row, not the selection.
    at(api, "/etc", files=[])                 # nothing selected
    api.choose = "Delete"
    api.answer_yes = True
    berry.on_widget(berry.TAB, "dfiles", "context", {"text": "hosts"})
    rm = api.last("device.remove")
    check(rm is not None and api.argument("device.remove", "path", rm[1], rm[2], "")
          == "/etc/hosts", f"right-click delete acted on the wrong row: {rm}")


# --- nine: the local page on a temp folder ---

def test_local_page() -> None:
    print("\n9. the local page on a real folder")
    api = fresh()
    root = Path(tempfile.mkdtemp(prefix="berry-test-"))
    try:
        (root / "subdir").mkdir()
        (root / "a.txt").write_text("hello\n", encoding="utf-8")
        (root / ".hidden").write_text("x", encoding="utf-8")

        berry.navigate(berry.LOCAL, str(root))
        seen = names(api, berry.LOCAL)
        check("subdir" in seen and "a.txt" in seen,
              f"the local listing is missing entries: {seen}")
        check(seen[0] == "subdir", f"folders should be on top locally too: {seen}")
        check(".hidden" not in seen, "a dotfile showed locally with hidden off")

        print("\n9b. local new folder and delete really touch the disk")
        api.inputs = ["made"]
        berry.new_folder(berry.LOCAL)
        check((root / "made").is_dir(), "new folder did not create a real directory")

        at_local(api, root, files=["a.txt"])
        api.answer_yes = True
        berry.do_delete(berry.LOCAL, None)
        check(not (root / "a.txt").exists(), "local delete left the file on disk")

        print("\n9c. local info reads stat, not the device")
        (root / "keep.txt").write_text("k\n", encoding="utf-8")
        at_local(api, root, files=["subdir"])
        before = api.times("device.run")
        berry.do_info(berry.LOCAL, None)
        check(api.times("device.run") == before, "local info wrongly ran a device command")
        check(api.called("ui.dialog"), "local info showed no dialog")

        print("\n9d. editing a local file opens the editor tab and writes on Save")
        (root / "note.json").write_text('{"one": 1}\n', encoding="utf-8")
        at_local(api, root)
        entry = berry.st(berry.LOCAL)["by_name"].get("note.json")
        check(entry is not None, "the freshly written local file was not in the listing")
        berry.open_editor(berry.LOCAL, entry)
        check(api.editor.get("ed-body") == '{"one": 1}\n', "the editor did not read the local file")
        check(berry.editing["detected"] == "json",
              f"a .json file was not auto-detected as json: {berry.editing['detected']!r}")
        check(berry.editor_spec(berry.editing["detected"])["children"][1].get("highlight") == "json",
              "the editor spec for a json file was not built to shade json")
        api.editor["ed-body"] = '{"two": 2}\n'
        berry.save_editor()
        check((root / "note.json").read_text(encoding="utf-8") == '{"two": 2}\n',
              "Save did not write the local file")
        check(not berry._editor_open(), "Save left the local editor open")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- ten: copy / paste across the pages ---

def test_copy_paste() -> None:
    print("\n10. copy and paste across the two pages")
    api = fresh()
    at(api, "/etc")
    berry.st(berry.DEVICE)["cwd"] = "/etc"

    print("\n10a. copy on the device, paste on the local page downloads it")
    berry.st(berry.LOCAL)["cwd"] = str(Path(tempfile.gettempdir()) / "berry-local")
    api.form["files"] = ["passwd"]
    berry.do_copy(berry.DEVICE, None)
    check(berry.clip and berry.clip["side"] == "device" and berry.clip["path"] == "/etc/passwd",
          f"copy did not take the device entry: {berry.clip}")
    api.form["files"] = []
    berry.do_paste(berry.LOCAL)
    dl = api.last("device.download")
    check(dl is not None
          and api.argument("device.download", "remote_path", dl[1], dl[2], "") == "/etc/passwd"
          and str(api.argument("device.download", "local", dl[1], dl[2], "")).endswith("passwd"),
          f"pasting a device file locally did not download it: {dl}")

    print("\n10b. copy a local file, paste on the device uploads it")
    api = fresh()
    berry.st(berry.DEVICE)["cwd"] = "/www/uploads"
    entry = {"name": "notes.txt", "path": "/home/keigh/notes.txt", "directory": False}
    berry.do_copy(berry.LOCAL, entry)
    check(berry.clip and berry.clip["side"] == "local", "copy did not take the local entry")
    berry.do_paste(berry.DEVICE)
    up = api.last("device.upload")
    check(up is not None
          and str(api.argument("device.upload", "local", up[1], up[2], ""))
          == "/home/keigh/notes.txt"
          and api.argument("device.upload", "remote_path", up[1], up[2], "")
          == "/www/uploads/notes.txt",
          f"pasting a local file on the device did not upload it: {up}")

    print("\n10c. paste on the same device page uses cp, not a transfer")
    api = fresh()
    berry.st(berry.DEVICE)["cwd"] = "/etc/backup"
    berry.do_copy(berry.DEVICE, {"name": "hosts", "path": "/etc/hosts", "directory": False})
    berry.do_paste(berry.DEVICE)
    command = api.command()
    check("cp -a" in command and shlex.quote("/etc/hosts") in command,
          f"a same-page device paste did not run cp: {command!r}")

    print("\n10d. the local toolbar upload button sends its selected file")
    api = fresh()
    berry.st(berry.DEVICE)["cwd"] = "/www"
    berry.st(berry.LOCAL)["cwd"] = "/home/keigh"
    berry.st(berry.LOCAL)["by_name"] = {
        "pic.png": {"name": "pic.png", "path": "/home/keigh/pic.png", "directory": False}}
    api.form["files"] = ["pic.png"]
    berry.upload_selected_to_device()
    up = api.last("device.upload")
    check(up is not None
          and str(api.argument("device.upload", "local", up[1], up[2], ""))
          == "/home/keigh/pic.png"
          and api.argument("device.upload", "remote_path", up[1], up[2], "") == "/www/pic.png",
          f"the local Upload-to-device button did not address both ends: {up}")

    print("\n10e. a folder is refused by Upload-to-device")
    api = fresh()
    berry.st(berry.LOCAL)["cwd"] = "/home/keigh"
    berry.st(berry.LOCAL)["by_name"] = {
        "stuff": {"name": "stuff", "path": "/home/keigh/stuff", "directory": True}}
    api.form["files"] = ["stuff"]
    before = api.times("device.upload")
    berry.upload_selected_to_device()
    check(api.times("device.upload") == before, "a folder was uploaded as a file")


# --- eleven: the tab and editor, as Qt builds them ---

def test_the_tabs(app: Any) -> None:
    print("\n11. the tab and the editor, built by Qt from their specs")
    import addon_api
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTabWidget, QPlainTextEdit

    fresh()                    # the addon's api hand, for the specs below

    view, reg = addon_api.build_spec(berry.berry_spec())
    check(isinstance(view, QTabWidget) and view.count() == 2,
          "the Berry tab is not a two-page frame")
    check([view.tabText(i) for i in range(view.count())] == ["Device", "Local"],
          "the two pages are not labelled Device and Local")

    wanted_device = {"dpath", "dgo", "dup", "drefresh", "droot", "dhome", "dplaces",
                     "dfilter", "dsort", "dhidden", "dfiles", "dstatus",
                     "dmenu", "dmkdir", "dupload", "ddownload", "dpaste"}
    check(not wanted_device - set(reg),
          f"the device page leaves out {sorted(wanted_device - set(reg)) or 'nothing'}")
    wanted_local = {"lpath", "lgo", "lup", "lrefresh", "lhome", "lplaces",
                    "lfilter", "lsort", "lhidden", "lfiles", "lstatus",
                    "lmenu", "lmkdir", "lup2dev", "lpaste"}
    check(not wanted_local - set(reg),
          f"the local page leaves out {sorted(wanted_local - set(reg)) or 'nothing'}")

    dtable = reg["dfiles"]
    check(dtable.columnCount() == 6, f"the device listing is not six wide: {dtable.columnCount()}")
    dheaders = [dtable.headerItem().text(i) for i in range(dtable.columnCount())]
    check(dheaders == ["Name", "Size", "Type", "Modified", "Mode", "Path"],
          f"the device column titles are wrong: {dheaders}")
    check(not dtable.rootIsDecorated(), "the flat listing should not show expanders")
    check(dtable.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu,
          "the listing is not set up for a right-click menu")

    ltable = reg["lfiles"]
    check(ltable.columnCount() == 5, f"the local listing is not five wide: {ltable.columnCount()}")
    lheaders = [ltable.headerItem().text(i) for i in range(ltable.columnCount())]
    check("Mode" not in lheaders, f"the local listing should have no Mode column: {lheaders}")

    # The action row sits above the listing on the device page: in that page's
    # column children, the toolbar row must come before the table.
    device_page = berry.berry_spec()["children"][0]
    kinds = [child.get("kind") for child in device_page["children"]]
    check(kinds.index("row", 2) < kinds.index("table"),
          f"the action row must sit above the listing: {kinds}")

    # Right-click wiring actually reaches the addon through on_widget: the text
    # of the row under the cursor is what the highlighter-independent menu needs.
    addon_api.apply_widget_property(dtable, "rows",
                                    [["passwd", "500B", "FILE", "", "0644 -", "/etc/passwd"]])
    item = dtable.topLevelItem(0)
    check(addon_api._context_text(item) == "passwd",
          "the row text for a right-click is wrong")

    view.deleteLater()

    print("\n11b. the editor spec carries a live syntax highlighter")
    ew, ereg = addon_api.build_spec(berry.editor_spec("python"))
    editor_body = ereg.get("ed-body")
    check(isinstance(editor_body, QPlainTextEdit), "the editor has no multi to type in")
    highlighter = getattr(editor_body, "_addon_highlighter", None)
    check(isinstance(highlighter, addon_api.RegexHighlighter),
          "a python editor did not get a highlighter")
    check(highlighter is not None and len(highlighter._rules)
          == len(addon_api._LANG_RULES["python"]),
          "the python highlighter did not load the python rules")
    for ident in ("ed-path", "ed-lang", "ed-body", "ed-save", "ed-reload", "ed-close"):
        check(ident in ereg, f"the editor spec is missing {ident}")
    ew.deleteLater()

    print("\n11c. the highlight property switches languages on a live widget")
    from PyQt6.QtWidgets import QPlainTextEdit as PE
    plain = PE()
    addon_api.apply_widget_property(plain, "highlight", "json")
    check(isinstance(getattr(plain, "_addon_highlighter", None), addon_api.RegexHighlighter)
          and len(plain._addon_highlighter._rules) == len(addon_api._LANG_RULES["json"]),
          "setting highlight=json did not attach the json rules")
    addon_api.apply_widget_property(plain, "highlight", "css")
    check(len(plain._addon_highlighter._rules) == len(addon_api._LANG_RULES["css"]),
          "switching the language did not swap the rule set")
    plain.deleteLater()

    berry.shutdown()


# --- twelve: the marks beside the tab labels ---

def test_the_marks(app: Any) -> None:
    """The drupelets and the page-and-pencil: what the addon sends, and its shape."""
    print("\n12. the tabs' marks")
    api = fresh()
    icons = [str(api.argument("tabs.set_icon", "icon", arguments, named, ""))
             for where, arguments, named in api.calls if where == "tabs.set_icon"]
    wanted = berry.tab_icon()
    check(len(icons) == 1 and wanted in icons,
          f"init should set exactly the Berry icon, saw {icons}")

    # The Berry mark is a shipped picture when the assets folder holds one, and
    # the drawn cluster when nobody has put berryicon.png there.
    picture = Path(str(api.paths.assets)) / berry.TAB_ICON_FILE
    if picture.is_file():
        check(wanted == str(picture),
              f"{berry.TAB_ICON_FILE} is in the assets folder, so it should be used")
        look(f"the tab wears the picture at {picture}")
    else:
        check(wanted == berry.TAB_ICON,
              "with no picture in assets the drawn drupelets are sent")

    # Opening the editor adds its own mark to its own tab.
    at(api, "/etc")
    berry.open_editor(berry.DEVICE, berry.st(berry.DEVICE)["by_name"]["passwd"])
    icons = [str(api.argument("tabs.set_icon", "icon", arguments, named, ""))
             for where, arguments, named in api.calls if where == "tabs.set_icon"]
    check(berry.EDITOR_ICON in icons, "the editor tab did not set its own icon")
    for sent in (berry.TAB_ICON, berry.EDITOR_ICON):
        check(sent.startswith("<svg") and sent.rstrip().endswith("</svg>"),
              "an icon is not a self-contained SVG document")
    if app is None:
        look("no Qt here, so the drawing itself is not checked")


# --- zero: Berry owns the file tab ---

def test_loads_standalone() -> None:
    """Berry must supply the file tab the app no longer builds, adding exactly one on its own."""
    print("\n0. Berry loads as the file tab, on its own")
    api = fresh()
    check("Berry" in api.open_tabs,
          "initialising did not add the Berry window tab")
    added = [str(api.argument("tabs.add_tab", "name", arguments, named, ""))
             for where, arguments, named in api.calls if where == "tabs.add_tab"]
    check(added.count("Berry") == 1,
          f"init should add exactly one Berry tab, saw {added}")


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint (● ○ ✓),
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    global berry
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    berry = addon_client.load_addon(SOURCE)
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the spec itself is not built: {error}")

    print(f"berry: the addon at {SOURCE.name}")
    berry.init(Api(addon_id="berry"))

    test_loads_standalone()
    test_formatting()
    test_routing()
    test_render()
    test_navigation()
    test_editor()
    test_actions()
    test_transfers_and_tools()
    test_menus()
    test_local_page()
    test_copy_paste()
    if app is not None:
        test_the_tabs(app)
    else:
        print("\n11. skipped: no Qt on this machine")
    test_the_marks(app)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


berry = None

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
