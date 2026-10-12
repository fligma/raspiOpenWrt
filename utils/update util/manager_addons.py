#!/usr/bin/env python3
"""The addon runtime: find the files in ``addons``, start them, switch them on
and off from Settings, and stop them again without leaving anything behind.

Two kinds of addon are supported and both get the same API (see
:mod:`addon_api`):

``name.py`` or ``name/addon.py``
    Imported into the app. Its module-level ``ADDON`` dict is the manifest and
    ``init(api)`` is the entry point. Optional hooks: ``start()``,
    ``shutdown()``, ``on_event(name, payload)``, ``on_<event>(payload)``,
    ``on_message(sender, data)`` and ``on_widget(tab, id, event, payload)``.

``name.exe`` (or any other executable) plus a ``name.json`` manifest
    Started as a child process that speaks line-delimited JSON on stdin/stdout.
    Its requests are forwarded to the same API and answered; the app pushes
    events to it. Because a second process cannot build Qt widgets, it describes
    its interface as a spec dict instead (SPEC FORMAT in addon_api) - which
    still gives it styled tabs, tables, text views and live updates.

A crash, a bad manifest or a refused call is written to the log and shown on the
addon's row in Settings; it can never take the window down.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from addon_api import (
    API_VERSION,
    AddonAPI,
    AddonError,
    app_icon_path,
    build_icon,
    addon_config_path,
    addon_schema_path,
    build_spec,
    config_get,
    guard,
    jsonable,
    merge_config,
    on_gui_thread,
    put_config_value,
    read_config_json,
    write_config_json,
)
from addon_common import addon_data_dir, build_mark, write_build_mark
from manager_const import (
    ADDON_DATA_DIR,
    ADDONS_DIR,
    ASSETS_DIR,
    BASE_DIR,
    BUILTIN_ADDONS_DIR,
    HAS_BUILTIN_ADDONS,
    LOG_FILE,
    SHIPPED_ADDON_IDS,
)
from manager_widgets import ToggleSwitch, tip_on_title


class _Bus(QObject):
    """The one way into the GUI thread for addon code that runs elsewhere."""

    _invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._invoke.connect(self._run)

    @staticmethod
    def _run(job) -> None:
        job()

    def post(self, job) -> None:
        """Run `job` on the GUI thread soon, without waiting for it."""
        if on_gui_thread():
            QTimer.singleShot(0, job)
        else:
            self._invoke.emit(job)

    def ask(self, job, timeout: float = 1800.0):
        """Run `job` on the GUI thread and wait for its result."""
        if on_gui_thread():
            return job()
        answers: queue.Queue = queue.Queue()

        def deliver() -> None:
            try:
                answers.put(("ok", job()))
            except BaseException as error:  # hand the real failure back
                answers.put(("error", error))

        self._invoke.emit(deliver)
        try:
            kind, value = answers.get(timeout=timeout)
        except queue.Empty:
            raise AddonError("the interface did not answer in time")
        if kind == "error":
            raise value
        return value


_ID_CLEAN = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_identifier(text: str) -> str:
    return _ID_CLEAN.sub("_", str(text).strip()) or "addon"


class Addon:
    """One discovered addon and everything the app handed out to it."""

    def __init__(self, addon_id: str, source: Path, home: Path, kind: str,
                 builtin: bool = False):
        self.id = addon_id
        self.source = source              # the .py file or the executable
        self.home = home                  # its folder (data, sidecar manifest)
        self.kind = kind                  # "python" or "process"
        # Shipped inside the program rather than dropped into the folder: the
        # file belongs to the build, so it can be switched on and off and given
        # a config, but nothing here may be written back to it.
        self.builtin = bool(builtin)
        self.name = addon_id
        self.version = ""
        self.author = ""
        self.description = ""
        self.enabled = True
        self.loaded = False
        self.error = ""
        self.notes = ""
        self.manifest: dict = {}
        self.api: AddonAPI | None = None
        self.module = None
        self.process: ProcessBridge | None = None
        self.data_dir = addon_data_dir(addon_id)
        # What the addon said its addons/_data/<id>/config.json means, when it
        # said anything at all (api.config.declare). See the config panel below.
        self.config_fields: list[dict] = []
        self.config_defaults: dict = {}
        self.config_title = ""
        # Everything created for the addon, remembered so it can be undone.
        self.tabs: dict[str, dict] = {}
        self.timers: list[QTimer] = []
        self.actions: list[tuple] = []
        self.setting_widget = None
        # Which transports the addon has actually exercised, recorded the first
        # time it runs a command (ssh) or touches a file (sftp) through the
        # device API. Drives the connection dots in Settings: a dot only shows
        # for a transport the addon uses.
        self.uses_ssh = False
        self.uses_sftp = False

    @property
    def is_bundled(self) -> bool:
        """Did this addon come with the program, rather than get dropped in?

        In a packaged build the shipped copies are marked ``builtin``; in a
        source checkout the name is what tells them apart, so a source tree
        still shows its own addons as bundled rather than as installed later.
        """
        return bool(self.builtin) or self.id in SHIPPED_ADDON_IDS


class ProcessBridge:
    """Line-delimited JSON between the app and an executable addon."""

    #: Requests that must be served on the GUI thread because they touch Qt.
    GUI_METHODS = ("ui.", "tabs.", "session.", "events.", "settings.", "config.",
                   "schedule.", "peers.")

    def __init__(self, manager: "AddonManager", addon: Addon):
        self.mgr = manager
        self.addon = addon
        self.lock = threading.Lock()
        self.counter = 0
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.stopping = False
        self.started_at = 0.0
        self.command: list[str] = []
        self.exit_code: int | None = None

    # --- start and stop ---------------------------------------------------

    def start(self) -> None:
        # A manifest may name the command to run ("python3", "addon.py"), which
        # lets an interpreter started from the shell act as an addon too.
        declared = self.addon.manifest.get("command")
        command = [str(part) for part in declared] if isinstance(declared, list) and declared \
            else [str(self.addon.source)]
        # A console window would pop up behind the app for every .exe addon
        # unless the child is started without one.
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.command = command
        self.exit_code = None
        self.started_at = time.monotonic()
        self.proc = subprocess.Popen(
            command,
            cwd=str(self.addon.home),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
            env=self._environment(),
        )
        self.reader = threading.Thread(target=self._read_stdout, daemon=True,
                                       name=f"addon-{self.addon.id}")
        self.reader.start()
        threading.Thread(target=self._pump_stderr, daemon=True,
                         name=f"addon-{self.addon.id}-err").start()
        self.push({"op": "start", "api": API_VERSION,
                   "addon": {"id": self.addon.id, "name": self.addon.name,
                             "version": self.addon.version,
                             "data": str(self.addon.data_dir)},
                   "session": self.mgr.window.app_config.get("name", "")})

    def _environment(self) -> dict:
        cfg = self.mgr.window.app_config
        env = dict(os.environ)
        # Python puts the *script's* folder on the path, not the app's, so an
        # addon started as a plain ``python3 mytool.py`` could not import
        # addon_client. Naming the app's folder is what lets a source addon run
        # as a process the same way its compiled build does.
        search = [str(BASE_DIR)]
        current = str(os.environ.get("PYTHONPATH", "")).strip()
        if current:
            search.append(current)
        env.update({
            "RASCONF_ADDON": "1",
            "RASCONF_ADDON_ID": self.addon.id,
            "RASCONF_API": str(API_VERSION),
            "PYTHONPATH": os.pathsep.join(search),
            "RASCONF_PROFILE": str(cfg.get("name", "")),
            "RASCONF_HOST": str(cfg.get("host", "")),
            "RASCONF_PORT": str(cfg.get("port", 22)),
            "RASCONF_USERNAME": str(cfg.get("username", "")),
            "RASCONF_REMOTE_ROOT": str(cfg.get("remote_root", "")),
            "RASCONF_SOURCE": str(cfg.get("source", "")),
            "RASCONF_ADDON_DATA": str(self.addon.data_dir),
            # The addon's own folders and its own config file, so a compiled
            # addon finds them without importing any of the app's path code.
            "RASCONF_ADDON_CONFIG": str(addon_config_path(self.addon.id)),
            "RASCONF_ADDONS": str(ADDONS_DIR),
            "RASCONF_BASE": str(BASE_DIR),
            # Where the pictures that ship inside the program are. A build
            # unpacks them into a folder of its own next to the executable, so
            # the path is handed over rather than worked out from RASCONF_BASE.
            "RASCONF_ASSETS": str(ASSETS_DIR),
            "RASCONF_LOG": str(LOG_FILE),
        })
        return env

    def stop(self, grace: float = 3.0) -> None:
        if self.proc is None:
            return
        self.stopping = True
        self.write({"op": "shutdown"})
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self.exit_code = self.proc.poll()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def info(self) -> dict:
        """What the Addon Dev tab shows for this child process."""
        code = None if self.proc is None else self.proc.poll()
        return {
            "pid": int(getattr(self.proc, "pid", 0) or 0),
            "alive": self.alive(),
            "exit": code,
            "command": list(self.command),
            "seconds": round(time.monotonic() - self.started_at, 1)
                       if self.started_at and code is None else None,
            "requests": self.counter,
        }

    # --- traffic ----------------------------------------------------------

    def _ui(self, job) -> None:
        """Run something that touches the window, whatever thread asked for it.

        The reader and the stderr pump are plain Python threads. Everything they
        report ends up in the log view or in an addon's own Settings row - real
        Qt widgets, which only the GUI thread may touch. Doing it from here
        directly is what makes Qt complain "Timers cannot be started from
        another thread" and "Cannot set parent, new parent is in a different
        thread", so hand the work to the bus instead of running it in place.
        """
        self.mgr.bus.post(job)

    def write(self, payload: dict) -> bool:
        if self.proc is None or self.proc.stdin is None:
            return False
        try:
            with self.lock:
                self.proc.stdin.write(json.dumps(payload, default=str) + "\n")
                self.proc.stdin.flush()
        except (OSError, ValueError) as error:
            if not self.stopping:
                self._ui(lambda e=error: self.mgr.log(
                    self.addon.id, f"cannot reach the addon: {e}", warning=True))
            return False
        return True

    def push(self, payload: dict) -> None:
        """A message the addon is not obliged to answer."""
        self.write(payload)

    def _pump_stderr(self) -> None:
        if self.proc is None or self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            text = line.rstrip()
            if text:
                self._ui(lambda t=text: self.mgr.log(self.addon.id, t, warning=True))

    def _read_stdout(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            text = line.strip()
            if text:
                try:
                    self._handle(text)
                except Exception as error:  # never let the reader die quietly
                    self._ui(lambda e=error: self.mgr.log(
                        self.addon.id, f"bad message: {e}", warning=True))
        code = None if self.proc is None else self.proc.poll()
        if not self.stopping:
            self._ui(lambda c=code: self.mgr.log(
                self.addon.id, f"the addon process ended (exit {c})", warning=True))
            self._ui(lambda: self.mgr.mark_stopped(self.addon))

    def _handle(self, text: str) -> None:
        if not text.startswith("{"):
            self._ui(lambda t=text: self.mgr.log(self.addon.id, t))
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            self._ui(lambda t=text: self.mgr.log(self.addon.id, t))
            return
        if not isinstance(message, dict):
            self._ui(lambda t=text: self.mgr.log(self.addon.id, t))
            return
        # ``op`` first: an addon may well name its api version in a manifest or
        # a log line, and then the push would look exactly like a request.
        operation = message.get("op")
        if operation == "manifest":
            def apply() -> None:
                self.mgr.apply_manifest(self.addon, message)
                self.mgr.refresh_rows()

            self._ui(apply)
            return
        if operation == "log" or (operation is None and "log" in message):
            self._ui(lambda m=message: self.mgr.log(
                self.addon.id, str(m.get("log", ""))))
            return
        if "api" in message:
            self._serve(message)

    def _serve(self, message: dict) -> None:
        identifier = message.get("id")
        method = str(message.get("api") or "")
        arguments = message.get("args") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        self.counter += 1
        if identifier is None:
            identifier = self.counter
        reply: dict = {"id": identifier}
        # A function cannot cross a pipe, so the addon names which argument is
        # the callback and keeps the real function at its end under a token.
        # What the app hands the API method is this closure: calling it pushes
        # the call back to the addon, with its own arguments as JSON.
        token = message.get("token")
        wanted = str(message.get("callback") or "")
        if token is not None and wanted:
            arguments.pop(wanted, None)
            arguments[wanted] = self._callback_for(token, wanted)
        try:
            if method.startswith(self.GUI_METHODS):
                result = self.mgr.bus.ask(lambda: self.addon.api.call(method, arguments))
            else:
                # Device and file work runs right here: this thread is the
                # addon's own, so blocking on the device blocks nobody else.
                result = self.addon.api.call(method, arguments)
            reply["result"] = jsonable(result)
        except AddonError as error:
            reply["error"] = str(error)
        except Exception as error:
            reply["error"] = f"{type(error).__name__}: {error}"
        if message.get("reply", True):
            self.write(reply)

    def _callback_for(self, token, argument: str):
        """The closure an in-process API method can call back into the addon."""
        def invoke(*values) -> None:
            if self.stopping:
                return
            self.push({"op": "invoke", "token": token, "argument": argument,
                       "args": jsonable(list(values))})

        return invoke


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class AddonManager:
    """Everything the app knows about its addons."""

    #: Events an addon may listen for; also what the Settings text advertises.
    EVENT_NAMES = (
        "startup", "shutdown", "sftp_connected", "sftp_disconnected",
        "ssh_connected", "ssh_disconnected", "operation_done", "tab_changed",
        "profile_changed", "settings_applied", "addon_enabled", "addon_disabled",
        "addon_config_changed",
    )

    def __init__(self, window):
        self.win = window
        self.bus = _Bus()
        self.addons: list[Addon] = []
        # Same-id copies that lost to the one in ``addons`` (a compiled program
        # and the .py it was built from). Only the Dev tab cares about them.
        self.shadowed: dict[str, list[Addon]] = {}
        self.listeners: dict[str, list[tuple[str, object]]] = {}
        self.box: QGroupBox | None = None
        self.rows: dict[str, dict] = {}
        self.body: QVBoxLayout | None = None
        self.hint: QLabel | None = None
        # The addons that ship with the program get their own top-level
        # category, separate from the drop-ins listed under "Installed". The
        # window builds this container early; it is only filled once the
        # addons have been scanned (a reload runs refresh_rows again).
        self.bundled_box: QGroupBox | None = None
        self.bundled_body: QVBoxLayout | None = None
        # Per addon id, the SSH/SFTP caption+dot labels living in its row or
        # category, so a change in connection state can recolour them.
        self.dot_refs: dict[str, dict] = {}
        # One config editor per addon id, kept across a rescan and across
        # switching the addon off and on: it belongs to the file, not to the run.
        self.config_panels: dict[str, tuple] = {}
        self._started = False
        # Nothing is read or started here: the window is still being built and
        # an addon's ``init`` already wants tabs to exist. Call reload() once
        # the interface is up.

    @property
    def window(self):
        """The main window, under the name the API and the bridge use."""
        return self.win

    # --- the settings records --------------------------------------------

    def records(self) -> dict:
        """The ``addons`` section of the active profile."""
        store = self.win.app_config.setdefault("addons", {})
        if not isinstance(store, dict):
            store = {}
            self.win.app_config["addons"] = store
        return store

    def record_for(self, addon_id: str) -> dict:
        store = self.records()
        record = store.get(addon_id)
        if not isinstance(record, dict):
            record = {"enabled": True, "settings": {}}
            store[addon_id] = record
        record.setdefault("enabled", True)
        if not isinstance(record.get("settings"), dict):
            record["settings"] = {}
        return record

    def save_records(self) -> None:
        self.win.save_app_config()

    # --- discovery --------------------------------------------------------

    def ensure_folder(self) -> Path:
        """Create ``addons`` on demand so there is somewhere to drop files."""
        try:
            ADDONS_DIR.mkdir(parents=True, exist_ok=True)
            ADDON_DATA_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.log("addons", f"could not create {ADDONS_DIR}: {error}", warning=True)
        return ADDONS_DIR

    def scan(self) -> list[Addon]:
        """Read the folder, then the addons that ship inside the program.

        A source addon and the program compiled from it share one id, and only
        one of them can be the addon called ``mytool``: which one is kept is
        what ``config/_build.json`` says to prefer (the source, unless
        the compiled copy was chosen on purpose). The other copy is remembered
        in ``shadowed`` so the Addon Dev tab can still show and switch it.

        A file in the folder beats a copy of the same id that came with the
        build, because the folder holds the one the user can read and change.
        """
        kept: dict[str, Addon] = {}
        self.shadowed = {}
        for addon in self._discovered():
            other = kept.get(addon.id)
            if other is None:
                kept[addon.id] = addon
                continue
            first, second = self._ordered(other, addon)
            kept[addon.id] = first
            self.shadowed.setdefault(first.id, []).append(second)
        return list(kept.values())

    def _discovered(self) -> list[Addon]:
        """Every addon the app can see: the drop-in folder first, then the build."""
        found: list[Addon] = []
        for directory, builtin in ((self.ensure_folder(), False),
                                   (self.bundled_folder(), True)):
            if directory is None or not directory.is_dir():
                continue
            for candidate in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
                if candidate.name.startswith((".", "_")):
                    continue
                addon = self._addon_from(candidate, builtin)
                if addon is not None:
                    found.append(addon)
        return found

    @staticmethod
    def bundled_folder() -> Path | None:
        """The addons packed inside this program, or ``None``.

        A source checkout keeps its addons in the one folder it also reads as
        drop-ins, so there is nothing separate to add there.
        """
        if not HAS_BUILTIN_ADDONS:
            return None
        return BUILTIN_ADDONS_DIR if BUILTIN_ADDONS_DIR.is_dir() else None

    def _ordered(self, first: Addon, second: Addon) -> tuple[Addon, Addon]:
        """Which of two same-id addons runs, and which one is the alternative."""
        # A copy the user dropped in wins over one that ships in the program,
        # whatever the build mark says about source versus compiled: the mark
        # is about the two forms of a file, not about who owns the folder.
        if first.builtin != second.builtin:
            return (second, first) if first.builtin else (first, second)
        source = next((item for item in (first, second) if item.kind == "python"), first)
        compiled = next((item for item in (first, second)
                         if item.kind == "process" and item is not source), second)
        prefer = str(build_mark(source.id).get("prefer") or "source").lower()
        if prefer in ("compiled", "program", "exe") and compiled.kind == "process":
            return compiled, source
        return source, compiled

    def _addon_from(self, candidate: Path, builtin: bool = False) -> Addon | None:
        folder = candidate.name
        # A folder is named after the addon; a loose file is named after itself
        # minus the extension, so the id in config.json stays clean.
        identifier = safe_identifier(folder if candidate.is_dir() else candidate.stem)
        if candidate.is_dir():
            for entry in ("addon.py", "main.py"):
                script = candidate / entry
                if script.is_file():
                    return Addon(identifier, script, candidate, "python", builtin)
            for entry in ("addon.exe", "addon.bin", f"{folder}.exe"):
                program = candidate / entry
                if program.is_file():
                    return Addon(identifier, program, candidate, "process", builtin)
            return None
        if candidate.suffix.lower() == ".py":
            return Addon(identifier, candidate, candidate.parent, "python", builtin)
        try:
            executable = os.name != "nt" and bool(candidate.stat().st_mode & 0o111)
        except OSError:
            executable = False
        if candidate.suffix.lower() in (".exe", ".bin") or executable:
            return Addon(identifier, candidate, candidate.parent, "process", builtin)
        return None

    def find(self, addon_id: str) -> Addon | None:
        return next((addon for addon in self.addons if addon.id == addon_id), None)

    def alternatives(self, addon_id: str) -> list[Addon]:
        """The same-id copies that were found but are not the running one."""
        return list(self.shadowed.get(str(addon_id), []))

    def preferred_kind(self, addon_id: str) -> str:
        """What the build mark says to run: ``"source"`` or ``"compiled"``."""
        return str(build_mark(addon_id).get("prefer") or "source").lower()

    def set_preferred_kind(self, addon_id: str, kind: str) -> None:
        """Record which of the two copies ``scan`` should keep, then rescan."""
        wanted = "compiled" if str(kind).lower() in ("compiled", "program", "exe",
                                                     "process") else "source"
        record = build_mark(addon_id)
        write_build_mark(addon_id, {**record, "prefer": wanted})
        self.reload()

    def loaded(self) -> list[Addon]:
        return [addon for addon in self.addons if addon.loaded]

    # --- what the Addon Dev tab shows ------------------------------------

    def describe(self, addon: Addon) -> dict:
        """Everything one addon is, as plain JSON-friendly values.

        Kept here rather than in the tab's own module so the tab only ever asks
        the runtime - and so a compiled addon and a Python one are described by
        the same code.
        """
        try:
            stat = addon.source.stat()
        except OSError:
            stat = None
        config = addon_config_path(addon.id)
        listeners = sorted({name for name, entries in self.listeners.items()
                            if any(where == addon.id for where, _callback in entries)})
        return {
            "id": addon.id,
            "name": addon.name,
            "version": addon.version,
            "author": addon.author,
            "description": addon.description,
            "notes": addon.notes,
            "error": addon.error,
            "kind": addon.kind,
            "builtin": bool(addon.builtin),
            "enabled": bool(addon.enabled),
            "loaded": bool(addon.loaded),
            "source": str(addon.source),
            "home": str(addon.home),
            "size": int(stat.st_size) if stat else 0,
            "modified": stat.st_mtime if stat else 0.0,
            "data": str(addon.data_dir),
            "config": str(config),
            "config_exists": bool(config.is_file()),
            "schema_exists": bool(addon_schema_path(addon.id).is_file()),
            "config_fields": len(addon.config_fields),
            "tabs": sorted(addon.tabs),
            "timers": len(addon.timers),
            "menu_items": len(addon.actions),
            "listeners": listeners,
            "has_setting_widget": addon.setting_widget is not None,
            "process": addon.process.info() if addon.process is not None else None,
            "alternatives": [{"kind": item.kind, "source": str(item.source),
                              "builtin": bool(item.builtin)}
                             for item in self.alternatives(addon.id)],
            "prefer": self.preferred_kind(addon.id),
            "build": build_mark(addon.id),
            "manifest": jsonable(addon.manifest),
        }

    def describe_all(self) -> list[dict]:
        return [self.describe(addon) for addon in self.addons]

    def log(self, addon_id: str, text, warning: bool = False) -> None:
        """Send an addon's output into the app log, attributed."""
        message = str(text)
        if not message:
            return
        first = message.splitlines()[0]
        self.win.log(f"[{addon_id}] {'error: ' if warning else ''}{first}")

    # --- (re)loading ------------------------------------------------------

    def reload(self) -> None:
        """Rescan the folder, then start whatever the profile has enabled."""
        keep = {addon.id: addon for addon in self.addons}
        for addon in list(self.addons):
            self.unload(addon)
        discovered = self.scan()
        for addon in discovered:
            record = self.record_for(addon.id)
            addon.enabled = bool(record.get("enabled", True))
            previous = keep.get(addon.id)
            if previous is not None and previous.loaded:
                # Same id, still on: tear the old one down first so the new
                # code is what runs after a reload.
                self.unload(previous)
        self.addons = discovered
        for addon in self.addons:
            if addon.enabled:
                self.load(addon)
        self.refresh_rows()

    def reconcile(self) -> None:
        """Apply the enable/disable flags after a profile switch or import."""
        for addon in self.addons:
            wanted = bool(self.record_for(addon.id).get("enabled", True))
            if wanted and not addon.loaded:
                self.load(addon)
            elif not wanted and addon.loaded:
                self.unload(addon)
        self.refresh_rows()

    def apply_manifest(self, addon: Addon, manifest: dict) -> None:
        """Copy the manifest fields the addon (or its sidecar file) declared."""
        for key in ("name", "version", "author", "description", "notes"):
            value = manifest.get(key)
            if value:
                setattr(addon, key, str(value).strip())
        if manifest.get("id"):
            addon.id = safe_identifier(str(manifest["id"]))

    def read_manifest(self, addon: Addon) -> dict:
        """A ``<id>.json`` (or ``addon.json``) next to the addon file."""
        for candidate in (addon.home / f"{addon.id}.json", addon.home / "addon.json",
                          addon.home / "manifest.json"):
            if candidate.is_file():
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    addon.error = f"bad manifest: {error}"
                    return {}
                if isinstance(data, dict):
                    return data
        return {}

    def load(self, addon: Addon) -> bool:
        if addon.loaded:
            return True
        addon.error = ""
        addon.manifest = self.read_manifest(addon)
        self.apply_manifest(addon, addon.manifest)
        try:
            if addon.kind == "python":
                self._load_python(addon)
            else:
                self._load_process(addon)
        except AddonError as error:
            addon.error = str(error)
            addon.loaded = False
            self.log(addon.id, addon.error, warning=True)
            self.refresh_row(addon.id)
            return False
        except Exception as error:
            addon.error = f"{type(error).__name__}: {error}"
            addon.loaded = False
            self.log(addon.id, f"failed to load: {addon.error}", warning=True)
            self.refresh_row(addon.id)
            return False
        addon.loaded = True
        addon.notes = ""
        self.log(addon.id, f"loaded ({addon.kind}{', shipped' if addon.builtin else ''})")
        self.refresh_row(addon.id)
        # An addon switched on after startup has missed the start event, so it
        # still gets its start() hook now rather than never.
        if self._started:
            self._call_hook(addon, "start")
        return True

    def _load_python(self, addon: Addon) -> None:
        module_name = f"rasconf_addon_{safe_identifier(addon.id)}"
        location = str(addon.source.parent)
        if location not in sys.path:
            sys.path.insert(0, location)
        spec = importlib.util.spec_from_file_location(module_name, str(addon.source))
        if spec is None or spec.loader is None:
            raise AddonError("the file could not be read as a Python module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        addon.module = module
        declared = getattr(module, "ADDON", None) or getattr(module, "MANIFEST", None)
        if isinstance(declared, dict):
            self.apply_manifest(addon, declared)
        else:
            doc = (module.__doc__ or "").strip().splitlines()
            if doc and not addon.description:
                addon.description = doc[0]
            for key in ("NAME", "VERSION", "AUTHOR", "DESCRIPTION"):
                value = getattr(module, key, None)
                if value and not getattr(addon, key.lower(), ""):
                    setattr(addon, key.lower(), str(value))
        wanted = None
        if isinstance(declared, dict):
            wanted = declared.get("api")
        if wanted is None:
            wanted = getattr(module, "API", None)
        if wanted is not None:
            try:
                if int(wanted) > API_VERSION:
                    raise AddonError(f"needs api version {wanted}, this build has {API_VERSION}")
            except (TypeError, ValueError):
                pass
        addon.api = AddonAPI(self, addon)
        self._call_hook(addon, "init", addon.api, required=True)

    def _load_process(self, addon: Addon) -> None:
        addon.data_dir.mkdir(parents=True, exist_ok=True)
        bridge = ProcessBridge(self, addon)
        addon.process = bridge
        addon.api = AddonAPI(self, addon)
        bridge.start()

    def _call_hook(self, addon: Addon, name: str, *arguments, required: bool = False) -> bool:
        """Call a module-level hook of a Python addon."""
        hook = getattr(addon.module, name, None)
        if not callable(hook):
            if required:
                raise AddonError(f"the addon has no {name}() function")
            return False

        def report(message: str) -> None:
            # Keep the failure on the addon's Settings row, not only in the log.
            addon.error = f"{name}: {message}"
            self.log(addon.id, message, warning=True)
            self.refresh_row(addon.id)

        guard(hook, *arguments, reporter=report)
        return True

    # --- teardown ---------------------------------------------------------

    def unload(self, addon: Addon, reason: str = "disabled") -> None:
        if not addon.loaded:
            self._clean(addon)
            return
        if addon.kind == "python":
            self._call_hook(addon, "shutdown")
        elif addon.process is not None:
            addon.process.push({"op": "shutdown"})
            addon.process.stop()
        self._clean(addon)
        if addon.kind == "python" and addon.module is not None:
            sys.modules.pop(f"rasconf_addon_{safe_identifier(addon.id)}", None)
            addon.module = None
        addon.api = None
        addon.loaded = False
        addon.process = None
        self.log(addon.id, f"stopped ({reason})")
        self.refresh_row(addon.id)

    def _clean(self, addon: Addon) -> None:
        """Undo everything the app created on this addon's behalf."""
        if addon.api is not None:
            for name in list(addon.tabs):
                try:
                    addon.api.tabs.close(name)
                except Exception:
                    pass
        addon.tabs.clear()
        for timer in addon.timers:
            timer.stop()
        addon.timers.clear()
        for menu, action in addon.actions:
            menu.removeAction(action)
        addon.actions.clear()
        for entries in self.listeners.values():
            entries[:] = [item for item in entries if item[0] != addon.id]
        widget = addon.setting_widget
        if widget is not None:
            holder = self.rows.get(addon.id, {}).get("extra")
            if holder is not None:
                holder.deleteLater()
            widget.deleteLater()
        addon.setting_widget = None

    def mark_stopped(self, addon: Addon) -> None:
        """A process addon exited by itself: keep the row, say why."""
        addon.loaded = False
        addon.error = ""
        addon.notes = "the addon closed its own process"
        self._clean(addon)
        self.refresh_row(addon.id)

    def shutdown_all(self) -> None:
        for addon in self.loaded():
            self.unload(addon, reason="app closing")

    # --- enabling ---------------------------------------------------------

    def set_enabled(self, addon_id: str, enabled: bool) -> None:
        addon = self.find(addon_id)
        if addon is None:
            return
        record = self.record_for(addon_id)
        record["enabled"] = bool(enabled)
        addon.enabled = bool(enabled)
        self.save_records()
        if enabled:
            if self.load(addon):
                self.notify("addon_enabled", {"addon": addon_id})
        else:
            self.notify("addon_disabled", {"addon": addon_id})
            self.unload(addon)

    def restart(self, addon_id: str) -> None:
        addon = self.find(addon_id)
        if addon is None:
            return
        was_enabled = addon.enabled
        self.unload(addon, reason="restarted")
        addon.enabled = was_enabled
        if was_enabled:
            self.load(addon)

    # --- events -----------------------------------------------------------

    def add_listener(self, event: str, addon_id: str, callback) -> Callable:
        if event not in self.EVENT_NAMES:
            self.log(addon_id, f"listening to unknown event '{event}' (it will never fire)", True)
        bucket = self.listeners.setdefault(event, [])
        bucket.append((addon_id, callback))

        def detach() -> None:
            bucket[:] = [item for item in bucket if item[1] is not callback]

        return detach

    def remove_listener(self, event: str, addon_id: str, callback=None) -> int:
        bucket = self.listeners.get(event, [])
        before = len(bucket)
        if callback is None:
            bucket[:] = [item for item in bucket if item[0] != addon_id]
        else:
            bucket[:] = [item for item in bucket
                         if not (item[0] == addon_id and item[1] is callback)]
        return before - len(bucket)

    def notify(self, event: str, payload: dict | None = None) -> None:
        """Tell every listener, plus the ``on_event``/``on_<event>`` hooks."""
        data = jsonable(payload or {})
        for addon_id, callback in list(self.listeners.get(event, [])):
            addon = self.find(addon_id)
            if addon is None or not addon.loaded:
                continue
            guard(lambda: callback(data),
                  reporter=lambda message: self.log(addon_id, f"{event}: {message}", True))
        for addon in self.loaded():
            if addon.kind == "python":
                hook = getattr(addon.module, "on_event", None)
                if callable(hook):
                    guard(hook, event, data,
                          reporter=lambda message: self.log(addon.id, f"on_event: {message}", True))
                named = getattr(addon.module, f"on_{event}", None)
                if callable(named) and named is not hook:
                    guard(named, data,
                          reporter=lambda message: self.log(addon.id, f"on_{event}: {message}", True))
            elif addon.process is not None and addon.process.alive():
                addon.process.push({"op": "event", "name": event, "payload": data})

    def deliver(self, sender_id: str, target_id: str, data) -> bool:
        """One addon messaging another; returns whether anyone received it."""
        addon = self.find(target_id)
        if addon is None or not addon.loaded:
            return False
        payload = jsonable(data)
        if addon.kind == "python":
            hook = getattr(addon.module, "on_message", None)
            if not callable(hook):
                return False
            guard(hook, sender_id, payload,
                  reporter=lambda message: self.log(addon.id, f"on_message: {message}", True))
        elif addon.process is not None:
            addon.process.push({"op": "peer", "from": sender_id, "data": payload})
        return True

    def push_widget_event(self, addon_id: str, tab: str, identifier: str,
                          event: str, payload: dict) -> None:
        """Report an interaction inside a spec-built tab back to its addon."""
        addon = self.find(addon_id)
        if addon is None:
            return
        message = {"op": "widget", "tab": tab, "id": identifier,
                   "event": event, "payload": jsonable(payload or {})}
        if addon.process is not None:
            addon.process.push(message)
            return
        if addon.module is not None:
            hook = getattr(addon.module, "on_widget", None)
            if callable(hook):
                guard(hook, tab, identifier, event, message["payload"],
                      reporter=lambda text: self.log(addon_id, f"on_widget: {text}", True))

    def refresh_app_icons(self) -> None:
        """Re-mark the addon tabs that asked to wear the app's own logo.

        The window calls this whenever it shows its icon - at startup, when
        Settings are applied, and from View > Alternate icon - so a tab that
        opted in changes in the same breath as the toolbar does, with no event
        for the addon to watch and no timer. An embedded page is left to the
        favicon the device sends; that is the page's choice, not the addon's.

        Every discovered addon is walked, not only the loaded ones, because an
        addon asks for this while it is still being imported: ``use_app_icon``
        lands in its ``init``, before anything has marked it as running. The
        tabs of an addon that is not running are cleared anyway, so the extra
        rows are simply skipped.
        """
        from PyQt6.QtGui import QIcon

        path = app_icon_path(self.win)
        for addon in self.addons:
            for entry in (addon.tabs or {}).values():
                if not entry.get("app_icon") or entry.get("web"):
                    continue
                page = entry.get("page")
                index = self.win.tabs.indexOf(page)
                if index < 0:
                    continue
                mark = build_icon(path, addon.home) if path else None
                self.win.tabs.setTabIcon(index, mark or QIcon())

    # --- the Addons section of the Settings tab -------------------------

    def build_settings_widget(self) -> QGroupBox:
        """The category listing every drop-in addon, with a switch for each.

        Addons that ship with the program are not listed here: they each get
        their own top-level category from :meth:`build_bundled_widget`, so this
        section is only the files the user added afterwards.
        """
        box = QGroupBox("Installed addons")
        # On the heading only: a tooltip here would cover the whole panel and
        # every addon row in it. See manager_widgets.tip_on_title.
        tip_on_title(box,
            "Python files and programs you put in the 'addons' folder next to "
            "rasconf Manager. Switching one off stops it and takes away its tabs; "
            "switching it on starts it again without a restart. Each profile "
            "remembers its own choices."
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 12, 10)
        layout.setSpacing(8)

        folder_row = QHBoxLayout()
        path_label = QLabel(str(self.ensure_folder()))
        path_label.setStyleSheet("color: #aaaaaa;")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setToolTip(
            "Drop .py files, addon folders or .exe programs here. A file that has "
            "the same name as an addon shipped with the program takes its place."
        )
        open_button = QPushButton("Open folder")
        open_button.clicked.connect(self.open_folder)
        config_button = QPushButton("Open addon data")
        config_button.setToolTip(
            "addons/_data: one folder per addon, each holding its config.json "
            "with the settings that belong to the installation rather than to "
            "one profile"
        )
        config_button.clicked.connect(self.open_config_folder)
        rescan_button = QPushButton("Rescan")
        rescan_button.setToolTip("Look for addons that were added or removed since startup")
        rescan_button.clicked.connect(self.reload)
        folder_row.addWidget(path_label, 1)
        folder_row.addWidget(open_button)
        folder_row.addWidget(config_button)
        folder_row.addWidget(rescan_button)
        layout.addLayout(folder_row)

        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        layout.addLayout(self.body)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(self.hint)
        self.box = box
        self.refresh_rows()
        return box

    def build_bundled_widget(self) -> QGroupBox:
        """The container for the addons that ship with the program.

        Built empty and handed to the Settings tab; :meth:`refresh_rows` fills
        it once the addons are known, giving each bundled addon its own titled
        category with a switch, its config and its connection dots - the same
        shape the SSH settings have.
        """
        box = QGroupBox("Bundled with the program")
        tip_on_title(box,
            "Addons that come inside rasconf Manager. Turn one off to stop it and "
            "take away its tab; switch it back on to bring it right back, with no "
            "restart. Each profile remembers its own choices."
        )
        body = QVBoxLayout(box)
        body.setContentsMargins(16, 12, 12, 10)
        body.setSpacing(8)
        self.bundled_box = box
        self.bundled_body = body
        if self.body is not None:
            self.refresh_rows()
        return box

    # --- connection indicators ------------------------------------------

    def make_transport_dots(self, addon_id: str, head_layout) -> None:
        """Add an SSH and an SFTP dot to a row or category header.

        A dot stays hidden until the addon has actually used that transport
        (see :meth:`mark_transport`); once it has, it shows the app's live state
        for that protocol - green connected, red not.
        """
        refs = {}
        for transport in ("ssh", "sftp"):
            caption = QLabel(transport.upper())
            caption.setStyleSheet("color: #aaaaaa;")
            caption.setVisible(False)
            dot = QLabel("\u25CF")
            dot.setObjectName("statusDotOffline")
            dot.setVisible(False)
            dot.setToolTip(f"{transport.upper()}: not connected")
            head_layout.addWidget(caption)
            head_layout.addWidget(dot)
            refs[transport] = (caption, dot)
        self.dot_refs[addon_id] = refs
        self.refresh_connection_dots(addon_id)

    def refresh_connection_dots(self, addon_id: str | None = None) -> None:
        """Show, hide and recolour the per-addon transport dots from live state.

        The window's own list of running addons - Settings > Advanced connection
        settings - is redrawn in the same breath, so a connection that comes up
        or goes down, a transport an addon starts using and an addon that gets
        switched on or off all reach the panel.
        """
        win = self.win
        shell = getattr(win, "shell_worker", None)
        states = {
            "sftp": bool(getattr(win, "sftp_connected", False)),
            "ssh": bool(shell is not None and shell.isRunning()),
        }
        identifiers = [addon_id] if addon_id else list(self.dot_refs)
        for identifier in identifiers:
            refs = self.dot_refs.get(identifier)
            if not refs:
                continue
            addon = self.find(identifier)
            uses = {
                "sftp": bool(addon is not None and getattr(addon, "uses_sftp", False)),
                "ssh": bool(addon is not None and getattr(addon, "uses_ssh", False)),
            }
            for transport, (caption, dot) in refs.items():
                visible = uses[transport]
                caption.setVisible(visible)
                dot.setVisible(visible)
                if not visible:
                    continue
                online = states[transport]
                dot.setObjectName("statusDotOnline" if online else "statusDotOffline")
                dot.setToolTip(f"{transport.upper()}: "
                               + ("connected" if online else "not connected"))
                dot.style().unpolish(dot)
                dot.style().polish(dot)
        panel = getattr(win, "refresh_connection_overview", None)
        if callable(panel):
            try:
                panel()
            except Exception:                # the panel never wins over an addon
                pass

    def mark_transport(self, addon, transport: str) -> None:
        """Record that an addon ran a command (ssh) or touched a file (sftp).

        Called from the device API, which may be on a worker thread; the dot is
        updated back on the GUI thread. Called every time an addon is loaded, so
        a fresh addon starts with no dots until it is first used.
        """
        if addon is None:
            return
        attribute = "uses_ssh" if transport == "ssh" else "uses_sftp"
        if getattr(addon, attribute, False):
            return
        setattr(addon, attribute, True)
        identifier = addon.id
        self.bus.post(lambda: self.refresh_connection_dots(identifier))

    def open_folder(self) -> None:
        self.ensure_folder()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ADDONS_DIR)))

    # --- the config files in addons/_data/<id> ---------------------------

    def config_layout(self, addon_id: str) -> tuple[str, list[dict], dict]:
        """Title, field descriptions and defaults for one addon's config.

        Taken from the running addon when there is one, and from the copy kept
        beside its config when not, so a switched-off addon still offers the
        tidy form instead of a wall of JSON.
        """
        addon = self.find(addon_id)
        cached = read_config_json(addon_schema_path(addon_id))
        fields, defaults, title = [], {}, ""
        if addon is not None and addon.config_fields:
            fields = [dict(field) for field in addon.config_fields if isinstance(field, dict)]
        elif isinstance(cached.get("fields"), list):
            fields = [dict(field) for field in cached["fields"] if isinstance(field, dict)]
        if addon is not None and addon.config_defaults:
            defaults = dict(addon.config_defaults)
        elif isinstance(cached.get("defaults"), dict):
            defaults = dict(cached["defaults"])
        if addon is not None and addon.config_title:
            title = addon.config_title
        else:
            title = str(cached.get("title") or "")
        return title, fields, defaults

    def config_values(self, addon_id: str) -> dict:
        """What the addon would read now: the file over its defaults."""
        _title, _fields, defaults = self.config_layout(addon_id)
        return merge_config(defaults, read_config_json(addon_config_path(addon_id)))

    def save_config(self, addon_id: str, values: dict, source: str = "settings") -> bool:
        try:
            write_config_json(addon_config_path(addon_id), jsonable(values))
        except OSError as error:
            self.log(addon_id, f"could not save the config: {error}", warning=True)
            return False
        self.log(addon_id, f"config saved ({source})")
        self.config_changed(addon_id, source)
        return True

    def config_changed(self, addon_id: str, source: str) -> None:
        """Tell the addon, and the others, that its config file is different."""
        payload = {"addon": addon_id, "source": str(source),
                   "file": str(addon_config_path(addon_id))}
        addon = self.find(addon_id)
        if addon is not None and addon.loaded:
            self._call_hook(addon, "on_config", payload)
        self.notify("addon_config_changed", payload)

    def open_config_file(self, addon_id: str) -> None:
        """Hand the file to whatever the system uses for JSON."""
        path = addon_config_path(addon_id)
        self.ensure_folder()
        if not path.is_file():
            try:
                write_config_json(path, self.config_values(addon_id))
            except OSError as error:
                self.log(addon_id, f"could not create {path.name}: {error}", warning=True)
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_config_folder(self) -> None:
        self.ensure_folder()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ADDON_DATA_DIR)))

    def config_panel(self, addon: Addon) -> QWidget | None:
        """The config editor under an addon's own row, built once and reused."""
        title, fields, defaults = self.config_layout(addon.id)
        has_file = addon_config_path(addon.id).is_file()
        if not fields and not has_file:
            return None     # the addon keeps no config: nothing to show
        signature = (tuple(str(field.get("key")) for field in fields), title, has_file)
        stored = self.config_panels.get(addon.id)
        if stored is not None and stored[0] == signature:
            return stored[1]
        panel = self._build_config_panel(addon, title, fields, defaults)
        self.config_panels[addon.id] = (signature, panel)
        return panel

    def _build_config_panel(self, addon: Addon, title: str,
                            fields: list[dict], defaults: dict) -> QWidget:
        box = QGroupBox("Config")
        path = addon_config_path(addon.id)
        try:
            shown = path.relative_to(BASE_DIR).as_posix()
        except ValueError:
            shown = str(path)
        tip_on_title(box,
            f"{shown}: this addon's own settings, shared by every "
            "profile and plain JSON, so any editor can change them. The addon reads "
            "the file whenever it needs it, so a change here works without a restart."
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 10, 10, 8)
        layout.setSpacing(6)

        heading = QLabel(title or shown)
        heading.setStyleSheet("color: #8f8f8f;")
        heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(heading)

        setters: list[tuple[dict, QWidget]] = []
        if fields:
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(6)
            values = self.config_values(addon.id)
            for field in fields:
                key = str(field.get("key"))
                control = _config_control(field)
                _write_config_value(control, field,
                                    config_get(values, key, config_get(defaults, key)))
                form.addRow(str(field.get("label") or key), control)
                setters.append((field, control))
            layout.addLayout(form)

            def collect() -> dict:
                # Start from the file so a key the addon has not declared here
                # - one the user added by hand - is not thrown away.
                stored = read_config_json(path)
                for field, control in setters:
                    put_config_value(stored, str(field.get("key")),
                                     _read_config_value(control, field))
                return stored

            def render(values: dict) -> None:
                for field, control in setters:
                    _write_config_value(control, field,
                                        config_get(values, str(field.get("key"))))
        else:
            editor = QPlainTextEdit()
            editor.setMaximumHeight(220)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            layout.addWidget(editor)

            def collect() -> dict:
                data = json.loads(editor.toPlainText() or "{}")
                if not isinstance(data, dict):
                    raise ValueError("the config has to be one JSON object")
                return data

            def render(values: dict) -> None:
                editor.setPlainText(json.dumps(values, indent=2, ensure_ascii=False))

        render(self.config_values(addon.id))

        note = QLabel("")
        note.setStyleSheet("color: #8f8f8f;")
        row = QHBoxLayout()
        save = QPushButton("Save config")
        save.setObjectName("primaryAction")
        save.setToolTip("Write %s" % shown)
        again = QPushButton("Reload")
        again.setToolTip("Read the file again, dropping unsaved changes")
        reset = QPushButton("Reset to defaults")
        reset.setToolTip("Replace the file with the values the addon shipped with")
        opener = QPushButton("Open file")
        opener.setToolTip("Edit it outside rasconf Manager")
        row.addWidget(save)
        row.addWidget(again)
        row.addWidget(reset)
        row.addWidget(opener)
        row.addWidget(note, 1)
        layout.addLayout(row)

        def report(text: str, bad: bool = False) -> None:
            note.setText(text)
            note.setStyleSheet("color: #ffb020;" if bad else "color: #23d18b;")

        def do_save() -> None:
            try:
                values = collect()
            except ValueError as error:
                report(f"not saved: {error}", True)
                return
            if self.save_config(addon.id, values, "settings"):
                report(f"saved {path.name}")
            else:
                report("the file could not be written", True)

        def do_reload() -> None:
            render(self.config_values(addon.id))
            report("reloaded from the file")

        def do_reset() -> None:
            if self.save_config(addon.id, jsonable(dict(defaults or {})), "reset"):
                render(self.config_values(addon.id))
                report("reset to the addon's defaults")

        def do_open() -> None:
            self.open_config_file(addon.id)

        save.clicked.connect(do_save)
        again.clicked.connect(do_reload)
        reset.clicked.connect(do_reset)
        opener.clicked.connect(do_open)
        return box

    def _salvage_row(self, extra: QVBoxLayout) -> list:
        """Empty one row's add-on area and hand back what the rebuilt row should
        put back in. The config panel never comes along: _build_row asks for it
        again, so the row gets the current form even when an addon has just
        declared or changed its fields."""
        panels = [stored[1] for stored in self.config_panels.values()]
        kept = []
        while extra.count():
            item = extra.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                if not any(widget is panel for panel in panels):
                    kept.append(widget)
        return kept

    def _settings_scrollbar(self):
        """The bar the page holding the addon rows scrolls with, if it has one.

        Rebuilding rows moves the bottom of the page around for a moment, and a
        scroll area scrolled into the middle of that clamps its bar to wherever
        the numbers allow - which reads as the page scrolling wildly under the
        pointer. So the position is saved around a rebuild and put back after.
        """
        widget = self.box.parentWidget() if self.box is not None else None
        while widget is not None and not isinstance(widget, QScrollArea):
            widget = widget.parentWidget()
        return widget.verticalScrollBar() if widget is not None else None

    def refresh_rows(self) -> None:
        """Rebuild the bundled categories and the installed list.

        Keeps any widget an addon added to its own row, and sends a bundled
        addon to the top-level category container while a drop-in stays in this
        list. Both containers are cleared first, and the transport dots are
        rebuilt with the rows, so the registry is reset here too.
        """
        if self.body is None:
            return
        bar = self._settings_scrollbar()
        where = bar.value() if bar is not None else 0
        carried = {addon_id: self._salvage_row(holder["extra"])
                   for addon_id, holder in self.rows.items()}
        for holder in self.rows.values():
            holder["row"].setParent(None)
            holder["row"].deleteLater()
        self.rows.clear()
        self.dot_refs.clear()

        installed = 0
        for addon in self.addons:
            if addon.is_bundled and self.bundled_body is not None:
                target = self.bundled_body
            else:
                target = self.body
            if not addon.is_bundled:
                installed += 1
            target.addWidget(self._build_row(addon, carried.get(addon.id, []), target))
        self.hint.setVisible(not installed)
        if not installed:
            self.hint.setText(
                "No drop-in addons. Put an 'addon.py' inside a folder named after "
                f"the addon, or a .py file, in {ADDONS_DIR} and press Rescan. The "
                "addons that ship with the program are switched on and off above."
            )
        if bar is not None:
            bar.setValue(where)

    def refresh_row(self, addon_id: str) -> None:
        """Rebuild one addon's row where it stands.

        This used to rebuild every row each time a single switch was touched,
        and that was worth avoiding: all of them left the layout and came back,
        the page height collapsed and grew again, and the Settings scroll bar
        jumped about under the hand that pressed the switch.
        """
        if self.body is None:
            return
        holder = self.rows.get(addon_id)
        addon = self.find(addon_id)
        if holder is None or addon is None:
            self.refresh_rows()
            return
        bar = self._settings_scrollbar()
        where = bar.value() if bar is not None else 0
        carried = self._salvage_row(holder["extra"])
        layout = holder.get("layout") or self.body
        index = layout.indexOf(holder["row"])
        holder["row"].setParent(None)
        holder["row"].deleteLater()
        self.rows.pop(addon_id, None)
        layout.insertWidget(max(index, 0), self._build_row(addon, carried, layout))
        if bar is not None:
            bar.setValue(where)

    def _build_row(self, addon: Addon, carried: list, target_layout=None) -> QWidget:
        entry = QWidget()
        column = QVBoxLayout(entry)
        column.setContentsMargins(4, 2, 4, 2)
        column.setSpacing(2)

        head = QHBoxLayout()
        switch = ToggleSwitch(addon.name or addon.id)
        switch.setToolTip(
            "Start the addon and add its tabs, without jumping to one. Its own "
            "settings stay saved either way, and fold away under the switch "
            "while it is off."
        )
        switch.blockSignals(True)
        switch.setChecked(addon.loaded)
        switch.blockSignals(False)
        switch.toggled.connect(lambda on, aid=addon.id: self.set_enabled(aid, on))
        head.addWidget(switch)
        if addon.version:
            version = QLabel(str(addon.version))
            version.setStyleSheet("color: #aaaaaa;")
            head.addWidget(version)
        if addon.author:
            author = QLabel(f"by {addon.author}")
            author.setStyleSheet("color: #aaaaaa;")
            head.addWidget(author)
        head.addStretch(1)

        status = QLabel(self._state_text(addon))
        status.setStyleSheet(self._state_style(addon))
        head.addWidget(status)
        # SSH / SFTP indicators, shown only for a transport this addon uses.
        self.make_transport_dots(addon.id, head)
        if not addon.is_bundled:
            # Restarting reads the file again. A shipped addon's file is inside
            # the program, so the switch is the only thing this row can offer.
            restart = QPushButton("Restart")
            restart.setToolTip("Reload this addon without restarting the app")
            restart.clicked.connect(lambda _=False, aid=addon.id: self.restart(aid))
            head.addWidget(restart)
        if addon.tabs:
            names = list(addon.tabs)
            jump = QPushButton("Show tab")
            jump.setToolTip(f"Switch to the '{names[0]}' tab this addon added")
            target = names[0]
            jump.clicked.connect(
                lambda _=False, aid=addon.id, tab=target: self._focus_tab(aid, tab))
            head.addWidget(jump)
        column.addLayout(head)

        if addon.is_bundled:
            cfg = addon_config_path(addon.id)
            try:
                shown = cfg.relative_to(BASE_DIR).as_posix()
            except ValueError:
                shown = str(cfg)
            shipped = QLabel("Shipped with the program: switch it on or off here. "
                             f"Its own settings are in {shown}.")
            shipped.setWordWrap(True)
            shipped.setStyleSheet("color: #8f8f8f;")
            column.addWidget(shipped)

        detail = addon.error or addon.description
        if detail:
            note = QLabel(detail)
            note.setWordWrap(True)
            note.setStyleSheet("color: #ffb020;" if addon.error else "color: #8f8f8f;")
            column.addWidget(note)

        extra = QVBoxLayout()
        extra.setContentsMargins(12, 2, 0, 2)
        holder = QWidget()
        holder.setLayout(extra)
        for widget in carried:
            extra.addWidget(widget)
        if addon.setting_widget is not None and addon.setting_widget not in carried:
            extra.addWidget(addon.setting_widget)
        # The addon's own config file, under its name, whether or not it runs.
        panel = self.config_panel(addon)
        if panel is not None and panel not in carried:
            extra.addWidget(panel)
        # The addon's own settings - its config panel and any widget it added
        # here - fold away with the switch: a stopped addon has nothing going
        # on, and its file can just as well wait until it runs again.
        holder.setVisible(bool(addon.loaded))
        switch.toggled.connect(holder.setVisible)
        column.addWidget(holder)

        outer = entry
        if addon.is_bundled:
            # A shipped addon reads as a top-level category the way the SSH
            # settings do: a titled box holding the same switch, dots and config.
            box = QGroupBox(addon.name or addon.id)
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 8, 12, 8)
            bl.setSpacing(2)
            bl.addWidget(entry)
            outer = box
        self.rows[addon.id] = {"row": outer, "extra": extra,
                               "layout": target_layout if target_layout is not None else self.body}
        return outer

    def _state_text(self, addon: Addon) -> str:
        if addon.error:
            return "failed"
        if addon.loaded:
            return "running"
        return "off" if addon.enabled is False else "stopped"

    def _state_style(self, addon: Addon) -> str:
        if addon.error:
            return "color: #c51a4a;"
        if addon.loaded:
            return "color: #23d18b;"
        return "color: #aaaaaa;"

    def _focus_tab(self, addon_id: str, tab: str) -> None:
        addon = self.find(addon_id)
        if addon is not None and addon.api is not None:
            addon.api.tabs.activate(tab)

    def set_setting_widget(self, addon: Addon, widget_or_spec) -> None:
        """Give an addon its own controls inside its Settings row."""
        if isinstance(widget_or_spec, QWidget):
            widget = widget_or_spec
        else:
            events = lambda identifier, event, payload, aid=addon.id: (
                self.push_widget_event(aid, "settings", identifier, event, payload))
            widget, _registry = build_spec(widget_or_spec, events=events)
        addon.setting_widget = widget
        holder = self.rows.get(addon.id)
        if holder is not None:
            holder["extra"].addWidget(widget)

    # --- startup ----------------------------------------------------------

    def start_done(self) -> None:
        """Called once the window is fully built: tell the addons to begin."""
        self._started = True
        for addon in self.loaded():
            self._call_hook(addon, "start")
        self.notify("startup", {"tabs": [self.win.tabs.tabText(i)
                                         for i in range(self.win.tabs.count())]})


# ---------------------------------------------------------------------------
# One declared config key <-> one control
# ---------------------------------------------------------------------------

def _config_kind(field: dict) -> str:
    return str(field.get("kind") or "text").lower()


def _config_control(field: dict) -> QWidget:
    """Build the editor the addon asked for, with its note as a tool-tip."""
    kind = _config_kind(field)
    if kind == "bool":
        control: QWidget = ToggleSwitch()
    elif kind == "number":
        spin = QSpinBox()
        spin.setRange(int(field.get("minimum", 0)), int(field.get("maximum", 1000000)))
        if field.get("suffix"):
            spin.setSuffix(str(field["suffix"]))
        control = spin
    elif kind == "choice":
        combo = QComboBox()
        combo.addItems([str(choice) for choice in field.get("choices") or []])
        control = combo
    elif kind == "multiline":
        box = QPlainTextEdit()
        box.setMaximumHeight(96)
        control = box
    else:
        line = QLineEdit()
        line.setPlaceholderText(
            str(field.get("placeholder") or
                ("comma separated" if kind == "list" else "")))
        control = line
    if field.get("hint"):
        # On the control, never on the panel: see manager_widgets.tip_on_title.
        control.setToolTip(str(field["hint"]))
    return control


def _read_config_value(widget: QWidget, field: dict):
    """What is in the control, in the shape the config file should hold."""
    kind = _config_kind(field)
    if kind == "bool":
        return bool(widget.isChecked())
    if kind == "number":
        return int(widget.value())
    if kind == "choice":
        return widget.currentText()
    if kind == "list":
        return [part.strip() for part in widget.text().split(",") if part.strip()]
    if kind == "multiline":
        return [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]
    return widget.text().strip()


def _write_config_value(widget: QWidget, field: dict, value) -> None:
    """The reverse: put a config value into the control."""
    kind = _config_kind(field)
    if kind == "bool":
        widget.setChecked(bool(value))
    elif kind == "number":
        widget.setValue(int(value) if isinstance(value, (int, float)) else 0)
    elif kind == "choice":
        widget.setCurrentText("" if value is None else str(value))
    elif kind == "list":
        items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
        widget.setText(", ".join(str(item) for item in items))
    elif kind == "multiline":
        items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
        widget.setPlainText("\n".join(str(item) for item in items))
    else:
        widget.setText("" if value is None else str(value))
