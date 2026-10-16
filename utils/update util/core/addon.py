#!/usr/bin/env python3
"""Addons: which are installed and running, and how one is made.

The first half finds, starts and stops the programs in ``addons`` and keeps
one process each; the second is the Addon Dev tab's machinery - the
templates, the build, the install, and the metadata read out of a file with no
import and no Qt. One owns what is installed, the other makes a new one of
them, so they answer to the same name here.
"""

from __future__ import annotations


###############################################################################
# was manager_addons.py
# The addon runtime: find the files in ``addons``, start them, switch them on
# and off from Settings, and stop them again without leaving anything behind.
#
# Two kinds of addon are supported and both get the same API (see
# :mod:`addon_api`):
#
# ``name.py`` or ``name/addon.py``
#     Imported into the app. Its module-level ``ADDON`` dict is the manifest and
#     ``init(api)`` is the entry point. Optional hooks: ``start()``,
#     ``shutdown()``, ``on_event(name, payload)``, ``on_<event>(payload)``,
#     ``on_message(sender, data)`` and ``on_widget(tab, id, event, payload)``.
#
# ``name.exe`` (or any other executable) plus a ``name.json`` manifest
#     Started as a child process that speaks line-delimited JSON on stdin/stdout.
#     Its requests are forwarded to the same API and answered; the app pushes
#     events to it. Because a second process cannot build Qt widgets, it describes
#     its interface as a spec dict instead (SPEC FORMAT in addon_api) - which
#     still gives it styled tabs, tables, text views and live updates.
#
# A crash, a bad manifest or a refused call is written to the log and shown on the
# addon's own row - in Settings for a shipped addon, on the store's Addon
# manager for one installed later; it can never take the window down.
#
###############################################################################


import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Callable

from PyQt6.QtCore import (QCoreApplication, QEventLoop, QObject, QSize, Qt, QTimer,
                          QUrl, pyqtSignal)
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
    QSpinBox,
    QTabWidget,
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
    fit_panes,
    fit_table_columns,
    guard,
    jsonable,
    merge_config,
    on_gui_thread,
    put_config_value,
    read_config_json,
    write_config_json,
)
from addon_common import (addon_data_dir, addon_manifest_path, addon_origin,
                          build_mark, future_handle, kill_process_tree,
                          popen_tree, release_process_tree,
                          terminate_process_tree, write_build_mark)
from core.const import (
    ADDON_DATA_DIR,
    ADDON_EXECUTABLE_SUFFIX,
    ADDON_ID_TRANSLATIONS,
    ADDONS_DIR,
    ASSETS_DIR,
    BASE_DIR,
    BUILTIN_ADDONS_DIR,
    HAS_BUILTIN_ADDONS,
    LOG_FILE,
    RETIRED_SHIPPED_ADDON_IDS,
    SHIPPED_ADDON_IDS,
    THEMES_DIR,
)
import core.ui
import core.popout
from core.ui import (ToggleSwitch, tab_caption, tip_on_title,
                     transport_dot)


class _Bus(QObject):
    """The one way into the GUI thread for addon code that runs elsewhere."""

    _invoke = pyqtSignal(object)

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        super().__init__()
        # Set once the window is fully closed: jobs still in flight would
        # only reach deleted Qt objects, and PyQt aborts on a slot that
        # raises, so they are dropped quietly from here on.
        self.dead = False
        # Where a job that raised gets said; the manager wires its own log.
        self._logger = logger
        self._invoke.connect(self._run)

    def _run(self, job: Callable[[], Any]) -> None:
        if self.dead:
            return
        self._guarded(job)

    def _guarded(self, job: Callable[[], Any]) -> None:
        """Run one job so nothing it raises can reach a Qt slot.

        A slot that lets an exception escape takes the whole process down
        with it, and one addon's broken job is not the app's: it is
        swallowed here and said in the log instead.
        """
        try:
            job()
        except (RuntimeError, AddonError):
            pass            # a widget went away mid-delivery while closing
        except Exception as error:
            self._note(error)

    def _note(self, error: BaseException) -> None:
        """Say what a failing job raised, without ever raising again."""
        if self._logger is None:
            return
        try:
            last = traceback.format_exc().strip().splitlines()[-1]
            self._logger(f"gui job failed: {type(error).__name__}: {error}; "
                         f"{last.strip()}")
        except Exception:
            pass

    def post(self, job: Callable[[], Any]) -> None:
        """Run `job` on the GUI thread soon, without waiting for it."""
        if self.dead:
            return
        if on_gui_thread():
            # singleShot hands the job to a slot of its own, so it needs
            # the same guard the signal path gets through _run.
            QTimer.singleShot(0, lambda: self._guarded(job))
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


# --- the leftovers of an addon this version no longer ships ---

def retired_addon_files(folder: Path, addon_id: str) -> list[Path]:
    """Every file one retired id may have left in the addons folder.

    The names are exactly the ones an install or a build writes: the source a
    checkout ships, the program the store compiles (with the extensionless name a
    Unix build uses), the older ``.bin`` shape, the manifest sidecar a process
    addon needs to be found at all, and the folder an addon of its own can be
    laid out in. Anything else in that folder is somebody else's addon and is not
    named here.
    """
    folder = Path(folder)
    wanted = [f"{addon_id}.py", f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}",
              f"{addon_id}.bin", f"{addon_id}.json", addon_id]
    seen: set[str] = set()
    out: list[Path] = []
    for name in wanted:
        if not name or name in seen:
            continue            # an extensionless build names the folder too
        seen.add(name)
        path = folder / name
        if path.exists():
            out.append(path)
    return out


def _take_out(path: Path) -> bool:
    """Delete one file or folder; whether anything actually went."""
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except OSError:
        return False
    return not path.exists()


def prune_retired_addons(log: Callable[[str], None] | None = None,
                         addons: Path | None = None,
                         data: Path | None = None) -> list[str]:
    """Clear what an addon the source no longer ships left behind, keeping its settings.

    An upgrade that drops a bundled addon has to finish the job on the machine it
    runs on: the program itself goes with the new build, but a copy in ``addons``
    from an older install, the manifest beside it and the answers it saved in
    ``addons/_data/<id>`` all stay, and an unknown id sitting in that folder is
    dead weight that Settings would go on listing.

    A retired name with a successor - the store taking over what used to ship -
    is not simply thrown away: its settings folder is moved under the new id when
    nothing is there yet, so an install that worked keeps its configuration. Once
    the successor has a folder of its own the old one is only confusion, and it
    goes. Every action is said through ``log`` and returned as a line, so a test
    can read the answer instead of a window.
    """
    folder = Path(addons) if addons is not None else ADDONS_DIR
    keeps = Path(data) if data is not None else ADDON_DATA_DIR
    said: list[str] = []
    for addon_id in RETIRED_SHIPPED_ADDON_IDS:
        for path in retired_addon_files(folder, addon_id):
            if _take_out(path):
                said.append(f"cleared the retired addon file {path.name}")
            else:
                said.append(f"{path.name} could not be removed")
        gone = keeps / safe_identifier(addon_id)
        if not gone.is_dir():
            continue
        heir = ADDON_ID_TRANSLATIONS.get(addon_id, "")
        into = keeps / safe_identifier(heir) if heir else None
        if into is not None and not into.exists():
            try:
                gone.rename(into)
                said.append(f"moved {addon_id}'s settings to {heir}")
                continue
            except OSError:
                # Nothing was copied and nothing was deleted: the settings are
                # still where they were, which is the safe answer to keep.
                said.append(f"{addon_id}'s settings could not be moved to "
                            f"{heir}, so they stay where they are")
                continue
        if _take_out(gone):
            said.append(f"removed {addon_id}'s old settings folder")
        else:
            said.append(f"{addon_id}'s settings folder could not be removed")
    for line in said:
        if callable(log):
            log(line)
    return said


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
        # Pages this addon owns in the app's Settings bar, beside its tabs.
        self.settings_pages: dict[str, dict] = {}
        self.timers: list[QTimer] = []
        self.actions: list[tuple] = []
        self.setting_widget = None
        self.uses_ssh = False                 # transports actually exercised
        self.uses_sftp = False
        self.requires: tuple = ()             # manifest "requires"
        self.own_connection = False           # False: share the app connection
        self.connection = "app"               # manifest "connection": app/own/both
        # The addon's own gates over the two transports, on its row. They are
        # drawn where the addon is listed: Settings for a shipped addon, the
        # store's Addon manager for one installed later.
        # The app-wide switch in the Connection panel stays above these, so an
        # addon reaches a transport only when both are on.
        self.ssh_enabled = True
        self.sftp_enabled = True
        # Where its tabs live. ``popout`` is what the manifest declared -
        # "always", "never" or "" for nothing - and ``popout_declared`` says
        # whether it named one at all; an addon that stayed silent leaves the
        # choice to the profile's switch. See ``effective_popout``.
        self.popout = ""
        self.popout_declared = False
        self.popout_record = False      # the profile's force-popout switch
        # Whether this addon may read the profile's global git token.
        self.git_token = False

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
    def effective_popout(self) -> bool:
        """Whether this addon's tabs belong in a window of their own.

        A manifest that named a state settles the question - an addon knows
        whether its pages are built to float - and only one that stayed silent
        leaves it to the switch beside its enable toggle.
        """
        if self.popout_declared:
            return self.popout == "always"
        return bool(self.popout_record)

    @property
    def popout_switchable(self) -> bool:
        """Whether the app may offer a force-popout switch for this addon.

        An addon that declared a state has answered the question already, and a
        shipped one belongs to the main window's bar unless its own code asks
        otherwise; neither is offered a switch that would be overruled anyway.
        """
        return not self.popout_declared and not self.is_bundled

    @property
    def uses_own(self) -> bool:
        """Whose connection this addon actually runs on: the manifest says
        whether it may have one of its own at all, and only a "both" addon
        leaves that choice to the switch on its row."""
        if self.is_bundled:
            return False          # a shipped addon is pinned to the app session
        if self.connection == "own":
            return True
        if self.connection == "both":
            return bool(self.own_connection)
        return False

    def transport_allowed(self, transport: str) -> bool:
        """Whether this addon's own switch lets it use that transport."""
        return bool(getattr(self, f"{transport}_enabled", True))

    def has_source_beside(self) -> bool:
        """Whether the file or folder this program was built from is next to it."""
        try:
            folder = self.source.resolve().parent
        except OSError:
            return False
        return (folder / f"{self.id}.py").is_file() or (folder / self.id).is_dir()


class _Answered:
    """A finished job wearing a future's face, kept under its old handle.

    The addon's side holds only a number, so the answer has to stay here; and it
    stays *as an answer*, not as a hole, because a future that was read once is
    still readable and still reports itself done. This is what a collected or
    cancelled handle is swapped for.
    """

    def __init__(self, value: Any = None, error: str = "") -> None:
        self.value = value
        self.error = error

    def done(self) -> bool:
        return True

    def result(self, timeout: Any = None) -> Any:
        if self.error:
            raise AddonError(self.error)
        return self.value

    def cancel(self) -> bool:
        return False


class ProcessBridge:
    """Line-delimited JSON between the app and an executable addon."""

    #: Requests served on the GUI thread because they touch Qt.
    GUI_METHODS = ("ui.", "tabs.", "session.", "events.", "settings.", "config.",
                   "settings_pages.", "schedule.", "peers.", "theme.", "window.")

    #: Asked by the addon about a job the app is still running for it. These
    #: never reach the api: the bridge answers them from its own registry,
    #: since the future itself cannot cross a pipe.
    FUTURE_METHODS = ("device.future_done", "device.future_result",
                      "device.future_cancel")

    #: How many answered-for handles are kept, oldest dropped first. One
    #: round's worth of devices stays readable, and an addon that forgets its
    #: handles cannot grow this without end.
    FUTURE_KEEP = 64

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
        # token -> the job this addon asked for and has not collected yet.
        self.futures: dict[int, Any] = {}
        self.future_lock = threading.Lock()
        self.future_counter = 0

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
            "RASCONF_THEMES": str(THEMES_DIR),
            "RASCONF_LOG": str(LOG_FILE),
        })
        return env

    def stop(self, grace: float = 3.0) -> None:
        proc = self.proc
        if proc is None:
            return
        self.stopping = True
        self._drop_futures()
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
            # One message, not two: the saying and the counting belong together
            # so a death cannot be logged and then forgotten by the breaker.
            seconds = round(time.monotonic() - self.started_at, 1) \
                if self.started_at else 0.0
            addon = self.addon
            self._ui(lambda: self.mgr.note_exit(addon, code, seconds))

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
        if method in self.FUTURE_METHODS:
            # A question about a job it already started: answered here, from
            # the registry, without touching the api at all.
            try:
                reply["result"] = jsonable(self._answer_future(method, arguments))
            except AddonError as error:
                reply["error"] = str(error)
            except Exception as error:
                reply["error"] = f"{type(error).__name__}: {error}"
            if message.get("reply", True):
                self.write(reply)
            return
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
            reply["result"] = self._answer_for(method, result)
        except AddonError as error:
            reply["error"] = str(error)
        except Exception as error:
            reply["error"] = f"{type(error).__name__}: {error}"
        if message.get("reply", True):
            self.write(reply)

    def _answer_for(self, method: str, result: Any) -> Any:
        """What one api answer becomes on this side of the pipe.

        A job still running is worth nothing to another process - only its
        handle is, so ``device.run(block=False)`` replies with a token the addon
        asks about through the ``device.future_*`` methods. ``run_async`` needs
        no handle: its result already arrives through its own callback token.
        """
        if isinstance(result, concurrent.futures.Future):
            if method == "device.run":
                return future_handle(self._track_future(result))
            return None
        return jsonable(result)

    def _track_future(self, future: concurrent.futures.Future) -> int:
        """Keep a running job until the addon collects it, and hand out its token."""
        with self.future_lock:
            self.future_counter += 1
            token = self.future_counter
            self.futures[token] = future
            spent = [key for key, held in self.futures.items()
                     if isinstance(held, _Answered)]
            # Counting the answer this job is about to leave behind, so the
            # number of remembered ones never goes over the limit.
            keep = max(0, len(spent) - self.FUTURE_KEEP + 1)
            for key in spent[:keep]:
                self.futures.pop(key, None)
        return token

    def _settle_future(self, token: int, value: Any, error: str = "") -> None:
        """Swap a job that is over for its answer, keeping the handle honest."""
        with self.future_lock:
            self.futures[token] = _Answered(value, error)

    def _answer_future(self, method: str, arguments: dict) -> Any:
        """Serve one ``device.future_*`` question out of the registry."""
        try:
            token = int(arguments.get("token"))
        except (TypeError, ValueError):
            raise AddonError("a job handle needs its token")
        with self.future_lock:
            future = self.futures.get(token)
        if future is None:
            raise AddonError(f"no job is known under handle {token}")
        if method == "device.future_done":
            return future.done()
        if method == "device.future_result":
            wanted = arguments.get("timeout")
            try:
                value = future.result() if wanted is None else \
                    future.result(timeout=float(wanted))
            except concurrent.futures.TimeoutError:
                # Still going: the handle is not spent, so it is left alone.
                raise AddonError("the job is still running")
            except concurrent.futures.CancelledError as error:
                self._settle_future(token, None, str(error) or "the job was cancelled")
                raise AddonError("the job was cancelled")
            except Exception as error:      # the worker's own failure, replayed
                detail = str(error) if isinstance(error, AddonError) \
                    else f"{type(error).__name__}: {error}"
                self._settle_future(token, None, detail)
                raise
            self._settle_future(token, value)
            return value
        cancelled = future.cancel()
        if cancelled:
            # The job never ran and never will: answer its handle from here on.
            self._settle_future(token, None, "the job was cancelled")
        return cancelled

    def _drop_futures(self) -> None:
        """Nothing is owed to a process that is going away."""
        with self.future_lock:
            pending = list(self.futures.values())
            self.futures.clear()
        for held in pending:
            if not isinstance(held, _Answered):
                held.cancel()

    def _callback_for(self, token: Any, argument: str) -> Callable[..., None]:
        """The closure an in-process API method can call back into the addon."""
        def invoke(*values: Any) -> None:
            if self.stopping:
                return
            self.push({"op": "invoke", "token": token, "argument": argument,
                       "args": jsonable(list(values))})

        return invoke


# --- the manager ---

def connection_choice(requires, is_bundled: bool, connection: str) -> str:
    """Which connection saying an addon's row shows, from its three answers.

    A row shows one of four words, and the order matters: an addon that named
    no transport reaches no device whatever it declares next, and a shipped
    addon runs on the app's session whatever its source says. Only an addon
    that declared ``both`` gets the switch, because only there is the choice
    the user's to make.
    """
    if not requires:
        return "none"
    if is_bundled:
        return "app"
    text = str(connection or "app").strip().lower()
    return text if text in ("none", "app", "own", "both") else "app"


class AddonManager:
    """Everything the app knows about its addons."""

    #: The store's own tab, the id of the sub-bar inside it and the page the
    #: Addon manager sits on. Settings no longer lists an installed addon, so
    #: reaching it means starting the store and moving that bar to its second
    #: page; the three names are written here rather than guessed at by the one
    #: button that does it.
    STORE_TAB = "Addon store"
    STORE_SUBTABS = "maintabs"
    STORE_MANAGER_PAGE = 1

    #: Events an addon may listen for; also what the Settings text advertises.
    EVENT_NAMES = (
        "startup", "shutdown", "sftp_connected", "sftp_disconnected",
        "ssh_connected", "ssh_disconnected", "operation_done", "tab_changed",
        "profile_changed", "settings_applied", "addon_enabled", "addon_disabled",
        "addon_config_changed", "connections_enabled", "connections_disabled",
        "transport_changed", "theme_changed",
        # One addon's own window: opened, hidden, docked back, moved, resized.
        # "window" carries every one of them with the change named in its payload,
        # so an addon that only cares about its window subscribes once.
        "window", "window_opened", "window_closed", "window_docked",
        "window_undocked", "window_resized", "window_moved",
    )

    #: How an addon that dies on the way up is kept from dying forever.
    #:
    #: A process addon that cannot come up at all - a launcher naming a module
    #: that was never bundled, a half-written build - stops within a second of
    #: being spawned, and anything that reads the addons folder again spawns it
    #: again. One such machine showed the same crash nine times in twenty
    #: seconds. So a start that ends almost immediately is counted, and once
    #: ``CRASH_LIMIT`` of them fall inside ``CRASH_WINDOW`` the app stops
    #: starting it by itself and says so on the row. A run that got properly
    #: under way, or an orderly exit 0, is not a crash and wipes the count; and
    #: the window is short enough that a file fixed in an editor is retried on
    #: its own a minute later, or at once when Restart is pressed.
    CRASH_WINDOW = 60.0     # how long a death is remembered
    CRASH_LIMIT = 3         # this many on the way up is the last of them
    CRASH_QUICK = 10.0      # shorter than this and it never really started

    def __init__(self, window: Any) -> None:
        self.win = window
        self.bus = _Bus(lambda text: self.log("", text, warning=True))
        self.addons: list[Addon] = []
        self.shadowed: dict[str, list[Addon]] = {}   # lost same-id copies
        self.listeners: dict[str, list[tuple[str, object]]] = {}
        self.rows: dict[str, dict] = {}
        self.body: QVBoxLayout | None = None   # the shipped addons' Settings page
        self.hint: QLabel | None = None
        self.settings_tabs: QTabWidget | None = None
        self.installed_scroll: QScrollArea | None = None
        # id -> a Settings page of its own, for the addons shipped inside the app
        self.bundled_tabs: dict[str, dict] = {}
        # id -> the window an addon's tabs were taken out into; see core.popout.
        self.windows: dict[str, Any] = {}
        self.dot_refs: dict[str, dict] = {}         # id -> transport -> its dot
        self.config_panels: dict[str, tuple] = {}   # id -> editor, per file
        self._started = False
        # id -> when its starts died on the way up; see CRASH_LIMIT.
        self._crashes: dict[str, list[float]] = {}
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
            record = {"enabled": False, "settings": {}, "own_connection": False,
                      "ssh_enabled": True, "sftp_enabled": True,
                      "popout": False, "popout_geometry": {}, "tab_slot": {},
                      "git_token": False}
            store[addon_id] = record
        record.setdefault("enabled", False)
        record.setdefault("own_connection", False)
        record.setdefault("ssh_enabled", True)
        record.setdefault("sftp_enabled", True)
        record.setdefault("popout", False)
        record.setdefault("git_token", False)
        if not isinstance(record.get("settings"), dict):
            record["settings"] = {}
        if not isinstance(record.get("popout_geometry"), dict):
            record["popout_geometry"] = {}
        if not isinstance(record.get("tab_slot"), dict):
            record["tab_slot"] = {}
        return record

    def slots_for(self, addon_id: str) -> dict:
        """Where each of an addon's tabs stood in the main bar, by tab name."""
        return self.record_for(addon_id)["tab_slot"]

    def remember_slot(self, addon_id: str, name: str, index: int,
                      pinned: bool = False, icon_only: bool = False) -> None:
        """Write down where a tab was, so docking it back can put it there again.

        Only the popped-out tabs leave the bar's order, and the bar is what
        ``capture_tab_order`` reads to save that order, so remembering the slot
        here is what keeps a tab's place from being lost while it is away.
        """
        self.slots_for(addon_id)[str(name)] = {
            "slot": int(index), "pinned": bool(pinned),
            "icon_only": bool(icon_only)}

    def slot_for(self, addon_id: str, name: str) -> dict:
        """The remembered place of one tab, or an empty answer for a new one."""
        where = self.slots_for(addon_id).get(str(name))
        return dict(where) if isinstance(where, dict) else {}

    def forget_slot(self, addon_id: str, name: str) -> None:
        self.slots_for(addon_id).pop(str(name), None)

    def window_events(self, addon_id: str, event: str, extra: dict) -> None:
        """Say that an addon's own window did something, to that addon.

        The tab the change concerns travels with the saying, so an addon that
        owns two pages can tell which one was moved without asking every window.
        Everything is said twice: once under ``window``, where the change is a
        field of the payload, and once under ``window_<change>``, so an addon can
        subscribe to its window as a whole or to one moment of it.
        """
        name = str(event or "").strip()
        if not name:
            return
        payload = {"addon": str(addon_id), "event": name, **dict(extra or {})}
        self.notify("window", payload)
        self.notify(f"window_{name}", payload)

    def settle_tab(self, addon_id: str, name: str) -> bool:
        """Hand a tab the addon has just built to its window, when it lives there.

        Called as the tab is created rather than once at load: a process addon
        opens its pages whenever it likes, and a window that only took the first
        tab would leave the rest in a bar the addon has left.
        """
        addon = self.find(addon_id)
        if addon is None or not addon.effective_popout:
            return False
        return core.popout.pop_out(self, addon, str(name))

    def recall_tab(self, addon_id: str, name: str) -> bool:
        """Put a tab back in the main bar before it is closed or deleted.

        An addon's page is deleted by name, and only the main bar is asked where
        it is; a page that is standing in a popout window would be deleted inside
        a window that still lists it. So the page comes home first.
        """
        addon = self.find(addon_id)
        if addon is None:
            return False
        moved = core.popout.dock_back(self, addon, str(name))
        if moved:
            self.forget_slot(addon_id, str(name))
        return moved

    def popped(self, addon_id: str) -> bool:
        """Whether this addon has a window of its own open right now."""
        window = self.windows.get(str(addon_id))
        return bool(window is not None and window.names())

    def window_for(self, addon_id: str, create: bool = False):
        """The window this addon owns in this run, making it when asked."""
        addon = self.find(addon_id)
        if addon is None:
            return None
        return core.popout.window_for(self, addon, create=create)

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
        """Record which of the two copies runs, then bring that one back.

        Only this id's file was chosen, so only this id is reloaded: every other
        addon keeps its process, its tabs and its open connection.
        """
        wanted = "compiled" if str(kind).lower() in ("compiled", "program", "exe",
                                                     "process") else "source"
        record = build_mark(addon_id)
        write_build_mark(addon_id, {**record, "prefer": wanted})
        self.reload_one(addon_id)

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
            "ssh_enabled": bool(addon.ssh_enabled),
            "sftp_enabled": bool(addon.sftp_enabled),
            "connection": addon.connection,
            "uses_own": bool(addon.uses_own),
            "popout": addon.popout,
            "popout_declared": bool(addon.popout_declared),
            "popout_forced": bool(addon.popout_record),
            "popout_switchable": bool(addon.popout_switchable),
            "effective_popout": bool(addon.effective_popout),
            "popped": self.popped(addon.id),
            "git_token": bool(addon.git_token),
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

    def reload(self, only: Any = None, force: bool = False) -> None:
        """Rescan the folder, then start whatever the profile has enabled.

        ``only`` narrows the whole job to the ids named - a program launched as
        one addon's window reads and starts that addon and nothing else, so no
        other addon can build a tab, take a connection or be in the way. With
        ``force`` those ids run whatever their switch says, which is what a
        Desktop link asks for: the link was made on purpose, and an addon left
        switched off in some profile should still open rather than refuse.
        """
        wanted = {str(item).strip().lower() for item in
                  (([only] if isinstance(only, str) else list(only or []))
                   if only else []) if str(item).strip()}
        keep = {addon.id: addon for addon in self.addons}
        active = self._active_tab()
        for addon in list(self.addons):
            self.unload(addon)
        discovered = self.scan()
        if wanted:
            discovered = [addon for addon in discovered
                          if str(addon.id).lower() in wanted]
        for addon in discovered:
            self.apply_record(addon)
            if force:
                addon.enabled = True
            previous = keep.get(addon.id)
            if previous is not None and previous.loaded \
                    and previous not in discovered:
                # Same id still on: tear the old one down so new code runs.
                self.unload(previous)
        self.addons = discovered
        wanted_addons = [addon for addon in self.addons if addon.enabled]
        for number, addon in enumerate(wanted_addons, start=1):
            # Named on the boot splash while it is up; harmless afterwards.
            core.ui.addon_step(number, len(wanted_addons),
                                      addon.name or addon.id)
            self.load(addon)
        self.refresh_rows()
        self._restore_window_tabs()
        self._reactivate_tab(active)

    def apply_record(self, addon: Addon) -> None:
        """Put the profile's stored switches onto one discovered addon."""
        record = self.record_for(addon.id)
        addon.enabled = bool(record.get("enabled", False))
        # Each transport needs its app-wide gate and this addon's own.
        addon.ssh_enabled = bool(record.get("ssh_enabled", True))
        addon.sftp_enabled = bool(record.get("sftp_enabled", True))
        addon.popout_record = bool(record.get("popout", False))
        addon.git_token = bool(record.get("git_token", False))
        # A switch left on for an addon that has since declared a state, or that
        # the program now ships itself, is not the app's to honour.
        if addon.popout_record and not addon.popout_switchable:
            record["popout"] = addon.popout_record = False
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

    def reload_one(self, addon_id: str) -> None:
        """Bring one addon back from its file and leave every other one running.

        :meth:`reload` is a whole-world job: it shuts every addon down, reads the
        folder, and starts each enabled one again, taking its tabs and its Settings
        rows with it. That is what a boot and a deliberate rescan want, and nothing
        else. Moving a source/compiled radio, saving a manifest or compiling one
        addon touches one file, and paying for it with a restart of the store, the
        terminal addon and everything else is both slow and visible - an addon
        holding a session loses it. So those callers name the addon they changed
        and this re-discovers only that id: the folder is read again, because that
        is cheap, but only the named record is replaced, unloaded and started.
        """
        addon_id = str(addon_id)
        active = self._active_tab()
        for addon in [item for item in self.addons if item.id == addon_id]:
            self.unload(addon, reason="reloaded")
        # scan() settles which of two same-id copies runs, and rebuilds the
        # shadowed ones, so a changed build mark is honoured here exactly as it
        # is across a full rescan.
        discovered = self.scan()
        retained = {item.id: item for item in self.addons if item.id != addon_id}
        # The folder's order is kept, so the Settings rows do not shuffle; every
        # other addon is put back object for object, with its process, its tabs
        # and its loaded flag still its own.
        self.addons = [retained.get(item.id, item) for item in discovered]
        gone = {item.id for item in discovered}
        self.addons += [item for item in retained.values()
                        if item.id not in gone]
        keeper = self.find(addon_id)
        if keeper is not None:
            self.apply_record(keeper)
            if keeper.enabled:
                self.load(keeper)
        self.refresh_rows()
        self._restore_window_tabs()
        self._reactivate_tab(active)

    def _active_tab(self) -> str:
        """The caption of the tab on show now, or "" when none is."""
        widget = getattr(self.win, "tabs", None)
        if widget is None:
            return ""
        index = widget.currentIndex()
        if index < 0:
            return ""
        reader = getattr(widget, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is None:
            return str(widget.tabText(index) or "")
        return str(tab_caption(bar, index) or "")

    def _reactivate_tab(self, caption: str) -> None:
        """Stand on the tab named ``caption`` again, when it is still there.

        A rescan takes every addon's tab down and puts them all back, and the bar
        can settle on any of them; a person who was looking at the store when an
        install reloaded the addons should still be looking at it afterwards.
        A caption that no longer has a tab (an addon that did not come back) is
        left alone rather than jumped to some other tab.
        """
        if not caption:
            return
        widget = getattr(self.win, "tabs", None)
        if widget is None:
            return
        try:
            at = self._tab_titles().index(caption)
        except ValueError:
            return
        if widget.currentIndex() != at:
            widget.setCurrentIndex(at)

    def reconcile(self) -> None:
        """Apply the enable/disable flags after a profile switch or import."""
        for addon in self.addons:
            record = self.record_for(addon.id)
            addon.ssh_enabled = bool(record.get("ssh_enabled", True))
            addon.sftp_enabled = bool(record.get("sftp_enabled", True))
            addon.popout_record = bool(record.get("popout", False))
            addon.git_token = bool(record.get("git_token", False))
            wanted = bool(record.get("enabled", False))
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
        # one it declared here. "none" is the fourth answer: it says the addon
        # never reaches a device at all, whatever its transports say.
        value = manifest.get("connection")
        if value is not None:
            text = str(value).strip().lower()
            if text in ("", "none"):
                addon.connection = "none"
            elif text in ("app", "main"):
                addon.connection = "app"
            elif text in ("own", "private"):
                addon.connection = "own"
            elif text == "both":
                addon.connection = "both"
        # "popout" says whether this addon's tabs live in a window of their own.
        # An addon that names it owns the answer: the app's force-popout switch
        # is then greyed, because a page built to float cannot be asked to sit in
        # a bar it was never drawn for.
        value = manifest.get("popout")
        if value is not None:
            text = str(value).strip().lower()
            if text in ("always", "window", "true"):
                addon.popout, addon.popout_declared = "always", True
            elif text in ("never", "tab", "false"):
                addon.popout, addon.popout_declared = "never", True
            elif text in ("ask", "optional", ""):
                # Names a key without choosing: the switch is the user's.
                addon.popout, addon.popout_declared = "", False

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
        if self.crash_halted(addon.id):
            # The breaker is on. The row carries the reason; the log does not,
            # because a rescan that walks every addon would else say it again.
            addon.loaded = False
            self.refresh_row(addon.id)
            return False
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
            for name in list(addon.settings_pages):
                try:
                    addon.api.settings_pages.remove(name)
                except Exception:
                    pass
        addon.tabs.clear()
        addon.settings_pages.clear()
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
        # Forget which transports this run reached for: the dot beside a switch
        # says whether the addon that is running now has used it, so a stopped
        # and restarted addon has to earn the green state again.
        addon.uses_ssh = addon.uses_sftp = False
        # Its tabs are home in the bar and nothing of the addon is left to show,
        # so the window it may have been standing in goes with them: a window is
        # only ever kept while its addon runs.
        core.popout.close_windows(self, addon)

    def mark_stopped(self, addon: Addon) -> None:
        """A process addon exited by itself: keep the row, say why."""
        addon.loaded = False
        addon.error = ""
        addon.notes = "the addon closed its own process"
        self._clean(addon)
        self.refresh_row(addon.id)

    # --- the crash breaker ---

    def _tally(self, addon_id: str) -> list[float]:
        """This addon's still-recent deaths, with the older ones dropped.

        Reading the list prunes it, so a count is only ever as old as
        :attr:`CRASH_WINDOW` and a fixed file is retried without anyone asking.
        """
        stamps = [stamp for stamp in self._crashes.get(addon_id, [])
                  if time.monotonic() - stamp < self.CRASH_WINDOW]
        if stamps:
            self._crashes[addon_id] = stamps
        else:
            self._crashes.pop(addon_id, None)
        return stamps

    def crashes(self, addon_id: str) -> int:
        """How many starts of this addon died on the way up just now."""
        return len(self._tally(str(addon_id)))

    def crash_halted(self, addon_id: str) -> bool:
        """Whether the app has stopped starting this addon by itself."""
        return len(self._tally(str(addon_id))) >= self.CRASH_LIMIT

    def clear_crashes(self, addon_id: str) -> None:
        """Forget the deaths: the addon was asked for by name, or has run."""
        self._crashes.pop(str(addon_id), None)

    def note_exit(self, addon: Addon, code: Any, seconds: float) -> None:
        """One process addon's ending, counted when it never got going.

        Called from the reader thread through the bus, so this is the single
        place a death is turned into a log line, a stopped row and - if it looks
        like a crash on the way up rather than a shutdown - a strike.
        """
        self.log(addon.id, f"the addon process ended (exit {code})",
                 warning=code != 0)
        self.mark_stopped(addon)
        if code == 0 and seconds >= self.CRASH_QUICK:
            # It ran and closed on its own terms; whatever it died of before
            # was the older file, so the count starts over.
            self.clear_crashes(addon.id)
            return
        stamps = self._tally(addon.id)
        stamps.append(time.monotonic())
        self._crashes[addon.id] = stamps
        if len(stamps) < self.CRASH_LIMIT:
            return
        addon.error = (f"not started again: {len(stamps)} runs ended within "
                       f"{int(self.CRASH_WINDOW)}s of beginning")
        addon.notes = ("it is failing as it comes up, so the app has stopped "
                       "restarting it; press Restart when the file is fixed")
        self.refresh_row(addon.id)
        self.log(addon.id, f"{addon.error} - {addon.notes}", warning=True)

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
            # Switching it on is a fresh ask for this addon, deaths and all.
            self.clear_crashes(addon_id)
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
        self._refresh_tab_dots()
        self.bus.post(lambda: self.refresh_connection_dots(addon_id))

    def set_transport_enabled(self, addon_id: str, transport: str,
                              on: bool) -> None:
        """One addon's own gate over SSH or SFTP.

        The app-wide switch in the Connection panel stays above this one: a
        transport is reachable only when both are on, and an addon riding the
        main connection still has to wait for it to be open.
        """
        if transport not in ("ssh", "sftp"):
            return
        addon = self.find(addon_id)
        if addon is None:
            return
        record = self.record_for(addon_id)
        record[f"{transport}_enabled"] = bool(on)
        setattr(addon, f"{transport}_enabled", bool(on))
        self.save_records()
        self.log(addon_id, f"{transport.upper()} "
                           + ("allowed for this addon" if on
                              else "switched off for this addon"))
        self._refresh_tab_dots()
        self.refresh_connection_dots(addon_id)
        # Tell every addon: an addon watching another one's gates, or showing a
        # connection state of its own, redraws from the same event.
        self.notify("transport_changed", {"addon": addon_id, "transport": transport,
                                          "scope": "addon", "enabled": bool(on)})

    def _refresh_tab_dots(self) -> None:
        """Let the dots beside the tabs follow a switch the user just moved."""
        sync = getattr(self.win, "sync_tab_transport_dots", None)
        if callable(sync):
            sync()

    # --- the window of its own ---

    def set_popout(self, addon_id: str, on: bool, force: bool = False) -> bool:
        """Move one addon's tabs out into a window, or back into the bar.

        The switch is the app's answer for an addon that said nothing about it,
        so an addon that declared a state - or one that came inside the program -
        is refused unless ``force`` says its own code is asking. Everything the
        addon built keeps running: only the widgets change hands.
        """
        addon = self.find(addon_id)
        if addon is None:
            return False
        if not force and not addon.popout_switchable:
            self.log(addon_id, "the popout switch is not this addon's to move: it "
                               + ("came with the program" if addon.is_bundled
                                  else f'declared "popout": "{addon.popout}"'), True)
            return False
        record = self.record_for(addon_id)
        # A forced move is the addon's own code asking for its window, not the
        # user's switch: the record only ever holds what the app may decide, so
        # an addon that declared a state keeps it out of the profile.
        if not force:
            record["popout"] = bool(on)
            addon.popout_record = bool(on)
        self.save_records()
        if on:
            core.popout.apply_popout(self, addon, force=force)
        else:
            core.popout.dock_back(self, addon)
        self.refresh_row(addon_id)
        return True

    def set_git_token(self, addon_id: str, on: bool) -> bool:
        """Whether this addon may read the profile's global git token."""
        addon = self.find(addon_id)
        if addon is None:
            return False
        record = self.record_for(addon_id)
        record["git_token"] = bool(on)
        addon.git_token = bool(on)
        self.save_records()
        self.refresh_row(addon_id)
        return True

    def show_addon_window(self, addon_id: str) -> bool:
        """Bring an addon's own window up, making it if its tabs are in the bar."""
        addon = self.find(addon_id)
        if addon is None:
            return False
        window = core.popout.window_for(self, addon, create=True)
        if window is None:
            return False
        for name in list(addon.tabs):
            if not window.has_page(name):
                core.popout.pop_out(self, addon, name)
        window.open()
        self.refresh_row(addon_id)
        return True

    def hide_addon_window(self, addon_id: str) -> bool:
        """Put an addon's tabs back in the bar and take its window down."""
        addon = self.find(addon_id)
        if addon is None:
            return False
        moved = core.popout.dock_back(self, addon)
        self.refresh_row(addon_id)
        return moved

    def restart(self, addon_id: str) -> None:
        addon = self.find(addon_id)
        if addon is None:
            return
        # Asked for by name, so the breaker is lifted whatever it was doing.
        self.clear_crashes(addon_id)
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
            window = self.windows.get(addon.id)
            for entry in (addon.tabs or {}).values():
                if not entry.get("app_icon") or entry.get("web"):
                    continue
                page = entry.get("page")
                index = self.win.tabs.indexOf(page)
                bar = self.win.tabs
                if index < 0 and window is not None:
                    index = window.tabs.indexOf(page)
                    bar = window.tabs
                if index < 0:
                    continue
                mark = build_icon(path, addon.home) if path else None
                bar.setTabIcon(index, mark or QIcon())
        # Marks came on or went off: let the bar re-measure the captions.
        reader = getattr(self.win.tabs, "tabBar", None)
        bar = reader() if callable(reader) else None
        if bar is not None and hasattr(bar, "refit"):
            bar.refit()

    # --- the Settings pages ---

    def build_settings_tabs(self, tabs: QTabWidget) -> None:
        """Give the addons their pages in the Settings tab bar.

        The **Installed addons** page made here holds the addons that came inside
        the program only; a page for each of them is made
        with its row in ``refresh_rows``, so an addon found by a later Rescan
        needs nothing else. Everything dropped in later is run from the store's
        Addon manager page, which the button below hands over to.
        """
        self.settings_tabs = tabs
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        saying = QLabel(
            "Only the addons that came inside the program are set out here - "
            "Berry, the addon store, the updater and the theme manager, each on a "
            "page of its own beside this one. Everything else is run from the "
            "Addon manager, the second page of the store's tab: an addon the store "
            "installed, one built on the Addon Dev tab or a file dropped into the "
            "folder below is switched, popped out into a window of its own, given "
            "a Desktop link or deleted from there. Each profile remembers its own "
            "answers for every addon, wherever it is managed from."
        )
        saying.setWordWrap(True)
        saying.setStyleSheet("color: #8f8f8f;")
        layout.addWidget(saying)

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
        manager_button = QPushButton("Open addon manager")
        manager_button.setObjectName("primaryAction")
        manager_button.setToolTip(
            "Start the addon store and go to the Addon manager page, where every "
            "addon that did not come with the program is switched on or off, moved "
            "into a window of its own, given a Desktop link or deleted. The store "
            "is what holds that page, so this starts it when it is switched off."
        )
        manager_button.clicked.connect(self.open_addon_manager)
        folder_row.addWidget(path_label, 1)
        folder_row.addWidget(open_button)
        folder_row.addWidget(config_button)
        folder_row.addWidget(rescan_button)
        folder_row.addWidget(manager_button)
        layout.addLayout(folder_row)

        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        layout.addLayout(self.body)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(self.hint)
        layout.addStretch(1)

        self.installed_scroll = QScrollArea()
        self.installed_scroll.setWidgetResizable(True)
        self.installed_scroll.setWidget(host)
        tabs.addTab(self.installed_scroll, "Installed addons")

    def owns_settings_page(self, addon: Addon) -> bool:
        """Whether this addon gets a Settings page of its own.

        Only an addon the program carries: ``is_bundled`` reads "came with the
        program", and in a source checkout that also covers anything dropped
        into the same folder, so the page follows the ids the build ships.
        Everything else - a store install, a program built on the Addon Dev tab,
        a file put in by hand - is listed and run from the store's Addon manager
        instead, and ``api.session.addons()`` says which of the two an addon
        belongs to so the pages cannot both show it, or neither.
        """
        if addon.origin:
            return False
        return bool(addon.builtin) or addon.id in SHIPPED_ADDON_IDS

    def _addon_tab_body(self, addon: Addon) -> QVBoxLayout | None:
        """One shipped addon's Settings page, made the first time its row is built.

        ``None`` for an addon the user put in or the store installed: it is run
        from the Addon manager on the store's tab rather than from anywhere in
        Settings, so it is not listed here at all.
        """
        if not self.owns_settings_page(addon) or self.settings_tabs is None:
            return None
        title = addon.name or addon.id
        entry = self.bundled_tabs.get(addon.id)
        if entry is None:
            host = QWidget()
            body = QVBoxLayout(host)
            body.setContentsMargins(12, 12, 12, 10)
            body.setSpacing(8)
            body.addStretch(1)
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(host)
            # The shipped pages come before the installed one, in the order the
            # addons were read; the installed page stays last.
            at = self.settings_tabs.indexOf(self.installed_scroll)
            self.settings_tabs.insertTab(max(at, 0), area, title)
            entry = {"scroll": area, "body": body}
            self.bundled_tabs[addon.id] = entry
        else:
            at = self.settings_tabs.indexOf(entry["scroll"])
            if at >= 0 and self.settings_tabs.tabText(at) != title:
                self.settings_tabs.setTabText(at, title)
        return entry["body"]

    def _forget_tabs(self, kept: set[str]) -> None:
        """Take away the pages of shipped addons that a rescan no longer finds."""
        if self.settings_tabs is None:
            return
        for identifier in [key for key in self.bundled_tabs if key not in kept]:
            entry = self.bundled_tabs.pop(identifier)
            at = self.settings_tabs.indexOf(entry["scroll"])
            if at >= 0:
                self.settings_tabs.removeTab(at)
            entry["scroll"].deleteLater()

    # --- connection indicators ---

    def make_transport_dots(self, addon_id: str, transport: str,
                            layout: QHBoxLayout) -> None:
        """The dot that speaks for one transport switch on an addon's row.

        The switch beside it already carries the name, so the dot says only the
        state: green while the addon can use that transport now, amber while
        every switch is on but no connection stands up, red while a switch
        refuses it.
        """
        dot = QLabel("\u25CF")
        dot.setObjectName("statusDotOffline")
        dot.setToolTip(f"{transport.upper()}: not connected")
        layout.addWidget(dot)
        self.dot_refs.setdefault(addon_id, {})[transport] = dot
        self._apply_dot_state(self.find(addon_id), transport, dot)

    def _live_transport_states(self) -> dict[str, bool]:
        """Whether the app's one main connection carries each transport now."""
        reader = getattr(self.win, "_live_transport_states", None)
        if callable(reader):
            return dict(reader())
        shared = getattr(self.win, "shared_conn", None)
        live = bool(shared.active()) if shared is not None else False
        live = live or bool(getattr(self.win, "main_connected", False))
        return {"ssh": live, "sftp": live}

    def transport_state(self, addon: Addon | None, transport: str) -> dict[str, str]:
        """What one transport's indicator says, for an addon or for the app.

        The gates are walked in the order every device call walks them - the
        master switch, that transport's app-wide switch, the addon's own switch,
        the transport on its row - and then the connection decides: an addon
        sharing the app's session can only work while that session is open,
        while an addon on a connection of its own brings it up itself, per call,
        so all the app can say is whether it may and whether it has.

        ``mode`` is ``"on"`` (usable right now), ``"ready"`` (every switch on,
        nothing connected yet) or ``"off"`` (a switch refuses it), and ``tip``
        says the whole story, including whose connection it rides.
        """
        shown = transport.upper()
        config = getattr(self.win, "app_config", {}) or {}
        master = getattr(self.win, "connections_enabled", None)
        if callable(master) and not master():
            return self._refused(f"{shown}: off - the master switch in the "
                                 "connection panel's header is off")
        if not bool(config.get(f"{transport}_enabled", True)):
            return self._refused(f"{shown}: off - {shown} is switched off on the "
                                 "Settings Connection page")
        if addon is not None:
            if not bool(getattr(addon, "enabled", True)):
                return self._refused(f"{shown}: off - this addon is switched off")
            if transport not in (getattr(addon, "requires", ()) or ()):
                return self._refused(f"{shown}: off - the addon's manifest does "
                                     "not declare it, so it cannot use it")
            if not addon.transport_allowed(transport):
                return self._refused(f"{shown}: off - its own switch, beside "
                                     "this dot, is off")
        live = bool(self._live_transport_states().get(transport))
        if addon is not None and addon.uses_own:
            source = ("its own connection, which it opens for every call; the "
                      "main connection is not involved")
            if bool(getattr(addon, f"uses_{transport}", False)):
                return {"mode": "on",
                        "tip": f"{shown}: it has used {source}"}
            return {"mode": "ready",
                    "tip": f"{shown}: it may use {source} - nothing has "
                           "been asked of it yet"}
        shared = "" if addon is None else ", which this addon shares"
        if live:
            return {"mode": "on",
                    "tip": f"{shown}: connected - the main connection{shared}"}
        return {"mode": "ready",
                "tip": f"{shown}: every switch is on, but the main connection is "
                       "closed - press Connect"}

    @staticmethod
    def _refused(tip: str) -> dict[str, str]:
        """The one answer every switched-off case shares."""
        return {"mode": "off", "tip": tip}

    def _apply_dot_state(self, addon: Addon | None, transport: str,
                         dot: QLabel) -> None:
        """Colour one dot from the shared answer and put the saying in its tip."""
        state = self.transport_state(addon, transport)
        transport_dot(dot, state["mode"])
        dot.setToolTip(state["tip"])

    def refresh_connection_dots(self, addon_id: str | None = None) -> None:
        """Recolour the dots on the addon rows, then redraw the status list.

        Each dot rides beside the switch it speaks for, so a row only carries
        dots for the transports its manifest declared, and they stay in view
        while the addon itself is folded away.
        """
        win = self.win
        identifiers = [addon_id] if addon_id else list(self.dot_refs)
        for identifier in identifiers:
            refs = self.dot_refs.get(identifier)
            if not refs:
                continue
            addon = self.find(identifier)
            for transport, dot in refs.items():
                self._apply_dot_state(addon, transport, dot)
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

    def open_addon_manager(self) -> bool:
        """Start the store if need be and put its Addon manager page on screen.

        Settings shows only the addons that came inside the program, so this is
        the one way to reach an installed addon's switches when the store itself
        has been switched off - which is why the button sits on the page it
        opens. The store is asked for its second sub-page by name through its own
        api, and a store that will not start is said so rather than left blank.
        """
        store = self.find(STORE_ADDON_ID)
        if store is None:
            self.log(STORE_ADDON_ID, "the addon store is not in the addons "
                                     "folder, so there is no Addon manager to "
                                     "open", True)
            return False
        if not store.loaded:
            self.set_enabled(STORE_ADDON_ID, True)
            store = self.find(STORE_ADDON_ID)
        if store is None or not store.loaded or store.api is None:
            self.log(STORE_ADDON_ID, "the addon store did not start, so there is "
                                     "no Addon manager to open", True)
            return False
        try:
            store.api.tabs.set_widget(self.STORE_TAB, self.STORE_SUBTABS,
                                      "current", self.STORE_MANAGER_PAGE)
            store.api.tabs.activate(self.STORE_TAB)
        except Exception as error:            # a store that cannot be shown
            self.log(STORE_ADDON_ID, f"the Addon manager page could not be "
                                     f"opened: {error}", True)
            return False
        return True

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

    def _scroll_of(self, widget: QWidget | None) -> QScrollArea | None:
        """The Settings page one widget scrolls with; each tab has its own."""
        while widget is not None and not isinstance(widget, QScrollArea):
            widget = widget.parentWidget()
        return widget

    def _keep_scroll(self) -> dict[int, int]:
        """Where every addon page stands, before a rebuild moves its rows."""
        saved: dict[int, int] = {}
        for holder in self.rows.values():
            area = self._scroll_of(holder["row"])
            if area is not None:
                saved[id(area)] = area.verticalScrollBar().value()
        return saved

    def _restore_scroll(self, saved: dict[int, int]) -> None:
        """Put each page back where its reader was, so a rebuild does not jump."""
        areas = [entry["scroll"] for entry in self.bundled_tabs.values()]
        if self.installed_scroll is not None:
            areas.append(self.installed_scroll)
        for area in areas:
            where = saved.get(id(area))
            if where is not None:
                area.verticalScrollBar().setValue(where)

    def refresh_rows(self) -> None:
        """Rebuild every addon page: one per shipped addon, the rest together."""
        if self.body is None:
            return
        saved = self._keep_scroll()
        carried = {addon_id: self._salvage_row(holder["extra"])
                   for addon_id, holder in self.rows.items()}
        for holder in self.rows.values():
            holder["row"].setParent(None)
            holder["row"].deleteLater()
        self.rows.clear()
        self.dot_refs.clear()

        installed = 0
        shipped: set[str] = set()
        for addon in self.addons:
            if not self.owns_settings_page(addon):
                # An addon that came from the store, from the Addon Dev tab or
                # from a file dropped in the folder is run from the store's Addon
                # manager rather than from here; its record is the same one either
                # way, so nothing about the addon changes - only where it is read.
                continue
            page = self._addon_tab_body(addon)
            target = self.body if page is None else page
            row = self._build_row(addon, carried.get(addon.id, []), target)
            if page is None:
                installed += 1
                target.addWidget(row)
            else:
                # A shipped addon's page keeps its stretch last, so its one row
                # is put in at the top rather than after it.
                shipped.add(addon.id)
                target.insertWidget(0, row)
        self._forget_tabs(shipped)
        self.hint.setVisible(not installed)
        if not installed:
            self.hint.setText(
                "Nothing is listed on this page: every addon that did not come "
                f"with the program is run from the Addon manager, on the second "
                f"page of the store's tab - press Open addon manager above. "
                f"Programs and files still go in {ADDONS_DIR}, and Rescan picks "
                "them up there."
            )
        self._restore_scroll(saved)

    def refresh_row(self, addon_id: str) -> None:
        """Rebuild one addon's row in place, keeping its page scrolled where it was."""
        if self.body is None:
            return
        holder = self.rows.get(addon_id)
        addon = self.find(addon_id)
        if holder is None or addon is None:
            self.refresh_rows()
            return
        area = self._scroll_of(holder["row"])
        where = area.verticalScrollBar().value() if area is not None else 0
        carried = self._salvage_row(holder["extra"])
        layout = holder.get("layout") or (self._addon_tab_body(addon) or self.body)
        index = layout.indexOf(holder["row"])
        holder["row"].setParent(None)
        holder["row"].deleteLater()
        self.rows.pop(addon_id, None)
        layout.insertWidget(max(index, 0), self._build_row(addon, carried, layout))
        if area is not None:
            area.verticalScrollBar().setValue(where)

    @staticmethod
    def _chip(text: str, tip: str) -> QLabel:
        """One grey word on a row, with the whole saying in its tool tip."""
        chip = QLabel(text)
        chip.setStyleSheet("color: #8f8f8f;")
        chip.setToolTip(tip)
        return chip

    def _connection_chip(self, addon: Addon) -> QWidget | None:
        """The one control on a row that says whose connection the addon rides.

        Only an addon that declared ``"both"`` gets a switch: for every other
        kind the manifest has already decided, and the row says so in words
        rather than offering a choice the app would then refuse. A shipped addon
        always rides the app's session, whatever its source declares, and an
        addon that named no transport reaches no device at all - both answers
        are shown too, so a row never leaves the connection to be guessed.
        """
        kind = connection_choice(addon.requires, addon.is_bundled,
                                 addon.connection)
        if kind == "none":
            return self._chip(
                "no device connection",
                "This addon declared no transport and no connection, so it never "
                "reaches a device: what it does, it does on this machine and over "
                "the network.")
        if kind == "both":
            # Declared "both": only here is the user's to choose between the
            # app's session and a fresh connection per call.
            own = ToggleSwitch("own connection")
            own.setToolTip(
                "Off: the addon shares the app's one main connection, and can"
                " only use it while that connection is open."
                "\nOn: the addon opens a connection of its own for each call, "
                "so it works whether or not the main connection is up, and "
                "through api.device.bind() it can even reach a different "
                "device."
                "\nEither way the addon's own switch and the switch in the "
                "Connection panel header gate every connection, this one "
                "included."
            )
            own.blockSignals(True)
            own.setChecked(bool(addon.own_connection))
            own.blockSignals(False)
            own.toggled.connect(lambda on, aid=addon.id: self.set_own_connection(aid, on))
            return own
        if kind == "own":
            return self._chip(
                "own connection",
                'This addon declared "connection": "own" in its manifest,\n'
                "so it always opens its own connection, main connection up or\n"
                "not; it cannot use the app's session.")
        return self._chip(
            "main connection",
            'This addon declared "connection": "app" in its manifest,\n'
            "so it only ever uses the app's one main connection, and can\n"
            "use it no earlier than the connection is open."
            + ("" if not addon.is_bundled else
               "\nIt ships with the program, which always runs on that "
               "connection."))

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
        head.addWidget(self._connection_chip(addon))
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

        if addon.requires:
            # The addon's own gates, one per transport it declared, each with the
            # dot that says what that transport is doing. Both this switch and
            # the app-wide one have to be on; an addon sharing the main
            # connection then waits for that connection as well, while one on a
            # connection of its own brings it up itself.
            gates = QHBoxLayout()
            gates.setContentsMargins(20, 0, 0, 0)
            gates.setSpacing(6)
            for transport in ("ssh", "sftp"):
                if transport not in addon.requires:
                    continue
                toggle = ToggleSwitch(transport.upper())
                toggle.setToolTip(
                    f"Off: this addon is refused every {transport.upper()} job, "
                    "even with the main connection open and "
                    f"{transport.upper()} on in the Connection panel."
                    "\nOn: it may use "
                    f"{transport.upper()}, along with that app-wide switch and "
                    "this addon's own switch above."
                )
                toggle.blockSignals(True)
                toggle.setChecked(bool(addon.transport_allowed(transport)))
                toggle.blockSignals(False)
                toggle.toggled.connect(
                    lambda on, aid=addon.id, part=transport:
                    self.set_transport_enabled(aid, part, on))
                gates.addWidget(toggle)
                # The dot belongs to the switch on its left, so the row says the
                # name once and the state beside it.
                self.make_transport_dots(addon.id, transport, gates)
                gates.addSpacing(6)
            saying = QLabel("connections this addon may use")
            saying.setStyleSheet("color: #8f8f8f;")
            saying.setToolTip(
                "Green: it can use that transport right now.\n"
                "Amber: every switch is on, but nothing stands connected - an "
                "addon on the main connection waits for Connect, one on its own "
                "has simply not been asked yet.\n"
                "Red: a switch refuses it, and the tool tip says which."
            )
            gates.addWidget(saying)
            gates.addStretch(1)
            gate_wrap = QWidget()
            gate_wrap.setLayout(gates)
            column.addWidget(gate_wrap)

        if self.owns_settings_page(addon):
            cfg = addon_config_path(addon.id)
            try:
                shown = cfg.relative_to(BASE_DIR).as_posix()
            except ValueError:
                shown = str(cfg)
            shipped = QLabel("Shipped with the program: switch it on or off on this "
                             f"page. Its own settings are in {shown}, and the "
                             "switches under its name say which connections it "
                             "may use.")
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

        # The addon's page in the Settings bar is the category now, so the row
        # needs no box of its own around it.
        self.rows[addon.id] = {"row": entry, "extra": extra,
                               "layout": target_layout if target_layout is not None else self.body}
        return entry

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
        """Give an addon its own controls inside its row on the Settings
        addons page - which is only drawn for an addon that came with the
        program, so an installed addon keeps its controls on its own tab."""
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
        running = self.loaded()
        for number, addon in enumerate(running, start=1):
            core.ui.addon_step(number, len(running),
                                      addon.name or addon.id,
                                      verb="Starting", band=core.ui.ADDON_START_BAND)
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
        # On the control, never on the panel; see core.ui.tip_on_title.
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


###############################################################################
# was manager_addondev.py
# The Addon Dev tab: what an addon is, what it does, and how to make one.
#
# The tab is switched like any other addon setting, on the store's Addon manager
# page. In a source checkout an addon
# is a ``.py`` file there to be read, edited, tested and compiled. In a packaged
# program the same panels still list every addon, show its log lines and its
# processes, and open the sources that ship inside it - but a file that came with
# the build cannot be written back. Building and testing an addon run in a Python
# of their own and import the app's protocol modules from disk, which is why a
# build carries a plain copy of them; a program built without that copy says so
# rather than start something that cannot finish.
#
# Four panes live here, each one a way of looking at the addon picked above:
#
# Details
#     The switches for this one addon (enable, restart, jump to its tab, open its
#     source) and the manifest - name, version, author, description, api - edited
#     in a form and written back where it belongs: into the ``ADDON = {`` literal
#     of the addon's own file when it has one, otherwise into the ``<id>.json``
#     manifest beside it. Below that, a plain-text editor for
#     ``addons/_data/<id>/config.json``.
#
# Build & test
#     PyInstaller and the test script, side by side, since one leads to the
#     other: ``addons/_tests/test_<id>.py`` is written from a template and run as
#     its own process using :mod:`addon_client`'s recording stub, so an addon can
#     be checked without a device and without opening a window; a build is started
#     for one addon with a generated launcher, staging in ``addons/_build/<id>``
#     and putting the result at ``addons/<id>.exe`` (or an extension-less
#     executable on Linux) so the runtime finds it as the same addon. The choice
#     that only matters once an addon has been compiled - run the source or run
#     the program built from it - is at the top of this pane too.
#
# Docs & log
#     The API reference and the guides above, the addon's own lines out of the
#     app log below. Every method, property and one-line summary in the reference
#     is read off the protocol module as it runs, so the panel cannot describe an
#     API the app no longer has.
#
# Processes
#     What is running: the child process of every process addon (pid, state, exit
#     code, command, uptime) and everything the app created for the in-process
#     ones - tabs, timers, menu entries, event listeners and threads.
#
# Nothing here can take the window down: the file writes are guarded, PyInstaller
# and the test script run in their own processes, and every refresh reads the
# runtime through ``AddonManager.describe``.
#
###############################################################################


import ast
import dis
import inspect
from string import Template
import struct
import tempfile

from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QHideEvent,
                         QPixmap, QShowEvent)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from addon_common import (
    AddonError,
    addon_config_path,
    build_mark,
    config_name,
    default_icon_path,
    find_icon,
    jsonable,
    kill_process_tree,
    popen_tree,
    read_config_json,
    release_process_tree,
    terminate_process_tree,
    write_build_mark,
)
from core.const import (
    ADDON_BUILD_DIR,
    ADDON_EXECUTABLE_SUFFIX,
    ADDON_TEST_DIR,
    ADDONS_DIR,
    ASSETS_DIR,
    BASE_DIR,
    HARNESS_DIR,
    HARNESS_FILES,
    ICON_FILES,
    IS_COMPILED,
    LOG_FILE,
    PROJECT_GITHUB_URL,
    find_asset,
    zoom_size,
)

#: How wide the branches of the Docs tree are drawn, and how far their lines are
#: pushed in: the two numbers that make the list read as a folder tree.
DOCS_INDENT = 16
DOCS_MIN_W = 236

#: How often the tables ask the runtime what it knows.
REFRESH_MS = 2000

#: One line per row, and the rows the index shows before it scrolls itself.
ROW_H = 30
MAX_ROWS = 12

#: The addon's own picture, beside its name in the index.
LIST_ICON = 22

#: Floors for the panes; the tab scrolls rather than clipping text.
PANES_MIN_H = 380
VIEW_MIN_H = 150

#: State words per row; Qt's darkGreen and gray vanish on a dark background.
STATE_COLORS = {
    "running": "#23d18b",
    "loaded": "#23d18b",
    "closed": "#8f8f8f",
    "off": "#8f8f8f",
    "failed": "#ff6b6b",
}

#: The keys the Details form owns; the rest of a manifest file stays put.
DETAIL_KEYS = ("name", "version", "author", "description", "notes")

#: What the panels say about an addon that came with the program itself.
SHIPPED_NOTE = ("Shipped inside the program. Its source is shown as it is, and "
                "it can only be switched on and off in Settings.")

_MONO = ("Consolas", 10)


def monospace(view: QPlainTextEdit) -> QPlainTextEdit:
    font = QFont(_MONO[0], _MONO[1])
    font.setStyleHint(QFont.StyleHint.Monospace)
    view.setFont(font)
    # An output view keeps its lines; the tab scrolls instead of crushing it.
    view.setMinimumHeight(VIEW_MIN_H)
    return view


def note_label(label: QLabel, color: str = "#8f8f8f") -> QLabel:
    """A grey note under a control, holding the height its wrapped text needs."""
    label.setStyleSheet(f"color: {color};")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


def module_name(source: Path) -> str:
    """The import name of an addon file: dots and dashes cannot be imported."""
    return re.sub(r"[^A-Za-z0-9_]", "_", source.stem) or "addon"


def _display_path(value: Any) -> str:
    """A config path shown relative to the program, or whole if it is outside."""
    path = Path(value)
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def human_size(size: Any) -> str:
    text = float(size or 0)
    for unit in ("B", "KB", "MB"):
        if text < 1024:
            return f"{text:.0f} {unit}" if unit == "B" else f"{text:.1f} {unit}"
        text /= 1024.0
    return f"{text:.1f} MB"


def duration(seconds: Any) -> str:
    if seconds is None:
        return ""
    value = max(0, int(float(seconds)))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m {value % 60}s"
    return f"{value // 3600}h {(value % 3600) // 60}m"


def make_table(headers: list[str], stretch: int | None = None,
               widths: dict | None = None) -> QTableWidget:
    """A list the way this tab draws all of them: whole rows, nothing typed in.

    The columns are laid out by the engine an addon's table is built with, so a
    list here behaves like every list there: each column can be dragged, the one
    named by `stretch` takes the room the others leave over instead of a Qt
    stretch that could not be moved, and a column pinned in `widths` starts at
    that width. Whatever is not pinned starts as wide as its own heading reads.
    """
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    pinned = [max(0, int((widths or {}).get(column) or 0))
              for column in range(len(headers))]
    fit_table_columns(table, [] if stretch is None else [int(stretch)], pinned)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_H)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setShowGrid(False)
    # Wide with the window, no taller than its rows; fit_rows() settles that.
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return table


def fit_rows(table: QTableWidget, cap: int = MAX_ROWS) -> None:
    """The exact height for the rows a table holds, up to ``cap``."""
    rows = min(max(table.rowCount(), 1), cap)
    head = table.horizontalHeader().sizeHint().height()
    edge = 2 * (table.frameWidth() + 1)
    height = head + rows * ROW_H + edge
    if table.minimumHeight() != height:
        table.setFixedHeight(height)


def state_color(state: str) -> QBrush:
    """The brush for a word in the state column."""
    return QBrush(QColor(STATE_COLORS.get(str(state), "#e0e0e0")))


#: The pictures already read, by file: the index is redrawn as often as it can
#: change, and decoding the same PNG every two seconds buys nothing.
_PICTURE_CACHE: dict[str, QIcon] = {}


#: The addon store's id, whose own data folder keeps a copy of every picture it
#: has downloaded for its catalog - the only copy of an installed addon's artwork
#: on a machine where that addon arrived as a program.
STORE_ADDON_ID = "addon_store"

#: Endings an addon's picture may reasonably have been published with.
PICTURE_SUFFIXES = (".png", ".ico", ".icns", ".svg", ".jpg", ".jpeg", ".webp")


def _picture_names(row: dict) -> list[str]:
    """Names besides the standard one this addon's picture may stand under.

    Its manifest may declare a picture of its own, and the store's catalog cache
    files one under the bare id with the ending it was published with - both are
    worth a try before an addon is shown the default mark.
    """
    names: list[str] = []
    declared = str((row.get("manifest") or {}).get("icon") or "").strip()
    if declared:
        names.append(declared)
    plain = config_name(str(row.get("id") or ""))
    names += [f"{plain}{suffix}" for suffix in PICTURE_SUFFIXES]
    return names


def addon_picture(row: dict) -> str:
    """The file this addon's picture stands in, or nothing when it brought none.

    An addon dropped in the folder keeps its artwork in an ``assets`` folder of its
    own; one that came with the program keeps it in the program's ``assets``, which
    is also where a packaged build packs the artwork of every addon shipped on the
    machine. A program the store installed has neither: its pictures went inside
    the build, where the app cannot read them. So its own ``addons/_data/<id>``
    folder is searched as well - the store leaves the addon's picture there when it
    installs it - and the copy the store downloaded for its own catalog is the last
    place of the three. What answers to none of them is shown the default rather
    than somebody else's picture.
    """
    addon_id = str(row.get("id") or "").strip()
    if not addon_id:
        return ""
    extra = _picture_names(row)
    folders = []
    home = str(row.get("home") or "").strip()
    if home:
        folders.append(Path(home) / "assets")
    folders.append(ASSETS_DIR)
    data = str(row.get("data") or "").strip() or str(addon_data_dir(addon_id))
    folders += [Path(data), Path(data) / "assets", Path(data) / "icons"]
    folders.append(ADDON_DATA_DIR / STORE_ADDON_ID / "icons")
    for folder in folders:
        found = find_icon(folder, addon_id, *extra)
        if found:
            return found
    return default_icon_path(ASSETS_DIR)


def addon_icon(row: dict) -> QIcon:
    """The mark for one row of the index, made once per file and remembered."""
    path = addon_picture(row)
    if not path:
        return QIcon()
    icon = _PICTURE_CACHE.get(path)
    if icon is None:
        icon = QIcon(path)
        _PICTURE_CACHE[path] = icon
    return icon


# --- reading and writing a manifest ---

_ADDON_LITERAL = re.compile(r"^[ \t]*ADDON[ \t]*=[ \t]*\{", re.MULTILINE)


def has_addon_literal(text: str) -> bool:
    """Whether this source spells its manifest as an ``ADDON = {`` dict."""
    return bool(_ADDON_LITERAL.search(text or ""))


def manifest_target(addon: Any) -> Path:
    """The file the Details form writes: the addon itself, or a sidecar json."""
    if addon.kind == "python":
        try:
            text = addon.source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if has_addon_literal(text):
            return addon.source
    return addon.home / f"{addon.id}.json"


def manifest_values(addon: Any) -> dict:
    """What the Details form starts from; the file on disk wins."""
    values = {key: str(getattr(addon, key, "") or "") for key in DETAIL_KEYS}
    values["api"] = str(addon.manifest.get("api") or "")
    stored = file_manifest(addon)
    for key in DETAIL_KEYS:
        text = str(stored.get(key) or "").strip()
        if text:
            values[key] = text
    api = str(stored.get("api") or "").strip()
    if api:
        values["api"] = api
    return values


def format_literal(values: dict) -> str:
    """The ``ADDON = {...}`` block, one key per line; unowned keys ride along."""
    owned = DETAIL_KEYS + ("api",)
    lines = ["ADDON = {"]
    for key in owned + tuple(item for item in values if item not in owned):
        value = values.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "api":
            try:
                lines.append(f'    "api": {int(value)},')
            except (TypeError, ValueError):
                lines.append(f'    "api": {value!r},')
            continue
        lines.append(f'    "{key}": {value!r},')
    lines.append("}")
    return "\n".join(lines)


def _literal_span(text: str) -> tuple[int, int] | None:
    """Start and end of the ``ADDON = {...}`` assignment, braces matched."""
    match = _ADDON_LITERAL.search(text)
    if match is None:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    return None


def replace_addon_literal(text: str, values: dict) -> str | None:
    """Swap the ``ADDON`` dict for the edited one, keeping everything else."""
    span = _literal_span(text)
    if span is None:
        return None
    start, stop = span
    return text[:start] + format_literal(values) + text[stop:]


def literal_values(text: str) -> dict:
    """The ``ADDON = {...}`` dict read back, without importing the addon."""
    span = _literal_span(text or "")
    if span is None:
        return {}
    body = text[span[0]:span[1]].split("=", 1)[1].strip()
    try:
        data = ast.literal_eval(body)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def file_manifest(addon: Any) -> dict:
    """What the addon's own files say about it, whether or not it is loaded."""
    target = manifest_target(addon)
    if target == addon.source:
        try:
            return literal_values(target.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return {}
    return read_config_json(target)


#: The names an addon may keep its settings form, and its starting values, under.
_FIELDS_NAMES = ("FIELDS", "CONFIG_FIELDS")
_DEFAULTS_NAMES = ("DEFAULTS", "CONFIG_DEFAULTS")


def _simple_value(node: ast.AST, known: dict) -> Any:
    """One value as it is written in the file, a name the file gives one included.

    An addon may write ``"maximum": 50000`` or ``"max_rows": MAX_ROWS``; both are
    plain. Anything that only has a value once the addon runs - a comprehension,
    a call - is not read, and says so rather than importing anything.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in known:
            return known[node.id]
        raise ValueError(f"{node.id} is not given a plain value in the file")
    if isinstance(node, ast.List):
        return [_simple_value(item, known) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_simple_value(item, known) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_simple_value(item, known) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {_simple_value(key, known): _simple_value(value, known)
                for key, value in zip(node.keys, node.values) if key is not None}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _simple_value(node.operand, known)
        return -value if isinstance(node.op, ast.USub) else +value
    raise ValueError("this is not a value written out in the file")


def _top_assigns(tree: ast.AST) -> list:
    """The ``NAME = ...`` lines at the top of a parsed file, in the order they sit."""
    return [node for node in getattr(tree, "body", [])
            if isinstance(node, ast.Assign)]


def _assigned(tree: ast.AST, names: tuple) -> ast.AST | None:
    """What the first of ``names`` is given at the top of the file, as a node."""
    for node in _top_assigns(tree):
        if any(isinstance(target, ast.Name) and target.id in names
               for target in node.targets):
            return node.value
    return None


def _names_written(tree: ast.AST) -> dict:
    """Every name the file gives a plain value, in case a form reads one of them."""
    known: dict[str, Any] = {}
    for node in _top_assigns(tree):
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                known[target.id] = _simple_value(node.value, known)
            except (ValueError, TypeError):
                continue          # a name only worth something once the addon runs
    return known


def _read_entry(keys: list, values: list, known: dict) -> dict:
    """One dict of a form or a defaults map, with every key it can be given now."""
    entry: dict[str, Any] = {}
    for one, two in zip(keys, values):
        if not isinstance(one, ast.Constant) or not isinstance(one.value, str):
            continue
        try:
            entry[one.value] = _simple_value(two, known)
        except (ValueError, TypeError):
            continue          # this one key waits for the addon itself to say it
    return entry


def _read_form(node: ast.AST | None, known: dict) -> list[dict]:
    """The field dicts an addon declares, one for each entry that names its key.

    A field whose ``choices`` only exists once the addon runs is no reason to lose
    the field: its label, its kind and its hint are read all the same, and the
    addon fills in the rest when it is switched on.
    """
    if not isinstance(node, ast.List):
        return []
    return [entry for entry in (_read_entry(item.keys, item.values, known)
                                for item in node.elts if isinstance(item, ast.Dict))
            if entry.get("key")]


def _read_map(node: ast.AST | None, known: dict) -> dict:
    """The defaults an addon declares, dropping only what has no plain value."""
    if not isinstance(node, ast.Dict):
        return {}
    return _read_entry(node.keys, node.values, known)


def addon_metadata(source: Path) -> dict:
    """What an addon's source says about itself: its manifest and its settings.

    A program built from that source cannot answer either until it runs, and the
    app needs both before then: ``requires`` and ``connection`` are what let
    ``api.device`` use a transport or its session at all, and the declared
    fields are the form Settings shows. So the
    store reads them out of the file, with no import and no Qt, and keeps them
    beside the program and in the addon's own ``_data`` folder.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return {"manifest": {}, "fields": [], "defaults": {}}
    known = _names_written(tree)
    return {
        "manifest": literal_values(text),
        "fields": _read_form(_assigned(tree, _FIELDS_NAMES), known),
        "defaults": _read_map(_assigned(tree, _DEFAULTS_NAMES), known),
    }


def write_details(addon: Any, values: dict) -> Path:
    """Save the edited manifest where it is read from. Raises on failure."""
    if getattr(addon, "builtin", False):
        raise AddonError(SHIPPED_NOTE)
    clean = {key: str(values.get(key) or "").strip() for key in DETAIL_KEYS}
    api = str(values.get("api") or "").strip()
    target = manifest_target(addon)
    if target == addon.source:
        text = target.read_text(encoding="utf-8")
        edited = {**literal_values(text), **clean, "api": api}
        updated = replace_addon_literal(text, edited)
        if updated is None:
            raise AddonError(f"{target.name} has no ADDON dict to edit")
        _write_text(target, updated)
        return target
    data: dict = {}
    if target.is_file():
        try:
            stored = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data = stored
        except (OSError, ValueError):
            data = {}
    data.update({key: value for key, value in clean.items() if value})
    if api:
        try:
            data["api"] = int(api)
        except (TypeError, ValueError):
            data["api"] = api
    else:
        data.pop("api", None)
    _write_text(target, json.dumps(jsonable(data), indent=2, ensure_ascii=False) + "\n")
    return target


def _write_text(target: Path, text: str) -> None:
    """Write a file the user may have open: same name, one temp hop."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


# --- what the tab writes: an addon, its test, its launcher ---

_ADDON_SOURCE = Template('''#!/usr/bin/env python3
"""$title

$description

Written by the Addon Dev tab. Everything the addon needs from the app comes
through ``api``, and nothing here touches Qt, so the same file runs imported
into Berry Manager and compiled into a program of its own: the tab is built
from a declarative spec, and what its controls do is reported back through
``on_widget``.

Every call used below has a page of its own in Addon Dev > Docs, read off the
app as it runs. The four worth a new author first are ``api.tabs.add_tab``,
``api.settings_pages.add``, ``api.config.declare`` and ``api.events.on``.
"""


from typing import Any

from addon_common import AddonError, config_get

ADDON = {
    "name": "$name",
    "version": "$version",
    "author": "$author",
    "description": "$description",
    # The transports api.device may use; leave the key out if never. Each one
    # also gets a switch of its own on this addon's row in the Addon manager,
    # and both the app-wide and the addon's own have to be on.
    "requires": ["ssh", "sftp"],
    # Whose connection the device calls use: "app" (the app's one main SSH+SFTP
    # session, which the user opens by button or at startup), "own" (a fresh
    # connection per call), "both" (the user chooses on this addon's Settings
    # row) or "none" - which says no more than leaving "requires" out above.
    "connection": "app",
    # A picture called <id>icon.png in the assets folder beside this file - in
    # assets/<id>/, or in the program's own assets - is found on
    # its own, both for the tab's mark and for its card in the addon store, so
    # only a file named differently needs this key.
    # "icon": "oldname.png",
    "api": 1,
}

#: The tab this addon adds, the page it adds to Settings, and what it has said.
TAB = "$tab"
PAGE = "$id-settings"
HISTORY: list[str] = []

#: The settings the user can change on this addon's Addon manager row, and their
#: defaults.
#: A key with a dot reads and writes inside a section of the config file.
FIELDS = [
    {"key": "greeting", "label": "Text to show", "kind": "text",
     "hint": "What the button on the tab prints", "placeholder": "Hello"},
    {"key": "lines", "label": "Lines to remember", "kind": "number",
     "hint": "How much of what it said the tab keeps on screen"},
    {"key": "show_page", "label": "Add a Settings page", "kind": "bool",
     "hint": "A page of this addon's own, outside the installed addons"},
]
DEFAULTS = {"greeting": "Hello from $id", "lines": 12, "show_page": True}

#: Set by init(); the app hands the addon its API there.
api = None


def value(key: str, fallback: Any = None) -> Any:
    """One config value, read the same way everywhere in this file."""
    return config_get(api.config.all(), key, DEFAULTS.get(key, fallback))


def remembered() -> int:
    """How many lines the history keeps; a hand-edited file can say anything."""
    try:
        return max(1, int(float(value("lines"))))
    except (TypeError, ValueError):
        return max(1, int(DEFAULTS["lines"]))


def show(identifier: str, prop: str, what: Any) -> None:
    """Write one control on the tab, thinking nothing of it when it is gone."""
    try:
        api.tabs.set_widget(TAB, identifier, prop, what)
    except AddonError:
        pass


def say(text: str) -> None:
    """One line on the tab, one in its history and one in the app log."""
    if api is None:
        return
    text = str(text)
    HISTORY.append(text)
    del HISTORY[:-remembered()]
    show("status", "text", text)
    show("history", "items", list(HISTORY))
    api.log(text)


def greeting() -> str:
    """What to say: the line on the tab first, then the config file."""
    text = ""
    if api is not None:
        try:
            text = str(api.tabs.widget_value(TAB, "greeting") or "").strip()
        except AddonError:
            text = ""
    return text or str(value("greeting", DEFAULTS["greeting"]))


def pressed() -> None:
    """The tab's button: read the form and report what it said."""
    if api is None:
        return
    say(f"you asked me to say: {greeting()}")


def about() -> None:
    """The Settings page's button: a dialog out of what the addon knows."""
    if api is None:
        return
    lines = [
        "$description",
        "",
        f"greeting: {greeting()}",
        f"starts so far: {api.settings.get('runs', 0)}",
        f"Berry Manager: {api.session.version()}",
        f"config file: {api.config.path()}",
    ]
    # chr(10) rather than a backslash: this file is written out of a template.
    api.ui.information("$name", chr(10).join(lines))


def build_tab(values: dict) -> None:
    """A spec, not widgets: a process addon can only describe what it wants.

    Two things a tab can ask for that this one does not: ``pinned=True`` with
    ``icon_only=True`` holds it in the short run beside Settings, showing the
    mark and no caption - and a mark has nothing but its tooltip left to say who
    it belongs to, which is why the app fills that in with this addon's name -
    while a second ``add_tab`` names a child tab, which
    the app puts to the left of the tab that made it.
    """
    api.tabs.add_tab(TAB, spec={
        "kind": "column",
        "children": [
            {"kind": "label", "text": "$title", "font_size": 12},
            {"kind": "label", "id": "status", "text": "Ready.", "word_wrap": True,
             "style": "color: #8f8f8f;"},
            {"kind": "line", "id": "greeting",
             "placeholder": str(config_get(values, "greeting", DEFAULTS["greeting"]))},
            {"kind": "button", "id": "press", "text": "Say it", "on": ["clicked"]},
            {"kind": "label", "text": "what it said, newest last", "font_size": 8,
             "style": "color: #7a7a7a;"},
            {"kind": "list", "id": "history", "items": list(HISTORY)},
            {"kind": "spacer"},
        ],
    })
    # Three other shapes, each one a call away rather than a rewrite:
    #
    #   api.tabs.add_tab(TAB, spec={...}, pinned=True, icon_only=True)
    #       holds the tab in the short run beside Settings: its mark, no caption
    #   api.tabs.add_web_tab(TAB + ' page', url='http://192.168.1.1/')
    #       shows a page the device serves, with the app's own address bar; give
    #       ip / port / path instead of a url to follow the active profile
    #   api.tabs.add_tab('Child', spec={...})
    #       opens a second tab, which the app places to the left of this one


def build_page(values: dict) -> None:
    """A page of the app's own Settings window, outside Installed addons.

    ``add`` replaces whatever holds the name, so building the page again after
    a config change is safe. Its controls arrive at ``on_widget`` with ``tab``
    set to :data:`PAGE`, which is how one handler serves both of them.
    """
    if not bool(config_get(values, "show_page", DEFAULTS["show_page"])):
        api.settings_pages.remove(PAGE)
        return
    api.settings_pages.add(PAGE, title="$name", spec={
        "kind": "form",
        "children": [
            {"kind": "label", "label": "About", "text": "$description",
             "word_wrap": True},
            {"kind": "check", "id": "show_page", "label": "", "text":
             "Keep this page in Settings", "checked": True},
            {"kind": "button", "id": "about", "label": "", "text": "About $name",
             "on": ["clicked"]},
        ],
    })


def init(plugin_api: Any) -> None:
    """The entry point: declare the config, then build what the addon shows."""
    global api
    api = plugin_api
    values = api.config.declare(FIELDS, DEFAULTS,
                                title="$title - addons/_data/$id/config.json")
    HISTORY.clear()
    build_tab(values)
    build_page(values)
    api.ui.menu_item("$menu", focus, menu="Tools")
    api.events.on("ssh_connected", on_connected)
    # Work that paces itself: api.schedule.every(30, tick) is called on the GUI
    # thread, and the app stops the timer when the addon is switched off.
    # News worth interrupting someone for, even with this tab closed:
    # api.ui.popup("$name", "the run finished") - the status line, and a
    # notification from the app's tray icon while Settings allows them.
    api.log("ready")


def start() -> None:
    """Called once at startup, and whenever the addon is switched on."""
    if api is None:
        return
    runs = int(api.settings.get("runs", 0) or 0) + 1
    api.settings.set("runs", runs)          # the profile's own copy, per profile
    say(f"$title is running (start no. {runs})")


def shutdown() -> None:
    """Switched off: drop the API, so nothing here asks the app for anything."""
    global api
    api = None


def focus() -> None:
    """The Tools menu entry."""
    if api is not None:
        api.tabs.activate(TAB)


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """What a spec-built control reported. Works in both kinds of addon."""
    if api is None or event not in ("clicked", "changed"):
        return
    if identifier == "press" and tab == TAB:
        pressed()
    elif identifier == "about":
        about()
    elif identifier == "show_page" and event == "changed":
        # The switch writes its own key; on_config then adds or drops the page.
        api.config.set("show_page", bool(payload.get("checked")))


def on_connected(payload: dict) -> None:
    """The device came up."""
    if api is not None:
        say("the device is reachable")


def on_config(payload: dict) -> None:
    """Called when addons/_data/$id/config.json changed: read it again."""
    if api is None:
        return
    values = api.config.all()
    build_tab(values)
    build_page(values)
    say("the config file was read again")


def on_event(name: str, payload: dict) -> None:
    """Every event the app broadcasts arrives here as well."""
    if api is not None and name == "operation_done":
        say('the app finished a ' + str(payload.get("operation", "job")))


def on_message(sender: str, data: Any) -> None:
    """Another addon wrote here; api.peers.list() says who answers a send."""
    if api is not None:
        say(f"{sender} sent {data!r}")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
''')

_TEST_SOURCE = Template('''#!/usr/bin/env python3
"""A test for addons/$id.py, written by the Addon Dev tab.

It runs the addon with the recording stub from :mod:`addon_client` instead of
the app: nothing is opened on screen, nothing is sent to a device, and every
call the addon makes is written down. So the test checks what the addon *asked
for*, which is the part an addon author owns.

Four questions are worth asking of any addon, and the checks below are grouped
by them:

- does ``init`` build what the addon promises, and name its controls?
- does a value the user set reach the code that uses it?
- does every button do the thing it sits there for?
- and does the addon go quiet once ``shutdown`` has taken the API back?

What the app answers is the test's to decide: ``api.answer("device.run", {"code": 1})``
makes a command look as if it failed, and ``api.asked("tabs.add_tab", "spec")``
reads back the spec the addon handed the app.

Run it from the Build & test pane, or on the command line::

    python addons/_tests/test_$id.py
"""


from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: The folder Berry Manager and addon_client live in, two levels up.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                            # noqa: E402

SOURCE = ROOT / "addons" / "$id.py"
TAB = "$tab"
PAGE = "$id-settings"
FAILURES: list[str] = []
CHECKS = 0


def check(condition: Any, text: str) -> None:
    """One thing the addon has to do; every one is reported, not just the first."""
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(text)


def nodes(spec: Any):
    """Every dict in a widget spec, so the ids it was built with can be counted."""
    if isinstance(spec, dict):
        yield spec
        for child in spec.get("children") or []:
            yield from nodes(child)
    elif isinstance(spec, (list, tuple)):
        for item in spec:
            yield from nodes(item)


def ids_of(spec: Any) -> set:
    return {str(node["id"]) for node in nodes(spec) if node.get("id")}


def shown(entry: tuple) -> str:
    """One recorded call on one line: a whole spec is not worth a screenful."""
    method, arguments, named = entry
    text = f"{method}{list(arguments)}"
    if named:
        text += " " + str(named)
    return text if len(text) <= 150 else text[:147] + "..."


def main() -> int:
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    # The values the config file would have had; a test never needs the file.
    api = addon_client.stub_api("$id", config={"greeting": "hello from the test"})
    addon = addon_client.load_addon(SOURCE)

    # --- init: what the addon asks the app to build ---
    addon.init(api)
    check(api.called("config.declare"), "init did not declare its config keys")
    check(api.asked("config.declare", "fields"), "the declared schema is empty")
    check(api.called("tabs.add_tab"), "init did not add a tab")
    named = api.asked("tabs.add_tab", "name")
    check(named == TAB, f"the tab is not named as the addon says: {named!r}")
    ids = ids_of(api.asked("tabs.add_tab", "spec"))
    check({"press", "status"} <= ids,
          f"the tab spec gives its controls no ids to write back to: {sorted(ids)}")
    check(api.called("ui.menu_item"), "init did not add its Tools menu entry")
    check(api.times("events.on") >= 1, "init did not listen for any event")
    if api.called("settings_pages.add"):
        page_ids = ids_of(api.asked("settings_pages.add", "spec"))
        check(bool(page_ids),
              f"the Settings page has no named controls: {sorted(page_ids)}")

    # --- what the user set reaches the code that uses it ---
    check(api.config.get("greeting") == "hello from the test",
          "the config value did not reach the addon")

    # --- start: a record of its own, kept per profile ---
    addon.start()
    check(api.times("settings.set") >= 1, "start recorded nothing in api.settings")

    # --- every control does the thing it is there for ---
    api.logs.clear()
    addon.on_widget(TAB, "press", "clicked", {})
    check(any("hello from the test" in line for line in api.logs),
          "pressing the button left nothing in the log to show for it")
    addon.on_widget(PAGE, "about", "clicked", {})
    check(api.called("ui.information"), "the page's button opened no dialog")

    # --- another addon speaking to this one, if it listens ---
    peer = getattr(addon, "on_message", None)
    if callable(peer):
        api.logs.clear()
        peer("tester", {"hello": 1})
        check(any("tester" in line for line in api.logs),
              "on_message took a peer's message and said nothing of it")

    # --- a config change is read again, not remembered from init ---
    mark = len(api.calls)
    addon.on_config({"addon": "$id", "source": "test", "file": "x"})
    check(len(api.calls) > mark, "on_config asked the app for nothing at all")

    # --- and shutdown is really the end of it ---
    addon.shutdown()
    mark = len(api.calls)
    addon.start()
    addon.on_widget(TAB, "press", "clicked", {})
    addon.on_config({"addon": "$id"})
    check(len(api.calls) == mark,
          "the addon still talks to the app after shutdown()")

    # --- what the addon did, for whoever has to read this ---
    tally: dict[str, int] = {}
    for method, _arguments, _named in api.calls:
        where = str(method).split(".")[0]
        tally[where] = tally.get(where, 0) + 1
    print(f"$id: {len(api.calls)} api call(s)"
          + (", " + ", ".join(f"{count} {where}"
                             for where, count in sorted(tally.items())) if tally else ""))
    for entry in api.calls[:20]:
        print(f"  {shown(entry)}")
    if len(api.calls) > 20:
        print(f"  ... and {len(api.calls) - 20} more")
    print("log lines written by the addon:")
    for line in api.logs:
        print(f"  {line}")
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        print(f"{CHECKS - len(FAILURES)} of {CHECKS} checks passed")
        return 1
    print()
    print(f"all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')

_LAUNCHER = Template('''#!/usr/bin/env python3
"""Runs $id as its own program. Written by the Addon Dev tab; do not edit."""

# Every name used below has to be named here. A frozen program is built from
# what this file visibly imports, so a module that is only reached by luck -
# left in __main__ by the bootloader - is both an undefined name at run time
# and missing from the build altogether.
import os
import sys

for _descriptor, _mode in ((1, "w"), (2, "w")):
    # A --windowed build has no streams; reopen the pipe rather than lose it.
    if getattr(sys, "stdout" if _descriptor == 1 else "stderr", None) is None:
        try:
            stream = os.fdopen(_descriptor, _mode, buffering=1, encoding="utf-8")
        except OSError:
            continue
        setattr(sys, "stdout" if _descriptor == 1 else "stderr", stream)

import addon_client

import $module as addon

if __name__ == "__main__":
    raise SystemExit(addon_client.serve(addon))
''')


# --- PyInstaller ---

#: Which interpreter can build, and whether it has PyInstaller (costly to ask).
_READY: dict[str, tuple[bool, str]] = {}

#: What a Python may be started by, tried in this order. ``py`` is the launcher
#: the Windows installer puts on the path in its own right.
PYTHON_NAMES = ("python3", "python", "py")

#: The interpreter the path was asked for, once; nothing means it was not asked.
_INTERPRETER: tuple[str | None, str] | None = None


def pyinstaller_ready() -> tuple[bool, str]:
    """Whether the interpreter that would do the building has PyInstaller."""
    where, why = build_interpreter()
    if where is None:
        return False, why
    cached = _READY.get(str(where))
    if cached is not None:
        return cached
    found = _has_pyinstaller_here() if str(where) == sys.executable \
        else _has_pyinstaller(where)
    answer = (found, "" if found else f"{where} -m pip install pyinstaller")
    _READY[str(where)] = answer
    return answer


def _not_a_python(found: str) -> str:
    """Why this command on the path cannot build; nothing when it can.

    Windows leaves zero-length python and python3 files in its WindowsApps
    folder - the app execution aliases. Running one prints that Python was not
    found, points at the Microsoft Store and stops with code 9009, which
    otherwise reads as a failure of the build itself. An empty file is therefore
    refused outright rather than started, and whatever is left is asked to start
    once, so a command that is broken is not chosen either.
    """
    try:
        size = Path(found).stat().st_size
    except OSError as error:
        return f"a command that cannot be read ({error})"
    if size == 0:
        return ("a Windows app execution alias, standing for the Microsoft Store "
                "page instead of a Python" if os.name == "nt"
                else "an empty file that is not a Python")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        done = subprocess.run([found, "-c", "import sys"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as error:
        return f"a command that did not start ({error})"
    if done.returncode != 0:
        return f"a command that stopped with code {done.returncode}"
    return ""


def build_interpreter() -> tuple[str | None, str]:
    """The Python to build or test with: ours, or a working one found on the path.

    A packaged program carries no interpreter of its own, so ``python3``,
    ``python`` and the Windows ``py`` launcher are tried in turn on either
    system, and each is checked rather than trusted. The answer is kept, because
    asking costs a process and the panels ask often; :func:`forget_interpreter`
    puts it back so a Python installed since is noticed.
    """
    global _INTERPRETER
    if not IS_COMPILED:
        return sys.executable, ""
    if _INTERPRETER is not None:
        return _INTERPRETER
    seen: list[str] = []
    for name in PYTHON_NAMES:
        found = shutil.which(name)
        if found is None:
            continue
        why = _not_a_python(found)
        if why:
            seen.append(f"{name} at {found} is {why}")
            continue
        _INTERPRETER = (found, "")
        return _INTERPRETER
    _INTERPRETER = (None, "no working Python found on the path"
                    + (" (" + "; ".join(seen) + ")" if seen else "")
                    + ". Install Python, or on Windows untick the app execution "
                      "aliases under Settings > Apps > Advanced app settings, or "
                      "run this app from a source checkout to compile and test "
                      "addons")
    return _INTERPRETER


def forget_interpreter() -> None:
    """Ask the path again next time, and drop what was found along with it."""
    global _INTERPRETER
    _INTERPRETER = None
    _READY.clear()


def harness_dir() -> Path | None:
    """Where the modules an addon imports lie on disk, or nothing.

    ``addon_common`` and ``addon_client`` are what a launcher and a test script
    import to reach the app. In a checkout they sit in the app's own folder; in
    a built program they are inside the archive unless the build packed a plain
    copy, and the Python that builds cannot read the archive.
    """
    return HARNESS_DIR if all((HARNESS_DIR / name).is_file()
                              for name in HARNESS_FILES) else None


def app_modules_ready() -> tuple[bool, str]:
    """Whether the app's modules lie on disk, where a build and a test look."""
    if harness_dir() is not None:
        return True, ""
    return False, ("this program has no copy of the modules an addon imports "
                   "(addon_client, addon_common) where a build and a test can "
                   "read them. Run Berry Manager from a source checkout, use a "
                   "build that packed them, or install the addon from the "
                   "addon store, which fetches them")


def _has_pyinstaller_here() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("PyInstaller") is not None
    except (ImportError, ValueError):
        return False


def _has_pyinstaller(where: str) -> bool:
    """Ask another interpreter whether it can build."""
    try:
        done = subprocess.run([where, "-c", "import PyInstaller"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def buildable(addon: Any) -> tuple[bool, str]:
    """Whether this one addon could be compiled here, and why not if it cannot."""
    if not str(addon.source).endswith(".py"):
        return False, "this addon is already a program"
    if getattr(addon, "builtin", False):
        return False, ("the file ships inside the program; compile the copy in "
                       "the addons folder instead")
    ready, why = app_modules_ready()
    if not ready:
        return False, why
    return pyinstaller_ready()


def icon_for_build() -> Path | None:
    """The program's own mark as a file this build can wear, or nothing.

    The shipped icon is a PNG, so on Windows it is rewrapped here too: a program
    built by this tab or by the store then carries the berry rather than
    PyInstaller's own logo.
    """
    path = find_asset(ICON_FILES[0])
    return usable_icon(path) if path is not None else None


#: The ``--icon`` shapes PyInstaller reads as it stands on each platform. Any
#: other one it converts only when Pillow happens to be installed, and the Python
#: a user builds with has no reason to have it: requirements.txt does not name
#: Pillow anywhere, and the store installs nothing but PyInstaller itself.
ICON_FORMATS = {
    "win32": (".ico", ".exe"),
    "darwin": (".icns",),
}

#: The eight bytes that say a file is a PNG, and where its width and height sit.
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def icon_formats() -> tuple[str, ...]:
    """What may be handed to ``--icon`` here without being converted first."""
    return ICON_FORMATS.get(sys.platform, (".ico", ".icns", ".png"))


def png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height out of its own header; nothing when it is not one."""
    if not data.startswith(_PNG_HEAD) or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def ico_from_png(png: Path, target: Path) -> Path | None:
    """One PNG rewrapped as a Windows ``.ico`` with nothing but the standard library.

    An icon directory may hold PNG-compressed image data - Windows has read it
    since Vista - so the reserved word, the type, the one directory entry and the
    picture's own bytes follow each other here with nothing decoded in between. A
    file that is not a PNG, a side the directory cannot count (it holds one byte,
    and 0 means 256) and a disk that will not have the file all answer with
    nothing rather than a broken icon.
    """
    try:
        data = png.read_bytes()
    except OSError:
        return None
    size = png_size(data)
    if size is None or not all(1 <= side <= 256 for side in size):
        return None
    header = struct.pack("<HHH", 0, 1, 1)
    # A directory entry is 16 bytes, so the picture starts after both of them.
    dirent = struct.pack("<BBBBHHII", size[0] % 256, size[1] % 256, 0, 0,
                         1, 32, len(data), len(header) + 16)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header + dirent + data)
    except OSError:
        return None
    return target


#: How wide a Windows icon's directory can say a picture is: one byte per side,
#: and 0 stands for 256. Anything bigger is taken down to it before being packed.
ICO_MAX_SIDE = 256


def ico_scaled(png: Path, target: Path) -> Path | None:
    """One PNG too wide for an icon's directory, drawn down and packed by Qt.

    :func:`ico_from_png` cannot count past 256, and the marks addons publish are
    often 512 - too big for the directory and too dear to throw away. Qt is
    already here (this tab is built out of it) and its ICO writer takes the
    picture down whole. A Qt without the ICO plugin, a file it cannot read and a
    disk that will not have the answer all say nothing, which costs a build its
    icon and not its program.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage

        image = QImage(str(png))
        if image.isNull():
            return None
        if max(image.width(), image.height()) > ICO_MAX_SIDE:
            image = image.scaled(ICO_MAX_SIDE, ICO_MAX_SIDE,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        if not image.save(str(target), "ICO"):
            return None
    except Exception:                          # noqa: BLE001 - only a picture at stake
        return None
    return target if target.is_file() else None


def scratch_ico(png: Path) -> Path | None:
    """One PNG's ``.ico`` in a file nobody keeps.

    A build's icon is read once and thrown away, so writing it beside the picture
    would pack the same art into the program twice under two names - and a
    read-only bundle could not write anywhere at all.
    """
    try:
        handle = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
    except OSError:
        return None
    where = Path(handle.name)
    try:
        handle.close()
    except OSError:
        return None
    return ico_from_png(png, where) or ico_scaled(png, where)


def usable_icon(path: Path) -> Path | None:
    """One picture as a file this platform's PyInstaller can use; nothing if it cannot.

    This is where a store addon's ``<id>icon.png`` - a tab and card mark, made
    for the bar and not for a Windows executable - is settled: on Windows it is
    rewrapped rather than handed over raw, because PyInstaller would otherwise
    stop the whole build over a picture, and a build that cannot use it at all
    loses its icon rather than its program.
    """
    try:
        if not path.is_file():
            return None
        suffix = path.suffix.lower()
    except OSError:
        return None
    if suffix in icon_formats():
        return path
    if suffix == ".png" and sys.platform == "win32":
        return scratch_ico(path)
    return None


def icon_choice(icon: Any) -> Path | None:
    """The ``--icon`` file for a build: one PyInstaller can read, the app's, or none.

    ``icon`` is ``True`` for the program's own mark, ``False``/empty for none, or
    a path an addon brought with it (a store addon's own icon). A named icon that
    is missing or the wrong shape is rewrapped if it can be, and falls back on the
    app's if it cannot, so a build never dies over a picture.
    """
    if not icon:
        return None
    if icon is True:
        return icon_for_build()
    made = usable_icon(Path(icon))
    if made is not None:
        return made
    return icon_for_build()


def build_root(root: Path | None = None) -> Path:
    """The folder a build is staged in: ``addons/_build``, or a caller's own.

    An addon that builds things of its own (the store) keeps its work under its
    own ``_data`` folder rather than in the dev tab's, so nothing it makes lands
    among the files the Addon Dev tab made. Nothing is created here: whoever
    stages a build makes the folder it needs.
    """
    return ADDON_BUILD_DIR if root is None else Path(root)


def staged_name(addon_id: str, onefile: bool,
                root: Path | None = None) -> Path:
    """Where PyInstaller puts the program before it is copied into ``addons``."""
    folder = build_root(root) / addon_id / ("dist" if onefile else Path("dist") / addon_id)
    return folder / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}"


def build_command(launcher: Path, addon_id: str, options: dict,
                  source: Path | None = None,
                  root: Path | None = None,
                  paths: list | tuple = ()) -> list[str]:
    """The PyInstaller line for a build staged inside ``<root>/<id>``.

    ``paths`` adds folders holding the modules the launcher imports, for a
    caller that keeps its own copy of them: the store fetches the app's two
    when the program it runs inside has none on disk.
    """
    work = build_root(root) / addon_id
    where, _why = build_interpreter()
    command = [
        where or sys.executable, "-m", "PyInstaller",
        # No --clean here on purpose: it throws away PyInstaller's own
        # analysis cache, so every build - the dev tab's and the store's -
        # starts cold and re-reads every module it has already worked out.
        # Nothing stale can ride into a new program: each build stages in
        # its own ``<root>/<id>`` folder, which the caller empties first.
        "--noconfirm",
        "--distpath", str(work / "dist"),
        "--workpath", str(work / "build"),
        "--specpath", str(work),
        "--name", addon_id,
    ]
    command.append("--onefile" if options.get("onefile", True) else "--onedir")
    command.append("--console" if options.get("console", True) else "--windowed")
    icon = icon_choice(options.get("icon", True))
    if icon is not None:
        command += ["--icon", str(icon)]
    for local, dest in (options.get("add_data") or ()):      # files packed into the build
        command += ["--add-data", os.pathsep.join([str(local), str(dest)])]
    folders, seen = [], set()
    for folder in [harness_dir() or BASE_DIR, *paths,
                   *([source.parent] if source is not None else [])]:
        if str(folder) not in seen:
            seen.add(str(folder))
            folders.append(Path(folder))
    if source is not None:
        command += ["--hidden-import", module_name(source)]
    for folder in folders:
        command += ["--paths", str(folder)]
    for extra in str(options.get("hidden", "")).replace(",", " ").split():
        command += ["--hidden-import", extra]
    command.append(str(launcher))
    return command


class _Stream:
    """One child process, its output as lines, and a way to stop it."""

    def __init__(self, command: list[str], cwd: Path | None = None,
                 env: dict | None = None) -> None:
        self.command = list(command)
        self.cwd = str(cwd) if cwd is not None else str(BASE_DIR)
        self.env = env                                  # None keeps the parent's
        self.lines: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.finished = False
        self.code: int | None = None

    def start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            # A tree-killable child: a PyInstaller run spawns more processes
            # than the one started here, and all of them must be reachable.
            self.proc = popen_tree(
                self.command, cwd=self.cwd, env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=flags)
        except OSError as error:
            self.lines.put(f"could not start: {error}")
            self.finished = True
            self.code = -1
            return
        self.reader = threading.Thread(target=self._pump, daemon=True,
                                       name="addon-dev-out")
        self.reader.start()

    def _pump(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.code = self.proc.wait()
        release_process_tree(self.proc)
        self.finished = True
        self.lines.put(None)              # the tab stops draining on this

    def drain(self, limit: int = 400) -> list[Any]:
        out: list = []
        while len(out) < limit:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return out

    def running(self) -> bool:
        return not self.finished

    def cancel(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        terminate_process_tree(self.proc)

    def kill(self) -> None:
        """Force the whole tree down, for the app's closing path."""
        if self.proc is None or self.proc.poll() is not None:
            return
        kill_process_tree(self.proc)
        release_process_tree(self.proc)


def log_lines(text: str, addon_id: str, warnings_only: bool = False) -> list[str]:
    """The addon's own lines out of the app log, newest kept last."""
    marker = f"[{addon_id}]"
    out: list[str] = []
    for line in str(text or "").splitlines():
        if marker not in line:
            continue
        if warnings_only and "error:" not in line:
            continue
        out.append(line.strip())
    return out


# --- the "New addon..." dialog ---

class NewAddonDialog(QDialog):
    """Ask for the header, then write the addon and its test script."""

    #: What may become a file name that Python can also import.
    CLEAN = re.compile(r"[^A-Za-z0-9_]+")

    def __init__(self, parent: QWidget | None = None,
                 known: Any = ()) -> None:
        super().__init__(parent)
        self.known = {str(item) for item in known}
        self.setWindowTitle("New addon")
        self.resize(460, 380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("mytool - letters, digits and underscores")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My tool")
        self.version_input = QLineEdit("1.0")
        self.author_input = QLineEdit()
        self.description_input = QLineEdit()
        self.description_input.setPlaceholderText("One line the Addons list shows")
        self.tab_input = QLineEdit()
        self.tab_input.setPlaceholderText("My tool")
        self.menu_input = QLineEdit()
        self.menu_input.setPlaceholderText("Show My tool")
        form.addRow("Identifier", self.id_input)
        form.addRow("Name", self.name_input)
        form.addRow("Version", self.version_input)
        form.addRow("Author", self.author_input)
        form.addRow("Description", self.description_input)
        form.addRow("Tab title", self.tab_input)
        form.addRow("Menu entry", self.menu_input)
        layout.addLayout(form)

        self.test_check = QCheckBox("Write a test script in addons/_tests as well")
        self.test_check.setChecked(True)
        self.run_check = QCheckBox("Run it as soon as it is written")
        self.run_check.setChecked(True)
        layout.addWidget(self.test_check)
        layout.addWidget(self.run_check)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #8f8f8f;")
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for field in (self.id_input, self.name_input, self.tab_input):
            field.textChanged.connect(self._follow)
        self.id_input.setFocus()

    def _follow(self, _text: str = "") -> None:
        """An identifier typed, a sensible title everywhere else."""
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        for field, wanted in ((self.name_input, cleaned.replace("_", " ").strip()),
                              (self.tab_input, cleaned.replace("_", " ").strip()),
                              (self.menu_input, f"Show {cleaned.replace('_', ' ')}")):
            if not field.text().strip():
                field.setText(wanted)
        taken = cleaned.lower() in self.known
        self.note.setText(
            "That identifier is already used by an addon." if taken else
            f"Written to {ADDONS_DIR / (cleaned or '...')}.py"
            + (f" and {ADDON_TEST_DIR / ('test_' + cleaned + '.py')}" if cleaned else ""))
        self.note.setStyleSheet("color: #ffb020;" if taken else "color: #8f8f8f;")

    def usable(self) -> bool:
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        return bool(cleaned) and not cleaned[0].isdigit() \
            and cleaned.lower() not in self.known

    def try_accept(self) -> None:
        if not self.usable():
            self.note.setText("Give it an identifier that Python can import "
                              "(letters, digits, underscore; not a digit first) "
                              "and that no addon uses yet.")
            self.note.setStyleSheet("color: #ffb020;")
            return
        self.accept()

    def values(self) -> dict:
        cleaned = self.CLEAN.sub("_", self.id_input.text().strip())
        text = lambda field, fallback: (field.text().strip() or fallback)
        name = text(self.name_input, cleaned.replace("_", " "))
        return {
            "id": cleaned,
            "name": name,
            "version": text(self.version_input, "1.0"),
            "author": text(self.author_input, ""),
            "description": text(self.description_input, f"{name} addon"),
            "tab": text(self.tab_input, name),
            "menu": text(self.menu_input, f"Show {name}"),
            "test": self.test_check.isChecked(),
            "run": self.run_check.isChecked(),
        }


# --- the guides: what the Docs pane shows before the reference ---

#: The README as published. A packaged program carries no markdown, so the
#: guide's "whole manual" link falls back to this when ``README.md`` is not on
#: disk next to the running program.
README_URL = PROJECT_GITHUB_URL + "/blob/main/utils/update%20util/README.md"

#: The pane's reading order: a guide, its title, and the markdown it is written
#: in. Links of the form ``[text](api:tabs.add_tab)`` and ``[text](guide:tabs)``
#: move around inside the panel; anything else opens in the browser.
GUIDES: tuple[tuple[str, str, str], ...] = (
    ("start", "Start here", """
# Start here

An addon is one Python file in the ``addons`` folder beside Berry Manager, or a
program compiled from such a file. The app reads it, calls ``init(api)`` and
hands it the one object through which everything else is reached: ``api``. An
addon never touches the app's widgets directly - a tab is described in a dict
and the app builds it - which is why the same file runs both imported into the
app and as a program of its own.

## Three minutes

- Press **New addon...** above: it writes ``addons/<id>.py`` and, when asked,
  ``addons/_tests/test_<id>.py``.
- Switch it on - the checkbox above, or the addon's row on the store's Addon
  manager page. Its tab appears in the bar.
- Edit the file in any editor, then press **Restart** here. Only the addon is
  read again; the app does not move.

## Where things live

- ``addons`` - the addon's file, and the program built from it.
- ``addons/_data/<id>`` - its ``config.json``, shared by every profile.
- ``addons/_tests`` - its test script.
- ``config/berry_manager.log`` - what ``api.log`` writes; the log under this
  panel's reference filters it to this one addon.

The reference in this panel is read off the running ``addon_api`` module, so it
cannot describe an API this program does not have.
"""),

    ("anatomy", "Anatomy of an addon", """
# Anatomy of an addon

    ADDON = {...}         the manifest: name, version, author, description,
                          requires, connection, icon, api
    TAB = "My addon"      the name of the tab the addon owns
    FIELDS, DEFAULTS      the settings the user can change
    def init(api)         the entry point: declare the config, then build
    def start()           called at startup and whenever the switch goes on
    def shutdown()        called before the app forgets the addon

Everything else is a hook the app calls when something happens:

- ``on_widget(tab, id, event, payload)`` - a control the addon asked for was
  clicked, typed in, picked from a list.
- ``on_config(payload)`` - ``config.json`` changed under its feet.
- ``on_event(name, payload)`` and ``on_<name>(payload)`` - an event arrived.
- ``on_message(sender, data)`` - another addon wrote to it through
  [api.peers.send](api:peers.send).

## The manifest

Only ``name`` is needed. ``api`` should stay at the version the app speaks, and
``requires`` names the transports ``api.device`` may use - ``["ssh", "sftp"]``,
or leave the key out for an addon that never connects. Each transport gets a
switch of its own on its row, and both the app-wide and the
addon's own have to be on. ``connection`` says whose session the calls go over:
``"app"`` for the program's one main connection, ``"own"`` for a connection the
addon opens for itself per call, ``"both"`` where the user picks with a switch on
the row, or ``"none"`` - which says no more than leaving ``requires`` out, and is
answered on the row and in **Connection status** as *no device connection*
rather than with a dot that could never go green. An addon that ships inside the
program always rides its session, whatever it declared. ``popout`` says where the
addon's tabs live: ``"always"`` for a window of their own, ``"never"`` for the
main window's bar, and leaving it out hands the choice to the user's **Force
popout** switch in the store's Addon manager (see
[A window of its own](guide:popout)). ``icon`` names the
picture the addon wears; a file named ``<id>icon.png`` in either of the two
folders the rule allows is found without being declared at all (see
[Notices and icons](guide:notice)).

## Mind the process boundary

A compiled addon is a separate program: everything it sends and receives has to
fit in JSON. So a tab is a spec dict rather than a widget, values come back as
plain data, and no module of the app is imported. Write it that way from the
start and the source and the build behave the same.
"""),

    ("tabs", "Tabs, pinned and plain", """
# Tabs, pinned and plain

[api.tabs.add_tab](api:tabs.add_tab) takes a spec dict - the language the app
turns into widgets - or any QWidget in an imported addon. A new tab does not
steal the view; [activate](api:tabs.activate) puts it on screen.

    api.tabs.add_tab("My addon", spec={
        "kind": "column",
        "children": [
            {"kind": "label", "text": "Hello", "font_size": 12},
            {"kind": "line", "id": "who", "placeholder": "name"},
            {"kind": "button", "id": "go", "text": "Go", "on": ["clicked"]},
            {"kind": "tree", "id": "rows", "columns": ["Thing", "State"]},
        ],
    })

Layouts: ``column`` / ``vbox``, ``row`` / ``hbox``, ``grid``, ``form``,
``group``, ``scroll``, ``splitter``, ``tabs``, ``overlay``. Leaves: ``label``,
``button``, ``line``, ``color``, ``multi``, ``check``, ``combo``, ``list``,
``tree``,
``table``, ``progress``, ``spin``, ``slider``, ``spacer``, ``image``, ``web``,
``terminal``.

Anything with an ``id`` can be read and written afterwards with
[set_widget](api:tabs.set_widget) and [widget_value](api:tabs.widget_value).
The property names are the spec's own: ``text``, ``value``, ``rows``,
``items``, ``clear``, ``append``, ``placeholder``, ``tooltip``, ``style``,
``enabled``, ``visible``, ``checked``, ``highlight``, ``maximum``, ``format``,
``current``, ``font_size``.

An ``image`` leaf is a picture of its own: ``size`` caps how far it may measure
in either direction (at the interface zoom), and its ``value`` - or
``set_widget(tab, id, "image", ...)`` - is an image file's path or an SVG
document as text, the same two shapes [set_icon](api:tabs.set_icon) takes. A
picture that cannot be read leaves the leaf blank rather than showing the user a
path. A ``list``, ``tree`` or ``table`` entry can wear a mark as well: a list
item as ``{"text": ..., "icon": ...}``, a row as a dict with ``"icon"`` beside
``"cells"``, and that picture is drawn in front of the row's first cell - which
is how one list can carry a device's own mark next to its own colour.

## Sharing the room

A ``splitter`` lays its children side by side, or one over another with
``"vertical": True``, with a line between each pair that the user can drag. Its
``sizes`` are shares rather than pixels: ``[3, 1]`` is three parts of the room to
the first pane and one to the second, and those parts are kept as the window
moves - until somebody drags the line, which is them saying they know better,
and then the panes stay where they were put until the tab lays them out again.
A row or a column becomes the same thing on ``"resizable": True``, so the change
is one key rather than a rewrite. ``"collapsed": True`` on a child starts it
folded away: nothing of it shows but its line, which is dragged open or
double-clicked, and which comes back at the share it was promised. A pane that
was never asked to fold cannot be dragged out of sight at all, so no row of
words can be squeezed into nothing - say ``"collapsible": True`` on one that is
meant to be put away.

    {"kind": "splitter", "vertical": True, "sizes": [3, 1], "children": [
        {"kind": "table", "id": "work", "columns": ["Thing", "State"]},
        {"kind": "multi", "id": "log", "collapsed": True, "readonly": True},
    ]}

The same from code is ``set_widget(tab, id, "sizes", [3, 0])``, which folds the
log, and ``[3, 1]``, which brings it back to a third of the room; a ``0`` there
means the folded pane, not one of no size at all.
[widget_value(tab, id, "panes")](api:tabs.widget_value) reads the shares back,
with which panes are open and what each is called, so a check box beside the tab
can be ticked from how the panes really stand rather than from what was last
sent to them.

A table's columns are all dragged to the width they are wanted at, and their
headings moved across to a new order. ``col_widths`` pins a starting width and
``stretch`` names the column that takes what the others leave over, so the table
fills its pane and still answers to a hand on its edge - a stretched column
would not. A column that was pinned nothing starts as wide as its own heading
reads, so a tab is not opened with a caption cut short; a pane too narrow for
the whole row of them grows a bar to scroll. Double-clicking the heading of the
stretching column hands its slack back after it has been dragged by hand.

## Where a tab lands

A tab an addon opens goes immediately to the left of the tab that addon already
owns, so a report or a helper sits beside its parent instead of at the far end
of the bar where the user has to go looking for it. Pass ``position`` to choose
otherwise.

A tab can leave the bar altogether as well: [api.window.popout](api:window.popout)
puts it in a window of its own, and docking it back returns it to the slot it left
(see [A window of its own](guide:popout)).

## Pinned, icon-only tabs

``pinned=True`` holds the tab in the block at the right edge, beside Settings:
out of the row the user drags about, kept in that run whatever else moves, and
remembered along the edge in its own saved order (``pinned_tab_order``) which the
user can arrange in Settings > Startup beside the tabs of their own. It keeps its
place while other tabs come and go. ``icon_only=True`` draws it as a mark with
no caption - the name still identifies it to the addon, and the tooltip says it
aloud. Both work on an embedded page from
[add_web_tab](api:tabs.add_web_tab) as well, and can be changed afterwards with
[set_pinned](api:tabs.set_pinned). The addon store and the manager updater's own
tabs are made this way. A mark has nothing left but its tooltip to say who it
belongs to, so the app fills that in with the addon's name and its tab's - an
addon that asks to be drawn as a mark should send a picture of its own with it
(see [Notices and icons](guide:notice)).

## Pages added and taken away

A ``tabs`` container is not fixed at build time. ``set_widget(tab, id,
"add_page", {"label": "eth1", "spec": {...}})`` builds one more sub-page out of
the same spec language and puts it at the end - ``"position"`` says elsewhere,
``"current": False`` leaves the view where it is - ``"remove_page"`` takes one
away by index or by caption, and [widget_value](api:tabs.widget_value) with
``"pages"`` reads every caption back. A control that only ever exists while its
page does needs no cleaning up: taking the page away drops its controls out of
the tab's registry, and closes any terminal sitting on it.

## A terminal of your own

A ``terminal`` leaf is the app's own terminal screen - the widget that draws the
device's escape sequences and sends the keys typed into it - on a connection of
its own rather than the app's main session. So one tab can hold four of them and
four machines are being watched, each on its own thread, and closing one takes
nothing else down with it.

    {"kind": "terminal", "id": "shell", "on": ["ready", "closed", "failed"]}

    api.tabs.set_widget("My addon", "shell", "connect",
                        {"host": "192.168.1.1", "username": "root",
                         "password": typed})

``"disconnect"`` drops it, ``"command"`` types a line with the Enter added (a
list types several, so one field can reach every shell open), ``"send"`` types
raw bytes and ``"write"`` prints a note from the addon itself. Its ``value`` is
what is on the screen, ``"state"`` says what it is attached to - host, port,
username, and whether it is up; never the password - and ``"grid"`` its shape.
The screen says what happens to it: *Connecting to*, *Connected*, *SSH error:*
and *Connection closed.* are drawn where the user is looking, and the same news
arrives in ``on_widget`` as ``ready``, ``closed`` and ``failed`` - which is how a
sub-page can close itself the moment its link drops.

Because this is a connection of its own, the addon has to have asked for it: a
manifest with ``"requires": ["ssh"]`` and ``"connection": "own"``, and the same
switches [api.device.run](api:device.run) passes through. Reading a screen,
typing into one that is already up and letting go of one are never refused - the
permission was asked for once, when the connection was opened. A key file, if
the addon offers one, comes from [api.ui.pick_file](api:ui.pick_file); nothing is
uploaded behind anybody's back. A password is asked for in the same spirit:
[api.ui.input](api:ui.input) with ``secret=True`` draws dots while the user types
and answers ``None`` when the box is cancelled - and asking each time, rather than
keeping one in a config file that gets shared and backed up, is the design an
addon with several machines should reach for.

## Pages on the device

[add_web_tab](api:tabs.add_web_tab) embeds an address; from there
[set_html](api:tabs.set_html), [inject_css](api:tabs.inject_css) and
[inject_js](api:tabs.inject_js) shape what it shows, and
[view](api:tabs.view) hands over the whole page object - in an imported addon
only.

## A view for a long job

An ``overlay`` is a stack instead of a row: its first child fills the area and
every child after it keeps to its own size and floats on top, at the place its
``align`` names - ``center`` (the default), ``top``, ``bottom``, ``left``,
``right`` - re-centred whenever the pane changes size. That is the shape a job
wants: the addon store and the manager updater both put a ``terminal`` in the
stack and float a card over it, so what the build is printing runs across the
whole tab while the addon or version being worked on, its step and its bar stay
in the middle of it. The stack starts hidden and is switched on with
``set_widget(tab, id, "visible", True)`` - and since
[set_widget](api:tabs.set_widget) answers ``False`` for an id the built tab does
not hold, the same code is safe on a copy whose layout was built before the view
existed: it keeps the browsing panes on screen and writes its steps to its log
instead.
"""),

    ("popout", "A window of its own", """
# A window of its own

An addon's tabs normally stand in the main window's bar with everybody else's. A
popout takes one of them out and puts it in a top-level window of its own - not a
copy of the page, the same widget, with the same live state and the same
``on_widget`` calls - and puts it back in the slot in the bar it came from.

## Who decides

Three things can say, and they are asked in that order:

- the manifest: ``"popout": "always"`` means the addon's tabs only ever live in a
  window, ``"never"`` means they only ever live in the bar. Either value is the
  addon's word on the matter, and the app's switch is greyed out beside an
  explanation rather than fighting it.
- the app's **Force popout** switch, on the addon's row in the store's Addon
  manager page. It is offered only for an addon that declared nothing, and it is
  kept in that profile's record beside the enable switch, so two profiles can
  disagree about the same addon.
- the addon itself, at any time, with
  [api.window.popout(tab, on)](api:window.popout).

An addon that ships inside the program - Berry, the addon store, the manager
updater, the theme manager - is never pushed out by the app's switch, since it has
no row to put the switch on; only its own call can move its tabs.

## What the window is

It wears the theme like the main window does, and the addon's name and mark as its
title and icon. Its own tab bar shows only when the addon has more than one tab
out. There is a **dock back** button, and closing the window hides it instead of
destroying it: the addon keeps running, and the row's **Raise window** brings it
back. Size and place are remembered per addon, and a tab that is out is simply not
in the bar's saved order any more - which is how a popped tab stops being dragged
about with the rest.

The addon steers the whole frame while it is out - [state](api:window.state) reads
it, [set_size](api:window.set_size), [set_position](api:window.set_position),
[set_minimum](api:window.set_minimum), [set_title](api:window.set_title),
[set_always_on_top](api:window.set_always_on_top),
[set_resizable](api:window.set_resizable), [set_closable](api:window.set_closable),
[set_style](api:window.set_style) and [set_icon](api:window.set_icon) write it, and
[open](api:window.open) / [close](api:window.close) / [show](api:window.show) /
[raise_window](api:window.raise_window) / [center](api:window.center) move it about.
What happens to it comes back as ``window`` events (see
[Events and hooks](guide:events)).

A popout window has no Settings, no profile picker and no pinned run, so
[add_settings_page](api:settings_pages.add) and a ``pinned=True`` tab are refused
plainly rather than crashing - on that window, that is all there is.

## Starting from a Desktop link

[api.window.set_shortcut(on)](api:window.set_shortcut) asks for a Desktop link
(``shortcut_path()`` says where it is, or why it is not) that starts this program
again with ``--popout <id>``. A copy launched that way loads exactly one addon: no
main window, no splash, no other addon - its peers answer *not running*. Its api is
the ordinary one, and the profile's connection is opened at launch only when that
addon rides it: an addon on a connection of its own is given nothing, and an addon
that needs no device is given nothing, whatever the startup switch says.
"""),

    ("notice", "Notices and icons", """
# Notices and icons

## Speaking when the tab is not on screen

    api.ui.popup("My addon", "the run finished - 3 packages installed")

[information](api:ui.information) stops the user with a dialog and wants an
answer; [popup](api:ui.popup) does neither. The app puts the line in its status
bar and, while **Pop up notifications** in Settings > Startup & defaults is on,
sends it to the system's notification area as well - which is how an addon can
report a long job to someone who has already gone back to another window.

The addon's own name is put on the title unless the addon wrote it in, so every
notice says who is talking: several addons sharing one notification area is only
readable if each line is named. The words always
reach the status line and the log, switch or no switch, so a notice that cannot
be shown is only quieter, never lost - and an addon has no way to reach the tray
or the window itself, because whether its words become a toast is the user's
setting rather than the addon's decision.

Use it for news the user would want even with the tab closed: a job that
finished, a device that stopped answering, an update that arrived. Anything that
only matters while they are watching belongs on the tab or in
[api.log](api:log). Keep it to a sentence or two - a notification is a nudge, and
the detail is one click away.

## A job that is worth watching

[progress_notice](api:ui.progress_notice) is the other kind of news: a small card
over the window's corner holding one line of text and a bar with its percentage,
painted by the app and needing nothing from the addon but the calls.

    api.ui.progress_notice("My addon", f"working on {name}", done, total)

Each step re-shows the same card rather than raising another, so a long job reads
as one thing moving along instead of a stack of toasts; it never covers the whole
window and never needs closing. Fill it to the end on the job's last line and it
holds a moment and fades; [progress_notice_close](api:ui.progress_notice_close)
takes it away at once, which is what a job that failed should do - leaving a bar
half full over the window saying nothing is worse than saying it stopped.

The card is for the percentage; what the job is doing belongs on the tab, and a
``terminal`` under an ``overlay`` is the plainest way to show it (see
[Tabs, pinned and plain](guide:tabs)).

## Wearing a picture

One name, in either of two folders: the addon's id and then ``icon.png``, so
``mytool`` is worn by ``mytoolicon.png``. The file goes in an ``assets`` folder
beside the addon's own source, in a subfolder named after the addon -
``assets/mytool/mytoolicon.png`` under the folder the ``.py`` sits in, which for
a store addon is ``addon.store/assets/mytool/mytoolicon.png`` - and the program's
own ``assets/mytoolicon.png`` is tried too, which is where an addon shipped
inside the program keeps its picture. Neither name is written anywhere in the
addon's code: ``addon_common.find_addon_picture`` is the lookup that tries both
folders, and a square PNG - the marks the program ships are 128x128 - is what
the tab draws. A file named anything else is declared instead:

    ADDON = {..., "icon": "oldname.png"}

Nothing is ever required. A tab with no picture draws the SVG document it sends
with [set_icon](api:tabs.set_icon), and the store shows the program's own
``storedefaultaddon.png``, so an addon is never filed under a blank box and still
found by eye. A compiled build keeps its assets packed inside it and
[api.paths.assets](api:paths.assets) is where they are read from;
``addon_common.find_icon`` takes a folder and an id and is the same rule for an
addon drawing a picture into a widget of its own, and
[api.session.app_icon](api:session.app_icon) is the program's own mark when it
wants to sit beside it.
"""),

    ("settings-pages", "A page in Settings", """
# A page in Settings

``api.settings_pages`` builds a page of the app's Settings window: the same
controls as a tab, on a page that stands beside the program's own categories -
outside **Installed addons**, the page that now keeps only the addons that ship
inside the program, and outside the store's Addon manager, where an installed
addon's switches and its declared form live.

    api.settings_pages.add("planner", title="My planner", spec={
        "kind": "form",
        "children": [
            {"label": "Days ahead", "kind": "spin", "id": "days"},
        ],
    })

- The page is the app's furniture: it goes away by itself when the addon is
  switched off or removed, so an addon never has to clean it up.
- Its controls report to the same ``on_widget`` hook, with the page's name where
  a tab's would be.
- [activate](api:settings_pages.activate) opens Settings on the page,
  [set_title](api:settings_pages.set_title) renames it, and
  [widget](api:settings_pages.widget) / [set_widget](api:settings_pages.set_widget)
  and [widget_value](api:settings_pages.widget_value) read and write its
  controls exactly as a tab's.
- A page wears no icon: [set_icon](api:settings_pages.set_icon) says so and
  answers False.

For one or two switches use [api.ui.set_setting_widget](api:ui.set_setting_widget)
instead - the same controls, but under the addon's own row.
"""),

    ("themes", "Themes: the look of the app", """
# Themes: the look of the app

A look is one small JSON file - ``themes/<name>.theme``, beside the program. The
app reads the file it is drawn from when it starts, and writes
``themes/default.theme`` when the folder holds none, so there is always something
to copy and always something to fall back on.

    {
      "name": "deep sea",
      "author": "fligma",
      "blurb": "dark blues, soft edges",
      "colors": {"accent": "#2b8aa8", "base": "#0d1b2a"},
      "tabs": {"icon_px": 30, "radius_px": 8, "captions": false},
      "styles": "QToolTip { color: #e8f4f8; }"
    }

What the file does not say comes from the shipped look, so two colours in it make
a whole, usable theme. The three parts each own:

- ``colors`` - every colour the interface is painted with, by its token name.
- ``tabs`` - the shape of the tab bar: how big a tab's picture is, its air and
  corners, and whether it shows its name and its connection dots at all.
- ``styles`` - a few lines of sheet for whatever the first two do not reach.

## Who owns what

The theme owns the furniture; the addon owns its own page. A tab's icon size, its
colours and whether it carries a caption come from the theme and not from the
addon - [api.tabs.set_icon](api:tabs.set_icon) hands over a picture and the theme
decides how big it is drawn, so an addon that sizes its own mark is fighting the
look rather than wearing it.

What an addon does choose is the look of its tab's content, and the way to keep
that in step with the theme is to ask for the colours instead of writing them
down:

    colors = api.theme.colors()
    sheet = "QPushButton { background: %s; }" % colors["raise"]

## Reading and writing a look

- [names](api:theme.names), [exists](api:theme.exists) and
  [folder](api:theme.folder) are the files; ``folder()`` makes the folder on the
  way, so an addon always has somewhere to write.
- [read](api:theme.read) is one theme complete, every token filled in;
  [raw](api:theme.raw) is the file as written, which is what an editor shows.
- [colors](api:theme.colors) and [tabs](api:theme.tabs) are the two halves as
  dicts, [color](api:theme.color) one token by name, and
  [groups](api:theme.groups) and [token_knobs](api:theme.token_knobs) the same
  thing in the shapes an editor lists them in - colour blocks with their hints,
  and each tab knob marked as pixels or as a switch.
- [validate](api:theme.validate) says what is wrong with a candidate, one line
  each, before it is written; [save](api:theme.save) refuses anything it dislikes
  unless it is told to force.
- [use](api:theme.use) draws the window from a theme and keeps the choice for
  this profile. [copy](api:theme.copy), [rename](api:theme.rename) and
  [remove](api:theme.remove) move the files; the shipped ``default`` stays.
- [templates](api:theme.templates) are the looks a new theme may be begun from,
  and [template_text](api:theme.template_text) is one as the text of a file.
- [stylesheet](api:theme.stylesheet) is the whole sheet the app applies, and
  [sheet_of](api:theme.sheet_of) is what a draft still being edited would give.
- [reference](api:theme.reference) is this list, read off the running API, for an
  addon that wants to document the namespace the way the Theme Manager's Docs tab
  does.

## When the look moves

Applying a theme sends [theme_changed](api:events.on) with
``{"theme": worn, "wanted": asked}``: what the window is drawn from now, and the
name that was asked for, which differ only when that theme has no file. Anything
that painted itself with colours read earlier should read them again there; a tab
built from a spec and coloured by the app's own sheet needs nothing, because the
window repaints it under it.

The program's editor for all of this is the bundled **Theme Manager** addon. It
works on one draft at a time - a colour, a knob, or the JSON direct - and nothing
reaches the disk until Save, or the window until Apply, so a look can be ruined
without anyone noticing.
"""),

    ("config", "Config, settings and defaults", """
# Config, settings and defaults

Two stores, and picking the right one is most of it:

- [api.config](api:config.all) is the addon's own file,
  ``addons/_data/<id>/config.json``. It belongs to the installation rather than
  to one profile, is what the form on its row edits - the Settings panel for an
  addon that came with the program, **Edit settings** on the store's Addon
  manager for one installed later - and is a plain JSON file a
  user can open and change.
- [api.settings](api:settings.all) is the addon's private keys inside the
  profile's record - per profile, never shown to the user, good for counters
  and last-used values.

Declare what the user may change once, from ``init``:

    FIELDS = [{"key": "greeting", "label": "Text to show", "kind": "text",
               "hint": "What the button prints"}]
    DEFAULTS = {"greeting": "Hello"}

    values = api.config.declare(FIELDS, DEFAULTS, title="My addon")

``declare`` writes the file with the defaults when it is missing, keeps a side
copy of the schema so the form still shows while the addon is off, and returns
the merged values. Kinds: ``text``, ``number``, ``bool``, ``choice``, ``list``,
``multiline``. A dotted key - ``"window.width"`` - reads and writes inside that
section of the file.

- [get](api:config.get), [update](api:config.update) and
  [pop](api:config.pop) work on the merged view;
  [stored](api:config.stored) is the file with nothing filled in.
- [text](api:config.text) and [save_text](api:config.save_text) are the file as
  JSON, for an editor; anything invalid is refused.
- When the user changes the form the app calls ``on_config`` - rebuild what the
  tab shows there.
"""),

    ("events", "Events and hooks", """
# Events and hooks

    api.events.on("ssh_connected", lambda payload: ...)

[on](api:events.on) returns the callable that unregisters again;
[off](api:events.off) drops what the addon had for one event, and
[names](api:events.names) lists everything the app broadcasts. A listener runs on
the GUI thread and is caught if it raises - the failure is written to the log
against the addon's name rather than taking the window down.

The same message arrives two ways:

- ``on_event(name, payload)`` is told everything.
- ``on_<name>(payload)`` - ``on_ssh_connected`` - is told just that one.

Keep a hook short; it runs in the app's event loop. Longer work goes to
[api.schedule.after](api:schedule.after) or to a device call with
``block=False``, and anything that has to wait for the user goes through
[api.ui](api:ui.question) rather than a sleep.
"""),

    ("device", "The device: SSH and SFTP", """
# The device: SSH and SFTP

The manifest says which transports an addon needs, and both the app-wide switch
and the addon's own have to be on;
[is_connected](api:device.is_connected) says whether a call would go through
right now.

    api.device.run("cat /etc/openwrt_release")
    # {'code': 0, 'out': '...', 'err': ''}

Every call runs off the GUI thread. [run](api:device.run) waits and returns the
result; [run_async](api:device.run_async) hands it to ``on_done`` later and lets
the interface breathe - prefer that one in a button.

Files: [list](api:device.list), [exists](api:device.exists),
[stat](api:device.stat), [read_text](api:device.read_text),
[write_text](api:device.write_text), [append_text](api:device.append_text),
[mkdir](api:device.mkdir), [remove](api:device.remove),
[rename](api:device.rename), [upload](api:device.upload),
[download](api:device.download). Paths are relative to the profile's remote root
unless ``outside_root=True``, and a path is never allowed to delete the root
itself.

    with api.device.open() as conn:
        conn.run("opkg update")
        conn.run("opkg install foo")

[open](api:device.open) holds one session for several calls instead of making
one per call, and [shell_send](api:device.shell_send) types into the terminal
tab when the user should watch it happen.
"""),

    ("test", "Testing an addon", """
# Testing an addon

The Test panel runs ``addons/_tests/test_<id>.py`` as its own program, using the
recording stub in ``addon_client``: the addon is loaded and driven, nothing is
drawn, nothing is sent to a device, and every call it made is written down. So
the test checks what the addon *asked for*, which is the part an addon author
owns.

    api = addon_client.stub_api("myaddon", config={"greeting": "hi"})
    addon = addon_client.load_addon(SOURCE)
    addon.init(api)
    check(api.called("tabs.add_tab"), "init added no tab")
    addon.on_widget("My addon", "go", "clicked", {})
    check(any("hi" in line for line in api.logs), "the button said nothing")

- ``stub_api(id, config=..., settings=..., answers=...)``: ``config`` is what
  the config file would hold, ``settings`` the profile's keys, and ``answers``
  what ``ui.question`` / ``ui.choose`` reply.
- ``api.called(name)``, ``api.times(name)``, ``api.calls`` and ``api.logs`` are
  what the addon asked for. A stub records and changes no state of its own, so
  hand it the values the addon will read back.
- Check everything and print every failure: one run should say all is well, not
  stop at the first crack.
- The same script runs from the command line, which is what the store's index
  and the app's CI can do before an addon is published.
"""),

    ("build", "Compiling and publishing", """
# Compiling and publishing

**Compile** builds the addon with PyInstaller into ``addons/<id>.exe`` beside
its source, from a generated launcher that calls ``addon_client.serve``. The id
does not change, so exactly one of the two copies runs, and *Which copy runs*
above says which; the choice is kept in ``config/_build.json``.

A build carries a plain copy of the protocol modules it imports
(``addon_common``, ``addon_client``). A program built without that copy says so
rather than start a test that cannot finish.

The picture a program is told to wear follows the rule above: the addon's own
``<id>icon.png`` when it brought one, Berry Manager's mark otherwise. On Windows
either is rewrapped as an ``.ico`` first, because PyInstaller converts a PNG only
when Pillow happens to be installed and this project never asks for it. A
picture that cannot be made to fit costs a build its icon and not its program,
and an asset that will not download is said and left out of the install.

## Publishing on the addon store

The store reads one or more GitHub repositories, each with an ``index.json`` in
its store folder:

- ``tools/gen_addon_store_index.py`` writes the index from the addons in a
  checkout - their manifests, their icons, their test scripts.
- ``addons/_data/<id>/config.json`` and ``fields.json`` travel with the file, so
  the addon's row on the store's Addon manager can show its settings form before
  the program has ever run.
- A source is a repository: owner, repository, branch or tag, and the store
  folder inside it. **Sources...** on the store's tab adds one, puts it first or
  drops it, and the first source wins an id that two of them publish.

Installing fetches the addon from the source its catalog row came out of, then
runs its test script against the harness before the app starts it.
"""),
)


# --- the API reference: read off the protocol module as it runs ---

#: Which guide an addon should read after each namespace, by the attribute the
#: namespace sits on. ``""`` is ``api`` itself, ``conn`` the connection object.
GUIDE_FOR_NAMESPACE = {
    "": "anatomy",
    "session": "start",
    "device": "device",
    "ui": "notice",
    "tabs": "tabs",
    "window": "popout",
    "events": "events",
    "settings": "config",
    "settings_pages": "settings-pages",
    "theme": "themes",
    "config": "config",
    "schedule": "anatomy",
    "paths": "anatomy",
    "peers": "anatomy",
    "conn": "device",
}

#: The reference is built once per run: the module cannot change under a window
#: that is open, and rebuilding it walks a hundred docstrings for nothing.
_API_REFERENCE: list[dict] | None = None

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^) ]+)\)")
_CODE_RE = re.compile(r"``([^`]+)``")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _escape(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _inline(text: Any) -> str:
    """The inline markup a guide or a docstring is allowed to use."""
    out = _escape(text)
    out = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    return _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)


def _html(text: Any) -> str:
    """A page out of the small slice of markdown the guides and docstrings are
    written in: headings, bullets, indented code and ``inline`` markup."""
    out: list[str] = []
    bullets: list[str] = []
    indented: list[str] = []

    def close() -> None:
        if bullets:
            out.append("<ul>" + "".join(bullets) + "</ul>")
            bullets.clear()
        if indented:
            out.append("<pre>" + "\n".join(indented) + "</pre>")
            indented.clear()

    for raw in str(text or "").expandtabs().rstrip().splitlines():
        if raw.startswith(("    ", "\t")):
            # An indented run is code: keep it, and drop the list that was open.
            if bullets:
                out.append("<ul>" + "".join(bullets) + "</ul>")
                bullets.clear()
            indented.append(_escape(raw.strip()))
            continue
        close()
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            bullets.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if line.startswith("# "):
            out.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("## "):
            out.append(f"<h3>{_inline(line[3:])}</h3>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    close()
    return "\n".join(out)


def _addon_api_module() -> Any:
    """The protocol module the app itself runs, or ``None`` when it cannot be had."""
    try:
        import addon_api
    except Exception:                            # a program without its protocol
        return None
    return addon_api


def _api_namespaces() -> list[tuple[str, str]]:
    """``(attribute, class name)`` for every namespace on api, in the order the
    app builds them.

    Read out of ``AddonAPI.__init__``'s bytecode rather than its text: a packaged
    program carries no source to search, and the instructions are the same in a
    checkout and in a build.
    """
    module = _addon_api_module()
    if module is None:
        return []
    classes = {name for name, value in vars(module).items()
               if inspect.isclass(value) and name.endswith("API")}
    found: list[tuple[str, str]] = []
    wanted = ""
    for instruction in dis.get_instructions(module.AddonAPI.__init__):
        if instruction.opname == "LOAD_GLOBAL":
            wanted = str(instruction.argval or "")
        elif instruction.opname == "STORE_ATTR":
            if wanted in classes and wanted not in {one[1] for one in found}:
                found.append((str(instruction.argval), wanted))
            wanted = ""
    return found


def _signature_of(func: Any) -> str:
    """The call as an addon writes it, with the hidden ``self`` taken out."""
    try:
        text = str(inspect.signature(func))
    except (TypeError, ValueError):                # a built-in has no signature
        return "(...)"
    if text.startswith("(self, "):
        return "(" + text[len("(self, "):]
    if text.startswith("(self)"):
        return "()" + text[len("(self)"):]
    return text


def _api_entry(name: str, func: Any, kind: str) -> dict:
    """One method or property, with the summary lifted from its docstring."""
    doc = inspect.getdoc(func) or ""
    lines = [line.rstrip() for line in doc.splitlines()]
    return {
        "name": name,
        "kind": kind,
        "call": f"{name}{_signature_of(func)}" if kind == "method" else name,
        "summary": lines[0] if lines else "(nothing said about this one yet)",
        "detail": "\n".join(lines[1:]).strip(),
    }


def _namespace_page(addr: str, title: str, cls: Any) -> dict:
    """One namespace: its methods and properties, in the order the class defines
    them, with the private machinery left out."""
    entries: list[dict] = []
    for name, value in vars(cls).items():
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, property):
            if value.fget is None or name.startswith("_"):
                continue
            entries.append(_api_entry(name, value.fget, "property"))
        elif inspect.isfunction(value):
            if name.startswith("_"):
                continue
            entries.append(_api_entry(name, value, "method"))
    lines = (inspect.getdoc(cls) or "").splitlines()
    return {"addr": addr, "title": title, "cls": cls.__name__,
            "summary": lines[0].strip() if lines else "", "entries": entries}


def api_reference() -> list[dict]:
    """The whole API as the Docs panel lays it out.

    ``api`` itself first - it has three calls of its own - then every namespace
    in the order the app builds them, then the connection object that
    ``api.device.open()`` hands out, which an addon reads like any namespace.
    """
    global _API_REFERENCE
    if _API_REFERENCE is not None:
        return _API_REFERENCE
    module = _addon_api_module()
    pages: list[dict] = []
    if module is not None:
        pages.append(_namespace_page("", "api", module.AddonAPI))
        for attr, cls_name in _api_namespaces():
            cls = getattr(module, cls_name, None)
            if inspect.isclass(cls):
                pages.append(_namespace_page(attr, f"api.{attr}", cls))
        connection = getattr(module, "Connection", None)
        if inspect.isclass(connection):
            pages.append(_namespace_page(
                "conn", "connection - from api.device.open()", connection))
    _API_REFERENCE = pages
    return pages


def api_entry(key: str) -> tuple[dict, dict] | None:
    """``(namespace, entry)`` for a reference key such as ``tabs.add_tab``.

    A key with no dot is one of the calls on ``api`` itself (``log``, ``error``,
    ``call``); when nothing at that level answers to it, the namespaces are
    tried in order, so a link whose prefix was left off still opens a page.
    """
    head, sep, tail = str(key).partition(".")
    pages = api_reference()
    wanted = tail if sep else head
    if sep:
        ordered = [page for page in pages if page["addr"] == head]
    else:
        ordered = ([page for page in pages if page["addr"] == ""]
                   + [page for page in pages if page["addr"] != ""])
    for page in ordered:
        for entry in page["entries"]:
            if entry["name"] == wanted:
                return page, entry
    return None


def api_counts() -> tuple[int, int]:
    """How much of the API the panel has to show: namespaces and entries."""
    pages = api_reference()
    return len(pages), sum(len(page["entries"]) for page in pages)


# --- the tab itself ---

class AddonDevWidget(QWidget):
    """The Addon Dev panel: one addon selected at a time, four views of it."""

    #: What 'find' in the Docs pane reads beyond the row's own label: the key,
    #: the summary and the call, kept in one lower-case string per row.
    SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.mgr = window.addons
        self.selected = ""
        self.build: _Stream | None = None
        self.test: _Stream | None = None
        self._loading = False
        # Set by 'Test, then compile' to the addon the test was started for, and
        # cleared by whatever the test ends with: a build may only start once that
        # addon's own test has passed.
        self._then_build = ""
        # What the index holds, so a refresh can tell a changed list from the same
        # list with a cell or two different (see refresh).
        self._shown_ids: list[str] = []
        # The Docs tree, by the key each line carries, and the folds the tree had
        # before 'find' opened them. Both are filled by _fill_docs.
        self._docs_items: dict[str, QTreeWidgetItem] = {}
        self._docs_folds: dict[str, bool] | None = None

        # A table, four panes and many notes do not fit a short window, so all
        # of it lives in one scroll area and takes the height it needs.
        self.body = QWidget()
        root = QVBoxLayout(self.body)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_index())

        self.panes = QTabWidget()
        self.panes.setMinimumHeight(PANES_MIN_H)
        self.panes.addTab(self._build_details(), "Details")
        self.panes.addTab(self._build_build(), "Build & test")
        self.panes.addTab(self._build_docs(), "Docs & log")
        self.panes.addTab(self._build_processes(), "Processes")
        root.addWidget(self.panes, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.scroll)

        # One short timer for the child processes, one longer for the tables:
        # a build's output should feel live, a table list does not. The short
        # one only earns its keep while a child is actually running (see
        # _watch_streams), so a tab that builds nothing ticks for nothing.
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(400)
        self.poll_timer.timeout.connect(self._poll_streams)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh)

    # --- closing ---

    def shutdown(self) -> None:
        """The app is going: no build or test child, nor anything it spawned,
        may be left running behind it."""
        self.poll_timer.stop()
        for stream in (self.build, self.test):
            if stream is not None:
                stream.kill()

    # --- the widgets ---

    def _build_index(self) -> QWidget:
        """The list on top: every addon in the folder, and the few actions that
        concern the folder rather than the one addon picked."""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        line = QHBoxLayout()
        line.setSpacing(6)
        self.index_filter = QLineEdit()
        self.index_filter.setPlaceholderText("filter")
        self.index_filter.setClearButtonEnabled(True)
        self.index_filter.setToolTip(
            "Show only the addons whose name, id, version, author, description or "
            "file answers to what is typed here. Every word has to turn up; the "
            "counts beside this box always speak for all of them.")
        self.index_filter.textChanged.connect(lambda _=False: self.refresh())
        line.addWidget(self.index_filter, 1)
        self.count_note = note_label(QLabel(""))
        line.addWidget(self.count_note)
        column.addLayout(line)

        self.table = make_table(
            ["Addon", "Name", "Version", "Runs", "State", "Where"],
            stretch=1, widths={5: 260})
        self.table.setIconSize(QSize(zoom_size(LIST_ICON), zoom_size(LIST_ICON)))
        self.table.itemSelectionChanged.connect(self._selected_row)
        fit_rows(self.table)
        column.addWidget(self.table, 0)

        row = QHBoxLayout()
        for text, tip, slot in (
            ("Rescan", "Read the addons folder again", self.rescan),
            ("New addon...", "Write a new addon, with a header and a test script",
             self.new_addon),
            ("Open folder", "The addons folder, where an addon is dropped", self.open_folder),
            ("Docs", "The API reference and the guides, read off the running app",
             self.show_docs),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            if text == "New addon...":
                button.setObjectName("primaryAction")
            row.addWidget(button)
        row.addStretch(1)
        self.state_note = QLabel("")
        self.state_note.setStyleSheet("color: #8f8f8f;")
        row.addWidget(self.state_note, 1)
        column.addLayout(row)
        return holder

    def _build_details(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        run = QGroupBox("This addon")
        tip_on_title(run,
                     "The doing part of the addon picked above, gathered where its "
                     "details are read: switch it on or off, load its file again, "
                     "come to the tab it opened, or edit the source it runs from. "
                     "The store's Addon manager page has the same switches for "
                     "every addon at once.")
        line = QHBoxLayout(run)
        line.setContentsMargins(14, 10, 12, 8)
        line.setSpacing(10)
        self.details_mark = QLabel()
        side = zoom_size(LIST_ICON)
        self.details_mark.setFixedSize(QSize(side, side))
        line.addWidget(self.details_mark)
        self.id_label = QLabel("-")
        self.id_label.setStyleSheet("font-weight: bold;")
        line.addWidget(self.id_label)
        line.addStretch(1)
        self.enabled_switch = QCheckBox("Enabled")
        self.enabled_switch.setToolTip(
            "The same switch as the Addon manager's row: it starts the addon and keeps "
            "its tabs, or stops it and takes them away")
        self.enabled_switch.toggled.connect(self._set_enabled)
        line.addWidget(self.enabled_switch)
        for text, tip, slot in (
            ("Restart", "Load the file again, without restarting the app", self.mgr_restart),
            ("Show tab", "Jump to the tab this addon added", self.show_tab),
            ("Open source", "Edit the addon file itself", self.open_source),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            line.addWidget(button)
        layout.addWidget(run)

        box = QGroupBox("Addon details")
        tip_on_title(box,
                     "The manifest the app reads when it loads the addon. It is saved "
                     "into the ADDON dict of the addon's own file when the file has "
                     "one, and otherwise into the <id>.json manifest beside it.")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(14, 10, 12, 8)
        inner.setSpacing(6)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        self.name_input = QLineEdit()
        self.version_input = QLineEdit()
        self.author_input = QLineEdit()
        self.description_input = QLineEdit()
        self.notes_input = QLineEdit()
        self.api_input = QLineEdit()
        self.api_input.setPlaceholderText("1")
        self.api_input.setFixedWidth(70)
        for label, field in (("Name", self.name_input), ("Version", self.version_input),
                             ("Author", self.author_input),
                             ("Description", self.description_input),
                             ("Notes", self.notes_input), ("API", self.api_input)):
            form.addRow(label, field)
        inner.addLayout(form)
        self.target_note = note_label(QLabel(""))
        inner.addWidget(self.target_note)
        row = QHBoxLayout()
        for text, tip, slot in (
            ("Save details", "Write the manifest back to the file named above",
             self.save_details),
            ("Reload", "Fill the form again from what is on disk", self.reload_details),
            ("Open manifest", "Edit it outside Berry Manager", self.open_manifest),
            ("Open source", "The addon's own code", self.open_source),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.details_note = QLabel("")
        row.addWidget(self.details_note, 1)
        inner.addLayout(row)
        layout.addWidget(box)

        config_box = QGroupBox("Config file")
        tip_on_title(config_box,
                     "addons/_data/<id>/config.json: this addon's own settings, shared "
                     "by every profile. Written as plain JSON, so the addon can read it "
                     "and a user can edit it. The addon's row in the Addon manager offers "
                     "the same thing as a form, once the addon has declared its keys.")
        column = QVBoxLayout(config_box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        self.config_path = note_label(QLabel(""), "#aaaaaa")
        self.config_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.config_path)
        self.config_edit = monospace(QPlainTextEdit())
        self.config_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        column.addWidget(self.config_edit, 1)
        row = QHBoxLayout()
        for text, tip, slot in (
            ("Save config", "Write the JSON above, and tell the addon it changed",
             self.save_config),
            ("Reload", "Read the file again, dropping unsaved edits", self.reload_config),
            ("Open file", "Edit it outside Berry Manager", self.open_config),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.config_note = QLabel("")
        row.addWidget(self.config_note, 1)
        column.addLayout(row)
        layout.addWidget(config_box, 1)
        return page

    def _build_compile(self) -> QWidget:
        """The left half of the Build & test pane: which copy runs, and PyInstaller."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        which = QGroupBox("Which copy runs")
        tip_on_title(which,
                     "A compiled addon keeps the same id as the source it was built "
                     "from, so exactly one of the two is started. The choice is kept "
                     "in config/_build.json.")
        row = QHBoxLayout(which)
        row.setContentsMargins(14, 10, 12, 8)
        row.setSpacing(14)
        self.source_radio = QRadioButton("the source (.py)")
        self.compiled_radio = QRadioButton("the compiled program")
        self.source_radio.setToolTip("Import the .py file into the app")
        self.compiled_radio.setToolTip("Start the built program as its own process")
        for button in (self.source_radio, self.compiled_radio):
            button.toggled.connect(self._prefer_changed)
            row.addWidget(button)
        row.addStretch(1)
        layout.addWidget(which)
        self.runs_note = note_label(QLabel(""))
        layout.addWidget(self.runs_note)

        box = QGroupBox("Compile to a release")
        tip_on_title(box,
                     "PyInstaller is started for this one addon with a generated "
                     "launcher that calls addon_client.serve. The staging folder is "
                     "addons/_build/<id> and the program lands beside the source, so "
                     "the runtime finds it under the same id.")
        column = QVBoxLayout(box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        options = QHBoxLayout()
        self.onefile_check = QCheckBox("one file")
        self.onefile_check.setChecked(True)
        self.onefile_check.setToolTip("--onefile; uncheck for a folder with the "
                                       "program and its libraries")
        self.console_check = QCheckBox("console")
        self.console_check.setChecked(True)
        self.console_check.setToolTip(
            "Keep the standard streams. The app starts an addon with no window "
            "either way, and a build without a console may lose the protocol pipe")
        self.icon_check = QCheckBox("app icon")
        self.icon_check.setChecked(True)
        self.icon_check.setToolTip("Use Berry Manager's icon, redrawn as an .ico "
                                   "on Windows when the shipped picture is a PNG")
        self.prefer_check = QCheckBox("run the compiled copy when it is ready")
        for button in (self.onefile_check, self.console_check, self.icon_check,
                       self.prefer_check):
            options.addWidget(button)
        options.addStretch(1)
        column.addLayout(options)

        extra = QHBoxLayout()
        extra.addWidget(QLabel("extra modules"))
        self.hidden_input = QLineEdit()
        self.hidden_input.setPlaceholderText(
            "anything the addon imports that PyInstaller cannot see, e.g. yaml, mylib")
        extra.addWidget(self.hidden_input, 1)
        column.addLayout(extra)

        actions = QHBoxLayout()
        self.compile_button = QPushButton("Compile addon")
        self.compile_button.setObjectName("primaryAction")
        self.compile_button.setToolTip("Start PyInstaller for this addon")
        self.compile_button.clicked.connect(self.compile_addon)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the build that is running")
        self.cancel_button.clicked.connect(self.cancel_build)
        self.cancel_button.setEnabled(False)
        self.clean_button = QPushButton("Clean staging")
        self.clean_button.setToolTip("Throw away addons/_build for this addon")
        self.clean_button.clicked.connect(self.clean_staging)
        self.reveal_button = QPushButton("Show the file")
        self.reveal_button.setToolTip("Open the addons folder, where the program is put")
        self.reveal_button.clicked.connect(self.open_folder)
        self.build_guide_button = QPushButton("Build guide")
        self.build_guide_button.setToolTip(
            "What a compiled addon carries, and how to publish it on the store")
        self.build_guide_button.clicked.connect(lambda: self._open_guide("build"))
        for button in (self.compile_button, self.cancel_button, self.clean_button,
                       self.reveal_button, self.build_guide_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.build_note = QLabel("")
        actions.addWidget(self.build_note, 1)
        column.addLayout(actions)

        self.build_log = monospace(QPlainTextEdit())
        self.build_log.setReadOnly(True)
        self.build_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        column.addWidget(self.build_log, 1)
        layout.addWidget(box, 1)
        return page

    def _build_test(self) -> QWidget:
        """The right half of the Build & test pane: the addon's own test script."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        box = QGroupBox("Test script")
        tip_on_title(box,
                     "addons/_tests/test_<id>.py: the addon is loaded and driven "
                     "through the recording stub in addon_client, so the test works "
                     "with no window open and no device connected. It prints every "
                     "call the addon made and fails on the checks it did not pass.")
        column = QVBoxLayout(box)
        column.setContentsMargins(14, 10, 12, 8)
        column.setSpacing(6)
        self.test_path = note_label(QLabel(""), "#aaaaaa")
        self.test_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.test_path)
        row = QHBoxLayout()
        self.create_test_button = QPushButton("Create test script")
        self.create_test_button.setToolTip("Write the template for this addon")
        self.create_test_button.clicked.connect(self.create_test)
        self.run_test_button = QPushButton("Run test")
        self.run_test_button.setObjectName("primaryAction")
        self.run_test_button.setToolTip("Run it with this interpreter and show its output")
        self.run_test_button.clicked.connect(self.run_test)
        self.then_compile_button = QPushButton("Test, then compile")
        self.then_compile_button.setToolTip(
            "Run the test and start PyInstaller only if it passes - the gate the "
            "store puts in front of publishing a compiled addon, so a build made "
            "here is a build that could be shipped")
        self.then_compile_button.clicked.connect(self.run_test_then_build)
        self.open_test_button = QPushButton("Open test")
        self.open_test_button.setToolTip("Edit it outside Berry Manager")
        self.open_test_button.clicked.connect(self.open_test)
        self.test_folder_button = QPushButton("Open folder")
        self.test_folder_button.setToolTip("addons/_tests")
        self.test_folder_button.clicked.connect(self.open_test_folder)
        self.test_guide_button = QPushButton("Testing guide")
        self.test_guide_button.setToolTip(
            "What the stub records, and the checks a test script should make")
        self.test_guide_button.clicked.connect(lambda: self._open_guide("test"))
        for button in (self.create_test_button, self.run_test_button,
                       self.then_compile_button, self.open_test_button,
                       self.test_folder_button, self.test_guide_button):
            row.addWidget(button)
        row.addStretch(1)
        self.test_note = QLabel("")
        row.addWidget(self.test_note, 1)
        column.addLayout(row)
        layout.addWidget(box)
        self.test_log = monospace(QPlainTextEdit())
        self.test_log.setReadOnly(True)
        self.test_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.test_log, 1)
        return page

    def _build_build(self) -> QWidget:
        """Build and test as one pane: a check and the release it earns belong
        side by side, not two tabs apart."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        note = note_label(QLabel(
            "The test is the gate: a compiled addon is only as good as the script "
            "that drives it, and 'Test, then compile' keeps them in that order."))
        layout.addWidget(note)
        split = QSplitter()
        split.addWidget(self._build_compile())
        split.addWidget(self._build_test())
        # Two halves of the same job, evenly read, with a line between them that
        # can be seen and pulled - and keeps the halves even as the window moves,
        # rather than the sizes Qt was asked for once at build time.
        fit_panes(split, [1, 1])
        layout.addWidget(split, 1)
        return page

    def _build_log(self) -> QWidget:
        """The lower half of the Docs & log pane: this addon's own lines."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("filter"))
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("only lines containing this")
        self.log_filter.textChanged.connect(lambda _=False: self.refresh_log())
        row.addWidget(self.log_filter, 1)
        self.log_warnings = QCheckBox("errors only")
        self.log_warnings.toggled.connect(lambda _=False: self.refresh_log())
        row.addWidget(self.log_warnings)
        self.log_follow = QCheckBox("follow")
        self.log_follow.setToolTip("Read the log again on every refresh")
        self.log_follow.setChecked(True)
        row.addWidget(self.log_follow)
        for text, tip, slot in (
            ("Refresh", "Read the log file again", self.refresh_log),
            ("Reset", "Start over from the end of the file", self.reset_log),
            ("Open log", "The whole log, in the system's text viewer", self.open_log),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.log_view = monospace(QPlainTextEdit())
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view, 1)
        note = QLabel(f"the lines the addon wrote with api.log, taken from {LOG_FILE}")
        note.setStyleSheet("color: #8f8f8f;")
        layout.addWidget(note)
        return page

    def _build_processes(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        first = QGroupBox("Addon processes")
        tip_on_title(first,
                     "Every addon the app started as a child program. An addon that "
                     "closed by itself keeps its exit code here.")
        column = QVBoxLayout(first)
        column.setContentsMargins(12, 10, 12, 8)
        self.process_table = make_table(
            ["Addon", "pid", "State", "Exit", "Uptime", "Requests", "Command"],
            stretch=6)
        column.addWidget(self.process_table)
        self.thread_note = note_label(QLabel(""))
        column.addWidget(self.thread_note)
        layout.addWidget(first, 1)

        second = QGroupBox("What the app holds for each addon")
        tip_on_title(second,
                     "Tabs, timers, menu entries and event listeners the app created "
                     "for an addon. They are all taken away again when the addon is "
                     "switched off, which is what this list is for checking.")
        column = QVBoxLayout(second)
        column.setContentsMargins(12, 10, 12, 8)
        self.holdings_table = make_table(
            ["Addon", "Kind", "Tabs", "Timers", "Menu", "Listeners"],
            stretch=5)
        column.addWidget(self.holdings_table)
        layout.addWidget(second, 1)
        return page

    def _build_docs(self) -> QWidget:
        """Docs and log as one pane: the reference to read, and under it the lines
        the addon being read about actually wrote."""
        page = QWidget()
        self.docs_pane = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._build_docs_side())
        split.addWidget(self._build_log())
        # The page being read above the lines it wrote, in about that order of
        # importance; the line is draggable, so either can be given the tab.
        fit_panes(split, [11, 6])
        layout.addWidget(split, 1)
        return page

    # --- docs: the guides and the reference generated from the code ---

    def _build_docs_side(self) -> QWidget:
        """The upper half of that pane: the tree down the left, the page it opens."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        pages, entries = api_counts()
        self.docs_note = note_label(QLabel(
            f"{max(0, pages - 1)} namespaces, {entries} methods and properties, read "
            "from addon_api as this program runs it - and the docstrings under "
            "them are the whole story, so the panel cannot go out of date."))
        layout.addWidget(self.docs_note)

        row = QHBoxLayout()
        row.addWidget(QLabel("find"))
        self.docs_filter = QLineEdit()
        self.docs_filter.setPlaceholderText("tabs, pinned, config, peers...")
        self.docs_filter.textChanged.connect(self._filter_docs)
        row.addWidget(self.docs_filter, 1)
        for text, tip, slot in (
            ("Start here", "The first page an addon author should read",
             lambda: self._show_docs_key("guide:start")),
            ("README", "The program's own manual, in the system's viewer or online",
             self.open_readme),
            ("On GitHub", "The manual as published, with the addon chapters",
             self.open_online_docs),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        split = QSplitter()
        self.docs_tree = QTreeWidget()
        self.docs_tree.setHeaderHidden(True)
        self.docs_tree.setColumnCount(1)
        self.docs_tree.setRootIsDecorated(True)
        self.docs_tree.setIndentation(DOCS_INDENT)
        self.docs_tree.setUniformRowHeights(True)
        self.docs_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.docs_tree.currentItemChanged.connect(self._docs_moved)
        self.docs_tree.setMinimumWidth(DOCS_MIN_W)
        split.addWidget(self.docs_tree)
        self.docs_view = QTextBrowser()
        self.docs_view.setOpenLinks(False)
        self.docs_view.setMinimumWidth(380)
        # QTextBrowser wraps to its own width and takes html, which is what the
        # generated pages are; the links below are handled by hand.
        self.docs_view.anchorClicked.connect(self._docs_link)
        split.addWidget(self.docs_view)
        # The tree keeps to about a quarter of the width and the page it opens
        # takes the rest, at every window size; a stretch factor only answers to
        # the room Qt has left over, which is why the pane used to drift.
        fit_panes(split, [240, 620])
        layout.addWidget(split, 1)

        self._fill_docs()
        return page

    def _add_docs_branch(self, key: str, text: str, tip: str,
                         expanded: bool = False) -> QTreeWidgetItem:
        """A group in the tree: it opens a page of its own and holds the lines
        under it, the way a folder holds the files it is named for.

        Bold and in the theme's accent, so the eye sorts the tree into its
        namespaces before it reads a single method name.
        """
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setToolTip(0, tip)
        item.setExpanded(expanded)
        item.setForeground(0, QBrush(QColor(
            core.ui.color("accent", default="#c51a4a"))))
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.docs_tree.addTopLevelItem(item)
        self._docs_items[key] = item
        return item

    def _add_docs_leaf(self, branch: QTreeWidgetItem, key: str, text: str,
                       tip: str = "", search: str = "") -> QTreeWidgetItem:
        """A page in the tree, addressed by the same key its links carry."""
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setToolTip(0, tip)
        # What 'find' reads: the key and the call as well as the label and the
        # first line, so typing 'pinned' still turns up add_tab, whose signature
        # is the place the option is actually spelled out.
        item.setData(0, self.SEARCH_ROLE,
                     f"{key} {text} {tip} {search}".casefold())
        branch.addChild(item)
        self._docs_items[key] = item
        return item

    def _docs_branches(self) -> list[QTreeWidgetItem]:
        """Every group in the tree, in the order the tree shows them."""
        return [one for one in (self.docs_tree.topLevelItem(index)
                                for index in range(
                                    self.docs_tree.topLevelItemCount()))
                if one is not None]

    def _fill_docs(self) -> None:
        """The tree down the left: the guides first, then one group per
        namespace, every method and property a line under the name it is
        actually called through."""
        self.docs_tree.blockSignals(True)
        self.docs_tree.clear()
        self._docs_items = {}
        guides = self._add_docs_branch(
            "page:guides", f"Guides ({len(GUIDES)})",
            "The pages that read like a manual, from the first addon to the "
            "last option.", expanded=True)
        for key, title, text in GUIDES:
            self._add_docs_leaf(
                guides, f"guide:{key}", title,
                text.strip().splitlines()[0] if text.strip() else title,
                search=text)
        for page in api_reference():
            branch = self._add_docs_branch(
                f"page:{page['addr'] or 'api'}",
                f"{page['title']} ({len(page['entries'])})",
                page["summary"] or "The calls of this namespace.")
            for entry in page["entries"]:
                addr = f"{page['addr']}." if page["addr"] else ""
                self._add_docs_leaf(branch, f"api:{addr}{entry['name']}",
                                    entry["name"], entry["summary"],
                                    search=entry["call"])
        self.docs_tree.blockSignals(False)
        self._show_docs_key("guide:start")

    def _filter_docs(self, text: str) -> None:
        """Only the pages that answer to what was typed, and the groups that
        still hold one; a group opens itself so a hit is never folded away.

        Every word typed has to turn up somewhere in the line - label, summary
        or call - which lets 'tabs pinned' narrow the tree to the one entry
        asked for. Emptying the box folds the groups back as they were, since
        the opening was the filter's doing and not whoever was reading.
        """
        words = str(text or "").strip().casefold().split()
        if words and self._docs_folds is None:
            self._docs_folds = {self._branch_key(one): one.isExpanded()
                                for one in self._docs_branches()}
        folds = self._docs_folds or {}
        self.docs_tree.blockSignals(True)
        for branch in self._docs_branches():
            hits = 0
            for index in range(branch.childCount()):
                leaf = branch.child(index)
                if leaf is None:
                    continue
                haystack = str(leaf.data(0, self.SEARCH_ROLE) or "")
                match = all(word in haystack for word in words)
                leaf.setHidden(not match)
                hits += 1 if match else 0
            branch.setHidden(bool(words) and not hits)
            branch.setExpanded(folds.get(self._branch_key(branch), True)
                               if not words else hits > 0)
        self.docs_tree.blockSignals(False)
        if not words:
            self._docs_folds = None

    @staticmethod
    def _branch_key(item: QTreeWidgetItem) -> str:
        """The key a line of the tree is addressed by, empty for none."""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _show_docs_key(self, key: str) -> bool:
        """Jump the tree to one page, by the key its links carry: its group is
        opened, the line is picked and the page is scrolled into view."""
        item = self._docs_items.get(str(key))
        if item is None:
            return False
        branch = item.parent()
        self.docs_tree.blockSignals(True)
        if branch is not None:
            branch.setHidden(False)
            branch.setExpanded(True)
        self.docs_tree.setCurrentItem(item)
        self.docs_tree.scrollToItem(
            item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.docs_tree.blockSignals(False)
        self._render_docs(str(key))
        return True

    def _docs_moved(self, current: QTreeWidgetItem | None,
                   _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        self._render_docs(self._branch_key(current))

    def _open_guide(self, key: str) -> None:
        """Come to the Docs & log pane and show one guide: the way the Build &
        test pane hands a developer the page that explains it."""
        self.panes.setCurrentWidget(self.docs_pane)
        self.docs_filter.blockSignals(True)
        self.docs_filter.setText("")
        self.docs_filter.blockSignals(False)
        self._show_docs_key(f"guide:{key}")

    def _docs_link(self, url: QUrl) -> None:
        """A link clicked in the page: inside the panel, into a file, or out to
        the browser - the panel itself never leaves the tab."""
        text = url.toString()
        if text.startswith(("guide:", "api:")):
            self._show_docs_key(text)
            return
        if text.startswith("file:"):
            self.open_path(Path(text[len("file:"):]))
            return
        QDesktopServices.openUrl(url)

    def _readme_link(self) -> str:
        """Where the manual is: the file beside the program, or its page online."""
        where = find_asset("README.md")
        return f"file:{where.as_posix()}" if where is not None else README_URL

    def _guide_page(self, key: str) -> str:
        """One guide, with the way back out at the bottom."""
        text = next((body for one, _title, body in GUIDES if one == key), "")
        back = ('<p><a href="guide:start">Start here</a> \u00b7 '
                '<a href="api:tabs.add_tab">api.tabs.add_tab</a> \u00b7 '
                f'<a href="{self._readme_link()}">the full README</a></p>')
        return _html(text) + back

    def _entry_page(self, key: str) -> str:
        """One method or property: how it is called, what its docstring says, and
        the neighbours an addon would read next to it."""
        found = api_entry(key)
        if found is None:
            return "<p>nothing in the API answers to that.</p>"
        page, entry = found
        addr = f"api.{page['addr']}" if page["addr"] else "api"
        usage = f"{addr}.{entry['call']}" if entry["kind"] == "method" \
            else f"{addr}.{entry['name']}"
        names = [one["name"] for one in page["entries"]]
        where = names.index(entry["name"])
        parts = [f"<h2>{_escape(entry['name'])}</h2>",
                 f"<p><i>{_escape(page['title'])} \u00b7 {entry['kind']}</i></p>",
                 f"<pre>{_escape(usage)}</pre>",
                 f"<p>{_inline(entry['summary'])}</p>"]
        if entry["detail"]:
            parts.append(_html(entry["detail"]))
        walk = []
        if where:
            previous = names[where - 1]
            walk.append(f'<a href="api:{page["addr"] + "." if page["addr"] else ""}{previous}">'
                        f'\u2190 {previous}</a>')
        if where + 1 < len(names):
            following = names[where + 1]
            walk.append(f'<a href="api:{page["addr"] + "." if page["addr"] else ""}{following}">'
                        f'{following} \u2192</a>')
        guide = GUIDE_FOR_NAMESPACE.get(page["addr"])
        if guide:
            title = next((one for one, text, _body in GUIDES if one == guide), "")
            walk.append(f'<a href="guide:{guide}">{_escape(title)}</a>')
        if walk:
            parts.append("<p>" + " \u00b7 ".join(walk) + "</p>")
        return "".join(parts)

    def _branch_page(self, key: str) -> str:
        """The page a group shows: every line under it, linked, so the tree can
        be read from either side of the split."""
        branch = self._docs_items.get(key)
        if branch is None:
            return "<p>nothing in the docs answers to that.</p>"
        parts = [f"<h2>{_escape(branch.text(0))}</h2>"]
        note = str(branch.toolTip(0) or "")
        if note:
            parts.append(f"<p>{_inline(note)}</p>")
        lines = []
        for index in range(branch.childCount()):
            leaf = branch.child(index)
            label = leaf.text(0)
            summary = str(leaf.toolTip(0) or "")
            if len(summary) > 96:
                summary = summary[:96].rsplit(" ", 1)[0] + "\u2026"
            lines.append(
                f'<li><a href="{self._branch_key(leaf)}">{_escape(label)}</a>'
                + (f" \u2014 {_inline(summary)}" if summary else "") + "</li>")
        parts.append("<ul>" + "".join(lines) + "</ul>")
        return "".join(parts)

    def _render_docs(self, key: str) -> None:
        kind, _, rest = str(key).partition(":")
        if not rest:
            return
        if kind == "guide":
            page = self._guide_page(rest)
        elif kind == "api":
            page = self._entry_page(rest)
        else:
            page = self._branch_page(key)
        self.docs_view.setHtml(page)

    def show_docs(self) -> None:
        """The Docs button above: come to this pane and read the selected addon."""
        self.panes.setCurrentWidget(self.docs_pane)

    def open_readme(self) -> None:
        """The manual: the file when the program carries one, the published page
        when it does not - a packaged build ships no markdown."""
        where = find_asset("README.md")
        if where is not None:
            self.open_path(where)
            return
        QDesktopServices.openUrl(QUrl(README_URL))

    def open_online_docs(self) -> None:
        QDesktopServices.openUrl(QUrl(README_URL + "#addons"))

    # --- the index ---

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh_timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.refresh_timer.stop()
        super().hideEvent(event)

    def current(self) -> Any:
        return self.mgr.find(self.selected)

    def refresh(self) -> None:
        """Re-read the runtime, keeping the selection and the scroll where they were.

        Only a list that changed in length or order is rebuilt. Handing Qt brand-new
        cells for every row every two seconds moves the selection anchor, so the row
        a person is reading flickers and a click can land on something else; the
        common case is the same addons, with a word or two changed.
        """
        wanted = self.selected
        rows = self.mgr.describe_all()
        self.count_note.setText(self._counts_text(rows))
        shown = self._visible_rows(rows)
        ids = [row["id"] for row in shown]
        rebuild = ids != self._shown_ids
        self.table.blockSignals(True)
        try:
            if rebuild:
                self.table.clearContents()
                self.table.setRowCount(len(ids))
                self._shown_ids = ids
            for index, row in enumerate(shown):
                self._fill_row(index, row, rebuild)
        finally:
            self.table.blockSignals(False)
        fit_rows(self.table)
        target = wanted if wanted in ids else (ids[0] if ids else "")
        if target and target == self.selected:
            # Same addon: only the parts that change on their own are touched.
            self.touch()
        else:
            self.select(target)
        self.refresh_processes(rows)

    def _visible_rows(self, rows: list[dict]) -> list[dict]:
        """The addons the filter box allows, in the order the runtime lists them.

        Every word typed has to turn up somewhere in the row - id, name, version,
        author, description or the file it runs from - which is the same rule the
        Docs filter above the reference follows.
        """
        words = str(self.index_filter.text() or "").strip().casefold().split()
        if not words:
            return list(rows)
        kept = []
        for row in rows:
            haystack = " ".join(str(row.get(key) or "") for key in (
                "id", "name", "version", "author", "description",
                "source")).casefold()
            if all(word in haystack for word in words):
                kept.append(row)
        return kept

    @staticmethod
    def _counts_text(rows: list[dict]) -> str:
        """The one line over the list: how many addons there are, and how they stand."""
        if not rows:
            return "no addons in the folder"
        running = sum(1 for row in rows if row["loaded"] and not row["error"])
        failed = sum(1 for row in rows if row["error"])
        parts = [f"{len(rows)} addon" + ("s" if len(rows) != 1 else ""),
                 f"{running} running"]
        if failed:
            parts.append(f"{failed} failed")
        parts.append(f"{len(rows) - running - failed} off")
        return " \u00b7 ".join(parts)

    @staticmethod
    def _row_values(row: dict) -> tuple:
        """The six words one row of the index shows, in their columns."""
        process = row.get("process") or {}
        state = "failed" if row["error"] else ("running" if row["loaded"] else "off")
        runs = ("process" if row["kind"] == "process" else "imported")
        if row.get("builtin"):
            # Came with the program, not dropped in.
            runs += " shipped"
        if process:
            runs += f" pid {process.get('pid') or '?'}"
        return (row["id"], row["name"], row["version"], runs, state,
                _display_path(row["source"]))

    def _fill_row(self, index: int, row: dict, fresh: bool) -> None:
        """One row of the index: the addon's picture, its words, its state colour.

        ``fresh`` says the table holds nothing for this row yet; otherwise the
        existing cells are kept and only the text that really differs is written.
        """
        mark = addon_icon(row)
        for column, text in enumerate(self._row_values(row)):
            item = None if fresh else self.table.item(index, column)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(index, column, item)
            if item.text() != str(text):
                item.setText(str(text))
            if column == 0:
                # The id travels as data, not as the text in the cell.
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                if not mark.isNull():
                    item.setIcon(mark)
            elif column == 4:
                item.setForeground(state_color(str(text)))

    def touch(self) -> None:
        addon = self.current()
        if addon is None:
            return
        info = self.mgr.describe(addon)
        self.state_note.setText(
            f"{info['id']} - {human_size(info['size'])}, "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(info['modified']))}")
        self.details_note.setText(info["error"] or "")
        self.details_note.setStyleSheet("color: #ffb020;" if info["error"] else "")
        self._load_run_panels(info, addon)
        if self.log_follow.isChecked():
            self.refresh_log()

    def select(self, addon_id: str) -> None:
        if addon_id and addon_id != self.selected:
            self.selected = addon_id
            for index in range(self.table.rowCount()):
                item = self.table.item(index, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == addon_id:
                    self.table.selectRow(index)
                    break
            self.load_panels()
        elif not addon_id:
            self.selected = ""
            self.load_panels()

    def _selected_row(self) -> None:
        index = self.table.currentRow()
        if index < 0:
            return
        item = self.table.item(index, 0)
        if item is None:
            return
        # The id travels in the first column as data, not as its text.
        addon_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        if addon_id != self.selected:
            self.selected = addon_id
            self.load_panels()

    def load_panels(self) -> None:
        """Fill every panel from the selected addon, without editing anything."""
        addon = self.current()
        self._loading = True
        try:
            if addon is None:
                self._clear_panels()
                return
            info = self.mgr.describe(addon)
            self.state_note.setText(
                f"{info['id']} - {human_size(info['size'])}, "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(info['modified']))}")
            self.id_label.setText(f"{info['name']}  ({info['id']})")
            self._show_mark(info)
            self.enabled_switch.blockSignals(True)
            self.enabled_switch.setChecked(bool(info["enabled"]))
            self.enabled_switch.blockSignals(False)
            details = manifest_values(addon)
            self.name_input.setText(details["name"])
            self.version_input.setText(details["version"])
            self.author_input.setText(details["author"])
            self.description_input.setText(details["description"])
            self.notes_input.setText(details["notes"])
            self.api_input.setText(details["api"])
            target = manifest_target(addon)
            self.target_note.setText(SHIPPED_NOTE if info["builtin"]
                                     else f"Saved to {target}")
            self.details_note.setText(info["error"] or "")
            self.details_note.setStyleSheet("color: #ffb020;" if info["error"] else "")
            self.config_path.setText(
                _display_path(info["config"])
                + ("" if info["config_exists"] else "  (no file yet)")
                + (f" - {info['config_fields']} declared keys"
                   if info["config_fields"] else ""))
            values = read_config_json(addon_config_path(info["id"]))
            self.config_edit.setPlainText(
                json.dumps(jsonable(values), indent=2, ensure_ascii=False))
            self._load_run_panels(info, addon)
            self.refresh_log()
        finally:
            self._loading = False

    def _show_mark(self, info: dict) -> None:
        """The addon's picture, at the head of the Details pane as well as in the
        list: the same file the store card and its own tab wear."""
        icon = addon_icon(info)
        side = self.details_mark.width()
        self.details_mark.setPixmap(
            icon.pixmap(QSize(side, side)) if not icon.isNull() else QPixmap())

    def _load_run_panels(self, info: dict, addon: Any) -> None:
        """The parts of the Build & test pane that depend on what is there."""
        shipped = bool(info.get("builtin"))
        copies = {str(item["kind"]): item for item in
                  [dict(kind=info["kind"], source=info["source"])] + list(info["alternatives"])}
        source, compiled = copies.get("python"), copies.get("process")
        both = bool(source and compiled) and not shipped
        want_compiled = both and info["prefer"] == "compiled"
        for button in (self.source_radio, self.compiled_radio):
            button.blockSignals(True)
        self.source_radio.setEnabled(both)
        self.compiled_radio.setEnabled(both)
        self.compiled_radio.setChecked(want_compiled)
        self.source_radio.setChecked(not want_compiled)
        for button in (self.source_radio, self.compiled_radio):
            button.blockSignals(False)
        self.runs_note.setText(SHIPPED_NOTE if shipped
                               else self._runs_text(info, compiled, source))
        # A shipped addon's file is inside the program: nothing to write back to.
        for field in (self.name_input, self.version_input, self.author_input,
                      self.description_input, self.notes_input, self.api_input):
            field.setReadOnly(shipped)
        busy = self.build is not None and self.build.running()
        can_build, why_build = buildable(addon)
        for field in (self.onefile_check, self.console_check, self.icon_check,
                      self.hidden_input, self.prefer_check):
            field.setEnabled(can_build and not busy)
        self.compile_button.setEnabled(can_build and not busy)
        self.cancel_button.setEnabled(busy)
        # One interpreter answer for both panels below: asking costs a call
        # and the two notes want the same words.
        where, why_python = build_interpreter()
        if busy:
            self.build_note.setText("building...")
            self.build_note.setStyleSheet("color: #8f8f8f;")
        elif not can_build:
            self.build_note.setText(why_build)
            self.build_note.setStyleSheet("color: #ffb020;")
        else:
            self.build_note.setText(f"PyInstaller from {where or sys.executable}")
            self.build_note.setStyleSheet("color: #8f8f8f;")

        # A test runs under its own Python, which must reach the addon protocol.
        path = self.test_file(addon)
        self.test_path.setText(str(path))
        self.create_test_button.setText("Rewrite test script" if path.is_file()
                                        else "Write test script")
        testing = self.test is not None and self.test.running()
        modules, why_modules = app_modules_ready()
        if not modules:
            why_run = why_modules
        elif where is None:
            why_run = why_python
        else:
            why_run = "" if path.is_file() else "write the test script first"
        self.create_test_button.setEnabled(bool(modules) and not testing)
        self.run_test_button.setEnabled(not why_run and not testing)
        self.then_compile_button.setEnabled(
            not why_run and not testing and can_build and not busy)
        if why_run and not testing:
            self.test_note.setText(why_run)
            self.test_note.setStyleSheet("color: #8f8f8f;")

    @staticmethod
    def _runs_text(info: dict, compiled: dict | None, source: dict | None) -> str:
        if not compiled:
            return (f"Only the source is there: {info['source']}. Compiling puts a "
                    "program beside it under the same id, and the choice appears here.")
        here = "the compiled program" if info["kind"] == "process" else "the source"
        return (f"Running {here}.\n"
                f"  source:  {source['source'] if source else '-'}\n"
                f"  program: {compiled['source']}\n"
                f"  built:   {info['build'].get('built', 'never')} "
                f"({info['build'].get('path', '-')}), preferred: {info['prefer']}")

    def _clear_panels(self) -> None:
        self.state_note.setText(
            "no addon answers to that filter" if self.index_filter.text().strip()
            else "nothing in the addons folder")
        for field in (self.name_input, self.version_input, self.author_input,
                      self.description_input, self.notes_input, self.api_input,
                      self.hidden_input):
            field.setText("")
        self.id_label.setText("-")
        self.details_mark.clear()
        self.target_note.setText("Pick an addon above.")
        self.config_path.setText("")
        self.config_edit.setPlainText("")
        self.runs_note.setText("")
        self.test_path.setText("")
        self.log_view.setPlainText("")
        self.process_table.setRowCount(0)
        self.holdings_table.setRowCount(0)

    # --- details ---

    def save_details(self) -> None:
        addon = self.current()
        if addon is None:
            return
        values = {
            "name": self.name_input.text(),
            "version": self.version_input.text(),
            "author": self.author_input.text(),
            "description": self.description_input.text(),
            "notes": self.notes_input.text(),
            "api": self.api_input.text(),
        }
        try:
            target = write_details(addon, values)
        except (OSError, ValueError, AddonError) as error:
            self.say_details(f"not saved: {error}", True)
            return
        self.say_details(f"saved {target.name}")
        self.mgr.win.log(f"[addons] manifest of {addon.id} saved to {target}")
        # One file changed, so one addon comes back; the rest keep running.
        self.mgr.reload_one(addon.id)

    def say_details(self, text: str, bad: bool = False) -> None:
        self.details_note.setText(text)
        self.details_note.setStyleSheet("color: #ffb020;" if bad else "color: #23d18b;")

    def reload_details(self) -> None:
        addon = self.current()
        if addon is None:
            return
        self.load_panels()
        self.say_details("reloaded from the file")

    def open_manifest(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(manifest_target(addon))

    def save_config(self) -> None:
        addon = self.current()
        if addon is None:
            return
        try:
            values = json.loads(self.config_edit.toPlainText() or "{}")
            if not isinstance(values, dict):
                raise ValueError("the config has to be one JSON object")
        except ValueError as error:
            self.say_config(f"not saved: {error}", True)
            return
        if self.mgr.save_config(addon.id, values, "Addon Dev"):
            self.say_config("saved")
        else:
            self.say_config("the file could not be written", True)

    def say_config(self, text: str, bad: bool = False) -> None:
        self.config_note.setText(text)
        self.config_note.setStyleSheet("color: #ffb020;" if bad else "color: #23d18b;")

    def reload_config(self) -> None:
        addon = self.current()
        if addon is None:
            return
        values = read_config_json(addon_config_path(addon.id))
        self.config_edit.setPlainText(
            json.dumps(jsonable(values), indent=2, ensure_ascii=False))
        self.say_config("reloaded from the file")

    def open_config(self) -> None:
        addon = self.current()
        if addon is not None:
            self.mgr.open_config_file(addon.id)

    # --- running one addon ---

    def _set_enabled(self, on: bool) -> None:
        if self._loading:
            return
        addon = self.current()
        if addon is not None:
            self.mgr.set_enabled(addon.id, on)
            self.refresh()

    def mgr_restart(self) -> None:
        addon = self.current()
        if addon is not None:
            self.mgr.restart(addon.id)
            self.refresh()

    def show_tab(self) -> None:
        addon = self.current()
        if addon is None or not addon.tabs:
            self.state_note.setText("this addon has opened no tab")
            return
        name = next(iter(addon.tabs))
        entry = addon.tabs.get(name) or {}
        page = entry.get("page")
        if page is not None:
            self.win.tabs.setCurrentWidget(page)

    def rescan(self) -> None:
        forget_interpreter()        # a Python installed since is noticed here
        self.mgr.reload()
        self.refresh()
        self.state_note.setText("the addons folder was read again")

    def open_folder(self) -> None:
        self.open_path(self.mgr.ensure_folder())

    def open_source(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(addon.source)

    def open_test_folder(self) -> None:
        ADDON_TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.open_path(ADDON_TEST_DIR)

    @staticmethod
    def open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # --- new addon ---

    def new_addon(self) -> None:
        dialog = NewAddonDialog(self, [row["id"] for row in self.mgr.describe_all()])
        if not dialog.exec():
            return
        values = dialog.values()
        try:
            written = self.write_addon(values)
        except OSError as error:
            QMessageBox.warning(self, "Could not write the addon", str(error))
            return
        self.mgr.reload_one(values["id"])
        self.select(values["id"])
        self.refresh()
        if values["run"]:
            self.mgr.set_enabled(values["id"], True)
            self.refresh()
        extra = " and its test script" if len(written) > 1 else ""
        self.state_note.setText(f"created {written[0].name}{extra}")

    @staticmethod
    def write_addon(values: dict) -> list[Path]:
        """The addon file and, when it was asked for, its test script."""
        ADDONS_DIR.mkdir(parents=True, exist_ok=True)
        title = values["name"]
        source = _ADDON_SOURCE.substitute(
            id=values["id"], name=values["name"], title=title, tab=values["tab"],
            menu=values["menu"], version=values["version"], author=values["author"],
            description=values["description"])
        target = ADDONS_DIR / f"{values['id']}.py"
        if target.exists():
            raise OSError(f"{target} is already there")
        _write_text(target, source)
        written = [target]
        if values["test"]:
            written.append(write_test(values["id"], values["tab"]))
        return written

    @staticmethod
    def test_file(addon: Any) -> Path:
        return ADDON_TEST_DIR / f"test_{config_name(addon.id)}.py"

    # --- test ---

    def create_test(self) -> None:
        addon = self.current()
        if addon is None:
            return
        try:
            path = write_test(addon.id, addon.name or addon.id)
        except OSError as error:
            self.test_note.setText(f"could not write: {error}")
            self.test_note.setStyleSheet("color: #ffb020;")
            return
        self.load_panels()
        self.test_note.setText(f"wrote {path.name}")
        self.test_note.setStyleSheet("color: #23d18b;")

    def _watch_streams(self) -> None:
        """Keep the short poll alive while a build or a test child is running."""
        if not self.poll_timer.isActive():
            self.poll_timer.start()

    def run_test(self) -> None:
        addon = self.current()
        if addon is None:
            return
        modules, why_modules = app_modules_ready()
        where, why_python = build_interpreter()
        if not modules or where is None:
            self.test_log.setPlainText(why_modules or why_python)
            self.test_note.setText("the test cannot be run here")
            self.test_note.setStyleSheet("color: #ffb020;")
            return
        path = self.test_file(addon)
        if not path.is_file():
            path = write_test(addon.id, addon.name or addon.id)
        self.test_log.setPlainText("")
        self.test = _Stream([where, str(path)])
        self.test.start()
        self._watch_streams()
        self.run_test_button.setEnabled(False)
        self.then_compile_button.setEnabled(False)
        self.test_note.setText("running...")
        self.test_note.setStyleSheet("color: #8f8f8f;")

    def run_test_then_build(self) -> None:
        """The store's order of business: run the test, and compile only on a pass.

        Nothing else in the tab enforces it - a person may still build straight
        away - but an addon published from here is not compiled until its own test
        has passed, so the same rule here costs nothing and keeps the two honest.
        """
        self._then_build = str(self.selected or "")
        if not self._then_build:
            return
        self.run_test()
        if self.test is None or not self.test.running():
            # It never started: no answer is coming, so nothing may be chained to it.
            self._then_build = ""
            self.build_note.setText("no build: the test did not start")
            self.build_note.setStyleSheet("color: #ffb020;")

    def open_test(self) -> None:
        addon = self.current()
        if addon is not None:
            self.open_path(self.test_file(addon))

    # --- compile ---

    def _prefer_changed(self, _on: bool = False) -> None:
        if self._loading:
            return
        addon = self.current()
        if addon is None:
            return
        kind = "compiled" if self.compiled_radio.isChecked() else "source"
        if self.mgr.preferred_kind(addon.id) == kind:
            return
        self.mgr.set_preferred_kind(addon.id, kind)
        self.refresh()
        self.runs_note.setText(f"the app will now run {kind}")

    def compile_addon(self) -> None:
        addon = self.current()
        if addon is None or not str(addon.source).endswith(".py"):
            return
        ready, why = buildable(addon)
        if not ready:
            self.build_log.setPlainText(
                f"This addon cannot be compiled here.\n\n{why}")
            return
        # Release the file the copy step overwrites, and any old process.
        if addon.loaded and addon.kind == "process":
            self.mgr.unload(addon, reason="rebuilding")
        identifier = config_name(addon.id)
        work = ADDON_BUILD_DIR / identifier
        try:
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)
            launcher = work / "launcher.py"
            _write_text(launcher, _LAUNCHER.substitute(
                id=identifier, module=module_name(addon.source)))
        except OSError as error:
            self.build_log.setPlainText(f"the staging folder could not be made: {error}")
            return
        options = {
            "onefile": self.onefile_check.isChecked(),
            "console": self.console_check.isChecked(),
            "icon": self.icon_check.isChecked(),
            "hidden": self.hidden_input.text(),
        }
        command = build_command(launcher, identifier, options, addon.source)
        self.build_log.setPlainText("$ " + " ".join(command) + "\n\n")
        self.build = _Stream(command, cwd=work)
        self.build.start()
        self._watch_streams()
        self.compile_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.build_note.setText("building...")

    def cancel_build(self) -> None:
        if self.build is not None:
            self.build.cancel()
            self.build_log.appendPlainText("\n(the build was asked to stop)")

    def clean_staging(self) -> None:
        addon = self.current()
        if addon is None:
            return
        work = ADDON_BUILD_DIR / config_name(addon.id)
        shutil.rmtree(work, ignore_errors=True)
        self.build_log.setPlainText(f"{work} removed")

    def _poll_streams(self) -> None:
        """Move a child process's output into its pane, and finish it off."""
        for kind, stream, view in (("build", self.build, self.build_log),
                                   ("test", self.test, self.test_log)):
            if stream is None:
                continue
            lines = [line for line in stream.drain() if line is not None]
            if lines:
                view.appendPlainText("\n".join(lines))
            if not stream.finished:
                continue
            if kind == "build":
                self.build_done(stream)
                self.build = None
            else:
                self.test_done(stream)
                self.test = None
            addon = self.current()
            if addon is not None:
                # Buttons and notes follow what the run just ended with.
                self._load_run_panels(self.mgr.describe(addon), addon)
        if self.build is None and self.test is None:
            # Nothing is in flight any more: the poll rests until the next
            # launch asks for it (_watch_streams).
            self.poll_timer.stop()

    def build_done(self, stream: _Stream) -> None:
        addon = self.current()
        code = stream.code
        if addon is None or code != 0:
            self.build_note.setText(f"the build stopped with code {code}")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        identifier = config_name(addon.id)
        staged = staged_name(identifier, self.onefile_check.isChecked())
        if not staged.is_file():
            self.build_note.setText("no program was produced; see the build log")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        try:
            target = install_build(staged, identifier, self.onefile_check.isChecked())
        except OSError as error:
            self.build_note.setText(f"could not be put in the addons folder: {error}")
            self.build_note.setStyleSheet("color: #ffb020;")
            return
        previous = build_mark(addon.id)
        write_build_mark(addon.id, {
            **{key: value for key, value in previous.items() if key != "prefer"},
            "built": time.strftime("%Y-%m-%d %H:%M"),
            "path": str(target),
            "onefile": self.onefile_check.isChecked(),
            "source": str(addon.source),
            "prefer": "compiled" if self.prefer_check.isChecked()
                      else previous.get("prefer", "source"),
        })
        self.mgr.reload_one(identifier)
        self.refresh()
        self.build_log.appendPlainText(f"\n{target} is ready")
        self.build_note.setText(f"wrote {target.name}")
        self.build_note.setStyleSheet("color: #23d18b;")

    def test_done(self, stream: _Stream) -> None:
        ok = stream.code == 0
        self.test_note.setText("passed" if ok else f"failed (code {stream.code})")
        self.test_note.setStyleSheet("color: #23d18b;" if ok else "color: #ffb020;")
        if not self._then_build:
            return
        wanted, self._then_build = self._then_build, ""
        if not ok:
            self.build_note.setText("not started: the test did not pass")
            self.build_note.setStyleSheet("color: #ffb020;")
        elif wanted != self.selected:
            # The person has moved on to another addon while it ran; a build of
            # that one instead would be nothing but a surprise.
            self.build_note.setText("not started: another addon is picked now")
            self.build_note.setStyleSheet("color: #ffb020;")
        else:
            self.compile_addon()

    # --- log and processes ---

    def reset_log(self) -> None:
        self.log_view.setPlainText("")

    def refresh_log(self) -> None:
        addon_id = self.selected
        if not addon_id:
            return
        try:
            text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = log_lines(text, addon_id, self.log_warnings.isChecked())
        needle = self.log_filter.text().strip().lower()
        if needle:
            lines = [line for line in lines if needle in line.lower()]
        keep = lines[-400:]
        shown = self.log_view.toPlainText().splitlines()
        if shown == keep:
            return
        self.log_view.setPlainText("\n".join(keep))
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def open_log(self) -> None:
        self.open_path(LOG_FILE)

    def refresh_processes(self, rows: list[dict]) -> None:
        running = [row for row in rows if row.get("process")]
        self.process_table.blockSignals(True)
        self.process_table.setRowCount(len(running))
        for index, row in enumerate(running):
            process = row.get("process") or {}
            state = "running" if process.get("alive") else "closed"
            if row.get("error"):
                state = "failed"
            command = " ".join(str(part) for part in (process.get("command") or []))
            values = (row["id"], process.get("pid") or "", state,
                      "" if process.get("exit") is None else process.get("exit"),
                      duration(process.get("seconds")), process.get("requests") or 0,
                      command)
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if column == 2:
                    item.setForeground(state_color(state))
                self.process_table.setItem(index, column, item)
        self.process_table.blockSignals(False)
        fit_rows(self.process_table, cap=6)

        threads = [name for name in (getattr(thread, "name", "")
                                     for thread in threading.enumerate())
                   if name.startswith(("addon-", "addon"))]
        self.thread_note.setText("threads serving the addons: "
                                 + (", ".join(sorted(set(threads))) or "none"))

        held = [row for row in rows if row["kind"] == "python"]
        self.holdings_table.blockSignals(True)
        self.holdings_table.setRowCount(len(held))
        for index, row in enumerate(held):
            values = (row["id"], "imported", ", ".join(row["tabs"]) or "-",
                      row["timers"], row["menu_items"],
                      ", ".join(row["listeners"]) or "-")
            for column, text in enumerate(values):
                self.holdings_table.setItem(index, column, QTableWidgetItem(str(text)))
        self.holdings_table.blockSignals(False)
        fit_rows(self.holdings_table, cap=6)


def write_test(addon_id: str, tab: str = "") -> Path:
    """``addons/_tests/test_<id>.py``, from the template."""
    ADDON_TEST_DIR.mkdir(parents=True, exist_ok=True)
    target = ADDON_TEST_DIR / f"test_{config_name(addon_id)}.py"
    _write_text(target, _TEST_SOURCE.substitute(id=config_name(addon_id),
                                                tab=tab or addon_id))
    return target


def install_build(staged: Path, addon_id: str, onefile: bool) -> Path:
    """Move the build result where the runtime looks: ``addons/<id>.exe`` or ``addons/<id>/``.

    An older install of the same id goes first whichever shape it was left in:
    on a machine with no executable suffix a one-file program and a folder
    program both want the bare name ``addons/<id>``, so the build landing now
    replaces what stands there rather than arguing with it. The store has
    already stopped the addon before calling this, and the Addon Dev tab only
    ever writes an id it is building itself.
    """
    if not str(addon_id or "").strip() or str(addon_id).strip() in (".", ".."):
        raise OSError("the build has no addon id to install under")
    for stale in (ADDONS_DIR / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}",
                  ADDONS_DIR / addon_id):
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.is_file():
            stale.unlink()
    if onefile:
        target = ADDONS_DIR / f"{addon_id}{ADDON_EXECUTABLE_SUFFIX}"
        shutil.copy2(staged, target)
        return target
    target = ADDONS_DIR / addon_id
    shutil.copytree(staged.parent, target)
    program = target / staged.name
    if ADDON_EXECUTABLE_SUFFIX == "":
        program = target / "addon.bin"
        os.replace(target / staged.name, program)
        program.chmod(program.stat().st_mode | 0o111)
    return program
