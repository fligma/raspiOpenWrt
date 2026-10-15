#!/usr/bin/env python3
"""A test for addons/web_installer.py.

It runs the addon with the recording stub from :mod:`addon_client`, with two
things of its own added: a real Qt build of the tab's spec, and a fake device
that answers the way a Pi with an old copy of the dashboard on it would.

So three things get looked at that nothing else here can reach:

* whether the tab's spec really turns into the controls the addon later sets,
  and whether every property it sets is one that widget understands;
* whether the job walks its whole route - the file list, the download, what the
  device has, the ask, the backup, the writing, the tidy, the exec bit, the
  restart and the check on the page - and says something true at the end;
* whether any path that reaches a shell stayed below the profile's remote
  folder, and whether ``cgi-bin/hash.file`` survived the lot.

The GitHub part asks the real repository, because that is the half of this addon
that cannot be reasoned about from the source: it is skipped, and said, when this
machine cannot reach GitHub *or* when GitHub refuses to answer it - a runner with
no token gets sixty anonymous requests an hour, shared with every other job on
that machine, and a refused ask says nothing about the addon. The local-folder
route needs no internet.

Run it from the Test panel, or on the command line::

    python addons/_tests/test_web_installer.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

SOURCE = ROOT / "addons" / "web_installer.py"
FAILURES: list[str] = []

#: The remote folder the fake profile deploys into, and the device's answer.
REMOTE = "/www_rasconf"

#: What the fake device already has: one file identical to the download, one
#: changed, the PIN, and two leftovers the tidy is meant to deal with.
ON_THE_DEVICE = ["cgi-bin/index.py", "css/main.css", "cgi-bin/hash.file",
                 "js/stale_leftover.js", "old/thing.html"]


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- the device, the SFTP and the api object the addon gets ---

class FakeSFTP:
    """Enough of paramiko's SFTP for the writing stage: stat, mkdir, put."""

    def __init__(self) -> None:
        self.put_to: list[tuple[str, str]] = []
        self.mkdir_to: list[str] = []
        self.known = {REMOTE}

    def stat(self, path: str) -> dict:
        if path in self.known:
            return {"size": 0}
        raise OSError(f"No such file: {path}")

    def mkdir(self, path: str) -> None:
        self.known.add(path)
        self.mkdir_to.append(path)

    def put(self, local: str, full: str) -> None:
        if not Path(str(local)).is_file():
            raise AssertionError(f"asked to send a file that is not here: {local}")
        if not str(full).startswith(REMOTE + "/"):
            raise AssertionError(f"{full} is outside the remote folder")
        if Path(str(local)).name != str(full).rsplit("/", 1)[-1]:
            raise AssertionError(f"{local} is not the file {full} asks for")
        self.put_to.append((str(local), full))


class FakeDevice:
    """Answers the shell the way a Pi with an untidy, partly outdated copy answers."""

    def __init__(self, installer: Any) -> None:
        self.installer = installer
        self.commands: list[str] = []

    def answer(self, command: str) -> dict:
        self.commands.append(str(command))
        if "-type f -exec stat" in command:
            lines = []
            for relative in ON_THE_DEVICE:
                staged = self.installer.staging() / relative
                lines.append(f"{staged.stat().st_size if staged.is_file() else 111}"
                             f"|{REMOTE}/{relative}")
            return {"code": 0, "out": "\n".join(lines), "err": ""}
        if "curl" in command or "wget" in command:
            return {"code": 0, "err": "",
                    "out": '<!DOCTYPE html>\n<html>\n<head>\n<title>rasconf</title>\n'
                           '<input type="password">\n'}
        return {"code": 0, "out": "", "err": ""}


class Api(addon_client.StubAPI):
    """The recording stub, with a device that answers and a session to hold.

    ``no_open`` is what a compiled addon gets: ``device.open`` is refused, and
    the addon then has to send each file through ``device.upload`` instead.
    """

    def __init__(self, *args: Any, installer: Any = None, no_open: bool = False, **named: Any) -> None:
        super().__init__(*args, **named)
        self.no_open = no_open
        self.device_state = FakeDevice(installer)
        self.sftp = FakeSFTP()
        self.form: dict = {}
        self.opens = 0
        self.quits = 0

    @contextlib.contextmanager
    def _session(self) -> Iterator[Any]:
        self.opens += 1
        try:
            yield types.SimpleNamespace(sftp=self.sftp, remote_root=REMOTE, client=None)
        finally:
            self.quits += 1

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        if method == "device.open":
            if self.no_open:
                raise addon_client.AddonError(
                    "a process addon cannot hold an SSH session")
            return self._session()
        if method == "device.upload":
            local = self.argument(method, "local", arguments, named)
            target = self.argument(method, "remote_path", arguments, named)
            self.sftp.put(str(local), REMOTE + "/" + str(target))
            return str(target)
        if method == "device.run_async":
            command = self.argument(method, "command", arguments, named, "")
            callback = self.argument(method, "on_done", arguments, named)
            result = self.device_state.answer(command)
            if callable(callback):
                callback(result)            # the app answers on the GUI thread
            return result
        if method == "tabs.widget_value":
            # The form on the tab, read the way the app would read it: without
            # this the addon falls back to its defaults and the test proves less.
            identifier = self.argument(method, "identifier", arguments, named, "")
            what = self.argument(method, "what", arguments, named, "value")
            if identifier not in self.form:
                return None
            value = self.form[identifier]
            if what == "checked":
                return bool(value)
            if isinstance(value, (list, tuple)):
                return ("\n" if identifier == "restart_command" else ", ").join(
                    str(item) for item in value)
            return value
        if method == "session.status":
            return {"host": "192.168.1.10", "port": 22, "username": "root",
                    "remote_root": REMOTE, "sftp_connected": True,
                    "ssh_connected": True, "web_port": 8989, "busy": False,
                    "source": str(ROOT.parent.parent / "src" / "www_rasconf")}
        if method == "session.web_url":
            return f"http://192.168.1.10:8989{self.form.get('web_path', '/cgi-bin/index.py')}"
        return super().default(method, arguments, named)

    def sets(self, identifier: str, prop: str | None = None) -> list:
        """What the addon sent to one control on its tab, in order."""
        out = []
        for where, arguments, _named in self.calls:
            if where == "tabs.set_widget" and arguments[1:2] == (identifier,):
                if prop is None or arguments[2:3] == (prop,):
                    out.append(arguments[3] if len(arguments) > 3 else None)
        return out

    def last(self, identifier: str, prop: str) -> Any:
        values = self.sets(identifier, prop)
        return values[-1] if values else None

    def log_text(self) -> str:
        return "\n".join(self.logs)


def quoted_words(command: str) -> list[str]:
    return re.findall(r'"([^"]*)"', command)


def cells(row: object) -> list:
    """A plan row's cells, whether the addon sent a bare list or the dict it
    now sends: ``{"cells": [...], "color": "#rrggbb"}``, tinted by action."""
    return list(row["cells"]) if isinstance(row, dict) else list(row)


# --- part one: the spec, built by Qt for real ---

def test_the_tab(installer: Any, app: Any) -> None:
    """Every control the addon later sets, and every property it sets on it."""
    print("\n1. the tab, built by Qt from the spec")
    import addon_api

    widget, registry = addon_api.build_spec(installer.tab_spec(installer.DEFAULTS))
    wanted = set(installer.FIELDS_BY_KEY) | {"status", "bar", "target", "plan", "log",
                                             "views", "state"}
    wanted |= {f"step_{key}" for key, _caption in installer.STEPS}
    wanted |= {f"sum_{name}" for name in ("write", "same", "extra")}
    wanted |= {name for name, _text in installer.BUTTONS} | {installer.STOP}
    check(not wanted - set(registry),
          f"the spec leaves out {sorted(wanted - set(registry)) or 'nothing'} "
          f"and adds {sorted(set(registry) - wanted) or 'nothing'}")
    check(registry["plan"].columnCount() == 4, "the plan table has four columns")
    check(registry["views"].count() == 3,
          f"the tab's notebook has {registry['views'].count()} pages, not 3")
    captions = [registry["views"].tabText(i) for i in range(registry["views"].count())]
    check(captions == ["Files", "Log", "Settings"],
          f"the notebook's pages are not the ones the tab promises: {captions}")
    flow = [str(registry[f"step_{key}"].text()) for key, _caption in installer.STEPS]
    check(all(text.endswith(caption) for (key, caption), text in zip(installer.STEPS, flow))
          and all(text.startswith(installer._STEP_GLYPHS["pending"])
                  for text in flow),
          f"the step flow does not start as six waiting steps: {flow}")
    check(registry["log"].isReadOnly(), "the log cannot be typed into")
    check(bool(registry["bar"].maximum()), "the progress bar starts with a range")
    missing_tip = [name for name, _text in installer.BUTTONS
                   if not registry[name].toolTip()]
    check(not missing_tip, f"these buttons have no tooltip: {missing_tip or 'none'}")
    check(registry["owner"].text() == "fligma" and registry["repo"].text() == "raspiOpenWrt"
          and registry["ref"].text() == "main" and registry["web_port"].value() == 8989,
          "the settings on the tab do not start as the config says")
    check(registry["skip"].text() == ", ".join(installer.DEFAULTS["skip"])
          and registry["restart_command"].toPlainText().strip()
          == "\n".join(installer.DEFAULTS["restart_command"]),
          "a list or multiline setting is not shown as it is stored")

    def applies(identifier: str, prop: str, value: Any) -> str:
        try:
            addon_api.apply_widget_property(registry[identifier], prop, value)
        except Exception as error:          # noqa: BLE001 - the point of the check
            return f"{type(error).__name__}: {error}"
        return ""

    properties = [("status", "text", "hello"),
                  ("status", "style", "color: #ffb020; padding: 2px 4px;"),
                  ("bar", "maximum", 22), ("bar", "value", 7),
                  ("bar", "format", "writing files  %p%"),
                  ("plan", "rows", [["a/b.py", "1 KiB", "-", "new"],
                                    {"cells": ["c/d.js", "2 KiB", "-", "replaced"],
                                     "color": installer.ACTION_COLORS["replaced"]}]),
                  ("log", "append", "one line"), ("log", "value", ""),
                  ("target", "text", "goes here"),
                  ("state", "text", "\u25cf 192.168.1.10"),
                  ("state", "style", installer._CHIP_STYLES["green"]),
                  ("views", "current", 1),
                  ("step_files", "text", installer._STEP_GLYPHS["done"] + "Files"),
                  ("step_files", "style", installer._STEP_STYLES["active"]),
                  ("sum_write", "text", "6 to write"),
                  ("sum_write", "style", installer._CHIP_STYLES["crimson"]),
                  ("install", "enabled", False)]
    for identifier, prop, value in properties:
        why = applies(identifier, prop, value)
        check(not why, f"{identifier} takes '{prop}'" + (f": {why}" if why else ""))
    check(isinstance(addon_api.read_widget_value(registry["bar"], "value"), int),
          "the progress bar reads back as a number")
    unreadable = [key for key in installer.FIELDS_BY_KEY
                  if addon_api.read_widget_value(
                      registry[key],
                      "checked" if str(installer.FIELDS_BY_KEY[key].get("kind")) == "bool"
                      else "value") is None]
    check(not unreadable, f"these form controls give back nothing: {unreadable or 'none'}")


# --- part two: the job, end to end ---

def pump(app: Any, installer: Any, seconds: float = 240) -> bool:
    """Let the addon's own timers run while this waits, as the app would."""
    limit = time.time() + seconds
    while installer.working and time.time() < limit:
        if app is not None:
            app.processEvents()
        else:
            time.sleep(0.02)
    return not installer.working


def fresh(installer: Any, defaults: dict, no_open: bool = False) -> Api:
    """A stub api whose form holds the addon's own values, and the addon on it."""
    api = Api(addon_id="web_installer", installer=installer, no_open=no_open)
    api.form = dict(defaults)
    installer.init(api)
    installer.start()
    return api


def github_token() -> str:
    """The token to ask GitHub with, when this machine names one.

    A workflow hands its own down, so a runner asks as the app would with a token
    on its form instead of burning sixty anonymous requests an hour.
    """
    for name in ("WEB_INSTALLER_TEST_TOKEN", "GITHUB_TOKEN"):
        text = str(os.environ.get(name) or "").strip()
        if text:
            return text
    return ""


def github_is_there(installer: Any, token: str = "") -> bool:
    """Whether this machine can ask GitHub anything, and have it answered.

    No route and a refused request are the same thing to the sections below: both
    leave them testing what GitHub says rather than what the addon does.
    """
    fetcher = installer.Fetcher(token)
    try:
        status, body = fetcher.get("api.github.com", "/repos/fligma/raspiOpenWrt",
                                   accept="application/json")
    except (http.client.HTTPException, OSError) as error:
        look(f"GitHub cannot be reached from here: {error}")
        return False
    finally:
        fetcher.close()
    if status == 200:
        return True
    try:
        why = str(json.loads(body.decode("utf-8", errors="replace")).get("message") or "")
    except (ValueError, AttributeError, TypeError):
        why = ""
    print(f"\n2-5. GitHub answered {status} for a request from here"
          + (f" - {why}" if why else "")
          + (" - the token named here is not accepted" if status == 401 else
             " (anonymous requests are capped per hour, per machine)"
             if status in (403, 429) else ""))
    return status not in (401, 403, 429)


def test_look_around(installer: Any, app: Any, defaults: dict) -> list:
    """Fetch only: the plan fills in, the files land here, nothing goes anywhere."""
    print("\n2. Fetch files, against the real repository")
    api = fresh(installer, defaults)
    check(api.called("config.declare") and api.called("tabs.add_tab")
          and api.called("ui.menu_item"),
          "init did not declare its config, add its tab and add its menu entry")
    check((installer.TAB,) in [tuple(arguments) for where, arguments, _n in api.calls
                               if where == "tabs.use_app_icon"],
          "the tab never asked to wear the program's own icon")
    check(REMOTE in str(api.last("target", "text")),
          f"start did not say the files go to {REMOTE}: {api.last('target', 'text')!r}")
    installer.on_widget(installer.TAB, "fetch", "clicked", {})
    check(pump(app, installer), "the fetch job never came to an end")
    plan = api.last("plan", "rows") or []
    look(f"the plan holds {len(plan)} row(s); the tab says: {api.last('status', 'text')}")
    check(len(plan) >= len(installer.FALLBACK_TREE),
          f"only {len(plan)} file(s) came back from GitHub")
    folder = Path(api.paths.data) / "staging"
    staged = sorted(path.relative_to(folder).as_posix()
                    for path in folder.rglob("*") if path.is_file())
    check(sorted(cells(row)[0] for row in plan) == staged,
          "the plan and what is really staged here do not match")
    check(all(cells(row)[0].startswith(("assets/", "cgi-bin/", "css/", "js/")) for row in plan),
          "something that is not the dashboard was staged")
    check(not api.sftp.put_to and not api.opens,
          "a look-around wrote to the device")
    big = {cells(row)[0]: cells(row)[1] for row in plan}
    check("KiB" in str(big.get("assets/android-chrome-512x512.png"))
          and float(str(big.get("assets/android-chrome-512x512.png")).split()[0]) > 100,
          f"the sizes are not said sensibly: {big.get('assets/android-chrome-512x512.png')}")
    seen = [row for row in plan if cells(row)[2] != "-"]
    check(len(seen) == 2 and all(cells(row)[3] == "already there" for row in seen),
          f"what the device has was not noticed: {[(cells(row)[0], cells(row)[2], cells(row)[3]) for row in seen]}")
    check(any(row.get("color") == installer.ACTION_COLORS["already there"] for row in plan),
          "the plan's rows do not wear the colour of what happens to them")
    check("press Install" in str(api.last("status", "text")),
          "a look-around should say what it would do and stop there")
    check(str(api.last("sum_write", "text")).startswith(str(len(plan) - 2)),
          f"the write badge does not count what is left to do: {api.last('sum_write', 'text')!r}")
    # The fetch route walked files and compare, stopped before the writing, and
    # never routes through backup, write or restart at all.
    check(str(api.last("step_files", "text")).startswith("\u2713")
          and str(api.last("step_compare", "text")).startswith("\u2713")
          and str(api.last("step_backup", "text")).startswith("\u00b7")
          and str(api.last("step_write", "text")).startswith("\u00b7")
          and str(api.last("step_check", "text")).startswith("\u00b7"),
          f"the step flow does not read as a look-around: "
          f"{[api.last(f'step_{key}', 'text') for key, _c in installer.STEPS]}")
    return plan


def test_install(installer: Any, app: Any, defaults: dict, plan: list) -> None:
    """The whole route, with the tidy switched on: what goes, and what stays."""
    print("\n3. Install, tidying away what is left over")
    api = fresh(installer, dict(defaults, remove_extra=True))
    installer.on_widget(installer.TAB, "install", "clicked", {})
    check(pump(app, installer), "the install never came to an end")
    sent = api.sftp.put_to
    commands = api.device_state.commands
    look(f"{len(sent)} file(s) went up over {api.opens} session(s); "
         f"the tab says: {api.last('status', 'text')}")
    check(api.opens == 1 and api.quits == 1,
          "an in-process addon should hold one session and close it")
    check(all(full.startswith(REMOTE + "/") for _local, full in sent),
          "a file was written outside the remote folder")
    check(sorted(set(api.sftp.mkdir_to)) == [f"{REMOTE}/{name}"
          for name in ("assets", "cgi-bin", "css", "js")],
          f"the folders it needed were not made: {sorted(set(api.sftp.mkdir_to))}")
    written = len([row for row in plan if cells(row)[3] != "already there"])
    check(len(sent) == written, f"{len(sent)} file(s) went up, {written} were planned")
    check(not any("hash.file" in full or full.endswith("README.md")
                  for _local, full in sent),
          "the PIN file or the README was uploaded")
    check(any("chmod 755" in command and f"{REMOTE}/cgi-bin/index.py" in command
              for command in commands), "the CGI entry did not get the exec bit")
    check(any("/etc/init.d/uhttpd restart" in command for command in commands),
          "the web server was not restarted")
    check(any(f"127.0.0.1:{defaults['web_port']}{defaults['web_path']}" in command
              for command in commands), "the page was not asked for on the device")
    check(api.last("bar", "value") == api.last("bar", "maximum"),
          f"the bar ended at {api.last('bar', 'value')} of "
          f"{api.last('bar', 'maximum')}, not full")
    check("the dashboard answers" in api.log_text(),
          "an answering dashboard was not reported")
    check(all(str(api.last(f"step_{key}", "text")).startswith("\u2713")
              for key, _caption in installer.STEPS),
          f"a finished install left a step unlit: "
          f"{[api.last(f'step_{key}', 'text') for key, _c in installer.STEPS]}")
    check("file(s) written" in str(api.last("status", "text"))
          and "answers on port" in str(api.last("status", "text")),
          f"the last word was not the summary: {api.last('status', 'text')!r}")
    removed = [word for command in commands if "rm -f" in command
               for word in quoted_words(command)]
    check(all(f"{REMOTE}/{relative}" in removed
              for relative in ("js/stale_leftover.js", "old/thing.html")),
          f"the leftovers were not tidied away: {removed}")
    check("cgi-bin/hash.file" not in str(removed), "the PIN file was deleted")
    outside = sorted({word for command in commands for word in quoted_words(command)
                      if word.startswith("/") and not word.startswith(
                          (REMOTE, defaults["backup_dir"]))})
    check(not outside, f"a command was handed a path outside the remote folder: {outside}")
    problems = [line for line in api.logs
                if line.startswith("error:") or "the job stopped" in line
                or line.startswith("problem:")]
    check(not problems, f"the way through was not clean: {problems}")

    print("\n4. the same install with no session to hold, as a compiled addon gets")
    api2 = fresh(installer, defaults, no_open=True)
    installer.on_widget(installer.TAB, "install", "clicked", {})
    check(pump(app, installer), "the compiled-style job never ended")
    check(api2.opens == 0 and api2.called("device.upload") and api2.sftp.put_to,
          "with no session the files should each go up through device.upload")
    look(f"{len(api2.sftp.put_to)} file(s) up through "
         f"{api2.times('device.upload')} upload(s), {api2.opens} sessions")


def test_local_folder(installer: Any, app: Any, defaults: dict) -> None:
    """The other source: a folder on this machine, with no internet involved."""
    print("\n6. the files from a local folder, which needs no internet")
    source = ROOT.parent.parent / "src" / "www_rasconf"
    if not source.is_dir():
        look(f"no checkout here to install from ({source}); skipped")
        return
    api = fresh(installer, dict(defaults, source="Local folder"))
    installer.on_widget(installer.TAB, "fetch", "clicked", {})
    check(pump(app, installer), "the local job never came to an end")
    plan = api.last("plan", "rows") or []
    look(f"{len(plan)} row(s); the tab says: {api.last('status', 'text')}")
    names = {cells(row)[0] for row in plan}
    check({"cgi-bin/index.py", "cgi-bin/sysinfo.py", "js/render.js"} <= names,
          f"the folder here did not give the whole dashboard: {sorted(names)[:4]}")
    check("README.md" not in names, "the skip list was not honoured")
    check(not any(".pyc" in cells(row)[0] or "__pycache__" in cells(row)[0] for row in plan),
          "the folder's own junk came along")
    check(api.times("device.run_async") == 1,
          f"a local fetch should ask the device once, not {api.times('device.run_async')} times")
    check(not any("api.github.com" in line for line in api.logs),
          "GitHub was asked for something on the local route")


def test_wrong_ref(installer: Any, app: Any, defaults: dict) -> None:
    """A branch that is not there: said once, in words that say what to check."""
    print("\n5. a ref that does not exist")
    asked: list[tuple[str, str]] = []
    original = installer.Fetcher.get

    def counting(self, host: str, path: str, accept: str = "application/octet-stream") -> Any:
        asked.append((host, path))
        return original(self, host, path, accept)

    installer.Fetcher.get = counting
    try:
        api = fresh(installer, dict(defaults, ref="not-a-branch-at-all"))
        installer.on_widget(installer.TAB, "fetch", "clicked", {})
        check(pump(app, installer, 120), "the bad-ref job never came to an end")
    finally:
        installer.Fetcher.get = original
    said = str(api.last("status", "text"))
    look(f"{len(asked)} request(s): {asked[:2]}")
    look(f"the tab said: {said}")
    check(len(asked) <= 3,
          f"a wrong ref cost {len(asked)} requests; it should stop at the first file")
    check("Check the" in said, "the message does not say what to check")


def test_the_bar(installer: Any, app: Any, defaults: dict) -> None:
    """Watch a whole local install through the bar alone: it must move for every
    stage, name each one, and finish full instead of stuck at 95%."""
    print("\n7. the progress bar, stage by stage")
    source = ROOT.parent.parent / "src" / "www_rasconf"
    if not source.is_dir():
        look(f"no checkout here to install from ({source}); skipped")
        return
    values = dict(defaults, source="Local folder", remove_extra=True)
    api = fresh(installer, values)
    installer.on_widget(installer.TAB, "install", "clicked", {})
    check(pump(app, installer), "the bar's install never came to an end")
    stages = installer.route(values, "install")
    spans, units = installer._bar_spans(stages)
    maximums = api.sets("bar", "maximum")
    values_on_bar = api.sets("bar", "value")
    formats = [str(text) for text in api.sets("bar", "format") if text]
    check(maximums[:1] == [units],
          f"the bar was not scaled to this route at the start: {maximums[:2]}")
    check(values_on_bar and values_on_bar[-1] == units,
          f"the bar ended at {values_on_bar[-1:]} of {units} instead of full")
    check(all(later >= sooner for sooner, later in zip(values_on_bar, values_on_bar[1:])),
          "the bar went backwards")
    missing = [installer.STAGE_WORDS.get(stage, stage) for stage in stages
               if not any(installer.STAGE_WORDS.get(stage, stage) in text
                          for text in formats)]
    check(not missing, f"these steps never appeared on the bar: {missing}")
    gaps = [spans[stage][0] for stage in stages if spans[stage][0] not in values_on_bar]
    check(not gaps, f"these stages did not start where the bar stood: {gaps}")
    check(formats and formats[0].endswith("%p%") and formats[-1].startswith("done"),
          f"the bar stopped counting the percentage or never said done: {formats[-1:]}")

    print("\n8. the one-command buttons speak on the bar too")
    api2 = fresh(installer, dict(defaults, source="Local folder"))
    installer.restart_server()
    check(pump(app, installer), "the restart button never finished")
    formats2 = [str(text) for text in api2.sets("bar", "format") if text]
    moved = api2.sets("bar", "value")
    check(any("restarting the web server" in text for text in formats2),
          f"the restart did not say its step on the bar: {formats2}")
    check(moved and moved[-1] == max(moved),
          f"the bar did not end full after the restart: {moved[-1:]}")


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the step marks this tab now paints,
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    installer = addon_client.load_addon(SOURCE)
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the spec itself is not built: {error}")

    print(f"web_installer: the addon at {SOURCE.name}")
    if app is not None:
        test_the_tab(installer, app)
    defaults = dict(installer.DEFAULTS)
    token = github_token()
    if token:
        defaults["token"] = token
        look("the GitHub half asks with a token from the environment")
    if github_is_there(installer, token):
        plan = test_look_around(installer, app, defaults)
        test_install(installer, app, defaults, plan)
        test_wrong_ref(installer, app, defaults)
    else:
        print("\n2-5. skipped: nothing to ask GitHub about here")
    test_local_folder(installer, app, defaults)
    test_the_bar(installer, app, defaults)

    installer.shutdown()
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
