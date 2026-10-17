#!/usr/bin/env python3
"""A test for the multi_ssh addon (for the addon store).

It runs the addon with the recording stub from :mod:`addon_client`, and keeps a
row of fake sub-pages so the addon's ``add_page`` / ``remove_page`` / ``pages``
round trip behaves. What is checked:

* the manifest, and the picture the store is expected to show for it;
* the pure helpers: an address cut into parts, an entry filled from the file,
  a caption that cannot collide with a session already open;
* quickstart entries drawn from the config, each with its own picture;
* a session opened on its own connection, password asked for and never kept;
* the shell's three sayings - up, refused, gone - and the two ways a dropped
  link can leave the tab;
* a broadcast reaching the sessions that are up and only those;
* adding, editing, removing an entry and giving one a picture.

Run it from the Test panel, or on the command line::

    python addon.store/_tests/test_multi_ssh.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the store install source or local tree.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "multi_ssh.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = ROOT / "addons" / "multi_ssh.py"

#: A picture of the right shape, so the icon code has something to copy.
PICTURE = b"\x89PNG\r\n\x1a\n" + b"0" * 32

FAILURES: list[str] = []
addon_module = None

#: Where the addon's data folder is pointed for the run, so it never writes in
#: the tree the test is read from.
WORK = Path(tempfile.mkdtemp(prefix="multi-ssh-test-"))


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


class Api(addon_client.StubAPI):
    """The recording stub, with a tabs container that remembers its own pages.

    The real container is a Qt widget; this is the same three answers - what is
    open, what adding does, what taking away does - so a check on a closed
    session reads the row of captions rather than a log line.
    """

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.pages: list[str] = []
        self.form: dict[str, Any] = {}       # widget_value("text") per id
        self.selected: list[str] = []        # the quickstart list's selection
        self.answers: list[Any] = []         # queued replies to ui.input
        self.dialogs: list[Any] = []         # queued replies to ui.dialog
        self.picked: list[Any] = []          # queued replies to ui.pick_file
        self.dead: set[str] = set()          # terminals whose shell has gone
        self.questions: bool = True          # what a yes/no box answers
        self.written: dict[str, Any] = {}    # what config.set was handed

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments,
                                                       named, fallback)
        if method == "tabs.add_tab":
            # The spec's own children make the standing-in page; a test that
            # never built the widget has to be told it is there.
            self.pages = [addon_module.EMPTY_CAPTION]
            return ask("name", "tab")
        if method == "tabs.set_widget":
            identifier = str(ask("identifier", ""))
            prop = str(ask("prop", ""))
            value = ask("value")
            if prop == "add_page":
                self._add_page(value)
            elif prop == "remove_page":
                self._remove_page(value)
            elif prop == "command" and identifier in self.dead:
                raise RuntimeError("closed channel")
            return None
        if method == "tabs.widget_value":
            identifier = str(ask("identifier", ""))
            what = str(ask("what", "value"))
            if what == "pages":
                return list(self.pages)
            if what == "selected":
                return list(self.selected)
            return self.form.get(identifier)
        if method == "ui.input":
            return self.answers.pop(0) if self.answers else None
        if method == "ui.dialog":
            return self.dialogs.pop(0) if self.dialogs else None
        if method == "ui.pick_file":
            return self.picked.pop(0) if self.picked else ""
        if method == "ui.question":
            return self.questions
        if method == "config.set":
            self.written[str(ask("key", ""))] = ask("value")
            return None
        if method == "config.get":
            key = str(ask("key", ""))
            stored = self.config_values.get(key)
            return stored if stored is not None else ask("default")
        return super().default(method, arguments, named)

    # --- the fake row of sub-pages ---

    def _add_page(self, value: Any) -> None:
        entry = value if isinstance(value, dict) else {}
        caption = str(entry.get("label") or "Tab")
        try:
            where = int(entry.get("position"))
        except (TypeError, ValueError):
            where = len(self.pages)
        self.pages.insert(max(0, min(where, len(self.pages))), caption)

    def _remove_page(self, value: Any) -> None:
        wanted = str(value or "")
        if wanted.isdigit():
            index = int(wanted)
            if 0 <= index < len(self.pages):
                del self.pages[index]
            return
        if wanted in self.pages:
            del self.pages[self.pages.index(wanted)]

    # --- reading what the addon did ---

    def run_clear(self) -> None:
        """Forget what has been asked, so the next section starts clean."""
        self.calls = []
        self.form.clear()
        self.dead.clear()
        self.written = {}

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None

    def says(self, identifier: str) -> Any:
        """The last text written on one label."""
        return self.last_set(identifier, "text")

    def notices(self) -> list[str]:
        return [str(arguments[0]) for where, arguments, _k in self.calls
                if where == "ui.notify"]

    def connects(self) -> list[dict]:
        """Every set of details handed to a terminal, oldest first."""
        return [arguments[3] for where, arguments, _k in self.calls
                if where == "tabs.set_widget" and len(arguments) >= 4
                and arguments[2] == "connect"]


def test_manifest(addon: Any, api: Api) -> None:
    print("\n1. what the addon says it is")
    check(addon.ADDON.get("requires") == ["ssh"],
          f"it asks for SSH and nothing else: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("connection")) == "own",
          "every shell is a connection of its own, and the manifest says so")
    check(str(addon.ADDON.get("api")) == "1", "the api version is 1")
    check(str(addon.ADDON.get("version") or "") == "1.2",
          f"the published version is 1.2: {addon.ADDON.get('version')!r}")
    check("icon" not in addon.ADDON,
          "the manifest names no picture, so the store picks one by the rule")
    check(addon.TAB_ICON_FILE == "multi_sshicon.png",
          f"the tab's mark follows {{addon}}icon.png: {addon.TAB_ICON_FILE!r}")
    # Where the picture is depends on how this test runs: read from the
    # repository it sits in addon.store/assets/<id>/; run by the store as part
    # of an install, the very same file is downloaded flat beside this test.
    # Either is the mark the store is expected to show, so neither may fail it.
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / addon.TAB_ICON_FILE,
                  ROOT / "addon.store" / "assets" / "multi_ssh"
                  / addon.TAB_ICON_FILE]
    picture = next((one for one in candidates if one.is_file()), candidates[-1])
    check(picture.is_file(), f"{picture.name} is in the store's assets folder")
    # The tab's shape, since the fake container trusts it rather than building
    # it: a sessions container, standing in for itself while it is empty.
    body = str(addon.TAB_SPEC)
    check("sessions" in body and addon.EMPTY_CAPTION in body
          and "terminal" in str(addon.session_spec("sh1", "root@h:22")),
          "the spec holds a sessions container and a terminal per page")
    keys = {field["key"] for field in addon.FIELDS}
    check({"default_user", "default_port", "trust_unknown_host",
           "close_on_disconnect"} <= keys,
          f"the four switches the code reads are declared: {sorted(keys)}")
    check("quickstart" in addon.DEFAULTS and "quickstart" not in keys,
          "the entries live in the file but not in the form, so the form "
          "leaves them alone")


def test_helpers(addon: Any, api: Api) -> None:
    """The pure half, where a mistake would only show up on somebody's router."""
    print("\n2. addresses, ports and captions")
    check(addon.split_address("root@192.168.12.1:2222") ==
          {"username": "root", "host": "192.168.12.1", "port": "2222"},
          "a whole address is cut into its three parts")
    check(addon.split_address("192.168.1.1") ==
          {"username": "", "host": "192.168.1.1", "port": ""},
          "an address with nothing in it is still a host")
    check(addon.split_address("fe80::1")["host"] == "fe80::1",
          "an IPv6 literal keeps its colons instead of losing a port")
    for given, want in ((None, 22), ("", 22), ("22", 22), (0, 22),
                        (70000, 22), ("ssh", 22), (80, 80)):
        check(addon.port_of(given, 22) == want,
              f"port {given!r} is read as {want}")
    check(addon.unique_caption("Router", ["Router", "Router 2"]) == "Router 3",
          "a caption is made to differ from the sessions already open")
    check(addon.unique_caption("", ["Tab"]) == "Session",
          "a page with no name says what it is")
    for name, suffix, want in (("My Router", ".png", "My_Router.png"),
                               ("a/b:c", ".svg", "a_b_c.svg"),
                               ("", ".png", "entry.png"),
                               ("r1", ".exe", "r1.png")):
        check(addon.icon_name(name, suffix) == want,
              f"{name!r} + {suffix!r} keeps a safe file name: "
              f"{addon.icon_name(name, suffix)!r}")


def test_entries(addon: Any, api: Api) -> None:
    """What the config file's list becomes, and what the list beside it shows."""
    print("\n3. quickstart entries from the config file")
    fresh(api)
    api.config_values["quickstart"] = [
        {"name": "Router", "host": "192.168.1.1"},
        "root@192.168.12.1:2222",
        {"host": "knet4b.lan", "port": "2200", "username": "keigh",
         "icon": "knet4b.png", "key_file": "id_ed25519"},
        {"name": "nowhere"},
        "",
        None,
    ]
    found = addon.reload_entries()
    check(len(found) == 3,
          f"three of the six are machines: {[e.get('host') for e in found]}")
    check(found[0]["port"] == 22 and found[0]["username"] == "root",
          f"an entry with nothing said gets the offered defaults: {found[0]}")
    check(found[1] == {"name": "192.168.12.1", "host": "192.168.12.1",
                       "port": 2222, "username": "root", "key_file": "",
                       "icon": ""},
          f"a bare address is a whole entry: {found[1]}")
    check(found[2]["port"] == 2200 and found[2]["key_file"] == "id_ed25519"
          and found[2]["icon"] == "knet4b.png",
          f"what the entry names is kept: {found[2]}")
    drawn = api.last_set("qs_list", "items")
    check([str(item["text"]) for item in drawn] ==
          ["Router", "192.168.12.1", "knet4b.lan"],
          f"the list shows one row per entry: {drawn}")
    # A picture that is not in the addon's folder is no picture at all, so the
    # row is drawn without one rather than with a broken thumbnail.
    check(drawn[2]["icon"] == "",
          f"a name nothing was copied under shows no mark: {drawn[2]}")
    stored = WORK / "icons" / "Router.png"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(PICTURE)
    api.config_values["quickstart"][0]["icon"] = "Router.png"
    fresh(api)
    api.config_values["quickstart"] = [
        {"name": "Router", "host": "192.168.1.1", "icon": "Router.png"}]
    addon.reload_entries()
    drawn = api.last_set("qs_list", "items")
    check(drawn[0]["icon"].replace("\\", "/").endswith("icons/Router.png"),
          f"a copied picture is found again by name: {drawn[0]['icon']!r}")


def test_open_and_close(addon: Any, api: Api) -> None:
    """One session, from the click to the tab going away with the link."""
    print("\n4. a session of its own, and no password kept")
    fresh(api)
    api.config_values["quickstart"] = [
        {"name": "Router", "host": "192.168.1.1", "port": 22,
         "username": "keigh"}]
    addon.reload_entries()
    api.run_clear()

    api.answers.append(None)                 # the box is cancelled
    addon.connect_row(0)
    check(api.pages == [addon.EMPTY_CAPTION] and not api.connects(),
          "walking out of the password box opens nothing at all")

    api.answers.append("s3cret")
    identifier = addon.connect_row(0)
    check(identifier == "sh1", f"the session is answerable by id: {identifier!r}")
    check(api.pages == ["Router"],
          f"the placeholder gives way to the session's page: {api.pages}")
    page = api.last_set("sessions", "add_page")
    spec = str(page.get("spec"))
    check("terminal" in spec and "sh1_conn" in spec and "sh1_disc" in spec,
          "the page carries the shell and its own two buttons")
    said = api.connects()[0]
    check(said["host"] == "192.168.1.1" and said["port"] == 22
          and said["username"] == "keigh" and said["password"] == "s3cret"
          and said["trust_unknown_host"] is True,
          f"the details reach the terminal to connect with: {said}")
    check(str(api.says("qs_status")) == "1 session(s), 0 connected",
          f"the header counts what is open: {api.says('qs_status')!r}")
    addon.on_widget(addon.TAB, "sh1", "ready", {"connected": True})
    check(api.says("sh1_state") == "connected"
          and api.last_set("sh1_conn", "enabled") is False
          and api.last_set("sh1_disc", "enabled") is True,
          "an up shell lights its state line and hands over to Disconnect")
    check(str(api.says("qs_status")) == "1 session(s), 1 connected",
          f"and the count follows it: {api.says('qs_status')!r}")

    # Nothing the addon stores may hold the password.
    kept = [str(value) for _w, arguments, _n in api.calls
            if _w in ("config.set", "config.update")
            for value in (arguments[1],)]
    blob = " ".join(kept) + " ".join(str(entry) for entry in api.pages)
    check("s3cret" not in blob,
          f"the password is in no saved value: {blob[:60]!r}")

    # The link drops: by default the page goes with it.
    api.run_clear()
    addon.on_widget(addon.TAB, "sh1", "failed", {"error": "Permission denied"})
    addon.on_widget(addon.TAB, "sh1", "closed", {"connected": False})
    check(api.pages == [addon.EMPTY_CAPTION],
          f"a dropped session takes its tab with it: {api.pages}")
    check("Permission denied" in " ".join(api.notices()),
          f"and says why before it goes: {api.notices()}")
    check(not api.connects() and not api.last_set("sh1", "connect"),
          "a closed page is not reconnected behind anyone")

    # Switched off, the page stays and offers Connect again.
    fresh(api)
    api.config_values["close_on_disconnect"] = False
    api.config_values["quickstart"] = [
        {"name": "AP", "host": "192.168.50.9"}]
    addon.reload_entries()
    api.answers.append("pw")
    identifier = addon.connect_row(0)
    addon.on_widget(addon.TAB, identifier, "ready", {})
    api.run_clear()
    addon.on_widget(addon.TAB, identifier, "closed", {"connected": False})
    check(api.pages == ["AP"],
          f"kept pages are the whole point of the switch: {api.pages}")
    check(api.last_set(f"{identifier}_conn", "enabled") is True,
          "and the page is left ready to try again")
    check(str(api.says(f"{identifier}_state")) == "disconnected",
          f"saying what happened on the page itself: "
          f"{api.says(f'{identifier}_state')!r}")


def test_buttons(addon: Any, api: Api) -> None:
    """A session's own two buttons, and a second session beside the first."""
    print("\n5. Connect and Disconnect on the page")
    fresh(api)
    api.config_values["quickstart"] = [
        {"name": "Router", "host": "192.168.1.1"},
        {"name": "Switch", "host": "192.168.1.2", "port": 2222}]
    addon.reload_entries()
    api.answers.extend(["one", "two"])
    first = addon.connect_row(0)
    second = addon.connect_row(1)
    check(api.pages == ["Router", "Switch"],
          f"two entries, two sub-tabs: {api.pages}")
    check([said["password"] for said in api.connects()] == ["one", "two"],
          "each session was asked its own way in")
    check(len({first, second}) == 2,
          f"ids are not reused: {first!r}, {second!r}")
    api.run_clear()
    addon.on_widget(addon.TAB, f"{second}_disc", "clicked", {})
    check(api.last_set(second, "disconnect") is None
          and any(where == "tabs.set_widget" and len(arguments) >= 3
                  and arguments[1] == second and arguments[2] == "disconnect"
                  for where, arguments, _n in api.calls),
          "Disconnect drops just that shell")
    api.run_clear()
    api.answers.append("again")
    addon.on_widget(addon.TAB, f"{second}_conn", "clicked", {})
    check(api.connects()[0]["host"] == "192.168.1.2"
          and api.connects()[0]["port"] == 2222
          and api.connects()[0]["password"] == "again",
          f"Connect asks again and reuses the machine, not the password: "
          f"{api.connects()[0]}")
    check(str(api.says(f"{second}_state")) == "opening...",
          f"the page says it is trying: {api.says(f'{second}_state')!r}")


def test_broadcast(addon: Any, api: Api) -> None:
    """One line into every session that is up - and none into one that is not."""
    print("\n6. broadcast")
    fresh(api)
    api.config_values["quickstart"] = [
        {"name": "A", "host": "10.0.0.1"},
        {"name": "B", "host": "10.0.0.2"},
        {"name": "C", "host": "10.0.0.3"}]
    addon.reload_entries()
    api.answers.extend(["x", "y", "z"])
    ids = [addon.connect_row(row) for row in range(3)]
    for identifier in ids:
        addon.on_widget(addon.TAB, identifier, "ready", {})
    api.run_clear()
    api.form["bc_cmd"] = "   uptime  "
    addon.broadcast()
    sent = [arguments[1] for where, arguments, _n in api.calls
            if where == "tabs.set_widget" and len(arguments) >= 3
            and arguments[2] == "command"]
    check(sent == ids and api.last_set(ids[0], "command") == "uptime",
          f"the line went to all three, trimmed: {sent}")
    check("Sent to 3 session(s)." in api.notices(),
          f"and says how many took it: {api.notices()}")

    api.run_clear()
    addon.on_widget(addon.TAB, ids[1], "closed", {"connected": False})
    api.run_clear()
    addon.broadcast("date")
    sent = [arguments[1] for where, arguments, _n in api.calls
            if where == "tabs.set_widget" and len(arguments) >= 3
            and arguments[2] == "command"]
    check(sent == [ids[0], ids[2]],
          f"a session that is gone is not typed into: {sent}")

    api.run_clear()
    api.dead.add(ids[0])                     # up in the addon's book, dead in fact
    addon.broadcast("reboot")
    check("could not be reached" in " ".join(api.notices()),
          f"a shell that raises is counted, not swallowed: {api.notices()}")
    check(addon.connected_ids() == [ids[2]],
          f"and is dropped from the live list: {addon.connected_ids()}")

    api.run_clear()
    for identifier in list(addon.connected_ids()):
        addon.on_widget(addon.TAB, identifier, "closed", {})
    api.run_clear()
    addon.broadcast("ls")
    check("nothing was sent" in " ".join(api.notices()),
          f"with nothing up, nothing goes out: {api.notices()}")
    api.run_clear()
    api.form["bc_cmd"] = ""
    addon.broadcast("")
    check("Type the command" in " ".join(api.notices()),
          f"and an empty field is not a command: {api.notices()}")


def test_editing(addon: Any, api: Api) -> None:
    """The list is the config file's, so every change has to land in it."""
    print("\n7. adding, editing and removing an entry")
    fresh(api)
    api.config_values["quickstart"] = []
    addon.reload_entries()
    api.dialogs.append({"name": "Nano", "host": "pi4.local", "port": 2222,
                        "username": "keigh", "key_file": ""})
    addon.edit_entry(-1)
    saved = api.written.get("quickstart")
    check(saved and saved[0]["host"] == "pi4.local"
          and saved[0]["port"] == 2222 and saved[0]["username"] == "keigh",
          f"the new entry is written to the file: {saved}")

    api.run_clear()
    api.selected = ["Nano"]
    addon.on_widget(addon.TAB, "qs_list", "selected", {"rows": [0]})
    api.dialogs.append({"name": "Nano", "host": "192.168.9.9", "port": 22,
                        "username": "", "key_file": ""})
    addon.edit_entry(0)
    saved = api.written.get("quickstart")
    check(len(saved) == 1 and saved[0]["host"] == "192.168.9.9",
          f"editing replaces in place rather than appending a second: {saved}")
    check(saved[0]["username"] == "root",
          f"a blank username falls back to the offered one: {saved[0]}")

    api.run_clear()
    api.questions = False
    addon.remove_entry(0)
    check("quickstart" not in api.written,
          "a 'no' to the question writes nothing at all")
    api.questions = True
    api.run_clear()
    addon.remove_entry(0)
    check(api.written.get("quickstart") == [],
          f"removing takes it out of the list: {api.written.get('quickstart')}")

    # A session already open keeps running when its bookmark goes.
    fresh(api)
    api.config_values["quickstart"] = [{"name": "Router", "host": "1.1.1.1"}]
    addon.reload_entries()
    api.answers.append("pw")
    identifier = addon.connect_row(0)
    addon.on_widget(addon.TAB, identifier, "ready", {})
    api.run_clear()
    addon.remove_entry(0)
    check(api.pages == ["Router"] and addon.connected_ids() == [identifier],
          f"deleting the entry does not touch the live session: {api.pages}")

    # "New" is the same form with nothing saved.
    fresh(api)
    api.config_values["quickstart"] = []
    addon.reload_entries()
    api.dialogs.append({"name": "", "host": "root@10.1.1.7:2200", "port": 22,
                        "username": "", "key_file": ""})
    api.answers.append("once")
    addon.new_session()
    check("quickstart" not in api.written,
          "a session opened once is not put in the file")
    check(api.connects()[0]["port"] == 2200
          and api.connects()[0]["username"] == "root",
          f"and the address said it all: {api.connects()[0]}")


def test_icons(addon: Any, api: Api) -> None:
    """A picked picture is copied into the addon's own folder and stored by
    name, so the config can travel without the file it came from."""
    print("\n8. a picture for an entry")
    fresh(api)
    api.config_values["quickstart"] = [{"name": "Router", "host": "1.1.1.1"}]
    addon.reload_entries()
    origin = WORK / "given.jpg"
    origin.write_bytes(PICTURE)
    api.run_clear()
    api.picked.append(str(origin))
    addon.choose_icon(0)
    saved = api.written.get("quickstart")
    check(saved[0]["icon"] == "Router.jpg",
          f"the file keeps its own ending and the entry its new name: "
          f"{saved[0]['icon']!r}")
    copied = WORK / "icons" / "Router.jpg"
    check(copied.is_file() and copied.read_bytes() == PICTURE,
          "the bytes are inside the addon's data folder, not merely a path")
    drawn = api.last_set("qs_list", "items")
    check(str(drawn[0]["icon"]).endswith("Router.jpg"),
          f"and the row is drawn from there: {drawn[0]['icon']!r}")

    api.run_clear()
    api.picked.append("")                     # the box was cancelled
    api.questions = False                     # and "take it off?" was not asked
    addon.choose_icon(0)
    check("quickstart" not in api.written,
          "nothing is written when the box was closed")

    api.run_clear()
    api.questions = True                      # "take it off instead?"
    addon.choose_icon(0)
    check(api.written.get("quickstart")[0]["icon"] == "",
          "saying yes to that one takes the picture off")
    drawn = api.last_set("qs_list", "items")
    check(drawn and drawn[0]["icon"] == "",
          f"and the row goes back to wearing nothing: {drawn[0]['icon']!r}")

    api.run_clear()
    api.answers.append("pw")
    identifier = addon.connect_row(0)
    check(identifier and api.pages == ["Router"],
          "an entry with no picture still opens a session")


def fresh(api: Api) -> None:
    """Start a section clean: no state on the addon's side, nothing recorded."""
    addon_module.api = api
    addon_module.entries = []
    addon_module.sessions = {}
    addon_module.current = -1
    addon_module._seq = 0
    api.pages = [addon_module.EMPTY_CAPTION]
    api.written = {}
    api.answers = []
    api.dialogs = []
    api.picked = []
    api.selected = []
    api.questions = True
    api.config_values = {}
    api.run_clear()


def main() -> int:
    global addon_module
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint, so
        # anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    os.environ["RASCONF_ADDON_DATA"] = str(WORK)
    addon = addon_client.load_addon(SOURCE)
    addon_module = addon
    api = Api(addon_id="multi_ssh")
    print(f"multi_ssh: testing addon at {SOURCE.name}")

    test_manifest(addon, api)
    addon.init(api)
    check(api.called("config.declare"), "its config form is declared")
    check(api.called("tabs.add_tab") and api.called("tabs.set_icon"),
          "the tab is added, and wears its own mark")
    check(isinstance(api.declared.get("defaults"), dict)
          and "close_on_disconnect" in api.declared["defaults"],
          f"the defaults go with the fields: {list(api.declared)}")

    test_helpers(addon, api)
    test_entries(addon, api)
    test_open_and_close(addon, api)
    test_buttons(addon, api)
    test_broadcast(addon, api)
    test_editing(addon, api)
    test_icons(addon, api)

    shutil.rmtree(WORK, ignore_errors=True)
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall multi_ssh checks passed")
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
