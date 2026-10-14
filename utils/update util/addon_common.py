#!/usr/bin/env python3
"""The parts of the addon system that need nothing but the standard library.

:mod:`addon_api` is what an addon running *inside* the app is handed, and it
imports PyQt6 and paramiko because that is what the app is made of. This module
is the same contract stripped down to plain Python, so it can also be used:

* by an addon that was **compiled** into its own executable, where dragging in
  Qt and SSH would be pointless - the app does that work over the pipe;
* by a **test script** for an addon, which wants to check what the addon asked
  for without opening a window (see ``addon_client.stub_api``);
* by the app itself, which imports these helpers from here through
  :mod:`addon_api` - the names are re-exported there, so nothing that says
  ``from addon_api import AddonError`` has to change.

Where things live
-----------------
A helper looks for its folder in this order: an environment variable the app
set for the addon process (``RASCONF_ADDON_CONFIG`` and friends, see
``manager_addons.ProcessBridge._environment``), then ``manager_const`` when it
is importable because the app is running around it, then the same folder beside
the program that is running - ``config`` for the shared build record and
``addons/_data/<id>`` for an addon's own config. That is why the same call works
in the app, in a compiled addon and in a bare test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any, Callable

API_VERSION = 1

#: Manifest keys the app copies onto an addon's record. See ``manifest_from``.
MANIFEST_KEYS = ("name", "version", "author", "description", "notes")


class AddonError(RuntimeError):
    """Anything that stops an addon's work, already written in readable words."""


def guard(callback: Callable, *arguments: Any, reporter: Callable[[str], Any] | None = None) -> Any:
    """Run addon code and keep its mistakes in the log instead of the app."""
    try:
        return callback(*arguments)
    except AddonError as error:
        if reporter is not None:
            reporter(str(error))
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        if reporter is not None:
            reporter(detail)
    return None


def jsonable(value: Any) -> Any:
    """Reduce an API result to what can cross a process boundary as JSON."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)


#: A job the app is still running cannot cross a pipe, so only its handle
#: does: the answer to a non-blocking call is this one-key dict, and the addon
#: asks about the job with the ``device.future_*`` methods.
FUTURE_KEY = "addon_future"


def future_handle(token: Any) -> dict:
    """The reply that stands for a job the app is running in the background."""
    return {FUTURE_KEY: int(token)}


def is_future_handle(value: Any) -> bool:
    """Whether an api answer is such a handle, and nothing that merely holds one."""
    return isinstance(value, dict) and set(value) == {FUTURE_KEY}


# --- what an addon said about itself ---

def manifest_from(module: Any) -> dict:
    """The manifest of a Python addon module: one ADDON/MANIFEST dict, or the loose NAME/VERSION/AUTHOR/DESCRIPTION constants."""
    declared = getattr(module, "ADDON", None) or getattr(module, "MANIFEST", None)
    if isinstance(declared, dict):
        manifest = dict(declared)
    else:
        manifest = {}
        for key in ("name", "version", "author", "description"):
            value = getattr(module, key.upper(), None)
            if value:
                manifest[key] = str(value)
        wanted = getattr(module, "API", None)
        if wanted is not None:
            manifest["api"] = wanted
    if not manifest.get("description"):
        doc = (getattr(module, "__doc__", "") or "").strip().splitlines()
        if doc:
            manifest["description"] = str(doc[0])
    return manifest


# --- paths ---

_ID_CLEAN = re.compile(r"[^A-Za-z0-9_.-]+")


def config_name(addon_id: str) -> str:
    """The file name an addon id is stored under, cleaned the same way everywhere."""
    return _ID_CLEAN.sub("_", str(addon_id).strip()) or "addon"


def _from_environment(variable: str) -> Path | None:
    """A folder or file the app told this process about; a name without a suffix is made, since a fresh checkout has none."""
    raw = str(os.environ.get(variable, "")).strip()
    if not raw:
        return None
    path = Path(raw)
    if path.suffix == "":
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return path


def _app_or_sibling(attribute: str, tail: str) -> Path:
    """The app's own layout through manager_const, or beside this file for a compiled addon with no app to ask."""
    try:
        import manager_const
    except ImportError:
        home = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        return home / "addons" / tail
    return Path(getattr(manager_const, attribute))


def _top_or_sibling(attribute: str, tail: str) -> Path:
    """A folder at the program's own root through manager_const, or beside it; the _app_or_sibling shape for folders not under addons."""
    try:
        import manager_const
    except ImportError:
        home = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        return home / tail
    return Path(getattr(manager_const, attribute))


def app_config_dir() -> Path:
    """The manager's own config folder, created on the way; it holds the profile config, history, log and the shared build record."""
    path = Path(_from_environment("RASCONF_APP_CONFIG_DIR")
                or _top_or_sibling("CONFIG_DIR", "config"))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def addon_data_root() -> Path:
    """addons/_data, the parent of every addon's own folder; a plain path, not made here."""
    return Path(_app_or_sibling("ADDON_DATA_DIR", "_data"))


def addon_data_dir(addon_id: str) -> Path:
    """The addon's own working folder, addons/_data/<id>; from RASCONF_ADDON_DATA, or resolved from manager_const."""
    raw = str(os.environ.get("RASCONF_ADDON_DATA", "")).strip()
    if raw:
        return Path(raw)
    return addon_data_root() / config_name(addon_id)


def addon_config_path(addon_id: str) -> Path:
    """``addons/_data/<id>/config.json``: the one addon's own settings file."""
    return addon_data_dir(addon_id) / "config.json"


def addon_schema_path(addon_id: str) -> Path:
    """The side copy of an addon's field descriptions, kept beside the config so Settings can show the form for a switched-off addon."""
    return addon_data_dir(addon_id) / "fields.json"


#: The manifest key that says where an addon came from. An addon the store
#: installs is described by its own file next to the program, so it travels with
#: the addon and keeps its answer even when copied elsewhere.
ORIGIN_KEY = "origin"

#: What the addon store writes there for the addons it installs.
STORE_ORIGIN = "addon store"

#: The names a compiled addon's manifest is looked for under, in that order.
MANIFEST_NAMES = ("addon.json", "manifest.json")


def addon_manifest_path(home: Path, addon_id: str) -> Path | None:
    """The manifest file that describes an addon, or ``None`` when it has none.

    A program built from an addon's source carries its details in a file beside
    it: ``<id>.json`` next to a loose ``<id>.exe``, or ``addon.json`` inside a
    folder addon.
    """
    folder = Path(home)
    for name in (f"{config_name(addon_id)}.json",) + MANIFEST_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def addon_origin(home: Path, addon_id: str) -> str:
    """Where an addon came from, as its own manifest says; empty when it says nothing."""
    path = addon_manifest_path(home, addon_id)
    if path is None:
        return ""
    return str(read_config_json(path).get(ORIGIN_KEY) or "").strip()


def addon_build_path() -> Path:
    """config/_build.json: what the Addon Dev tab recorded, one file for every addon keyed by id; only the manager touches it."""
    return app_config_dir() / "_build.json"


def read_build_marks() -> dict:
    """Every build record, or ``{}`` when the file is missing or broken."""
    return read_config_json(addon_build_path())


def build_mark(addon_id: str) -> dict:
    """One addon's build record; empty when it has never been compiled."""
    mark = read_build_marks().get(config_name(addon_id))
    return mark if isinstance(mark, dict) else {}


def write_build_mark(addon_id: str, values: dict) -> dict:
    """Merge ``values`` into one addon's build record and return it."""
    key = config_name(addon_id)
    marks = read_build_marks()
    record = marks.get(key)
    record = dict(record) if isinstance(record, dict) else {}
    record.update(jsonable(values or {}))
    marks[key] = record
    write_config_json(addon_build_path(), marks)
    return record


# --- reading and writing one config dict ---

def read_config_json(path: Path) -> dict:
    """A config file as a dict; missing or broken reads as empty."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config_json(path: Path, values: dict) -> None:
    """Save a config file: pretty, utf-8, and replaced in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(values, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def merge_config(defaults: dict | None, stored: dict | None) -> dict:
    """The values an addon sees: what the user wrote filled in with defaults; nested dicts merge key by key."""
    merged = dict(defaults or {})
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[str(key)] = value
    return merged


def config_get(values: dict, key: str, default: Any = None) -> Any:
    """Read ``"subject.CN"`` out of a nested config dict."""
    node = values
    for part in str(key).split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def put_config_value(store: dict, key: str, value: Any) -> None:
    """``"subject.CN"`` writes into the ``subject`` section, creating it."""
    parts = [part for part in str(key).split(".") if part]
    if not parts:
        return
    node = store
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = jsonable(value)


def take_config_value(store: dict, key: str) -> Any:
    """Remove a dotted key from a config dict and say what went."""
    parts = [part for part in str(key).split(".") if part]
    node = store
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if not isinstance(node, dict):
        return None
    return node.pop(parts[-1], None)


# --- child processes whose whole tree must die with them ---
#
# ``Popen.terminate/kill`` only reach the direct child. A PyInstaller onefile
# addon is two processes - a bootloader and the real program it spawns - so
# killing the first orphans the second, and a crash of the app leaves it behind
# the same way. These helpers put every child somewhere the OS can take the
# whole tree down at once: a job object on Windows, whose last handle closing
# kills everything inside it (even when this app dies without cleaning up),
# and its own session on POSIX, where one signal reaches the group.

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.windll.kernel32
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, ctypes.c_uint]
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_TERMINATE = 0x0001
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64)]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 2
    _JOBS: dict[int, Any] = {}        # pid -> job handle, one per child

#: Every child started with ``popen_tree`` and not yet released; the list is
#: the last line of defence, walked again when this process goes down.
_TREES: list = []


def _job_assign(proc: subprocess.Popen) -> None:
    """Put a fresh child into a job of its own; failure just means no tree sweep.

    A child already inside the app's own job (a nested job) may refuse to join
    a second one, so assignment is best-effort: when it fails, crash cleanup of
    the deep grandchildren is lost, but ``kill_process_tree`` still reaches them
    by walking the process table.
    """
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info))
        assigned = ok and _kernel32.AssignProcessToJobObject(job, int(proc._handle))
        if assigned:
            _JOBS[int(proc.pid)] = job
        else:
            _kernel32.CloseHandle(job)
    except (OSError, AttributeError, ValueError):
        pass


def _win_child_map() -> dict[int, list[int]]:
    """parent pid -> child pids, read once from the live process table."""
    children: dict[int, list[int]] = {}
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE:
        return children
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    try:
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parent = int(entry.th32ParentProcessID)
            children.setdefault(parent, []).append(int(entry.th32ProcessID))
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return children


def _win_descendants(root_pid: int) -> list[int]:
    """Every process below ``root_pid``, deepest first, ready to reap bottom up."""
    children = _win_child_map()
    found: list[int] = []
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            found.append(child)
            pending.append(child)
    found.reverse()
    return found


def _win_terminate_pid(pid: int) -> None:
    handle = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if handle:
        try:
            _kernel32.TerminateProcess(handle, 1)
        except OSError:
            pass
        finally:
            _kernel32.CloseHandle(handle)


def clean_child_env(env: Any = None) -> dict:
    """A child's environment: a copy of ``env`` (the parent's when none),
    with the frozen app's own unpack folder taken out of the search paths.

    A PyInstaller onefile puts its unpacked folder - bundled libcrypto and
    friends and all - into LD_LIBRARY_PATH (and PATH on Windows), and the
    whole environment passes to children. A child running the machine's
    own Python would then resolve system modules against those older
    libraries and die on a version mismatch; a relaunched copy of the app
    brings its own unpack folder and must not look in the dying one's.
    """
    env = dict(os.environ if env is None else env)
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return env
    wanted = os.path.realpath(str(bundle))
    for key in ("LD_LIBRARY_PATH", "PATH"):
        entries = [e for e in env.get(key, "").split(os.pathsep)
                   if e and os.path.realpath(e) != wanted]
        if entries:
            env[key] = os.pathsep.join(entries)
        else:
            env.pop(key, None)
    return env


def popen_tree(command: Any, **kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` for a child that must never outlive the app.

    Takes the same arguments; the child starts in a place where
    ``kill_process_tree`` reaches it and everything it spawns.
    """
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)
        if getattr(sys, "_MEIPASS", None):
            kwargs["env"] = clean_child_env(kwargs.get("env"))
        proc = subprocess.Popen(command, **kwargs)
        _TREES.append(proc)
        return proc
    proc = subprocess.Popen(command, **kwargs)
    _job_assign(proc)
    _TREES.append(proc)
    return proc


def _group_signal(proc: subprocess.Popen, sig: int) -> bool:
    """Signal the child's whole POSIX session; False when only it can be reached."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
        return True
    except OSError:
        return False


def terminate_process_tree(proc: subprocess.Popen) -> None:
    """Ask the child (and on POSIX its whole group) to go down politely."""
    if os.name == "nt":
        try:
            proc.terminate()
        except OSError:
            pass
        return
    if not _group_signal(proc, signal.SIGTERM):
        try:
            proc.terminate()
        except OSError:
            pass


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Force the child and everything it spawned down, then reap it."""
    if os.name == "nt":
        pid = int(proc.pid)
        # Read the tree before the parent dies, or the link to the children
        # is lost: a job that refused a nested child cannot reap it.
        descendants = _win_descendants(pid)
        job = _JOBS.get(pid)
        if job:
            try:
                _kernel32.TerminateJobObject(job, 1)
            except OSError:
                pass
        for child in descendants:
            _win_terminate_pid(child)
    else:
        _group_signal(proc, signal.SIGKILL)
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def release_process_tree(proc: subprocess.Popen) -> None:
    """Forget the child's job handle once it is done; call it in every case.

    A Windows job kills its remaining members when its last handle closes, so
    this also sweeps up any grandchild the bootloader left behind; on POSIX
    there is nothing attached to release.
    """
    try:
        _TREES.remove(proc)
    except ValueError:
        pass
    if os.name != "nt":
        return
    job = _JOBS.pop(int(proc.pid), None)
    if job:
        try:
            _kernel32.CloseHandle(job)
        except OSError:
            pass


def kill_all_trees() -> None:
    """Take down every child this program started and has not let go of.

    Registered at the app's exit, so even a crash or a window closed by
    force leaves no addon process behind; released children are not here.
    """
    for proc in list(_TREES):
        try:
            if proc.poll() is None:
                kill_process_tree(proc)
            release_process_tree(proc)
        except Exception:
            pass
    _TREES.clear()
