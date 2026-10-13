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
from typing import Any, Callable

from PyQt6.QtCore import (QCoreApplication, QEventLoop, QObject, Qt, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import QDesktopServices, QIcon
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
    QScrollBar,
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
from addon_common import (addon_data_dir, addon_manifest_path, addon_origin,
                          build_mark, kill_process_tree, popen_tree,
                          release_process_tree, terminate_process_tree,
                          write_build_mark)
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
from manager_widgets import ToggleSwitch, tab_caption, tip_on_title


class _Bus(QObject):
    """The one way into the GUI thread for addon code that runs elsewhere."""

    _invoke = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        # Set once the window is fully closed: jobs still in flight would
        # only reach deleted Qt objects, and PyQt aborts on a slot that
        # raises, so they are dropped quietly from here on.
        self.dead = False
        self._invoke.connect(self._run)

    def _run(self, job: Callable[[], Any]) -> None:
        if self.dead:
            return
        try:
            job()
        except (RuntimeError, AddonError):
            pass            # a widget went away mid-delivery while closing

    def post(self, job: Callable[[], Any]) -> None:
        """Run `job` on the GUI thread soon, without waiting for it."""
        if self.dead:
            return
        if on_gui_thread():
            QTimer.singleShot(0, job)
        else:
            self._invoke.emit(job)

    def ask(self, job: Callable[[], Any], timeout: float = 1800.0) -> Any:
        """Run `job` on the GUI thread and wait for its result."""
        if self.dead:
            raise AddonError("the app is closing")
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

    def pump(self, seconds: float = 0.02) -> None:
        """Serve queued jobs while the GUI thread has to wait during closing.

        Only for the close path: the window is frozen anyway, and without
        this a bridge waiting on a child would strand that child's own
        request on the event queue and freeze the app for whole seconds.
        """
        QCoreApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if seconds:
            time.sleep(seconds)


_ID_CLEAN = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_identifier(text: str) -> str:
    return _ID_CLEAN.sub("_", str(text).strip()) or "addon"


class Addon:
    """One discovered addon and everything the app handed out to it."""

    def __init__(self, addon_id: str, source: Path, home: Path, kind: str,
                 builtin: bool = False) -> None:
        self.id = addon_id
        self.source = source              # the .py file or the executable
        self.home = home                  # its folder (data, sidecar manifest)
        self.kind = kind                  # "python" or "process"
        self.builtin = bool(builtin)   # shipped inside the program
        self.origin = addon_origin(home, addon_id)   # what its manifest says
        self.name = addon_id
        self.version = ""
        self.author = ""
        self.description = ""
        self.enabled = False        # addons stay off until the profile says so
        self.loaded = False
        self.error = ""
        self.notes = ""
        self.manifest: dict = {}
        self.api: AddonAPI | None = None
        self.module = None
        self.process: ProcessBridge | None = None
        self.data_dir = addon_data_dir(addon_id)
        self.config_fields: list[dict] = []   # declared schema for config.json
        self.config_defaults: dict = {}
        self.config_title = ""
        self.tabs: dict[str, dict] = {}       # everything created, for undo
        self.timers: list[QTimer] = []
        self.actions: list[tuple] = []
        self.setting_widget = None
        self.uses_ssh = False                 # transports actually exercised
        self.uses_sftp = False
        self.requires: tuple = ()             # manifest "requires"
        self.own_connection = False           # False: share the app connection
        self.connection = "app"               # manifest "connection": app/own/both

    @property
    def is_bundled(self) -> bool:
        """True when the addon came with the program rather than got dropped in.

        A program the addon store installed says so in its own manifest, and that
        answer holds in a checkout and in a build alike: it is what the user put
        in, however much its id matches an addon the source also carries.
        """
        if self.builtin:
            return True
        if self.origin:
            return False
        if self.id in SHIPPED_ADDON_IDS:
            return True
        if getattr(sys, "frozen", False):
            return False
        if self.kind == "process" and not self.has_source_beside():
            # A program with no source of the same name next to it was built
            # here or installed here: neither is part of what the app ships.
            return False
        try:
            return ADDONS_DIR.resolve() in self.source.resolve().parents
        except OSError:
            return False

    @property
    def uses_own(self) -> bool:
        """Whose connection this addon actually runs on: the manifest says
        whether it may have one of its own at all, and only a "both" addon
        leaves that choice to the user's Settings switch."""
        if self.is_bundled:
            return False          # a shipped addon is pinned to the app session
        if self.connection == "own":
            return True
        if self.connection == "both":
            return bool(self.own_connection)
        return False

    def has_source_beside(self) -> bool:
        """Whether the file or folder this program was built from is next to it."""
        try:
            folder = self.source.resolve().parent
        except OSError:
            return False
        return (folder / f"{self.id}.py").is_file() or (folder / self.id).is_dir()


class ProcessBridge:
    """Line-delimited JSON between the app and an executable addon."""

    #: Requests served on the GUI thread because they touch Qt.
    GUI_METHODS = ("ui.", "tabs.", "session.", "events.", "settings.", "config.",
                   "schedule.", "peers.")

    def __init__(self, manager: "AddonManager", addon: Addon) -> None:
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

    # --- start and stop ---

    def start(self) -> None:
        # A manifest may name the command, so an interpreter can be an addon.
        declared = self.addon.manifest.get("command")
        command = [str(part) for part in declared] if isinstance(declared, list) and declared \
            else [str(self.addon.source)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console pop-up
        self.command = command
        self.exit_code = None
        self.started_at = time.monotonic()
        # Started in a way that lets the whole tree die with the app: a
        # compiled onefile addon is a bootloader plus the real program, and
        # killing only the first leaves the second running.
        self.proc = popen_tree(
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
        # Name the app's folder so a source addon can run as a process too.
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
            "RASCONF_ADDON_CONFIG": str(addon_config_path(self.addon.id)),
            "RASCONF_ADDONS": str(ADDONS_DIR),
            "RASCONF_BASE": str(BASE_DIR),
            "RASCONF_ASSETS": str(ASSETS_DIR),  # a build unpacks beside the exe
            "RASCONF_LOG": str(LOG_FILE),
        })
        return env

    def stop(self, grace: float = 3.0) -> None:
        proc = self.proc
        if proc is None:
            return
        self.stopping = True
        self.write({"op": "shutdown"})
        # Wait in slices, serving the queue between them: the addon may be
        # finishing a GUI-thread request right now, and a plain wait on a
        # frozen GUI thread deadlocks until the reply times out.
        if not self._wait(proc, grace):
            # Still busy: closing stdin sends EOF down the pipe, which wakes
            # every request the addon has pending on a dead answer.
            self.close_streams()
            terminate_process_tree(proc)
            if not self._wait(proc, grace):
                kill_process_tree(proc)
        release_process_tree(proc)
        self.close_streams()
        self.exit_code = proc.poll()

    def _wait(self, proc: subprocess.Popen, seconds: float) -> bool:
        """Poll while the closing GUI thread keeps answering this addon."""
        deadline = time.monotonic() + seconds
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                return False
            self.mgr.bus.pump()
        return True

    def close_streams(self) -> None:
        if self.proc is None:
            return
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

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

    # --- traffic ---

    def _ui(self, job: Callable[[], Any]) -> None:
        """Hand window-touching work to the bus; the reader threads are not Qt."""
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
        # ``op`` first: a log line naming an api version is not a request.
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
        # No function crosses a pipe: the addon names the callback argument and
        # keeps the real function under a token; this closure calls back.
        token = message.get("token")
        wanted = str(message.get("callback") or "")
        if token is not None and wanted:
            arguments.pop(wanted, None)
            arguments[wanted] = self._callback_for(token, wanted)
        try:
            if method.startswith(self.GUI_METHODS):
                result = self.mgr.bus.ask(lambda: self.addon.api.call(method, arguments))
            else:
                # Device and file work: this thread is the addon's own.
                result = self.addon.api.call(method, arguments)
            reply["result"] = jsonable(result)
        except AddonError as error:
            reply["error"] = str(error)
        except Exception as error:
            reply["error"] = f"{type(error).__name__}: {error}"
        if message.get("reply", True):
            self.write(reply)

    def _callback_for(self, token: Any, argument: str) -> Callable[..., None]:
        """The closure an in-process API method can call back into the addon."""
        def invoke(*values: Any) -> None:
            if self.stopping:
                return
            self.push({"op": "invoke", "token": token, "argument": argument,
                       "args": jsonable(list(values))})

        return invoke


# --- the manager ---

class AddonManager:
    """Everything the app knows about its addons."""

    #: Events an addon may listen for; also what the Settings text advertises.
    EVENT_NAMES = (
        "startup", "shutdown", "sftp_connected", "sftp_disconnected",
        "ssh_connected", "ssh_disconnected", "operation_done", "tab_changed",
        "profile_changed", "settings_applied", "addon_enabled", "addon_disabled",
        "addon_config_changed", "connections_enabled", "connections_disabled",
    )

    def __init__(self, window: Any) -> None:
        self.win = window
        self.bus = _Bus()
        self.addons: list[Addon] = []
        self.shadowed: dict[str, list[Addon]] = {}   # lost same-id copies
        self.listeners: dict[str, list[tuple[str, object]]] = {}
        self.box: QGroupBox | None = None
        self.rows: dict[str, dict] = {}
        self.body: QVBoxLayout | None = None
        self.hint: QLabel | None = None
        self.bundled_box: QGroupBox | None = None   # shipped addons, own category
        self.bundled_body: QVBoxLayout | None = None
        self.dot_refs: dict[str, dict] = {}         # id -> SSH/SFTP caption+dot
        self.config_panels: dict[str, tuple] = {}   # id -> editor, per file
        self._started = False
        # Nothing is read or started here; call reload() once the interface is up.

    @property
    def window(self) -> Any:
        """The main window, under the name the API and the bridge use."""
        return self.win

    # --- the settings records ---

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
            record = {"enabled": False, "settings": {}, "own_connection": False}
            store[addon_id] = record
        record.setdefault("enabled", False)
        record.setdefault("own_connection", False)
        if not isinstance(record.get("settings"), dict):
            record["settings"] = {}
        return record

    def save_records(self) -> None:
        self.win.save_app_config()

    # --- discovery ---

    def ensure_folder(self) -> Path:
        """Create ``addons`` on demand so there is somewhere to drop files."""
        try:
            ADDONS_DIR.mkdir(parents=True, exist_ok=True)
            ADDON_DATA_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.log("addons", f"could not create {ADDONS_DIR}: {error}", warning=True)
        return ADDONS_DIR

    def scan(self) -> list[Addon]:
        """Read the drop-in folder, then the shipped ones; one addon per id wins."""
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
                    self.preview_manifest(addon)
                    found.append(addon)
        return found

    @staticmethod
    def bundled_folder() -> Path | None:
        """Addons packed inside this program, or ``None`` for a source checkout."""
        if not HAS_BUILTIN_ADDONS:
            return None
        return BUILTIN_ADDONS_DIR if BUILTIN_ADDONS_DIR.is_dir() else None

    def _ordered(self, first: Addon, second: Addon) -> tuple[Addon, Addon]:
        """Which of two same-id addons runs, and which one is the alternative."""
        source = next((item for item in (first, second) if item.kind == "python"), first)
        compiled = next((item for item in (first, second)
                         if item.kind == "process" and item is not source), second)
        prefer = str(build_mark(source.id).get("prefer") or "").lower()
        # The Addon Dev tab's choice is a decision about this machine's build, so
        # it is honoured before any rule about which copy reads as a drop-in.
        if prefer in ("compiled", "program", "exe", "process") \
                and compiled.kind == "process":
            return compiled, source
        if prefer == "source" and source.kind == "python":
            return source, compiled
        # A copy the user dropped in, or had the store install, wins over one that
        # came inside the program: asking for it is the newer decision. The build
        # mark says nothing about which folder a file belongs to.
        if first.is_bundled != second.is_bundled:
            return (second, first) if first.is_bundled else (first, second)
        return source, compiled

    def _addon_from(self, candidate: Path, builtin: bool = False) -> Addon | None:
        folder = candidate.name
        # A folder is named after the addon; a loose file after itself minus
        # the extension, so the id in config.json stays clean.
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

    # --- what the Addon Dev tab shows ---

    def describe(self, addon: Addon) -> dict:
        """Everything one addon is, as plain JSON-friendly values."""
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
            "origin": addon.origin,
            "bundled": bool(addon.is_bundled),
            "enabled": bool(addon.enabled),
            "requires": list(addon.requires),
            "own_connection": bool(addon.own_connection),
            "connection": addon.connection,
            "uses_own": bool(addon.uses_own),
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

    def log(self, addon_id: str, text: Any, warning: bool = False) -> None:
        """Send an addon's output into the app log, attributed."""
        message = str(text)
        if not message:
            return
        first = message.splitlines()[0]
        self.win.log(f"[{addon_id}] {'error: ' if warning else ''}{first}")

    # --- (re)loading ---

    def _restore_window_tabs(self) -> None:
        """Ask the window to put addon tabs back in their saved positions."""
        restore = getattr(self.win, "restore_tab_layout", None)
        if callable(restore):
            try:
                restore()
            except Exception as error:      # a layout fix-up never breaks a load
                self.log("", f"restore tab order: {error}", warning=True)

    def reload(self) -> None:
        """Rescan the folder, then start whatever the profile has enabled."""
        keep = {addon.id: addon for addon in self.addons}
        for addon in list(self.addons):
            self.unload(addon)
        discovered = self.scan()
        for addon in discovered:
            record = self.record_for(addon.id)
            addon.enabled = bool(record.get("enabled", False))
            # Bundled addons are pinned to the main connection.
            addon.own_connection = (
                bool(record.get("own_connection", False)) and not addon.is_bundled)
            if addon.own_connection and "connection" not in (addon.manifest or {}):
                # An install from before the "connection" declaration, which the
                # profile had moved onto a connection of its own, keeps that
                # choice: undeclared plus an own switch means it may use both.
                addon.connection = "both"
                self.log(addon.id, "no \"connection\" in its manifest, but the "
                           "profile had it on its own connection: treated as "
                           "\"both\"", warning=True)
            previous = keep.get(addon.id)
            if previous is not None and previous.loaded:
                # Same id still on: tear the old one down so new code runs.
                self.unload(previous)
        self.addons = discovered
        for addon in self.addons:
            if addon.enabled:
                self.load(addon)
        self.refresh_rows()
        self._restore_window_tabs()

    def reconcile(self) -> None:
        """Apply the enable/disable flags after a profile switch or import."""
        for addon in self.addons:
            wanted = bool(self.record_for(addon.id).get("enabled", False))
            if wanted and not addon.loaded:
                self.load(addon)
            elif not wanted and addon.loaded:
                self.unload(addon)
        self.refresh_rows()

    def apply_manifest(self, addon: Addon, manifest: dict,
                       rename: bool = True) -> None:
        """Copy the manifest fields the addon (or its sidecar file) declared.

        ``rename`` is off only for a preview read before the addon runs: the id is
        what the whole record is filed under, so it moves when the addon itself
        moves, at load.
        """
        for key in ("name", "version", "author", "description", "notes"):
            value = manifest.get(key)
            if value:
                setattr(addon, key, str(value).strip())
        if rename and manifest.get("id"):
            addon.id = safe_identifier(str(manifest["id"]))
        # "requires" names the transports the API will allow; others are dropped.
        value = manifest.get("requires")
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple, set)):
            addon.requires = tuple(sorted(
                {str(part).strip().lower() for part in value} & {"ssh", "sftp"}))
        # "connection" declares whose connection the addon uses; undeclared
        # means the app's, and an addon may never use another kind than the
        # one it declared here.
        value = manifest.get("connection")
        if value is not None:
            text = str(value).strip().lower()
            if text in ("app", "main"):
                addon.connection = "app"
            elif text in ("own", "private"):
                addon.connection = "own"
            elif text == "both":
                addon.connection = "both"

    def preview_manifest(self, addon: Addon) -> None:
        """What a program's sidecar says about it, read before it has run once.

        A row is built while the addon is still switched off, and a compiled addon
        cannot report its name, its version or the transports it needs until it is
        started - so without this an installed addon would sit in Settings as a
        bare id with no form and no connection choice. The manifest is read again
        when the addon loads, from whatever describes it by then.
        """
        data = self.read_manifest(addon)
        if not data:
            return
        addon.manifest = data
        self.apply_manifest(addon, data, rename=False)

    def read_manifest(self, addon: Addon) -> dict:
        """The ``<id>.json`` (or ``addon.json``) next to the addon file."""
        candidate = addon_manifest_path(addon.home, addon.id)
        if candidate is None:
            return {}
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            addon.error = f"bad manifest: {error}"
            return {}
        return data if isinstance(data, dict) else {}

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
        # An addon switched on after startup missed the start event; give it now.
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

    def _call_hook(self, addon: Addon, name: str, *arguments: Any,
                   required: bool = False) -> bool:
        """Call a module-level hook of a Python addon."""
        hook = getattr(addon.module, name, None)
        if not callable(hook):
            if required:
                raise AddonError(f"the addon has no {name}() function")
            return False

        def report(message: str) -> None:
            # Keep the failure on the row, not only in the log.
            addon.error = f"{name}: {message}"
            self.log(addon.id, message, warning=True)
            self.refresh_row(addon.id)

        guard(hook, *arguments, reporter=report)
        return True

    # --- teardown ---

    def unload(self, addon: Addon, reason: str = "disabled") -> None:
        if not addon.loaded:
            self._clean(addon)
            return
        if addon.kind == "python":
            self._call_hook(addon, "shutdown")
        elif addon.process is not None:
            addon.process.stop()          # it writes the shutdown line itself
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
            try:
                self.unload(addon, reason="app closing")
            except Exception as error:
                self.log(addon.id, f"closing: {error}", warning=True)
        # Whatever a broken unload left behind goes down with the app.
        for addon in self.addons:
            proc = getattr(addon.process, "proc", None) if addon.process else None
            if proc is not None and proc.poll() is None:
                kill_process_tree(proc)
                release_process_tree(proc)

    # --- enabling ---

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
                # A tab switched on mid-session lands at the bar's end; put back.
                self._restore_window_tabs()
        else:
            self.notify("addon_disabled", {"addon": addon_id})
            self.unload(addon)

    def set_own_connection(self, addon_id: str, own: bool) -> None:
        """Share the app's connection (off) or open a fresh one per call (on)."""
        addon = self.find(addon_id)
        if addon is None:
            return
        if addon.is_bundled:
            # A shipped addon always uses the app's main connection.
            return
        if addon.connection != "both":
            # The source is the addon's declaration, not the user's to switch;
            # clear the stored choice so it cannot outlive that declaration.
            record = self.record_for(addon_id)
            if record.get("own_connection") or addon.own_connection:
                record["own_connection"] = False
                addon.own_connection = False
                self.save_records()
            self.log(addon_id, f"ignoring the own-connection switch: this "
                               f"addon declared \"connection\": "
                               f"\"{addon.connection}\"", True)
            return
        record = self.record_for(addon_id)
        record["own_connection"] = bool(own)
        addon.own_connection = bool(own)
        self.save_records()
        self.bus.post(lambda: self.refresh_connection_dots(addon_id))

    def restart(self, addon_id: str) -> None:
        addon = self.find(addon_id)
        if addon is None:
            return
        was_enabled = addon.enabled
        self.unload(addon, reason="restarted")
        addon.enabled = was_enabled
        if was_enabled:
            self.load(addon)
            self._restore_window_tabs()

    # --- events ---

    def add_listener(self, event: str, addon_id: str,
                     callback: Callable[..., Any]) -> Callable[[], None]:
        if event not in self.EVENT_NAMES:
            self.log(addon_id, f"listening to unknown event '{event}' (it will never fire)", True)
        bucket = self.listeners.setdefault(event, [])
        bucket.append((addon_id, callback))

        def detach() -> None:
            bucket[:] = [item for item in bucket if item[1] is not callback]

        return detach

    def remove_listener(self, event: str, addon_id: str,
                        callback: Callable[..., Any] | None = None) -> int:
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

    def deliver(self, sender_id: str, target_id: str, data: Any) -> bool:
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
        """Re-mark the addon tabs that asked to wear the app's own logo."""
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
        # Marks came on or went off: let the bar re-measure the captions.
        reader = getattr(self.win.tabs, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is not None and hasattr(bar, "refit"):
            bar.refit()

    # --- the Addons section of the Settings tab ---

    def build_settings_widget(self) -> QGroupBox:
        """The category listing every drop-in addon, with a switch for each."""
        box = QGroupBox("Installed addons")
        # On the heading only, so no tooltip covers the panel's rows.
        tip_on_title(box,
            "Python files and programs you put in the 'addons' folder next to "
            "Berry Manager. Switching one off stops it and takes away its tabs; "
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
        """The empty container ``refresh_rows`` fills with one box per bundled addon."""
        box = QGroupBox("Bundled with the program")
        tip_on_title(box,
            "Addons that come inside Berry Manager. Turn one off to stop it and "
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

    # --- connection indicators ---

    def make_transport_dots(self, addon_id: str, head_layout: QHBoxLayout) -> None:
        """Add an SSH and an SFTP dot for each transport the manifest requires."""
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
        """Show, hide and recolour the transport dots, and redraw the overview."""
        win = self.win
        shell = getattr(win, "shell_worker", None)
        reader = getattr(win, "_live_transport_states", None)
        if callable(reader):
            states = reader()
        else:
            shared = getattr(win, "shared_conn", None)
            live = shared.active() if shared is not None else False
            states = {
                "sftp": bool(getattr(win, "sftp_connected", False) or live),
                "ssh": bool((shell is not None and shell.isRunning()) or live),
            }
        identifiers = [addon_id] if addon_id else list(self.dot_refs)
        for identifier in identifiers:
            refs = self.dot_refs.get(identifier)
            if not refs:
                continue
            addon = self.find(identifier)
            declared = set(getattr(addon, "requires", ()) or ()) if addon else set()
            uses = {transport: transport in declared for transport in refs}
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

    def mark_transport(self, addon: Addon | None, transport: str) -> None:
        """Note that an addon used a transport; may come from a worker thread."""
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

    # --- the config files in addons/_data/<id> ---

    def config_layout(self, addon_id: str) -> tuple[str, list[dict], dict]:
        """Title, fields and defaults for one addon's config, live or cached."""
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
                # Start from the file, so a key the addon never declared lives on.
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
        opener.setToolTip("Edit it outside Berry Manager")
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
        """Empty one row's add-on area; hand back what the rebuilt row keeps."""
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

    def _settings_scrollbar(self) -> QScrollBar | None:
        """The bar the addon page scrolls with; saved around a rebuild."""
        widget = self.box.parentWidget() if self.box is not None else None
        while widget is not None and not isinstance(widget, QScrollArea):
            widget = widget.parentWidget()
        return widget.verticalScrollBar() if widget is not None else None

    def refresh_rows(self) -> None:
        """Rebuild the bundled categories and the installed list from scratch."""
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
                f"the addon, or a .py file, in {ADDONS_DIR}, install one from the "
                "addon store, and press Rescan. The addons that ship with the "
                "program are switched on and off above."
            )
        if bar is not None:
            bar.setValue(where)

    def refresh_row(self, addon_id: str) -> None:
        """Rebuild one addon's row in place, keeping the scroll position."""
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

    def _build_row(self, addon: Addon, carried: list,
                   target_layout: QVBoxLayout | None = None) -> QWidget:
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
        # SSH / SFTP dots, for every transport this addon declared.
        self.make_transport_dots(addon.id, head)
        if addon.requires and not addon.is_bundled and addon.connection == "both":
            # Declared "both": only here is the user's to choose between the
            # app's session and a fresh connection per call.
            own = ToggleSwitch("own connection")
            own.setToolTip(
                "Off: the addon shares the app's main SSH/SFTP session.\n"
                "On: the addon opens a connection of its own, and through\n"
                "api.device.bind() it can even reach a different device.\n"
                "Either way the switch in the Connection panel header gates\n"
                "every connection, this one included."
            )
            own.blockSignals(True)
            own.setChecked(bool(addon.own_connection))
            own.blockSignals(False)
            own.toggled.connect(lambda on, aid=addon.id: self.set_own_connection(aid, on))
            head.addWidget(own)
        elif addon.requires and not addon.is_bundled and addon.connection == "own":
            fixed = QLabel("own connection")
            fixed.setStyleSheet("color: #8f8f8f;")
            fixed.setToolTip(
                "This addon declared \"connection\": \"own\" in its manifest,\n"
                "so it always opens its own connection; it cannot use the\n"
                "app's session."
            )
            head.addWidget(fixed)
        if not addon.is_bundled:
            # A shipped addon's file is inside the program, so no restart here.
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
        elif addon.origin:
            cfg = addon_config_path(addon.id)
            try:
                shown = cfg.relative_to(BASE_DIR).as_posix()
            except ValueError:
                shown = str(cfg)
            came = QLabel(f"Installed from the {addon.origin}, and put in here with "
                          f"the other drop-ins. Its own settings are in {shown}.")
            came.setWordWrap(True)
            came.setStyleSheet("color: #8f8f8f;")
            column.addWidget(came)

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
        # Its settings fold away with the switch: a stopped addon has nothing
        # going on.
        holder.setVisible(bool(addon.loaded))
        switch.toggled.connect(holder.setVisible)
        column.addWidget(holder)

        outer = entry
        if addon.is_bundled:
            # A shipped addon reads as a top-level category, like the SSH ones.
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

    def set_setting_widget(self, addon: Addon, widget_or_spec: Any) -> None:
        """Give an addon its own controls inside its Settings row."""
        if isinstance(widget_or_spec, QWidget):
            widget = widget_or_spec
        else:
            def events(identifier: str, event: str, payload: dict,
                       aid: str = addon.id) -> None:
                self.push_widget_event(aid, "settings", identifier, event, payload)

            widget, _registry = build_spec(widget_or_spec, events=events)
        addon.setting_widget = widget
        holder = self.rows.get(addon.id)
        if holder is not None:
            holder["extra"].addWidget(widget)

    # --- startup ---

    def start_done(self) -> None:
        """Called once the window is fully built: tell the addons to begin."""
        self._started = True
        for addon in self.loaded():
            self._call_hook(addon, "start")
        self.notify("startup", {"tabs": self._tab_titles()})

    def _tab_titles(self) -> list[str]:
        """Every open tab's full name, from the stored captions, not the bar."""
        widget = self.win.tabs
        reader = getattr(widget, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is None:
            return [widget.tabText(i) for i in range(widget.count())]
        return [tab_caption(bar, i) for i in range(widget.count())]


# --- one declared config key <-> one control ---

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
        # On the control, never on the panel; see manager_widgets.tip_on_title.
        control.setToolTip(str(field["hint"]))
    return control


def _read_config_value(widget: QWidget, field: dict) -> Any:
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


def _write_config_value(widget: QWidget, field: dict, value: Any) -> None:
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
