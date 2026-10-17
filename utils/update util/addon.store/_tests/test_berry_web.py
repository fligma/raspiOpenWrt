#!/usr/bin/env python3
"""A test for the berry_web addon (in addon.store, or addons if re-shipped).

This addon writes into a router's web server and edits the file that decides
what that server serves, so the two things worth checking without a device in
front of them are the text that reaches the device and the shape the tab is
built into. A wrong ``uci`` line does not crash - it leaves a router serving
nothing, or serving the wrong folder, which a person finds out by finding no
page. So this looks at:

* the device's own answer, read back: what a folder, a ``uci get`` and a page
  fetch become, and where the tab opens once it has been read;
* the commands: the ``uci`` script that creates or repairs a section, the check
  that asks the host everything at once, the move that refuses when the new name
  is taken, and that no word a person typed can break out of a quote;
* the settings that came from the retired installer: the served folder a
  subfolder used to mean, carried across once and not again;
* the colours: the stylesheet generated from a theme, and what a theme that names
  nothing usable leaves behind;
* the tab as Qt builds it: three pages, the uhttpd page holding two of its own, a
  two-column summary, one control per config key, and a report for every button;
* the two routes the files come by: this checkout read as the dashboard, GitHub's
  list of the repository cut down to what is installed, and the built-in list that
  stands in when GitHub refuses - the one half that asks the real repository only
  runs when this machine names a token for it, and says so when it does not.

No device is needed, and no internet unless a token is named. Run it from the Test
panel, or::

    python addon.store/_tests/test_berry_web.py

The Berry addon store also runs this file before it compiles the addon, setting
``RASCONF_ADDON_SOURCE`` to the freshly downloaded ``.py`` so the copy under test
is the one that is about to be built.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the copy a store install handed over, else the one in the
#: tree - the store folder first, the addons folder if it was ever re-shipped.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "berry_web.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = ROOT / "addons" / "berry_web.py"
FAILURES: list[str] = []

#: The address the fake connection panel reports, and the folder a profile of
#: this checkout would have had before the served folder got its own name.
HOST = "192.168.50.1"
OLD_ROOT = "/www_rasconf"

#: What the retired installer kept, in its own file, for a working install: no
#: served folder of its own, only the profile's root and a subfolder inside it.
LEGACY_SAVED = {
    "source": "GitHub",
    "owner": "fligma",
    "repo": "raspiOpenWrt",
    "ref": "main",
    "repo_path": "src/www_rasconf",
    "subdir": "dash",
    "skip": ["README.md"],
    "keep": ["cgi-bin/hash.file"],
    "web_port": 8989,
    "web_path": "/cgi-bin/index.py",
}

#: A device that has the dashboard and serves it: every line the check script
#: prints, in the order it prints them, with six lines of the page underneath.
LIVE = {"code": 0, "err": "", "out": (
    'FOLDER yes\n'
    'FILES 22\n'
    'HOME /www_berry\n'
    'PY /usr/bin/python3\n'
    f'ANSWERED http://{HOST}:8989/cgi-bin/index.py\n'
    'B <!DOCTYPE html>\n'
    'B <html>\n'
    'B <head>\n'
    'B     <title>Berry - Dashboard</title>\n'
)}

#: The same question of a router that has never had this installed: no folder,
#: no section of its own, and nothing listening on the port.
BLANK = {"code": 0, "err": "", "out": (
    "FOLDER no\n"
    "FILES 0\n"
    "HOME \n"
    "PY /usr/bin/python3\n"
    "NOANSWER\n"
)}

#: A folder left by an older install, with a page that answers as something else.
OTHER = {"code": 0, "err": "", "out": (
    "FOLDER yes\n"
    "FILES 3\n"
    f"HOME {OLD_ROOT}\n"
    "PY -\n"
    f"ANSWERED http://{HOST}:8989/\n"
    "B <html><head><title>LuCI</title></head>\n"
)}

#: The router as it stands before a move from an older name: the folder the form
#: names is not there, this addon's section has never been written, the page's
#: files sit under a folder of an older install, and a section of the router's
#: own serves it. Exactly the shape a rename has to be able to read.
ELDER = {"code": 0, "err": "", "out": (
    "FOLDER no\n"
    "FILES 0\n"
    "HOME \n"
    "SH uhttpd.lan.home='/www'\n"
    f"SH uhttpd.rasconf.home='{OLD_ROOT}'\n"
    f"SERVED {OLD_ROOT} 22\n"
    "PY /usr/bin/python3\n"
    "ANSWERED http://127.0.0.1:8989/cgi-bin/index.py\n"
    "B <title>Berry - Dashboard</title>\n"
)}

#: A command the app gave up on. Nothing answered, so the number the addon is
#: handed is one the app made up, and the reason is in ``err``.
STALLED = {"code": -1, "out": "", "err": "Timeout opening channel: timed out"}

#: A device with neither curl nor wget on it: the page could not be asked at all.
NOFETCH = {"code": 0, "err": "", "out": (
    "FOLDER yes\n"
    "FILES 22\n"
    "HOME /www_berry\n"
    "PY /usr/bin/python3\n"
    "NOFETCH\n"
    "NOANSWER\n"
)}

#: The same live dashboard on a device whose own uhttpd section listens over
#: https: the plain probe still answers over http first, but what the config
#: says decides the address the page is opened at.
TLS_LIVE = {"code": 0, "err": "", "out": (
    "FOLDER yes\n"
    "FILES 22\n"
    "HOME /www_berry\n"
    "HTTPS on\n"
    "PY /usr/bin/python3\n"
    f"ANSWERED http://{HOST}:8989/cgi-bin/index.py\n"
    "B <!DOCTYPE html>\n"
    "B <title>Berry - Dashboard</title>\n"
)}


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- the api object the addon is handed ---

class Api(addon_client.StubAPI):
    """The recording stub, with a device that answers and a form that holds."""

    def __init__(self, *args: Any, answer: Any = None, host: str = HOST,
                 remote_root: str = OLD_ROOT, question: bool = True,
                 **named: Any) -> None:
        super().__init__(*args, **named)
        self.device_answer = answer if answer is not None else BLANK
        self.host = host
        self.remote_root = remote_root
        self.question_answer = question
        self.refuse_web = False
        self.form: dict = {}
        self.commands: list[str] = []
        self.asked_titles: list[str] = []

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        if method == "tabs.set_widget" and self.refuse_web:
            # A build without QtWebEngine: the leaf that shows a page is a notice
            # instead, and being given an address for it is refused.
            if (self.argument(method, "identifier", arguments, named, "") == "dash"
                    and self.argument(method, "prop", arguments, named, "") == "url"):
                raise addon_client.AddonError("QtWebEngine is not available")
        if method == "device.run_async":
            command = self.argument(method, "command", arguments, named, "")
            callback = self.argument(method, "on_done", arguments, named)
            self.commands.append(str(command))
            if callable(callback):
                callback(self.device_answer)     # the app answers on the GUI thread
            return self.device_answer
        if method == "tabs.widget_value":
            identifier = self.argument(method, "identifier", arguments, named, "")
            what = self.argument(method, "what", arguments, named, "value")
            if identifier not in self.form:
                return None
            value = self.form[identifier]
            if what == "checked":
                return bool(value)
            if isinstance(value, (list, tuple)):
                return ", ".join(str(item) for item in value)
            return value
        if method == "config.update":
            values = self.argument(method, "values", arguments, named, {}) or {}
            self.config_values = dict(values)
            return dict(values)
        if method == "session.status":
            return {"host": self.host, "port": 22, "username": "root",
                    "remote_root": self.remote_root, "source": "test",
                    "ssh_connected": True, "sftp_connected": True, "busy": False}
        if method == "ui.question":
            self.asked_titles.append(str(self.argument(method, "title", arguments,
                                                       named, "")))
            return self.question_answer
        if method == "session.web_url":
            page = self.argument(method, "tab", arguments, named, {}) or {}
            scheme = str(page.get("scheme") or "http")
            return f"{scheme}://{self.host}:8989/cgi-bin/index.py"
        return super().default(method, arguments, named)

    def sets(self, identifier: str, prop: str | None = None) -> list:
        """What the addon sent to one control on its tab, in order."""
        out = []
        for where, arguments, _named in self.calls:
            if where == "tabs.set_widget" and arguments[1:2] == (identifier,):
                if prop is None or arguments[2:3] == (prop,):
                    out.append(arguments[3] if len(arguments) > 3 else None)
        return out

    def last_set(self, identifier: str, prop: str) -> Any:
        values = self.sets(identifier, prop)
        return values[-1] if values else None

    def command_holding(self, needle: str) -> list[str]:
        return [command for command in self.commands if needle in command]

    def log_text(self) -> str:
        return "\n".join(self.logs)


def as_form(values: dict) -> dict:
    """``values`` as the tab would hold it, keyed by config key."""
    import addon_common

    out: dict = {}
    for key, field in berry_web.FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        value = addon_common.config_get(values, key,
                                        addon_common.config_get(berry_web.DEFAULTS, key))
        if kind in ("list", "multiline"):
            value = ", ".join(str(item) for item in (value or []))
        out[key] = value
    return out


def fresh(values: dict | None = None, answer: Any = None, *,
          saved: dict | None = None, host: str = HOST,
          remote_root: str = OLD_ROOT, question: bool = True) -> Api:
    """The addon on a stub api, its form filled in as ``values`` says."""
    api = Api(addon_id="berry_web", config=dict(saved or {}), answer=answer,
              host=host, remote_root=remote_root, question=question)
    api.form = as_form(dict(berry_web.DEFAULTS, **(values or {})))
    berry_web.init(api)
    return api


# --- one: the device's answer, read back ---

def test_the_check() -> None:
    print("\n1. what the host's answer becomes")
    live = berry_web.parse_check(LIVE)
    check(live["folder"] and live["files"] == 22,
          f"a folder with files in it did not read as one: {live}")
    check(live["home"] == "/www_berry",
          f"the served folder was not read off uci: {live['home']!r}")
    check(live["python"] == "/usr/bin/python3", "python3 went unnoticed")
    check(live["answered"] and live["is_berry"],
          f"a live dashboard was not recognised: {live}")
    check("Berry - Dashboard" in live["body"], "the page's own lines were dropped")

    blank = berry_web.parse_check(BLANK)
    check(not blank["folder"] and not blank["answered"] and not blank["is_berry"],
          f"a bare router read as an install: {blank}")
    check(blank["python"], "python3 was lost from the answer")

    other = berry_web.parse_check(OTHER)
    check(other["answered"] and not other["is_berry"],
          "a page that is not this dashboard was taken for one")
    check(other["home"] == OLD_ROOT, "the old served folder was not read")
    check(not other["python"], "'PY -' should say there is no python3")

    check(not berry_web.parse_check({})["answered"], "nothing at all answered")
    check(not berry_web.parse_check(None)["folder"], "no answer made a folder")
    check(berry_web.page_title(live["body"]) == "Berry - Dashboard",
          f"the page's own name was not read off it: {berry_web.page_title(live['body'])!r}")
    check(not berry_web.page_title("<p>no title here</p>"),
          "a page with no title was given one")

    rows = {row["cells"][0]: row["cells"][1] for row in berry_web.summary_rows(live)}
    check(len(berry_web.summary_rows(live)) == 5,
          f"a live install should fill five lines, it fills "
          f"{len(berry_web.summary_rows(live))}")
    check(rows.get("Folder served") == "/www_berry",
          f"the table does not name the served folder: {rows}")
    check("22 file(s)" in rows.get("Folder is there", ""),
          "how much is in the folder was left out")
    check("8989" in rows.get("Page", ""), "the address that answered is not shown")
    bare = {row["cells"][0]: row["cells"][1]
            for row in berry_web.summary_rows(berry_web.parse_check(BLANK))}
    check(len(bare) == 4 and bare["Page"] == "no answer",
          f"a router with nothing on it is explained wrong: {bare}")
    check(bare["python3"] == "/usr/bin/python3", "python3 is missing from the table")

    elder = berry_web.parse_check(ELDER)
    check(elder["answered"] and elder["is_berry"],
          f"a dashboard served from an older folder was not recognised: {elder}")
    check(not elder["folder"], "the folder the form names was taken for the one there")
    check(elder["served"] == [{"folder": OLD_ROOT, "files": 22}],
          f"the folders holding the page were not read: {elder['served']}")
    check(elder["sections"].get("rasconf") == OLD_ROOT
          and elder["sections"].get("lan") == "/www",
          f"which section serves what was not read: {elder['sections']}")
    check(berry_web.elsewhere(elder, "/www_berry") == OLD_ROOT,
          "the answer to \"mine is in /www_rasconf\" was not found")
    check(not berry_web.elsewhere(elder, OLD_ROOT),
          "the named folder was reported as somewhere else")
    rows = {row["cells"][0]: row["cells"][1]
            for row in berry_web.summary_rows(elder)}
    check(OLD_ROOT in rows.get("Folders holding the page", ""),
          f"the table does not say where the page's files are: {rows}")
    check("rasconf" in rows.get("uhttpd serves", "")
          and "/www" in rows.get("uhttpd serves", ""),
          f"the table hides which section serves which folder: {rows}")

    silent = berry_web.parse_check(NOFETCH)
    check(silent["no_fetch"] and not silent["answered"],
          f"a device with no fetch tool read as a page that refused: {silent}")
    rows = {row["cells"][0]: row["cells"][1]
            for row in berry_web.summary_rows(silent)}
    check("curl" in rows.get("Asking the page", ""),
          f"the missing tool is not said: {rows}")

    secured = berry_web.parse_check(TLS_LIVE)
    check(secured["https"] and secured["is_berry"],
          f"a section that listens over https was not noticed: {secured}")
    check(not berry_web.parse_check(LIVE)["https"],
          "https was claimed of a device that never said so")
    rows = {row["cells"][0]: row["cells"][1]
            for row in berry_web.summary_rows(secured)}
    check("https" in rows, f"the table does not say the server speaks https: {rows}")


# --- two: where the tab opens once it has been told ---

def test_the_landing() -> None:
    print("\n2. the page the tab opens on")
    for name, answer, page in (
            ("a live dashboard", LIVE, berry_web.STATUS_PAGE),
            ("nothing installed", BLANK, berry_web.INSTALL_PAGE),
            ("something else serving", OTHER, berry_web.INSTALL_PAGE)):
        api = fresh(answer=answer)
        berry_web.check_device()
        landed = api.last_set("views", "current")
        check(landed == page,
              f"{name} landed on page {landed} rather than {page}")
        check(api.command_holding("uci -q get uhttpd."),
              "the served folder was never asked of uci")
        check("command -v python3" in (api.commands[-1] if api.commands else ""),
              "the check does not ask whether python3 is there")
        check("NOANSWER" in api.commands[-1] and "curl" in api.commands[-1],
              "the check never asks the host for its own page")
        berry_web.shutdown()

    # A live dashboard is looked at inside this tab: the page leaf is given the
    # address and the forms step back. No browser tab is opened by itself - that
    # is a button on the side bar, and a landing that pressed it for nobody would
    # be the addon putting a window over the person's work.
    api = fresh(answer=LIVE)
    berry_web.check_device()
    opened = str(api.last_set("dash", "url") or "")
    check("8989" in opened and opened.startswith("http"),
          f"the page was not handed an address to show: {opened!r}")
    check(api.last_set("dash", "visible") is True,
          "the tab did not put the page over its forms")
    check(api.last_set("views", "visible") is False,
          "the forms were left showing under the page")
    check(not api.called("tabs.add_web_tab"),
          "a landing opened a browser tab that nobody asked for")
    berry_web.shutdown()

    # The same landing with the switch on the uhttpd page off: the forms stay.
    api = fresh(answer=LIVE, values={"auto_show": False})
    berry_web.check_device()
    check(not api.last_set("dash", "url"),
          "the page was fetched with the switch that says not to show it off")
    check(api.last_set("dash", "visible") is not True
          and api.last_set("views", "visible") is not False,
          "the forms were covered anyway with the switch off")
    check(api.last_set("views", "current") == berry_web.STATUS_PAGE,
          "with the page off the tab did not at least stand on the status page")
    berry_web.shutdown()

    # The side bar's other two ways of seeing the page: a browser tab of its own,
    # with the corner menu that leads back travel with it, and the system browser.
    api = fresh(answer=LIVE)
    berry_web.open_in_tab()
    check(api.called("tabs.add_web_tab"),
          "the side bar's browser button opened nothing")
    menu = api.asked("tabs.add_web_tab", "menu") or []
    check(bool(menu) and all(str(one.get("id")) and str(one.get("label"))
                             for one in menu),
          f"the corner menu did not travel with the page: {menu!r}")
    berry_web.shutdown()

    # A re-check pressed on the status page reports; it does not yank a page
    # in front of the buttons being read.
    api = fresh(answer=LIVE)
    berry_web.check_device(landing=False)
    check(not api.last_set("dash", "url"),
          "a manual re-check covered the status buttons with the page")
    check(api.last_set("views", "current") == berry_web.STATUS_PAGE,
          "the re-check did not even leave the notebook on the status page")
    berry_web.shutdown()

    print("\n2b. what the status line says about each")
    api = fresh(answer=LIVE)
    berry_web.check_device()
    check("answers on" in str(api.last_set("status", "text")),
          f"a live dashboard is not greeted as one: {api.last_set('status', 'text')!r}")
    check("Berry - Dashboard" in str(api.last_set("last_check", "text")),
          "the line under the table does not quote the page")
    berry_web.shutdown()

    api = fresh(answer=BLANK)
    berry_web.check_device()
    said = str(api.last_set("status", "text"))
    check("not on the device" in said, f"a bare router should be offered an install: "
                                       f"{said!r}")
    berry_web.shutdown()

    api = fresh(answer=OTHER)
    berry_web.check_device()
    check("not this dashboard" in str(api.last_set("status", "text")),
          "a different page was not called a different page")
    berry_web.shutdown()


# --- three: the words that reach the device ---

def test_the_scripts() -> None:
    print("\n3. the uci script")
    script = berry_web.uci_script("berry", "/www_berry", 8989)
    check("uci -q get uhttpd.berry >/dev/null || uci set uhttpd.berry=uhttpd" in script,
          "an existing section would be replaced rather than repaired")
    check("uci set uhttpd.berry.home='/www_berry'" in script,
          f"the served folder is not set the way uci takes it:\n{script}")
    check("uci -q add_list uhttpd.berry.listen_http='0.0.0.0:8989'" in script,
          "the listen address is not added quietly")
    check("uci set uhttpd.berry.cgi_prefix='/cgi-bin'" in script,
          "a CGI prefix is assumed rather than written")
    check("uci commit uhttpd" in script, "nothing was committed")
    check(script.rstrip().endswith("echo done"), "the script does not say it finished")

    print("\n3b. what a typed word cannot do")
    evil = berry_web.uci_script("ber'; rm -rf / #", "/www; rm -rf /", "80; reboot")
    check("rm -rf" not in evil and "reboot" not in evil,
          f"a command reached the script:\n{evil}")
    check("uhttpd.berry" in evil or "uci set uhttpd." in evil,
          f"a name that is not a bare word was not replaced: {evil}")
    check("'" in evil, "uci values are not quoted at all")
    look(evil.replace("\n", "\n       "))

    for folder, want in (("/www_berry/", "/www_berry"), ("www_berry", "/www_berry"),
                         ("/etc/config", "/etc/config"), ("", ""),
                         ("/www/../etc", ""), ('/a"b', "")):
        got = berry_web.home_path(folder, "")
        check(got == want, f"home_path({folder!r}) is {got!r}, expected {want!r}")
    for danger in ('/a"b; rm -rf /', "/a$(reboot)", "/a b", "/a`x`", "/a|b"):
        got = berry_web.home_path(danger, "")
        check(got == "", f"{danger!r} was taken for a folder: {got!r}")
    check(berry_web.home_path('/a"b', "/www_berry") == "/www_berry",
          "a rejected folder did not fall back")
    check(berry_web.home_path("/www/../etc", "/www_berry") == "/www_berry",
          "a folder climbing out of the tree did not fall back")
    check(berry_web.uci_name("ok-name_1") == "ok-name_1"
          and berry_web.uci_name("two words", "berry") == "berry"
          and berry_web.uci_name("", "x") == "x",
          "a section name is not read the way uci would take it")
    check(berry_web.target_path("/www_berry", "cgi-bin/index.py")
          == "/www_berry/cgi-bin/index.py", "a file could not be placed in the folder")
    for escape in ("../etc/passwd", "/etc/passwd", "..", ""):
        check(not berry_web.target_path("/www_berry", escape).startswith("/etc"),
              f"{escape!r} escaped the served folder")

    print("\n3c. the move, and the restart")
    paths = berry_web.job_paths(dict(berry_web.DEFAULTS))
    check(paths["home"] == "/www_berry" and paths["section"] == "berry"
          and paths["port"] == 8989, f"the defaults are not what they say: {paths}")
    check(paths["restart"] == ["/etc/init.d/uhttpd restart"],
          "the restart command was lost on the way to the job")
    lines = berry_web.apply_uci_lines("berry", ["/etc/init.d/uhttpd restart"])
    check(any("uci -q show uhttpd.berry" in line for line in lines),
          "the section is not read back after a write")
    check(any("/etc/init.d/uhttpd restart" in line for line in lines),
          "the restart command was not carried into the script")
    check(not berry_web.apply_uci_lines("berry", [])[-1].startswith("/"),
          "an empty restart list invented a command")

    api = fresh({"web_home": "/www_berry"}, saved=dict(LEGACY_SAVED,
                                                       web_home="/www_berry"),
                 question=False)
    # The device serves the old folder, so the move is on offer.
    berry_web.last_check.update(berry_web.parse_check(OTHER))
    berry_web.move_home()
    check(not api.command_holding("mv "), "a refused move still ran a command")
    check(api.asked_titles and "Move" in api.asked_titles[0],
          f"the move was not asked about by name: {api.asked_titles}")
    check("left where it is" in str(api.last_set("status", "text")),
          "a refused move was not said back")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry"}, saved=dict(LEGACY_SAVED,
                                                       web_home="/www_berry"))
    berry_web.last_check.update(berry_web.parse_check(OTHER))
    berry_web.move_home()
    moved = api.command_holding("mv ")
    check(bool(moved), "an accepted move sent nothing")
    check(moved and 'if [ -e "$NEW" ]' in moved[0] and "STOP" in moved[0],
          "the script does not refuse a name that is already taken")
    check(moved and "uci commit uhttpd" in moved[0], "the new folder was never committed")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry"}, saved=dict(LEGACY_SAVED,
                                                       web_home="/www_berry"))
    berry_web.last_check.update(berry_web.parse_check(LIVE))
    berry_web.move_home()
    check(not api.commands or "mv " not in api.commands[-1],
          "a folder that already serves what the form says was moved anyway")
    check("already" in str(api.last_set("status", "text")),
          f"the addon is silent about a move with nothing to do: "
          f"{api.last_set('status', 'text')!r}")
    berry_web.shutdown()


# --- three and a half: a check that can answer, and a move that knows where ---

def test_the_probe() -> None:
    """The two things 1.2 is: a check that cannot stall, and a rename that starts
    from what the device said rather than from a setting it may never have had."""
    print("\n3d. the check's own seconds")
    paths = berry_web.job_paths(dict(berry_web.DEFAULTS))
    script = berry_web.check_script(paths)
    lines = script.splitlines()
    check(any(line.startswith("WEBHOME=") for line in lines),
          "the served folder is not named at the top of the script")
    check(not any(line.startswith("HOME=") for line in lines),
          "the script still re-assigns HOME, which curl reads a config off")
    check("--connect-timeout" in script and "--max-time" in script,
          "curl is asked for the page with no bound on it")
    check(f"-T {berry_web.PROBE_ASK}" in script and "-t 1" in script,
          "the wget fallback is still asked without a timeout - the one that "
          "hung the check until the app gave up")
    check("head -c" in script, "a page that streams forever would be read forever")
    check("NOFETCH" in script,
          "a device with neither curl nor wget is not said to be one")
    check('SERVED $D' in script and "grep home=" in script,
          "the device is never asked which of its folders holds the page")
    check("listen_https" in script and "redirect_https" in script,
          "the device is not asked whether its section speaks https")
    check('echo "HTTPS $' in script,
          "the https answer is not reported on its own line")
    check(script.count("for URL in") == 1, "the page is asked for twice over")

    api = fresh(answer=LIVE)
    urls = berry_web.page_urls(berry_web.job_paths(dict(berry_web.DEFAULTS)))
    check(urls[0] == f"http://127.0.0.1:8989{paths['web_path']}",
          f"the device is not asked to answer itself first: {urls}")
    check(len(urls) <= 4, f"the device is asked {len(urls)} times over")
    budget = len(urls) * berry_web.PROBE_ASK
    check(budget < 45,
          f"the probe's worst case is {budget}s, more than the 45s it is given")
    berry_web.shutdown()

    print("\n3e. what a command that never came back is said as")
    said = berry_web._why_failed(STALLED, "asking the host what it has", 45)
    check("-1" not in said,
          f"the app's made-up number is shown as the device's answer: {said!r}")
    check("never came back" in said and "45" in said and "Timeout" in said,
          f"a stall is not said as one: {said!r}")
    check("answered 2" in berry_web._why_failed({"code": 2, "err": ""}, "a step", 30),
          "a real exit code is hidden")
    check("No such file" in berry_web._why_failed(
        {"code": 1, "err": "No such file or directory"}, "a step", 30),
        "a device's own reason is thrown away")

    api = fresh(answer=STALLED)
    berry_web.check_device()
    said = str(api.last_set("status", "text"))
    check("answered -1" not in said,
          f"the tab blames the device for the app's number: {said!r}")
    check("never came back" in said, f"the tab does not say it waited: {said!r}")
    check("Timeout" in said, f"the tab hides why: {said!r}")
    berry_web.shutdown()

    print("\n3f. a move that starts where the device really is")
    api = fresh({"web_home": "/www_berry"})
    berry_web.last_check.clear()
    berry_web.move_home()
    check(not api.commands, "a move ran on a device that had never been asked")
    check("Re-check" in str(api.last_set("status", "text")),
          f"nothing told anyone to look first: {api.last_set('status', 'text')!r}")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry"})
    berry_web.last_check.clear()
    berry_web.last_check.update(berry_web.parse_check(BLANK))
    berry_web.move_home()
    check(not api.commands, "a router with nothing on it was offered a move")
    check("no folder to move from" in str(api.last_set("status", "text")),
          f"the empty device was not explained: {api.last_set('status', 'text')!r}")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry"})
    berry_web.last_check.clear()
    berry_web.last_check.update({"served": [{"folder": "/www", "files": 40}],
                                 "sections": {"lan": "/www"}, "home": ""})
    berry_web.move_home()
    check(not api.commands, "the router's own /www was moved out from under it")
    check("own folder" in str(api.last_set("status", "text")),
          f"refusing was not said: {api.last_set('status', 'text')!r}")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry"})
    berry_web.last_check.clear()
    berry_web.last_check.update(berry_web.parse_check(ELDER))
    berry_web.move_home()
    moved = api.command_holding("mv ")
    check(bool(moved), "the folder the device named was not moved")
    script = moved[0] if moved else ""
    check(f'OLD="{OLD_ROOT}"' in script and 'NEW="/www_berry"' in script,
          f"the move renames the wrong folders: {script.splitlines()[:4]}")
    check("uci set uhttpd.rasconf.home='/www_berry'" in script,
          "the section that served the old folder was left naming a gone path")
    check("uci set uhttpd.berry.home='/www_berry'" in script,
          "this addon's own section was not written")
    check("uci commit uhttpd" in script and "/etc/init.d/uhttpd restart" in script,
          "the change was neither committed nor the server brought back up")
    check('if [ -e "$NEW" ]' in script and "STOP" in script,
          "the script does not refuse a name that is already taken")
    pushed = api.last("config.update")
    written = dict((pushed[1][0] if pushed and pushed[1] else pushed[2]) or {})
    check(str(written.get("web_home") or "") == "/www_berry",
          f"the new name was not kept in the settings: {written}")
    check(str(api.last_set("web_home", "value")) == "/www_berry",
          "the form still shows the folder that no longer exists")
    berry_web.shutdown()

    api = fresh({"web_home": "/www_berry", "subdir": "dash"})
    berry_web.last_check.clear()
    berry_web.last_check.update(berry_web.parse_check(ELDER))
    berry_web.move_home()
    moved = api.command_holding("mv ")
    script = moved[0] if moved else ""
    check(f'OLD="{OLD_ROOT}"' in script and 'NEW="/www_berry"' in script,
          f"a subfolder made the rename work on the path inside it: "
          f"{script.splitlines()[:4]}")
    check("uci set uhttpd.berry.home='/www_berry/dash'" in script,
          f"the setting lost the subfolder it serves: {script!r}")
    berry_web.shutdown()


# --- four: the settings that came from the retired installer ---

def test_the_bridge() -> None:
    print("\n4. the old installer's settings, read as this addon's")
    carried = berry_web.bridge_settings(dict(LEGACY_SAVED), "/www_rasconf")
    check(carried.get("web_home") == "/www_rasconf/dash",
          f"a subfolder did not become the served folder: {carried.get('web_home')!r}")
    check(carried.get("subdir") == "", "the subfolder was left to be added twice")
    check(carried.get("keep") == ["cgi-bin/hash.file"],
          "the file holding the PIN was not carried across untouched")

    plain = berry_web.bridge_settings({"source": "GitHub"}, "/www_rasconf")
    check(plain.get("web_home") == "/www_rasconf",
          f"with no subfolder the profile's own root should serve: {plain}")
    check("subdir" not in plain, "a key nobody set was written anyway")

    kept = {"web_home": "/srv/own", "subdir": "dash"}
    check(berry_web.bridge_settings(kept, "/www_rasconf") == kept,
          "a folder chosen since the upgrade was moved under the bridge")
    check(berry_web.bridge_settings({}, "/www_rasconf") == {},
          "a machine with no settings file of its own was written one")

    print("\n4b. the bridge running once, at startup")
    api = Api(addon_id="berry_web", config=dict(LEGACY_SAVED), answer=BLANK,
              remote_root="/www_rasconf")
    api.form = as_form(dict(berry_web.DEFAULTS))
    berry_web.init(api)
    pushed = api.last("config.update")
    check(pushed is not None, "the carried settings were never written back")
    written = dict((pushed[1][0] if pushed and pushed[1] else pushed[2]) or {})
    check(str(written.get("web_home") or "") == "/www_rasconf/dash",
          f"the file was written with {written.get('web_home')!r} as its folder")
    check("carried" in api.log_text(),
          f"the carry-over was not said out loud: {api.log_text()}")
    look(api.log_text().replace("\n", "\n       "))
    stored = api.config.stored()
    check(str(stored.get("subdir") or "") == "",
          "the old subfolder key still holds a value")
    berry_web.shutdown()

    print("\n4c. a fresh install is left alone")
    api = Api(addon_id="berry_web", answer=BLANK)
    api.form = as_form(dict(berry_web.DEFAULTS))
    berry_web.init(api)
    check(not api.last("config.update"),
          "a config that never existed was rewritten at startup")
    check("carried" not in api.log_text(), "a fresh install was told about an upgrade")
    declared = api.declared.get("defaults") or {}
    check(declared.get("web_home") == "/www_berry",
          "the declared default is not the folder this dashboard is set up for")
    berry_web.shutdown()


# --- five: the colours ---

def test_the_theme() -> None:
    print("\n5. the stylesheet the theme makes")
    colors = {token: "#102030" for token, _var in berry_web.THEME_VARS}
    css = berry_web.theme_css(colors)
    check(css.startswith("/*"), "the file does not say who wrote it")
    check(":root {" in css and css.rstrip().endswith("}"), f"no :root block: {css!r}")
    for _token, variable in berry_web.THEME_VARS:
        check(f"{variable}: #102030;" in css, f"{variable} is not in the file")
    check(css.count("--color-") == len(berry_web.THEME_VARS),
          "the file names a variable twice, or one more than the theme has")

    part = berry_web.theme_css({"base": "#FFFFFF", "accent": "not a colour",
                                "text": "#0a0a0a"})
    check("--color-background: #ffffff;" in part, "a usable colour was refused")
    check("--color-accent" not in part, "a word that is not a colour was written")
    check(not berry_web.theme_css({}), "a theme naming nothing produced a file anyway")
    check(not berry_web.theme_css(None), "no theme at all produced a file")
    check(not berry_web.theme_css({"base": "#12345"}), "a malformed hex was written")

    # The variables have to be the ones the page declares, or nothing changes.
    sheet = (ROOT.parent.parent / "src" / "www_rasconf" / "css" / "main.css")
    if sheet.is_file():
        declared = sheet.read_text(encoding="utf-8", errors="replace")
        missing = [variable for _token, variable in berry_web.THEME_VARS
                   if f"{variable}:" not in declared]
        check(not missing,
              f"the page declares none of {missing}; the sync would paint nothing")
        committed = sheet.parent / "theme.css"
        check(committed.is_file(),
              "there is no committed theme.css, so a hand-copied tree has no colours")
        if committed.is_file():
            # The committed copy is the shipped theme written out, which is what
            # lets a folder copied across by hand look like the program rather
            # than like nothing at all. An install overwrites it with the theme
            # actually being worn, so this file only has to agree with the app.
            import core.const

            wanted = berry_web.theme_css(
                {token: str(core.const.THEME_COLORS.get(token) or "")
                 for token, _variable in berry_web.THEME_VARS}).strip()
            wrote = committed.read_text(encoding="utf-8",
                                        errors="replace").strip()
            check(wrote == wanted,
                  "the committed theme.css is not the shipped theme written out")
        check('href="/css/theme.css"' in
              (sheet.parent.parent / "cgi-bin" / "pages.py").read_text(
                  encoding="utf-8", errors="replace"),
              "the pages do not load the generated stylesheet")
    else:
        look(f"no dashboard stylesheet at {sheet} to compare with")


# --- six: the tab ---

def test_the_tab(app: Any) -> None:
    print("\n6. the tab, built by Qt from the spec")
    import addon_api

    values = berry_web.merge_config(berry_web.DEFAULTS, LEGACY_SAVED)
    spec = berry_web.tab_spec(values)
    widget, registry = addon_api.build_spec(spec)
    check(widget is not None, "the spec built no widget")

    views = registry["views"]
    check(views.count() == 3, f"the tab has {views.count()} pages, not three")
    labels = [views.tabText(index) for index in range(views.count())]
    check(labels == ["Status", "Installer", "uhttpd"],
          f"the pages are named {labels}")

    nested = registry["uci_views"]
    check(nested.count() == 2, f"the uhttpd page holds {nested.count()} pages")
    inner = [nested.tabText(index) for index in range(nested.count())]
    check(inner == ["Options", "Raw file"], f"the uhttpd pages are named {inner}")
    check(registry["install_views"].count() == 3,
          "the installer does not hold its plan, log and form")

    check(registry["summary"].columnCount() == 2,
          "the status summary is not a two-column table")
    check(registry["plan"].columnCount() == 4,
          "the plan table lost a column")
    check(registry["raw_uci"].isReadOnly(), "the raw file can be typed into")
    check(not registry["raw_edit"].isReadOnly(),
          "there is no editable copy of the file to read in")
    check(registry["log"].isReadOnly(), "the job's log can be typed into")

    missing = [key for key in berry_web.FIELDS_BY_KEY if key not in registry]
    check(not missing, f"the form is missing a control for {missing}")
    shared = {key for _title, keys in berry_web.GROUPS_INSTALL for key in keys} & \
        {key for _title, keys in berry_web.GROUPS_UCI for key in keys}
    check(not shared, f"one key is on two forms, so they could disagree: {shared}")
    for group in (berry_web.GROUPS_INSTALL, berry_web.GROUPS_UCI):
        for _title, keys in group:
            for key in keys:
                check(key in registry, f"{key} is in a group but not on the tab")
    covered = {key for _title, keys in berry_web.GROUPS_INSTALL
               for key in keys} | {key for _title, keys in berry_web.GROUPS_UCI
                                   for key in keys}
    check(covered == set(berry_web.FIELDS_BY_KEY),
          f"shown in a form: {sorted(set(berry_web.FIELDS_BY_KEY) - covered)} "
          f"missing, {sorted(covered - set(berry_web.FIELDS_BY_KEY))} invented")

    for identifier, text in berry_web.BUTTONS:
        check(identifier in registry, f"there is no {text!r} button on the tab")
    check("stop" in registry, "a job with twelve stages has no way of being stopped")
    for identifier in ("step_files", "step_serve", "step_check"):
        check(identifier in registry, f"the step flow is missing {identifier}")
    for identifier in ("target", "state", "status", "bar", "last_check",
                       "sum_write", "sum_same", "sum_extra"):
        check(identifier in registry, f"the header is missing {identifier}")

    check(registry["web_home"].text() == "/www_berry",
          "the folder box does not start at the folder this dashboard is for")
    check(registry["theme_sync"].isChecked(), "the colour sync does not start switched on")
    check(registry["remove_extra"].isChecked() is False,
          "tidying files away starts switched on")
    check(registry["web_port"].value() == 8989, "the port box does not start at 8989")
    check("hash.file" in registry["keep"].text(),
          "the PIN file is not in the list that is never deleted")


def test_the_reports() -> None:
    print("\n6b. every button has a report, and every report a handler")
    spec = berry_web.tab_spec(berry_web.DEFAULTS)
    ids: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if str(node.get("kind") or "") == "button":
                ids.append(str(node.get("id") or ""))
                check(node.get("on") == ["clicked"],
                      f"a button reports {node.get('on')!r} rather than clicked")
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(spec)
    check(len(ids) == len(set(ids)), f"a button id is used twice: {ids}")
    check(set(ids) <= set(berry_web.HANDLERS),
          f"with nothing to run: {sorted(set(ids) - set(berry_web.HANDLERS))}")
    check({name for name, _text in berry_web.BUTTONS} <= set(ids),
          "a button named for greying out is not on the tab")
    for identifier, handler in sorted(berry_web.HANDLERS.items()):
        check(callable(handler), f"{identifier} maps to {handler!r}, which cannot run")
    for stage in berry_web.STAGES:
        check(callable(berry_web.STAGES[stage]), f"stage {stage} is not callable")
    look(f"{len(ids)} buttons, {len(berry_web.STAGES)} stages, "
         f"{len(berry_web.HANDLERS)} handlers")


def test_the_routes() -> None:
    print("\n6c. what a button sends")
    api = fresh(answer=LIVE)
    before = len(api.commands)
    berry_web.apply_uci()
    sent = api.commands[before] if len(api.commands) > before else ""
    check("uci set uhttpd.berry.home=" in sent and "uci commit uhttpd" in sent,
          f"Apply did not send a uci script: {sent!r}")
    check("/etc/init.d/uhttpd restart" in sent, "Apply left the server stopped")
    check("uhttpd.berry now serves" in str(api.last_set("status", "text")),
          "Apply did not say what it had set")
    berry_web.shutdown()

    api = fresh(answer=BLANK)
    berry_web.restart_server()
    check(api.commands and api.commands[-1].strip() == "/etc/init.d/uhttpd restart "
          '|| echo "note: /etc/init.d/uhttpd restart did not succeed"',
          f"Restart sent something odd: {api.commands[-1] if api.commands else None!r}")
    berry_web.shutdown()

    api = fresh({"restart_command": []})
    berry_web.restart_server()
    check(not api.commands, "an empty restart list still sent a command")
    check("no restart command" in str(api.last_set("status", "text")),
          "the empty list was not explained")
    berry_web.shutdown()

    api = fresh(answer=BLANK)
    berry_web.read_uci()
    check(api.commands and "cat /etc/config/uhttpd" in api.commands[-1]
          and "uci -q show uhttpd.berry" in api.commands[-1],
          "Read asked for neither the file nor the section")
    check(str(api.last_set("raw_uci", "value") or "").strip()
          == BLANK["out"].strip(),
          "what came back never reached the raw page")
    check(api.last_set("uci_views", "current") == 1,
          "the raw page was left hidden after a read")
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    api.form["raw_uci"] = "option uhttpd.berry.home '/www_berry'"
    berry_web.copy_raw()
    check(api.last_set("raw_edit", "value") == api.form["raw_uci"],
          "Copy to editor did not copy what the device said")
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    api.form["raw_uci"] = ""
    berry_web.copy_raw()
    check("read the file first" in str(api.last_set("status", "text")),
          "an empty box was copied without a word about it")
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    berry_web.open_dashboard()
    opened = str(api.last_set("dash", "url") or "")
    check("8989" in opened, f"the dashboard was not opened at its port: {opened!r}")
    check(api.last_set("dash", "visible") is True,
          "the button that shows the page left the forms up in front of it")
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    berry_web.last_check.update(berry_web.parse_check(TLS_LIVE))
    berry_web.open_dashboard()
    opened = str(api.last_set("dash", "url") or "")
    check(opened.startswith("https"),
          f"the https device was visited over plain http: {opened!r}")
    berry_web.shutdown()

    # The same address, in the other two places the page can be looked at.
    api = fresh(answer=LIVE)
    berry_web.last_check.update(berry_web.parse_check(TLS_LIVE))
    berry_web.open_in_tab()
    check(str(api.asked("tabs.add_web_tab", "url") or "").startswith("https"),
          "the browser tab was handed a plain address for an https device")
    berry_web.open_in_system()
    check(api.called("ui.browse"), "the system browser was given nothing")
    berry_web.shutdown()

    # A build that cannot show a page inside a tab keeps its forms and says so,
    # with the way round it named - a hidden notebook behind a blank pane would
    # be the worst of both.
    api = fresh(answer=LIVE)
    api.refuse_web = True
    berry_web.on_widget(berry_web.TAB, "open", "clicked", {})
    check(api.last_set("dash", "visible") is not True,
          "the forms were hidden behind a page this build cannot show")
    check("browser tab" in str(api.last_set("status", "text")),
          f"the refusal did not say how to see the page anyway: "
          f"{api.last_set('status', 'text')!r}")
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    berry_web.on_widget(berry_web.PAGE, "menu_installer", "clicked", {})
    check(api.last_set("views", "current") == berry_web.INSTALL_PAGE,
          "the corner menu's installer entry did not move the notebook")
    check(api.called("tabs.activate"),
          "the corner menu did not bring the addon's own tab forward")
    berry_web.on_widget(berry_web.PAGE, "no such entry", "clicked", {})
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    berry_web.on_widget(berry_web.TAB, "to_installer", "clicked", {})
    check(api.last_set("views", "current") == berry_web.INSTALL_PAGE,
          "the page button did not move the notebook")
    berry_web.on_widget("some other tab", "to_status", "clicked", {})
    berry_web.on_widget(berry_web.TAB, "no such button", "clicked", {})
    berry_web.shutdown()


def test_the_side_bar() -> None:
    """The bar down the left of the tab: the way out of the page, the way to the
    other two places it can be seen, and the lit button that says where the tab
    is looking."""
    print("\n6d. the side bar")
    # Back out to the forms, and the page's own moves.
    api = fresh(answer=LIVE)
    berry_web.open_dashboard()
    berry_web.on_widget(berry_web.TAB, "nav_install", "clicked", {})
    check(api.last_set("views", "current") == berry_web.INSTALL_PAGE
          and api.last_set("dash", "visible") is False
          and api.last_set("views", "visible") is True,
          "the side bar's installer button did not get back out to the forms")
    berry_web.on_widget(berry_web.TAB, "nav_reload", "clicked", {})
    moves = [str(arguments[2]) for where, arguments, _named in api.calls
             if where == "tabs.set_widget" and arguments[1:2] == ("dash",)]
    check("visible" in moves and api.last_set("dash", "url") is not None,
          f"a move pressed while the page was behind did not bring it up: {moves}")
    berry_web.on_widget(berry_web.TAB, "nav_reload", "clicked", {})
    moves = [str(arguments[2]) for where, arguments, _named in api.calls
             if where == "tabs.set_widget" and arguments[1:2] == ("dash",)]
    check("reload" in moves, f"the reload button reached no leaf: {moves}")
    check(api.last_set("nav_reload", "enabled") is True,
          "the page's moves were left greyed out with the page up")
    berry_web.shutdown()

    # What the page said of itself; a blank pane is not an answer.
    api = fresh(answer=LIVE)
    berry_web.on_widget(berry_web.TAB, "dash", "loaded", {"ok": False, "url": "x"})
    check("did not load" in str(api.last_set("status", "text")),
          "a page that refused to load said nothing about it")
    berry_web.on_widget(berry_web.TAB, "dash", "loaded", {"ok": True, "url": "x"})
    check("showing x" in str(api.last_set("status", "text")),
          "a page that loaded was not said to be showing")
    berry_web.on_widget(berry_web.TAB, "dash", "clicked", {})
    berry_web.on_widget(berry_web.TAB, "some other leaf", "loaded", {})
    berry_web.shutdown()

    # The bar is lit for the page that is up, and a job does not grey it out.
    api = fresh(answer=LIVE)
    berry_web.view_page(berry_web.UHTTPD_PAGE)
    check(api.last_set("nav_uci", "style") != api.last_set("nav_status", "style"),
          "the side bar does not say which of its places the tab is looking at")
    berry_web.open_dashboard()
    check(api.last_set("nav_dash", "style") != api.last_set("nav_uci", "style"),
          "the bar still said a form was showing with the page over it")
    before = len(api.calls)
    berry_web._buttons(False)
    greyed = {str(arguments[1]) for where, arguments, _named in api.calls[before:]
              if where == "tabs.set_widget" and arguments[2:3] == ("enabled",)}
    check(api.last_set("open", "enabled") is False,
          "a running job left its own buttons lit")
    check(not [one for one in greyed if str(one).startswith("nav_")],
          f"a running job greyed out the side bar, which is the way out: "
          f"{sorted(greyed)}")
    berry_web.shutdown()


def test_the_stages() -> None:
    print("\n6e. the stages a job walks")
    install = berry_web.route(berry_web.DEFAULTS, "install")
    fetch = berry_web.route(berry_web.DEFAULTS, "fetch")
    check(install[0] == "tree" and "fetch" in install,
          f"a GitHub install does not start by finding the files: {install}")
    for wanted in ("backup", "write", "theme", "uci", "after", "verify"):
        check(wanted in install, f"an install no longer does {wanted}")
    check("write" not in fetch and "uci" not in fetch,
          f"a fetch would have written to the device: {fetch}")
    check(set(install) <= set(berry_web.STAGES),
          f"a route names a stage nothing implements: "
          f"{sorted(set(install) - set(berry_web.STAGES))}")
    local = berry_web.route(dict(berry_web.DEFAULTS, source="Local folder"), "install")
    check("fetch" not in local, "a local install still goes to GitHub")
    spans, units = berry_web._bar_spans(install)
    check(units == sum(spans[stage][1] for stage in install),
          "the bar's maximum is not the ground it has to cover")
    check(spans["write"][0] + spans["write"][1] <= units,
          "the last stage runs off the end of the bar")

    print("\n6f. the two stages this addon adds to the old installer")
    api = fresh(answer=LIVE)
    berry_web.job = {"stages": ["uci"], "stage": "uci", "index": 0, "items": [],
                     "values": dict(berry_web.DEFAULTS),
                     "paths": berry_web.job_paths(berry_web.DEFAULTS),
                     "label": "Install", "spans": {}, "errors": []}
    before = len(api.commands)
    running = berry_web.job            # the stage ends the job, and takes it with it
    berry_web._stage_uci()
    check(len(api.commands) == before + 1 and "uci commit uhttpd" in api.commands[-1],
          "the uhttpd stage sent no script")
    check(running["errors"] == [], f"a clean uci run logged a problem: {running['errors']}")
    berry_web.job = None
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    paths = berry_web.job_paths(dict(berry_web.DEFAULTS))
    berry_web.job = {"stages": ["after"], "stage": "after", "index": 0, "items": [],
                     "values": dict(berry_web.DEFAULTS), "paths": paths,
                     "label": "Install", "spans": {}, "errors": []}
    before = len(api.commands)
    berry_web._stage_after()
    script = api.commands[-1] if len(api.commands) > before else ""
    check("/www_berry/cgi-bin/index.py" in script and "chmod 755" in script,
          f"the CGI entry was not made executable: {script!r}")
    check("/etc/init.d/uhttpd restart" in script, "the server was left running old files")
    berry_web.job = None
    berry_web.shutdown()

    api = fresh(answer=LIVE)
    berry_web.job = {"stages": ["after"], "stage": "after", "index": 0, "items": [],
                     "values": dict(berry_web.DEFAULTS, make_executable=False),
                     "paths": berry_web.job_paths(dict(berry_web.DEFAULTS,
                                                        make_executable=False)),
                     "label": "Install", "spans": {}, "errors": []}
    before = len(api.commands)
    berry_web._stage_after()
    check("chmod" not in api.commands[-1],
          "the exec bit was set on a job that was told not to set it")
    berry_web.job = None
    berry_web.shutdown()

    print("\n6g. the proof that the page works")
    api = fresh(answer=LIVE)
    berry_web.job = {"stages": ["verify"], "stage": "verify", "index": 0,
                     "items": [], "values": dict(berry_web.DEFAULTS),
                     "paths": berry_web.job_paths(berry_web.DEFAULTS),
                     "label": "Install", "spans": {}, "errors": [], "written": 3}
    running = berry_web.job
    berry_web._stage_verify()
    check(not running["errors"],
          f"a live dashboard was reported as a problem: {running['errors']}")
    berry_web.job = None
    berry_web.shutdown()

    api = fresh(answer=BLANK)
    berry_web.job = {"stages": ["verify"], "stage": "verify", "index": 0,
                     "items": [], "values": dict(berry_web.DEFAULTS),
                     "paths": berry_web.job_paths(berry_web.DEFAULTS),
                     "label": "Install", "spans": {}, "errors": [], "written": 3}
    running = berry_web.job
    berry_web._stage_verify()
    check(running["errors"],
          "a page that never answered was reported as a success")
    check("python3" in str(running["errors"][0]),
          f"the advice does not name the usual reason: {running['errors']}")
    berry_web.job = None
    berry_web.shutdown()


def test_the_plan() -> None:
    print("\n6h. what the plan table and the badges say")
    api = fresh(answer=LIVE)
    items = [{"path": "js/main.js", "size": 640, "action": "queued", "remote": None},
             {"path": "css/main.css", "size": 4096, "action": "already there",
              "remote": "4 KiB"},
             {"path": "cgi-bin/index.py", "size": 9000, "action": "written",
              "remote": "there"}]
    berry_web.show_plan(items)
    rows = api.last_set("plan", "rows")
    check(len(rows) == 3 and rows[0]["cells"][0] == "js/main.js",
          f"the table did not take the plan: {rows}")
    check(any(row.get("color") for row in rows),
          "a plan row carries no colour to tell it apart")
    check("1 to write" in str(api.last_set("sum_write", "text")),
          f"the badges do not count: {api.last_set('sum_write', 'text')!r}")
    check("1 already there" in str(api.last_set("sum_same", "text")),
          "what the device already had was not counted")
    check(str(api.last_set("sum_extra", "text") or "") == "",
          "the leftover badge speaks before the device has been asked")
    berry_web.reset_totals()
    check("no plan yet" in str(api.last_set("sum_write", "text")),
          "the badges cannot be cleared back to a placeholder")
    berry_web.shutdown()


def test_the_icon() -> None:
    print("\n7. the mark the tab wears")
    svg = berry_web.TAB_ICON
    check(svg.startswith("<svg") and svg.rstrip().endswith("</svg>"),
          "TAB_ICON is not one SVG document")
    check("#c51a4a" in svg, "the mark holds no berry colour")
    check(berry_web.TAB_ICON_FILE == "berry_webicon.png",
          "the picture file is not named by the rule every addon uses")
    api = fresh(answer=LIVE)
    icon = str(api.last("tabs.set_icon")[1][1])
    check(icon.endswith("berry_webicon.png") or icon.startswith("<svg"),
          f"the tab was handed something odd as its mark: {icon[:60]!r}")
    # Where the picture is depends on how this test runs: read from the
    # repository it sits in addon.store/assets/<id>/; run by the store as part
    # of an install, the very same file is downloaded flat beside this test.
    # Either is the mark the store is expected to show, so neither may fail it.
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / berry_web.TAB_ICON_FILE,
                  ROOT / "addon.store" / "assets" / "berry_web"
                  / berry_web.TAB_ICON_FILE]
    picture = next((one for one in candidates if one.is_file()), candidates[-1])
    check(picture.is_file(), f"the store card has no picture at {picture}")
    berry_web.shutdown()


def github_token() -> str:
    """The token to ask GitHub with, when this machine names one.

    Only the last half of the GitHub check needs it: asking the real repository
    is the one thing here that cannot be answered from the source. Anonymous
    requests are capped at sixty an hour for a machine, and a runner spends that
    on its own work, so the live ask is made with a token or not at all.

    The addon has no token field of its own any more - a profile keeps the one
    token and hands it over through ``api.session.git_token()`` - so the token
    named here is put in where the addon reads it from, for the length of the one
    ask, and the stubbed api has no session to answer it.
    """
    for name in ("BERRY_WEB_TEST_TOKEN", "GITHUB_TOKEN"):
        text = str(os.environ.get(name) or "").strip()
        if text:
            return text
    return ""


def read_tree(values: dict) -> tuple[list[str], str]:
    """Run a job that only lists what there is to install, and give back the plan.

    One stage is a whole route: the job opens, ``tree`` fills the table and the
    route ends again, so nothing is written anywhere and the addon is left as it
    was found. What the plan table was handed is the answer to the only question
    this stage asks - which files, and in what order.
    """
    api = fresh()
    berry_web.start_job(["tree"], "listing", dict(berry_web.DEFAULTS, **values))
    rows = api.last_set("plan", "rows") or []
    paths = [str(row["cells"][0]) for row in rows]
    berry_web.shutdown()
    return paths, api.log_text()


def served(answer: tuple[int, bytes]):
    """A ``Fetcher.get`` that answers GitHub with one fixed reply, and no network.

    The shape of GitHub's tree answer - blobs and folders, the whole repository -
    is what the filter is written against, so it is worth holding still and
    asking what comes out of the other side of it.
    """
    def fake(self, host: str, path: str,
             accept: str = "application/octet-stream") -> tuple[int, bytes]:
        return answer if host == "api.github.com" else (404, b"{}")
    return fake


def test_the_files() -> None:
    print("\n8. where the files come from")
    # --- a folder on this machine, which needs no internet at all ---
    source = ROOT.parent.parent / "src" / "www_rasconf"
    if source.is_dir():
        paths, said = read_tree({"source": "Local folder", "local_dir": str(source)})
        check({"cgi-bin/index.py", "cgi-bin/sysinfo.py", "js/render.js",
               "css/theme.css"} <= set(paths),
              f"this checkout did not give the whole dashboard: {paths[:4]}")
        check("README.md" not in paths, "the skip list was not honoured")
        check(not any(part.endswith((".pyc", ".pyo")) or "__pycache__" in part
                       for part in paths),
              "the folder's own junk came along")
        check("GitHub" not in said,
              "GitHub was asked for something on the local route")
        look(f"{len(paths)} file(s) read off this checkout")
    else:
        look(f"no checkout here to install from ({source}); that half is skipped")

    # --- GitHub's list of a repository, cut down to what this install means ---
    tree = {"tree": [
        {"path": "src/www_rasconf", "type": "tree", "size": 0},
        {"path": "src/www_rasconf/cgi-bin/index.py", "type": "blob", "size": 900},
        {"path": "src/www_rasconf/cgi-bin/pages.py", "type": "blob", "size": 600},
        {"path": "src/www_rasconf/README.md", "type": "blob", "size": 40},
        {"path": "src/www_rasconf/cgi-bin/__pycache__/index.cpython-313.pyc",
         "type": "blob", "size": 70},
        {"path": "utils/update util/README.md", "type": "blob", "size": 10},
    ]}
    original = berry_web.Fetcher.get
    try:
        berry_web.Fetcher.get = served((200, json.dumps(tree).encode("utf-8")))
        paths, _said = read_tree({})
        check(paths == ["cgi-bin/index.py", "cgi-bin/pages.py"],
              f"the tree was not filtered to this folder: {paths}")

        # A GitHub that will not answer still installs the built-in list.
        limit = json.dumps({"message": "API rate limit exceeded"}).encode("utf-8")
        berry_web.Fetcher.get = served((403, limit))
        paths, said = read_tree({})
        check(len(paths) == len(berry_web.FALLBACK_TREE),
              f"a refused file list did not fall back to the built-in one: {paths}")
        check("css/theme.css" in paths and "cgi-bin/index.py" in paths,
              "the built-in list is not the whole dashboard")
        check("would not give the file list" in said,
              f"the refusal did not say where the list came from instead: {said}")

        # A ref that is not there is the same story: said, and installed anyway.
        berry_web.Fetcher.get = served((404, b'{"message": "Not Found"}'))
        paths, said = read_tree({"ref": "not-a-branch-at-all"})
        check(len(paths) == len(berry_web.FALLBACK_TREE),
              f"a wrong ref left nothing to install: {paths}")
        check("branch, tag or commit" in said,
              f"a wrong ref was not named as one: {said}")

        # No answer at all - a repository with nothing under that folder.
        berry_web.Fetcher.get = served(
            (200, json.dumps({"tree": []}).encode("utf-8")))
        paths, said = read_tree({"repo_path": "src/not_a_folder"})
        check(not paths, f"an empty repository still made a plan: {paths}")
        check("nothing to install" in said,
              f"an empty list was not said as one: {said}")

        # The one half that cannot be answered from the source: the real thing.
        token = github_token()
        if token:
            berry_web.Fetcher.get = original
            held = berry_web.profile_token
            berry_web.profile_token = lambda: token
            try:
                paths, said = read_tree({})
            finally:
                berry_web.profile_token = held
            check({"cgi-bin/index.py", "css/theme.css"} <= set(paths)
                  and "README.md" not in paths,
                  f"the repository did not give the dashboard: {paths[:6]} "
                  f"(said: {said.splitlines()[-1] if said else 'nothing'})")
            look(f"{len(paths)} file(s) off the real repository")
        else:
            look("no token named here; the real GitHub ask is skipped")
    finally:
        berry_web.Fetcher.get = original


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint (● ○ ✓),
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    global berry_web
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    berry_web = addon_client.load_addon(SOURCE)
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the spec itself is not built: {error}")

    print(f"berry_web: the addon at {SOURCE.name}")
    # The pure half needs no api standing anywhere, but the tab does, so one is
    # opened before any of it runs and shut after the last of it.
    test_the_check()
    test_the_scripts()
    test_the_probe()
    test_the_bridge()
    test_the_theme()
    if app is not None:
        test_the_landing()
        test_the_tab(app)
        test_the_reports()
        test_the_routes()
        test_the_side_bar()
        test_the_stages()
        test_the_plan()
        test_the_icon()
    else:
        print("\n2, 6-7. skipped: no Qt on this machine")
    # The two sources of the files need no widgets: one reads a folder here, the
    # other is answered from a canned GitHub reply.
    test_the_files()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


berry_web = None

if __name__ == "__main__":
    raise SystemExit(main())
