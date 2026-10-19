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

Where things live:
A helper looks for its folder in this order: an environment variable the app
set for the addon process (``RASCONF_ADDON_CONFIG`` and friends, see
``core.addon.ProcessBridge._environment``), then ``core.const`` when it
is importable because the app is running around it, then the same folder beside
the program that is running - ``config`` for the shared build record and
``addons/_data/<id>`` for an addon's own config. That is why the same call works
in the app, in a compiled addon and in a bare test.
"""

from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any, Callable

# The one module of the app's own that this file may name: :mod:`core.const`
# carries no Qt, which is what keeps the promise this module is built on - an
# addon process or a bare test can read it with no screen. Nothing below may
# reach into :mod:`core.ui`, :mod:`core.net`, :mod:`core.views`,
# :mod:`core.addon` or :mod:`core.window`, and the two places that do ask for
# ``core.const`` sit inside a ``try`` so a compiled addon on its own still works.

API_VERSION = 1

#: Manifest keys the app copies onto an addon's record. See ``manifest_from``.
#: ``icon`` is the picture an addon ships under a name that is not the standard
#: ``<id>icon.png``; ``connection`` says how it reaches the device.
MANIFEST_KEYS = ("name", "version", "author", "description", "notes",
                 "requires", "connection", "icon")


def one_line(value: Any) -> str:
    """One clean line of text out of whatever a manifest or a config holds."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


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
    """The app's own layout through core.const, or beside this file for a compiled addon with no app to ask."""
    try:
        import core.const
    except ImportError:
        home = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        return home / "addons" / tail
    return Path(getattr(core.const, attribute))


def _top_or_sibling(attribute: str, tail: str) -> Path:
    """A folder at the program's own root through core.const, or beside it; the _app_or_sibling shape for folders not under addons."""
    try:
        import core.const
    except ImportError:
        home = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        return home / tail
    return Path(getattr(core.const, attribute))


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
    """The addon's own working folder, addons/_data/<id>; from RASCONF_ADDON_DATA, or resolved from core.const."""
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


# --- the bytecode that stands in for an addon's source ---

#: The header flags of a ``.pyc`` carrying a checked hash of the source it was
#: made from: bit 0 says the header holds a hash where a date would be, bit 1
#: says whoever reads it must compare that hash before running it. Only that
#: kind is ever used here - see :func:`bytecode_ready`.
PYC_CHECKED_HASH = 0b11

#: The ending a precompiled addon is kept under: the source's own name, then
#: this, sitting in the same folder as the text it stands for.
BYTECODE_SUFFIX = ".pyc"

#: How many bytes of a ``.pyc`` say what it is: magic, flags, and the hash.
PYC_HEADER = 16


def bytecode_path(source: Any) -> Path:
    """The file that would hold ``source``'s bytecode: its name beside it, ``.pyc``."""
    path = Path(str(source))
    return path.with_name(path.stem + BYTECODE_SUFFIX)


def bytecode_header(sidecar: Any) -> bytes:
    """The first bytes of a bytecode file, or fewer than sixteen when it is shorter."""
    with Path(str(sidecar)).open("rb") as handle:
        return handle.read(PYC_HEADER)


def bytecode_ready(source: Any, sidecar: Any = None) -> bool:
    """Whether a bytecode file may be run in place of the source beside it.

    Three things have to agree before it is used, and any one of them failing
    sends the reader back to the ``.py``. The magic number is the interpreter's
    bytecode version, so a file built by another Python is never run by this
    one. The header must be the checked-hash kind: a date says nothing once a
    build has unpacked the file somewhere new and every date in it is fresh. And
    the hash must be this source's hash, so an addon edited in the Dev tab is
    read as the text it holds now rather than as the bytecode of the text it
    used to be - which is what lets a shipped ``.pyc`` sit beside a shipped
    ``.py`` without anybody having to keep the two in step by hand.
    """
    source = Path(str(source))
    sidecar = Path(str(sidecar)) if sidecar is not None else bytecode_path(source)
    try:
        header = bytecode_header(sidecar)
        text = source.read_bytes()
    except OSError:
        return False
    if len(header) < PYC_HEADER or header[:4] != importlib.util.MAGIC_NUMBER:
        return False
    if int.from_bytes(header[4:8], "little") != PYC_CHECKED_HASH:
        return False
    try:
        return header[8:16] == importlib.util.source_hash(text)
    except ValueError:                      # no sha256 in this interpreter
        return False


#: The manifest key that says where an addon came from. An addon the store
#: installs is described by its own file next to the program, so it travels with
#: the addon and keeps its answer even when copied elsewhere.
ORIGIN_KEY = "origin"

#: What the addon store writes there for the addons it installs.
STORE_ORIGIN = "addon store"

# --- an addon's own picture ---

#: The one ending an addon's picture is shipped under, so an id and this make
#: the name: ``openwrt_logs`` is found by ``openwrt_logsicon.png``.
ICON_SUFFIX = "icon.png"

#: The picture a listing shows for an addon that brought none of its own. It
#: lives in the program's assets folder, next to the logos.
DEFAULT_ADDON_ICON = "storedefaultaddon.png"


def icon_filename(addon_id: str) -> str:
    """The standard name of one addon's picture, from its id."""
    return f"{config_name(addon_id)}{ICON_SUFFIX}"


def find_icon(folder: Any, addon_id: str, *extra: str) -> str:
    """The addon's picture inside ``folder``, as a path; nothing when it has none.

    The standard name is tried first, then each further name the caller offers -
    an older file the addon shipped under its own name, or one its manifest
    declares. So an addon that follows the rule needs no code at all, and one
    that predates it keeps the picture it has. Nothing is guessed from a plain
    ``icon.png``: the folder an addon is shown its assets in is the program's own
    in a checkout, where that name belongs to the program rather than to the
    addon. A folder that is missing, or holds nothing readable, is only a folder
    with no pictures: the caller draws its own mark or shows the default.
    """
    if folder is None:
        return ""
    try:
        base = Path(str(folder))
    except (TypeError, ValueError):
        return ""
    names = [icon_filename(addon_id)] if str(addon_id or "").strip() else []
    names += [str(item or "").strip() for item in extra]
    for name in names:
        if not name or Path(name).is_absolute() or "/" in name or "\\" in name:
            continue                      # a name, not an address
        candidate = base / name
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def picture_folders(paths: Any, addon_id: str) -> list:
    """Every folder this addon's picture may live in, the likeliest first.

    Three layouts are worth looking at, and all hold the same file name. A store
    addon keeps its pictures beside its own source, in
    ``addon.store/assets/<id>/`` - the folder the catalog is built from and the
    one a person drops artwork into without rebuilding anything. The program's
    ``assets`` folder is the second: a shipped addon's picture lives there, and so
    does an installed one's, since a build made by the store packs the addon's
    assets flat into the executable that ``paths.assets`` points at. The third is
    the addon's own data folder, ``addons/_data/<id>/``: an addon installed as a
    program has no readable source folder of its own, so its artwork is kept
    where its settings are and both the addon and the app read it from there. A
    folder that is not there is not a place to look, so a shipped addon - whose
    own folder has no ``assets`` of it - simply goes on to the program's.
    """
    found: list[Path] = []
    for read in (lambda: Path(str(paths.addon)) / "assets" / config_name(addon_id),
                 lambda: Path(str(paths.assets)),
                 lambda: Path(str(paths.data))):
        try:
            folder = read()
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            continue                    # this api knows no such folder
        if folder.is_dir():
            found.append(folder)
    return found


def find_addon_picture(paths: Any, addon_id: str, *extra: str) -> str:
    """The addon's own picture, in whichever layout this addon is running in.

    The same name everywhere - the addon's id and then ``icon.png`` - searched
    through :func:`picture_folders`, so an addon needs no code that knows whether
    it is being read out of ``addon.store`` or out of the program's ``assets``.
    Nothing when no folder holds it: the caller draws its own mark, or the store
    shows the default.
    """
    if paths is None:
        return ""
    for folder in picture_folders(paths, addon_id):
        found = find_icon(folder, addon_id, *extra)
        if found:
            return found
    return ""


def default_icon_path(folder: Any) -> str:
    """The picture an addon with none of its own is shown with."""
    return find_icon(folder, "", DEFAULT_ADDON_ICON)


# --- what an addon says it needs ---

#: How an addon talks to the device, and the words a listing shows for it. An
#: addon names one of these in its manifest as ``"connection"``; the app and the
#: store both read it the same way, so a person can see before installing
#: whether the addon works on the machine in front of them or reaches a router.
#: The two extra spellings are the ones ``core.addon`` already accepts, so a
#: fork's own wording still lands on the same sentence.
CONNECTION_MEANINGS = {
    "": "needs no device",
    "none": "needs no device",
    "app": "uses the app's own connection",
    "main": "uses the app's own connection",
    "own": "opens a connection of its own",
    "private": "opens a connection of its own",
    "both": "uses the app's connection and opens its own",
}


def connection_kind(manifest: Any) -> str:
    """One manifest's ``connection`` word, or ``""`` when it says nothing.

    An addon that names nothing is an addon that needs no device, which is what
    most of them are; a word nothing recognises is kept as it came, so a fork's
    own vocabulary still shows rather than being quietly dropped.
    """
    data = manifest if isinstance(manifest, dict) else {}
    return one_line(data.get("connection")).lower()


def connection_words(manifest: Any) -> str:
    """The sentence a listing shows for how an addon reaches the device."""
    kind = connection_kind(manifest)
    return CONNECTION_MEANINGS.get(kind, f"says about its connection: {kind}")


def requires_list(manifest: Any) -> list[str]:
    """One manifest's ``requires`` as a clean list of names, however it was written."""
    data = manifest if isinstance(manifest, dict) else {}
    raw = data.get("requires")
    if raw is None:
        raw = data.get("needs") or []
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,; ]+", raw) if part]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        name = one_line(item)
        if name and name not in out:
            out.append(name)
    return out


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


#: path -> [mtime_ns, size, the file as read]. Only ever the build file, which
#: is why it is kept apart from the configs an addon may rewrite at any moment.
_MARK_CACHE: dict[str, list] = {}


def read_build_marks() -> dict:
    """Every build record, or ``{}`` when the file is missing or broken.

    Remembered between calls, and re-read when the file's own stamp moves: this
    is asked once per addon by a scan and again by every row of the Addon Dev
    tab, and on Windows opening and parsing the same small file two hundred times
    a minute is the kind of work that shows as a sluggish window.
    """
    path = addon_build_path()
    key = str(path)
    try:
        stamp = path.stat()
    except OSError:
        _MARK_CACHE.pop(key, None)
        return {}
    held = _MARK_CACHE.get(key)
    if held is not None and held[:2] == [stamp.st_mtime_ns, stamp.st_size]:
        return dict(held[2])
    data = read_config_json(path)
    _MARK_CACHE[key] = [stamp.st_mtime_ns, stamp.st_size, dict(data)]
    return dict(data)


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
    # Whatever the file's stamp says, the answer has to be the new one: a write
    # of the same length in the same clock tick would otherwise be read as no
    # change at all, and a build that has just finished is never too soon to show.
    _MARK_CACHE.clear()
    return record


# --- reading and writing one config dict ---

def read_config_json(path: Path) -> dict:
    """A config file as a dict; missing or broken reads as empty."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


#: path -> [mtime_ns, size, the file as read] for config-shaped files that
#: are asked for several times a repaint. The same bargain ``_MARK_CACHE``
#: makes for the build record: the file's own stamp is checked on every read,
#: so a hand edit between calls still comes back fresh, and a write through
#: :func:`write_config_json` drops the remembered reading at once - whatever
#: the clock says about when the new file landed.
_CONFIG_CACHE: dict[str, list] = {}
_CONFIG_CACHE_MAX = 48


def read_config_cached(path: Path) -> dict:
    """``read_config_json``, parsed once per change of the file.

    Each call hands back a copy of what was parsed, so a caller may merge
    into it or keep it without the next reading - or the cache itself -
    moving under it."""
    path = Path(path)
    key = str(path)
    try:
        stamp = path.stat()
    except OSError:
        _CONFIG_CACHE.pop(key, None)
        return {}
    held = _CONFIG_CACHE.get(key)
    if held is not None and held[:2] == [stamp.st_mtime_ns, stamp.st_size]:
        return jsonable(held[2])
    data = read_config_json(path)
    if len(_CONFIG_CACHE) >= _CONFIG_CACHE_MAX:
        _CONFIG_CACHE.clear()
    _CONFIG_CACHE[key] = [stamp.st_mtime_ns, stamp.st_size, data]
    return jsonable(data)


def write_config_json(path: Path, values: dict) -> None:
    """Save a config file: pretty, utf-8, and replaced in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(values, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    # The file has just been written, so no remembered reading of it is true
    # any more - and a clock too coarse to notice the change is not a reason to
    # keep answering from the last one.
    _MARK_CACHE.pop(str(path), None)
    _CONFIG_CACHE.pop(str(path), None)


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


#: What a PyInstaller bootloader passes between its own processes to say that
#: a folder is already unpacked and its contents are there to be worked from.
#: Whoever holds them decides whether a program started from the same file is
#: a copy of its own or a worker of the one that is going away.
PYI_WORKER_VARS = ("_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
                   "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC", "MEIPASS2")

#: The public way of answering the same question: a program that starts with
#: this set forgets what it inherited and unpacks itself again.
PYI_RESET = "PYINSTALLER_RESET_ENVIRONMENT"


def clean_child_env(env: Any = None) -> dict:
    """A child's environment: a copy of ``env`` (the parent's when none), with
    the frozen app's own unpack folder taken out of the search paths and its
    hold on the child's idea of what it is.

    A PyInstaller onefile puts its unpacked folder - bundled libcrypto and
    friends and all - into LD_LIBRARY_PATH (and PATH on Windows), and the
    whole environment passes to children. A child running the machine's
    own Python would then resolve system modules against those older
    libraries and die on a version mismatch; a relaunched copy of the app
    brings its own unpack folder and must not look in the dying one's.

    The second half is the same mistake from the other side. A program
    started from the same executable as its parent is taken for a worker of
    that parent, and a worker reuses the folder it was handed instead of
    unpacking. On a restart that folder is deleted as the dying copy exits,
    so the fresh one runs with its code - which comes out of the executable
    itself - and none of the loose files beside it: a built paramiko, say,
    finds its module and not the metadata next to it. So the handoff is
    dropped and the reset asked for, and every child of a built app is an
    instance of its own.
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
    for key in PYI_WORKER_VARS:
        env.pop(key, None)
    env[PYI_RESET] = "1"
    return env


#: The Windows flag that keeps a child from being given a console of its own.
#: A program built without a console window - which is what Berry Manager is -
#: still starts a console for any *console* program it spawns, because Windows
#: hands a child a console when its parent has none to inherit. So every launch
#: has to say it wants none, or the user watches PowerShell and Python windows
#: flash over the interface every time a shortcut is written or a build is
#: asked about. Zero everywhere else, and harmless there.
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def quiet_kwargs(kwargs: dict | None = None) -> dict:
    """Launch arguments with the console pop-up switched off, unless told otherwise.

    Only fills in ``creationflags`` when the caller named none, so a launch that
    wants a console of its own - a relaunched copy that asks to be detached, a
    build that must be its own process group - keeps exactly what it asked for.
    """
    filled = dict(kwargs or {})
    if NO_CONSOLE and "creationflags" not in filled:
        filled["creationflags"] = NO_CONSOLE
    return filled


def run_quiet(command: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` that never shows a window; the normal call for a helper.

    For the one-line questions the app asks the machine - where is the Desktop,
    can this interpreter build - which run while the interface is on screen and
    have no business putting a terminal in front of the user.
    """
    return subprocess.run(command, **quiet_kwargs(kwargs))


def popen_tree(command: Any, **kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` for a child that must never outlive the app.

    Takes the same arguments; the child starts in a place where
    ``kill_process_tree`` reaches it and everything it spawns, with an
    environment that is its own rather than this program's, and with no console
    window of its own unless the caller asked for one (see :func:`NO_CONSOLE`).
    """
    kwargs["env"] = clean_child_env(kwargs.get("env"))
    kwargs = quiet_kwargs(kwargs)
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)
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
