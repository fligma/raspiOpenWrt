#!/usr/bin/env python3
"""Berry Manager setup: put a working build on this machine, one of two ways.

A compiled Berry Manager is a *folder* now (the program file and an
``_internal`` beside it - PyInstaller's ``--onedir`` shape), not the single file
it used to be. Unpacking that folder by hand is easy to get half-right, so this
setup does it for you, and it wears the same face the program shows while it
boots: the frameless card, the logo, the line of what is happening and the bar.

There are two doors, and they share everything but where the build comes from:

``Install release``
    Bring down the ``--onedir`` build this very setup was published with - the
    ``berry_manager-<system>.zip`` attached to its GitHub release - and unpack
    it whole into a folder you choose. This is the door for someone who only
    wants the app: nothing is built here, only fetched and laid down.

``Compile``
    Run PyInstaller on this machine against the program's own source, so the
    build is made by the interpreter, the CPU and the libraries that are
    actually here. This is the door for a machine the published build does not
    suit, or someone who wants the build to be theirs. It needs the toolchain
    already present - a Python with PyInstaller and the app's requirements - and
    says so plainly, with the exact line to run, when one of them is missing.

Both doors let you pick where the app lands, and both clean up after
themselves: a compile builds into a throwaway folder that is taken away when the
result has been moved, so no ``build/``, ``dist/``, ``*.spec`` or staging folder
is left in the source tree or anywhere else.

Both doors take what they need from GitHub - the release archive, or the source
a compile builds - and the repository they come from can be set, though it
defaults to the one this setup was published under (fligma/raspiOpenWrt).
Nothing else is fetched, and the setup carries no copy of the program: it
downloads, unpacks and lays down, and cleans up after itself - a compile builds
into a throwaway folder that goes when the result has been moved, so no
``build/``, ``dist/``, ``*.spec`` or staging folder is left behind.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# --- what this setup is, and where the release it belongs to lives ---

#: The version THIS setup carries, kept equal to ``core/const.py``'s
#: ``APP_VERSION`` so its own title reads the same as the program it installs.
#: It is the byte-packed hex form (the release decimal would call ``3.2.1``).
#: It is NOT the release the doors reach for: both default to asking GitHub for
#: the newest ``berry`` tag - see :func:`resolve_tag` - the same way the Manager
#: update addon does. This value is only the tag used when that ask cannot be
#: made: an offline machine, or a fork with no releases published yet.
APP_VERSION = "0x3.0x2.0x4"
OWNER = "fligma"
REPO = "raspiOpenWrt"
TAG_PREFIX = "berry"

#: The two logos the program wears; the setup shows the same one it would find.
ICON_FILES = ("icon.png", "icon2.png")

#: ``_internal`` is the folder a ``--onedir`` build keeps its libraries in; its
#: presence is what tells a whole build from a stray single file.
INTERNAL_DIR = "_internal"

#: The drop-in addons folder and the note that stands in it, worded as the
#: program's own scanner expects to find it.
ADDONS_README = (
    "Drop an addon in this folder: a .py file, a folder holding addon.py,\n"
    "or a program (.exe) with a .json manifest of the same name. The app\n"
    "reads the folder when it starts and lists what it finds on the\n"
    "Addon manager page of the addon store.\n"
    "\n"
    "An addon that came with the program is packed inside the executable\n"
    "and is switched on and off on its own page in Settings instead. A\n"
    "file dropped here with the same name takes the place of that shipped\n"
    "addon. Each addon keeps its own settings in addons/_data/<name>/config.json,\n"
    "and its own tab picture in addons/assets/<name>/<name>icon.png - or in\n"
    "assets/ beside the program, under that same name.\n"
)

#: The subset of the shipped theme the card is drawn with, kept here so the setup
#: looks like the program's boot splash without importing the whole interface.
#: These are the ``default.theme`` values in ``core/const.py``.
PALETTE = {
    "base": "#121212",
    "surface": "#212121",
    "soft": "#2e2e2e",
    "raise": "#333333",
    "hover": "#414141",
    "border": "#3c3c3c",
    "raise_edge": "#4a4a4a",
    "text": "#e0e0e0",
    "bright": "#ffffff",
    "muted": "#aaaaaa",
    "accent": "#c51a4a",
    "accent_deep": "#a0153c",
    "ok": "#23d18b",
    "warn": "#ffb020",
    "disabled_text": "#777777",
    "disabled_fill": "#292929",
    "disabled_edge": "#383838",
}

#: The longest step line the job card wears. The whole line goes onto the
#: terminal behind the card, so the card only has to stay readable over it -
#: the same bargain the in-app update's job card makes.
_JOB_STEP_LEN = 52

#: How many times a download that broke off is picked up from the byte it
#: stopped at before it is called lost, and the pause before each pickup. A
#: twenty-megabyte program over a home connection loses a packet now and then;
#: that used to throw away every byte already in hand - the same patience the
#: in-app updater gives a large download.
MAX_RESUMES = 4
RESUME_WAIT = 0.8

#: How many redirect hops one download may follow before it is called lost.
MAX_REDIRECTS = 8

#: The size of a read: big enough to keep a large build moving, small enough
#: that a broken body loses little and the counter still ticks.
DOWNLOAD_CHUNK = 512 * 1024

#: How far apart two progress reports may be, in seconds. The download's byte
#: counter and PyInstaller's output can each say something new many times a
#: second; the card and the terminal behind it only need to read as alive, and
#: every report is a trip across to the interface thread.
PROGRESS_MIN_INTERVAL = 0.1

#: Suffixes granted the run bit without sniffing, because on the one system this
#: walk runs (never Windows) they are always code to load: ``.so`` objects, whole
#: or versioned (``libcrypto.so.1.1``). ``_internal`` is mostly made of them, so
#: this is where the walk saves the most. Any file whose suffix does not say
#: "executable" still has its first four bytes read, so no ELF is missed.
KNOWN_EXEC_SUFFIXES = (".so",)

#: What the compile door needs a machine's Python to already hold. One name here,
#: so the door's probe and the job's own check cannot drift apart mid-install.
TOOLCHAIN_MODULES = ("PyInstaller", "PyQt6", "paramiko", "keyring")

#: A setup window has no console of its own, and on Windows every program it
#: starts would be given one unless this says otherwise: a black box flashing for
#: a toolchain probe, and one held open for the whole PyInstaller run. The app
#: keeps the same hygiene for its addons (see ``addon_common.NO_CONSOLE``); it is
#: repeated here because the setup is a standalone file that imports none of it.
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: How long a stuck job is given to come out of itself when the person insists on
#: leaving, in the milliseconds ``QThread.wait`` counts in (the app's own window
#: waits the same way - see ``core/window.py``). The job checks its cancel flag
#: between the reads of a download and between the lines a build prints, so this
#: is normally a short pause; a run blocked in something a cancel cannot reach -
#: a socket that has stopped answering - is not, and a QThread destroyed while it
#: still runs takes the program down with it, so the wait is bounded and the
#: thread is then stopped by force.
#: A wait here is never a bare ``wait(3)``: that is three milliseconds, which
#: gives up on a thread that would have landed a second later and jumps straight
#: to ``terminate()``, and killing a thread that is between Python bytecodes is
#: how the setup aborts instead of closing.
FORCE_WAIT_MS = 3000

#: The same bargain for a toolchain probe, which holds an interpreter for as long
#: as that interpreter cares to take.
PROBE_WAIT_MS = 1500

#: The same bargain for the newest-release probe, whose wait covers the ask it
#: can still be inside: :func:`resolve_tag` gives the catalog read eight seconds
#: and falls back when they run out, so a probe waited out past this bound is a
#: probe that would have answered - and terminating one mid-socket is the quiet
#: crash this wait exists to avoid.
NEWEST_WAIT_MS = 9000


class _Resumable(Exception):
    """A download body that broke off, keeping how many bytes reached disk."""

    def __init__(self, have: int, error: BaseException) -> None:
        super().__init__(str(error))
        self.have = int(have)
        self.error = error


def system_name() -> str:
    """The word the release uses for this machine: ``windows`` or ``linux``.

    The published archives are named for exactly this, so the setup asks for the
    one that carries its own program.
    """
    return "windows" if os.name == "nt" else "linux"


def program_file_name() -> str:
    """The program file inside the build, named for this system."""
    return "berry_manager.exe" if system_name() == "windows" else "berry_manager"


def resource_dir() -> Path:
    """The read-only folder this setup was packed into, or its own source folder.

    A frozen build unpacks its data (the logo it shows) under ``sys._MEIPASS``;
    run from a checkout, that is simply the folder this file sits in.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def find_icon() -> Path | None:
    """The berry logo, wherever this copy of the setup can see it."""
    here = resource_dir()
    base = Path(getattr(sys, "executable", __file__)).resolve().parent
    for candidate in (here / "assets" / ICON_FILES[0], here / ICON_FILES[0],
                      base / "assets" / ICON_FILES[0]):
        if candidate.is_file():
            return candidate
    return None


def default_install_dir() -> Path:
    """Where the app lands if the user does not choose: a folder in their own.

    Neither default needs administrator rights, which is the whole point of a
    per-user install: Windows under ``LOCALAPPDATA``, Linux under the home
    folder.
    """
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA")
                    or Path.home() / "AppData" / "Local")
        return root / "Programs" / "Berry Manager"
    return Path.home() / "BerryManager"


def release_tag() -> str:
    """The tag this setup falls back to: ``berry`` + its own version.

    Used only when the newest published release cannot be asked for; the doors
    reach the live catalog through :func:`resolve_tag` and come here on failure.
    """
    return f"{TAG_PREFIX}{APP_VERSION}"


def asset_name() -> str:
    """The archive name the release carries for this system."""
    return f"berry_manager-{system_name()}.zip"


# --- reading a byte-packed version, and choosing the newest release ---

def _version_parts(value) -> tuple:
    """The bytes out of a byte-packed hex version, as a comparable tuple.

    Each dotted part is its own ``0x`` hex byte (``0x3.0x2.0x1`` is what the
    release decimal would call ``3.2.1``), so each is read in base 16 and the
    run compared as numbers - which is why a ``0x10`` patch beats ``0x9``,
    where a text compare would lose it. A part that is not hex is skipped, and
    a version with no readable byte is only compared as text below.
    """
    parts: list[int] = []
    for raw in str(value or "").split("."):
        part = raw.strip()
        if part[:2].lower() == "0x":
            part = part[2:]
        if part and all(c in "0123456789abcdefABCDEF" for c in part):
            parts.append(int(part, 16))
    return tuple(parts)


def _readable(value) -> str:
    """A byte-packed hex version as the decimal a person reads (``3.2.1``).

    The tag and the comparison stay in hex; the words this setup prints go
    through here, the same way the running program's title bar and splash do,
    so the installer never shows ``0x3.0x2.0x1`` where the app would show
    ``3.2.1``. A version nothing can be made of is returned as it came.
    """
    parts = _version_parts(value)
    if not parts:
        return str(value or "")
    return ".".join(str(byte) for byte in parts)


def _version_newer(remote, local) -> bool:
    """Whether one byte-packed version reads as ahead of another."""
    wanted, have = _version_parts(remote), _version_parts(local)
    if wanted and have:
        length = max(len(wanted), len(have))
        return (wanted + (0,) * (length - len(wanted))
                > have + (0,) * (length - len(have)))
    clean_remote, clean_local = str(remote or ""), str(local or "")
    return bool(clean_remote) and clean_remote != clean_local


def tag_version(name, prefix=TAG_PREFIX) -> str:
    """The version a ``berry<version>`` tag names, or nothing when it is not one.

    ``berry0x3.0x2.0x1`` reads as ``0x3.0x2.0x1``; a tag that does not start with
    the prefix, or carries nothing comparable after it, is not this program's
    release and is passed over.
    """
    text = str(name or "").strip()
    if not text or not text.lower().startswith(str(prefix).lower()):
        return ""
    rest = text[len(prefix):].strip()
    return rest if _version_parts(rest) else ""


def pick_latest(tags) -> tuple | None:
    """The newest ``(tag, version)`` in a list of tag names, or nothing.

    GitHub gives tags back in an order of its own, so this does not trust it:
    each one that reads as a version is compared and the best kept. This is the
    same rule the Manager update addon applies, so the setup reaches for the
    build that tab would offer.
    """
    best: tuple | None = None
    for name in tags or []:
        version = tag_version(name)
        if not version:
            continue
        if best is None or _version_newer(version, best[1]):
            best = (str(name).strip(), version)
    return best


# --- what touches the network: the downloads and one catalog read ---

def _fetch_once(base: str, dest: Path, have: int, report, token: str,
                cancel: dict | None) -> None:
    """One walk of the redirect and one read of the body, resuming at ``have``.

    The ``Range`` that picks a broken body up from its stopping byte rides on the
    request that finally answers with the file; the ``github.com`` hop only points
    somewhere else, so asking it for a range is harmless. A ``206`` means the rest
    is coming and is appended to what is already on disk; a ``200`` means the
    range was ignored, so the file is started fresh - no worse than never asking.
    """
    host, path = "github.com", base
    hops = 0
    while True:
        conn = http.client.HTTPSConnection(host, timeout=30)
        headers = {"User-Agent": f"berry-setup/{APP_VERSION}",
                   "Accept": "application/octet-stream"}
        # The one-time signature GitHub redirects to carries its own access in
        # the URL, so a token is sent to the github.com hop only.
        if token and host.endswith("github.com"):
            headers["Authorization"] = f"Bearer {token}"
        if have > 0:
            headers["Range"] = f"bytes={have}-"
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = int(response.status)
        if status in (301, 302, 303, 307, 308):
            location = response.getheader("Location") or ""
            response.read()
            conn.close()
            hops += 1
            if not location or hops > MAX_REDIRECTS:
                raise OSError("the download kept being pointed elsewhere")
            parts = urlsplit(location)
            if parts.scheme != "https" or not parts.netloc:
                raise OSError(f"{location} is not an https address")
            host, path = parts.netloc, (parts.path or "/")
            if parts.query:
                path = f"{path}?{parts.query}"
            continue
        if status == 416 and have > 0:
            # The range sits past the end: whatever was kept was wrong, so drop
            # it and let the caller ask for the whole file again.
            response.read()
            conn.close()
            raise _Resumable(0, OSError("the kept bytes were past the end"))
        if status == 200:
            mode, written = "wb", 0
        elif status == 206:
            mode, written = "ab", have
        else:
            body = response.read(400)
            conn.close()
            raise OSError(f"GitHub refused with {status}: "
                          f"{body.decode('utf-8', 'replace')[:200]}")
        length = response.getheader("Content-Length") or ""
        part = int(length) if length.isdigit() else 0
        total = written + part if part else 0
        done = written
        with dest.open(mode) as handle:
            while True:
                if cancel is not None and cancel.get("cancel"):
                    conn.close()
                    raise OSError("the download was cancelled")
                try:
                    chunk = response.read(DOWNLOAD_CHUNK)
                except (http.client.HTTPException, OSError) as error:
                    handle.flush()
                    raise _Resumable(done, error)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                report(done, total)
        conn.close()
        if done == 0:
            raise OSError("the download answered with nothing at all")
        if total and done < total:
            # The body ended short of what it promised: keep what landed and
            # pick the rest up on the next try rather than accept a cut file.
            raise _Resumable(done, OSError("the download ended early"))
        return


def fetch_github(tail: str, dest: Path, report, owner: str, repo: str,
                 cancel: dict | None = None) -> None:
    """GET one address from GitHub, following the redirect, into ``dest``.

    Both fetches - the release archive and the source snapshot - go through
    here, which is where the two archive downloads leave the machine (the
    catalog read is its own small door, ``github_api_json``). GitHub answers
    these links by pointing somewhere else, so the redirect is followed by hand,
    one hop at a time, and a private token (when the environment carries one) is
    sent to the ``github.com`` hop only and never on to the file server that
    finally hands the bytes over. ``report`` is called with
    ``(bytes_done, bytes_total)`` as they arrive.

    A body that breaks off mid-way is not thrown away: the bytes on disk are kept
    and the same address is asked for the rest, up to ``MAX_RESUMES`` times with a
    short pause. A server that will not resume answers the whole file again, so a
    lost partial costs only the retry. ``cancel``, when given, is a
    ``{"cancel": bool}`` flag checked between chunks, so a download can be walked
    away from too - not only a build - and a cancellation is honoured, not retried.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    base = f"/{owner.strip('/')}/{repo.strip('/')}{tail}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = 0
    attempt = 0
    while True:
        attempt += 1
        try:
            _fetch_once(base, dest, have, report, token, cancel)
            return
        except _Resumable as lost:
            have = lost.have
            if attempt >= MAX_RESUMES:
                raise OSError(f"the download broke off {attempt} times: "
                              f"{lost.error}") from lost.error
            time.sleep(RESUME_WAIT)
        except (OSError, http.client.HTTPException):
            # A cancellation is a wish to stop, not a stumble to recover from.
            if cancel is not None and cancel.get("cancel"):
                raise
            if attempt >= MAX_RESUMES:
                raise
            have = 0
            dest.unlink(missing_ok=True)
            time.sleep(RESUME_WAIT)


def github_api_json(owner: str, repo: str, tail: str, timeout: int = 20):
    """GET one ``api.github.com`` answer for this repository and parse it.

    Both doors ask the catalog before they download, so this is the one small
    read that is not a file: the JSON back, or an OSError saying why not. A
    token in the environment is sent, which lifts the shared anonymous rate
    limit - the same bargain the update addon makes. ``api.github.com`` answers
    in one hop, so unlike the downloads there is no redirect to walk. The
    patience is the caller's to shorten: a tag lookup that a machine cannot
    reach is better off giving up quickly and falling back than hanging.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    # The API hangs repositories off /repos/, unlike the download host where
    # owner/repo sits at the top; asking without it answers a quiet 404.
    path = f"/repos/{owner.strip('/')}/{repo.strip('/')}/{tail.lstrip('/')}"
    headers = {"User-Agent": f"berry-setup/{APP_VERSION}",
               "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPSConnection("api.github.com", timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = int(response.status)
        body = response.read()
    finally:
        conn.close()
    if status != 200:
        raise OSError(f"the GitHub API refused with {status}")
    return json.loads(body.decode("utf-8", "replace"))


#: The newest tag each ``owner/repo`` resolved to this run, so a door that asks
#: twice does not wait on the network twice.
_tag_cache: dict[tuple[str, str], str] = {}


def resolve_tag(owner: str, repo: str) -> str:
    """The tag to install: the newest published ``berry`` release if it can be
    asked for, else the tag this setup was built to match.

    Asking the catalog is what lets a setup published weeks ago still install
    the current build, and lets a fork reach its own newest - the very thing the
    in-app update tab does. When the network cannot answer, :func:`release_tag`
    keeps the offline path working rather than dead in the water - and the ask
    wears a short patience so a known-offline machine falls back in a couple of
    seconds, not twenty. A real answer is remembered for the rest of the run; a
    fallback is not, so a link that comes back later is still taken.
    """
    key = (owner, repo)
    cached = _tag_cache.get(key)
    if cached:
        return cached
    try:
        rows = github_api_json(owner, repo, "tags?per_page=100", timeout=8)
    except (OSError, ValueError, http.client.HTTPException):
        return release_tag()
    names = ([row.get("name") for row in rows if isinstance(row, dict)]
             if isinstance(rows, list) else [])
    best = pick_latest(names)
    if not best:
        return release_tag()
    _tag_cache[key] = best[0]
    return best[0]


def download_release(dest: Path, report, owner: str, repo: str,
                     tag: str, cancel: dict | None = None) -> None:
    """Fetch ``tag``'s archive for the running system into ``dest``."""
    fetch_github(f"/releases/download/{tag}/{asset_name()}",
                 dest, report, owner, repo, cancel)


def download_source(dest: Path, report, owner: str, repo: str,
                    tag: str, cancel: dict | None = None) -> None:
    """Fetch the source snapshot ``tag`` names into ``dest``.

    A compile reads the program's own ``src/berry_manager`` off GitHub rather
    than carrying a private copy inside the setup, so the source it builds is
    exactly the one the release names - and the setup stays a small file.
    """
    fetch_github(f"/archive/refs/tags/{tag}.tar.gz",
                 dest, report, owner, repo, cancel)


# --- how the build is recognised and laid down ---

def looks_like_program_tree(root: Path) -> bool:
    """Whether ``root`` holds a ``--onedir`` build: the program file and a
    ``_internal`` beside it.

    Structure, not size: a lone stub would pass a header test yet be nothing
    without its libraries, which is exactly the mistake this guard is here to
    keep out.
    """
    if not root.is_dir():
        return False
    return ((root / program_file_name()).is_file()
            and (root / INTERNAL_DIR).is_dir())


def extract_release(archive: Path, staging: Path, report) -> Path:
    """Unpack the release archive into ``staging`` and hand back the build.

    The archive carries the build at its top (program file, ``_internal``, the
    drop-in ``addons``), but a folder packed a different way is tolerated: the
    pair is looked for in ``staging`` and then one level down, rather than being
    guessed at. The caller owns ``staging`` and takes it away when the build it
    holds has been put in place.
    """
    report("unpacking")
    with zipfile.ZipFile(archive) as bundle:
        rows = bundle.infolist()
        total = len(rows) or 1
        for index, row in enumerate(rows, start=1):
            bundle.extract(row, path=staging)
            if index % 40 == 0 or index == total:
                report(f"unpacking ({index}/{total} files)")
    if looks_like_program_tree(staging):
        return staging
    for child in sorted(staging.iterdir()):
        if child.is_dir() and looks_like_program_tree(child):
            return child
    raise OSError("the archive holds no Berry Manager build - a "
                  f"{program_file_name()} and an {INTERNAL_DIR}/ were looked "
                  "for and not found")


def _restore_exec_bits(root: Path) -> None:
    """Put the run bit back on every ELF a copied or unpacked tree holds.

    The release archive is the one place permissions cannot be trusted: the
    artifact upload hands files back at 0644, a zip written on Windows carries
    no unix mode at all, and Python's own unzip never sets the run bit. A
    helper like ``QtWebEngineProcess`` then lands unrunnable, and Chromium's
    zygote dies on the failed exec with a fatal CHECK - which reads to a
    person as the program vanishing the moment it opens.

    A suffix that gives the file away settles it without an open: ``.so``
    objects (see ``KNOWN_EXEC_SUFFIXES``) are code to load on this system and
    are granted ``+x`` straight away, which spares the walk through the hundreds
    of libraries that make up most of ``_internal``. Everything whose suffix
    does not say "executable" still has its first four bytes read - the ELF
    magic is the authority - so a real binary is never missed for the sake of a
    name, and a data file with a misleading one costs only a short read. The
    running program is chmodded by the caller whatever its header.
    """
    for here, _dirs, files in os.walk(root):
        for name in files:
            path = Path(here) / name
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                if not any(suffix in KNOWN_EXEC_SUFFIXES
                           for suffix in path.suffixes):
                    with path.open("rb") as handle:
                        if handle.read(4) != b"\x7fELF":
                            continue
                path.chmod(path.stat().st_mode | 0o111)
            except OSError:
                continue


def staging_dir(target: Path, prefix: str = "berry_stage_") -> Path:
    """A fresh throwaway folder on the same filesystem as ``target``.

    Laying a build down is a move, and a move is only free when it stays on one
    device. The default temp folder so often lives on another (a ramdisk at
    ``/tmp`` while the app goes under the home folder), which would force a
    byte-for-byte copy of a hundred megabytes of libraries. So the staging area
    is made beside the target - under it, then by it, then above it, and only in
    the ordinary temp folder as a last resort - whichever first lets a folder be
    created there, so ``place_build`` can rename where it can and copy where it
    must.
    """
    seen: set[Path] = set()
    parent = target
    while parent is not None and parent not in seen:
        seen.add(parent)
        try:
            if parent.is_dir():
                return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
        except OSError:
            pass
        parent = parent.parent if parent.parent != parent else None
    return Path(tempfile.mkdtemp(prefix=prefix))


def _take_away(path: Path) -> None:
    """Throw out a leftover, best-effort: by now it is only garbage."""
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def place_build(build_root: Path, target: Path) -> None:
    """Move an unpacked build to where the user asked for it, and make it run.

    The program and ``_internal`` come over together; the drop-in ``addons``
    folder is added if the archive did not carry one, so the first start has the
    folder the program looks for already beside it. Anything of the same name
    already at the target is taken first - this is a fresh install, not a merge.

    Taken first means moved aside under a ``.old`` name, not deleted up front. A
    delete can half-happen: Windows refuses to clear a folder whose files the
    running app is holding, and doing that with the errors swallowed used to
    leave the target holding some of the old build and some of the new one - a
    program that does not fail to install, but fails to start, which is a far
    worse thing to meet. A rename is all or nothing, what cannot be renamed
    aside is said as the refusal it is (close the app and try again), and a stop
    part way through puts every name back the way it found it: a folder holding
    the new program beside the old ``_internal`` is the same half build this
    function exists to avoid.
    """
    target.mkdir(parents=True, exist_ok=True)
    program = program_file_name()
    moved: list[tuple[Path, Path]] = []       # landing -> aside, undone on a stop
    half: Path | None = None                  # an aside a refused move left part
    try:
        for name in (program, INTERNAL_DIR):
            source = build_root / name
            landing = target / name
            aside = target / f"{name}.old"
            half = aside
            if aside.exists():
                _take_away(aside)         # a leftover of an earlier attempt
            if landing.exists():
                try:
                    shutil.move(str(landing), str(aside))
                except OSError as error:
                    raise OSError(f"{landing} could not be moved out of the way "
                                  f"- close Berry Manager and install again "
                                  f"({error})") from error
                moved.append((landing, aside))
            # A rename is one syscall whatever the size; a copy moves every byte.
            # The build is staged on the target's own filesystem (see
            # ``staging_dir``), so this is normally the rename - and ``shutil.move``
            # still falls back to copying across devices when it is not.
            try:
                shutil.move(str(source), str(landing))
            except OSError as error:
                raise OSError(f"the new build could not be put at {landing}: "
                              f"{error}") from error
            half = None
    except OSError:
        for landing, aside in reversed(moved):
            if landing.exists():          # the new copy came: take it back out
                _take_away(landing)
            try:
                shutil.move(str(aside), str(landing))
            except OSError:               # nothing more to do but say where it stands
                pass
        if half is not None:
            _take_away(half)              # a refused move may have copied part of it
        raise
    for _landing, aside in moved:
        _take_away(aside)                 # replaced now, so the old copy goes
    if os.name != "nt":
        _restore_exec_bits(target)
        try:
            (target / program).chmod(0o755)
        except OSError:
            pass
    addons = target / "addons"
    addons.mkdir(exist_ok=True)
    readme = addons / "README.txt"
    if not readme.exists():
        readme.write_text(ADDONS_README, encoding="utf-8")


def launch_program(program: Path) -> None:
    """Start the freshly installed program, detached from this setup.

    The setup opened a door onto the app; launching walks through it. The new
    copy is started detached - its own session on unix, a detached process on
    Windows - with the install folder as its home, so it outlives this setup and
    finds the ``_internal`` and ``addons`` beside it. Nothing here waits on it:
    the app is on its own the moment it is up.
    """
    if not program.is_file():
        raise OSError(f"{program.name} is not there to start")
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen([str(program)], cwd=str(program.parent),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=flags)
        return
    try:
        program.chmod(program.stat().st_mode | 0o111)
    except OSError:
        pass
    subprocess.Popen([str(program)], cwd=str(program.parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     start_new_session=True)


# --- the source a compile reads, and the staging a faithful build needs ---

def extract_source(tarball: Path, dest: Path, report) -> Path:
    """Pull ``src/berry_manager`` out of the source snapshot and hand it back.

    The GitHub archive wraps everything in one ``<repo>-<tag>/`` folder, and a
    whole repository carries far more than a build needs (the images and the
    stores among it). Only the members under ``src/berry_manager`` are written,
    with that prefix kept, so the result is the source folder a compile expects
    and nothing else is put on disk. The caller owns ``dest``.
    """
    report("reading the source")
    dest.mkdir(parents=True, exist_ok=True)
    kept = 0
    with tarfile.open(tarball, "r:*") as source:
        members = source.getmembers()
        total = len(members) or 1
        for index, member in enumerate(members, start=1):
            parts = member.name.split("/")
            if "src" not in parts or "berry_manager" not in parts:
                continue
            tail = _after_source_prefix(parts)
            if tail is None:
                continue
            landing = dest.joinpath(*tail)
            if member.isdir():
                landing.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                landing.parent.mkdir(parents=True, exist_ok=True)
                handle = source.extractfile(member)
                if handle is not None:
                    landing.write_bytes(handle.read())
                kept += 1
            if index % 200 == 0 or index == total:
                report(f"reading the source ({index}/{total})")
    root = dest / "src" / "berry_manager"
    if not (root / "berry_manager.py").is_file():
        raise OSError("the source snapshot held no src/berry_manager to build "
                      f"(read {kept} file(s)) - is the tag right?")
    return root


def _after_source_prefix(parts: list[str]) -> list[str] | None:
    """The ``["src", "berry_manager", ...]`` tail of an archive name, or nothing.

    Anchored on the last ``src``/``berry_manager`` pair so a repository folder
    named on the front is dropped while the real source path is kept.
    """
    try:
        at = len(parts) - 1 - parts[::-1].index("berry_manager")
    except ValueError:
        return None
    if at >= 1 and parts[at - 1] == "src":
        return parts[at - 1:]
    return None


def _stage_addon_bundle(source: Path, into: Path) -> None:
    """Copy the shipped addons into ``into``, minus the runtime's scratch folders.

    This is the setup's own reading of what the CI workflow stages: a loose
    ``.py``, a compiled program with its ``.json`` manifest beside it, and a
    folder that holds one of the shapes the scanner accepts. ``_tests``,
    ``_data``, ``__pycache__`` and the like do not belong to the build.
    """
    addons = source / "addons"
    if not addons.is_dir():
        into.mkdir(parents=True, exist_ok=True)
        return
    into.mkdir(parents=True, exist_ok=True)
    for entry in addons.iterdir():
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_file() and entry.suffix == ".py":
            shutil.copy2(entry, into / entry.name)
        elif entry.is_file() and entry.suffix in (".exe", ".bin"):
            shutil.copy2(entry, into / entry.name)
            manifest = addons / f"{entry.stem}.json"
            if manifest.is_file():
                shutil.copy2(manifest, into / manifest.name)
        elif entry.is_dir() and _is_addon_folder(entry):
            shutil.copytree(entry, into / entry.name,
                            ignore=shutil.ignore_patterns("__pycache__",
                                                          "tests", "_tests",
                                                          "*.pyc"))


def _is_addon_folder(folder: Path) -> bool:
    """Whether a folder is shaped like an addon the scanner would read."""
    if (folder / "addon.py").is_file() or (folder / "main.py").is_file():
        return True
    for name in (f"{folder.name}.exe", "addon.exe", "addon.bin"):
        if (folder / name).is_file():
            return True
    return False


def _stage_harness(source: Path, into: Path) -> None:
    """Copy the modules a compiled addon imports: the loose ``*.py`` and ``core``.

    Building or testing an addon happens in a Python of its own, outside the
    archive, and reads ``addon_client`` and ``addon_common`` off disk; the
    program ships a plain copy for it to point at. The app's other modules go
    along so an addon's own test can import them too.
    """
    into.mkdir(parents=True, exist_ok=True)
    for script in source.glob("*.py"):
        shutil.copy2(script, into / script.name)
    core = source / "core"
    if core.is_dir():
        shutil.copytree(core, into / "core",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def quiet_kwargs() -> dict:
    """How to start a child of the setup without it showing a console window."""
    return {"creationflags": NO_CONSOLE} if os.name == "nt" else {}


def which_python() -> str | None:
    """A real interpreter a compile can drive, or ``None``.

    A packaged setup is frozen - its ``sys.executable`` is the setup itself, not
    a Python - so the interpreter is looked for on the path under the usual
    names, and the current one is only trusted when it is a genuine ``python``.
    """
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found:
            return found
    if not getattr(sys, "frozen", False) and Path(sys.executable).name.lower() \
            .startswith("python"):
        return sys.executable
    return None


def python_has(py: str, modules: tuple[str, ...]) -> tuple[bool, list[str]]:
    """Whether ``py`` can import each of ``modules``; answer what is missing.

    One probe runs every import in a fresh interpreter, so a machine missing
    three of them is told all three at once rather than one dialog per name.
    """
    script = ("import importlib.util, sys; "
              f"names={list(modules)!r}; "
              "missing=[n for n in names if importlib.util.find_spec(n) is None]; "
              "print('\\n'.join(missing))")
    try:
        answer = subprocess.run([py, "-c", script], capture_output=True,
                                text=True, timeout=60, **quiet_kwargs())
    except (OSError, subprocess.SubprocessError) as error:
        return False, [f"could not run {py}: {error}"]
    if answer.returncode != 0:
        return False, [f"{py} would not start: "
                       f"{answer.stderr.strip()[:200] or 'unknown error'}"]
    missing = [line.strip() for line in answer.stdout.splitlines()
               if line.strip()]
    return not missing, missing


def run_compile(source: Path, target: Path, py: str, report,
                cancel_flag, base: str | None = None) -> None:
    """Build the program from ``source`` with PyInstaller into ``target``.

    Everything happens in a throwaway copy, so the source tree is never touched
    and the leftovers a compile makes (``build/``, ``dist/``, the ``.spec``, the
    staging folders) go with that copy when the finished build has been moved.
    The flags mirror the workflow exactly, so a local build and a published one
    are the same program. ``base`` is where that throwaway is made; passing a
    folder on the target's own filesystem makes the final move a rename.
    """
    work = Path(tempfile.mkdtemp(prefix="berry_build_", dir=base))
    try:
        stage = work / "src"
        report("copying the source to a build folder")
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "build", "dist", "_addon_bundle",
            "_addon_harness", ".idea"))
        _stage_addon_bundle(stage, stage / "_addon_bundle")
        _stage_harness(stage, stage / "_addon_harness")
        icon = stage / "assets" / ICON_FILES[0]

        sep = ";" if os.name == "nt" else ":"
        arguments = [
            py, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
            "--icon", str(icon),
            "--add-data", f"assets{sep}assets",
            "--add-data", f"config.example.json{sep}.",
            "--add-data", f"_addon_bundle{sep}addons",
            "--add-data", f"_addon_harness{sep}harness",
            "--hidden-import", "PyQt6.QtWebEngineWidgets",
            "--hidden-import", "PyQt6.QtWebEngineCore",
            "--distpath", str(work / "dist"),
            "--workpath", str(work / "build"),
            "--specpath", str(work),
        ]
        if os.name != "nt":
            arguments += ["--hidden-import", "secretstorage",
                          "--hidden-import", "jeepney"]
        arguments.append("berry_manager.py")

        report("running PyInstaller (this takes a minute or two)")
        process = subprocess.Popen(arguments, cwd=str(stage),
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace",
                                   **quiet_kwargs())
        assert process.stdout is not None
        for line in process.stdout:
            if cancel_flag["cancel"]:
                process.kill()
                raise OSError("the build was cancelled")
            text = line.strip()
            if text:
                report(text[-80:])
        code = process.wait()
        if code != 0:
            raise OSError(f"PyInstaller stopped with code {code}; "
                          "see its output above")

        built = work / "dist" / "berry_manager"
        if not looks_like_program_tree(built):
            raise OSError("the build finished but its folder is not a Berry "
                          "Manager build (a program file and _internal/)")
        report("putting the new build where you asked")
        place_build(built, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- the worker thread: the job runs here, the card is drawn on the UI thread ---

class Worker(QThread):
    """One door's work, off the interface thread, reporting as it goes.

    A download or a PyInstaller run holds its thread for a minute or more; done
    on the UI thread that would freeze the card the user is watching, so the job
    lives here and speaks through signals - a step of progress, and one final
    outcome either way.
    """

    progress = pyqtSignal(str, float)          # (what now, 0..1 or -1 unknown)
    finished = pyqtSignal(bool, str)           # (ok, the closing words)

    def __init__(self, mode: str, target: Path, owner: str, repo: str,
                 python: str | None) -> None:
        super().__init__()
        self.mode = mode
        self.target = target
        self.owner = owner
        self.repo = repo
        self.python = python
        self._cancel = {"cancel": False}
        self._last_emit = 0.0

    def cancel(self) -> None:
        self._cancel["cancel"] = True

    def _progress(self, text: str, frac: float = -1,
                  force: bool = False) -> None:
        """Report a step, no more than about ten times a second.

        The download's byte counter and PyInstaller's log can each say something
        new many times a second, and every report is a trip across to the
        interface thread that redraws a terminal line. The card only has to read
        as alive, so reports inside the interval are dropped - unless a step is
        changing (``force``), which is worth showing whole and on time.
        """
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_MIN_INTERVAL:
            return
        self._last_emit = now
        self.progress.emit(text, frac)

    def run(self) -> None:                        # noqa: D401 - QThread contract
        try:
            if self.mode == "install":
                self._do_install()
            else:
                self._do_compile()
        except Exception as error:            # noqa: BLE001 - a stray error let
            # loose from a QThread's run() calls qFatal and aborts the whole
            # process; caught here it is only a job the card can wear as failed,
            # not a crash the moment the work is done.
            self.finished.emit(False, str(error) or error.__class__.__name__)

    def _do_install(self) -> None:
        tag = resolve_tag(self.owner, self.repo)
        version = _readable(tag_version(tag) or tag)
        archive = Path(tempfile.gettempdir()) / asset_name()
        staging = staging_dir(self.target, "berry_install_")
        try:
            self._progress(f"fetching {asset_name()} ({version}) from the "
                           f"release", -1, force=True)

            def on_bytes(done: int, total: int) -> None:
                frac = (done / total) if total else -1
                size = f"{done / 1_048_576:.1f} MB"
                if total:
                    size += f" of {total / 1_048_576:.1f} MB"
                self._progress(f"downloading {size}", frac)

            download_release(archive, on_bytes, self.owner, self.repo, tag,
                             self._cancel)
            build_root = extract_release(archive, staging,
                                         lambda t: self._progress(t, -1))
            self._progress("putting the build in place", -1, force=True)
            place_build(build_root, self.target)
            self.finished.emit(True, f"Berry Manager {version} is "
                                     f"installed at {self.target}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                archive.unlink()
            except OSError:
                pass

    def _do_compile(self) -> None:
        assert self.python is not None
        self._progress(f"checking what {self.python} can import", -1, force=True)
        # Probed here, on the job's own thread: asking an interpreter what it can
        # import is starting that interpreter, and it is under no obligation to
        # answer quickly. Done on the interface thread this was the setup card
        # frozen solid for up to a minute, with a console window flashing beside
        # it on Windows, the moment the compile door was opened.
        ok, missing = python_has(self.python, TOOLCHAIN_MODULES)
        if not ok:
            self.finished.emit(False, f"{self.python} is missing "
                                      f"{', '.join(missing)}")
            return
        if self._cancel["cancel"]:
            self.finished.emit(False, "the build was cancelled")
            return
        tag = resolve_tag(self.owner, self.repo)
        version = _readable(tag_version(tag) or tag)
        work = staging_dir(self.target, "berry_source_")
        try:
            tarball = work / "source.tar.gz"
            self._progress(f"fetching the source of {version} from "
                           f"{self.owner}/{self.repo}", -1, force=True)

            def on_bytes(done: int, total: int) -> None:
                frac = (done / total) if total else -1
                size = f"{done / 1024} KB"
                if total:
                    size += f" of {total / 1024} KB"
                self._progress(f"downloading source {size}", frac)

            download_source(tarball, on_bytes, self.owner, self.repo, tag,
                            self._cancel)
            source = extract_source(tarball, work,
                                    lambda t: self._progress(t, -1))
            tarball.unlink(missing_ok=True)
            run_compile(source, self.target, self.python,
                        lambda text: self._progress(text, -1),
                        self._cancel, base=str(work))
            self.finished.emit(True, f"a local build of Berry Manager "
                                     f"{version} is installed at "
                                     f"{self.target}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --- the card ---

class ToolchainProbe(QThread):
    """One ask of what a machine's Python can import, off the interface thread.

    The answer only decorates the door's hint line, so it is worth neither a
    frozen card nor a black console window; the card starts one of these and is
    told the answer through ``answered`` when the interpreter gets round to it.
    """

    answered = pyqtSignal(str, bool, list)      # (interpreter, ok, missing)

    def __init__(self, py: str, modules: tuple[str, ...]) -> None:
        super().__init__()
        self.py = py
        self.modules = tuple(modules)

    def run(self) -> None:                        # noqa: D401 - QThread contract
        try:
            ok, missing = python_has(self.py, self.modules)
        except Exception as error:            # noqa: BLE001 - as in Worker.run:
            # a stray error loose from a QThread's run() aborts the whole setup,
            # and this one is only a hint nobody was waiting for.
            ok, missing = False, [f"{type(error).__name__}: {error}"]
        self.answered.emit(self.py, ok, list(missing))


#: Probes this card overtook while they were still asking: kept referenced
#: until they land, because a QThread destroyed mid-run takes the setup down
#: with it, and a UI-thread wait on a network call would be its own small freeze.
_ABOARD: list = []


class GitHubProbe(QThread):
    """The newest published release, asked off the interface thread.

    The setup form names the version it is about to offer before anybody
    presses a button, and the only honest way to know it is to read the
    catalog - which is a network call wearing whatever patience the network
    has (up to the eight seconds :func:`resolve_tag` gives it). So the ask
    lives here, started when the card opens and retargeted when the git
    fields move; the answer only decorates a label.
    """

    answered = pyqtSignal(str)      # the readable newest version, or ""

    def __init__(self, owner: str, repo: str) -> None:
        super().__init__()
        self.owner = owner
        self.repo = repo

    def run(self) -> None:                        # noqa: D401 - QThread contract
        try:
            tag = resolve_tag(self.owner, self.repo)
            text = _readable(tag_version(tag) or tag)
        except Exception:                      # noqa: BLE001 - as in Worker.run:
            # a stray error loose from a QThread's run() aborts the whole setup,
            # and this one was only ever going to fill in a label; the card
            # falls back to the version the setup carries itself.
            text = ""
        self.answered.emit(text)


class InstallerCard(QWidget):
    """The window: the splash's look, the two doors, and a job view.

    It opens as the boot card does - logo, name, and what an installer needs:
    a choice of door, the folder it will write to, and the buttons to start and
    to close. Once a door opens the form gives way to the view an in-app update
    wears for a long job: a terminal for a background printing every step as it
    arrives, with a card floating in the middle naming the version and counting
    its share. When the job lands, the card's bar is replaced by a line that
    says it is done and the two things worth doing next - launch the program, or
    close the setup.
    """

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._worker: Worker | None = None
        self._probe: ToolchainProbe | None = None
        self._gprobe: GitHubProbe | None = None
        # The last toolchain answer for this card's interpreter, so opening and
        # shutting the compile door does not start a fresh interpreter each time.
        self._toolchain: tuple[str, bool, list[str]] | None = None
        self._cancel_asked = False
        self._leaving = False
        self._drag: tuple[int, int] | None = None
        self._ok = False
        self._program: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("splashCard")
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(30, 24, 30, 20)
        body.setSpacing(9)

        # Two faces for one card: the setup form it opens with, and the job the
        # doors run - a centred card, with its console folded behind it until
        # somebody asks. Only one is on screen at a time.
        self.stack = QStackedWidget(card)
        self.stack.addWidget(self._build_setup_page())
        self.stack.addWidget(self._build_job_page())
        body.addWidget(self.stack)

        self.install_choice.toggled.connect(self._door_changed)
        # The release either door reaches for is newest-whatever-GitHub-has;
        # re-ask when the git fields move, so the label always names the
        # repository the next press would actually use.
        self.owner_edit.editingFinished.connect(self._start_gprobe)
        self.repo_edit.editingFinished.connect(self._start_gprobe)
        self.setFixedWidth(520)
        self.setStyleSheet(self._card_sheet())
        self.stack.setCurrentIndex(0)
        self.adjustSize()
        self._center_on_screen()
        self._door_changed()
        self._toggle_advanced()     # the git source starts folded away
        self._start_gprobe()

    # -- the setup form --

    def _build_setup_page(self) -> QWidget:
        """The face the installer opens with: the splash, and the two doors."""
        page = QWidget()
        grid = QVBoxLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(9)

        self.logo = QLabel(page)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._place_logo()

        title = QLabel(f"Berry Manager V{_readable(APP_VERSION)} - setup", page)
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Install the published build, or compile one here.",
                          page)
        subtitle.setObjectName("splashStep")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        # The newest release this setup can find, named on the form before
        # anything is pressed - what "Install" would actually bring down.
        self.newest = QLabel("checking for the newest release...", page)
        self.newest.setObjectName("splashStep")
        self.newest.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.newest.setWordWrap(True)

        doors = QHBoxLayout()
        self.install_choice = QRadioButton("Install release", page)
        self.compile_choice = QRadioButton("Compile on this PC", page)
        self.install_choice.setChecked(True)
        doors.addWidget(self.install_choice)
        doors.addStretch(1)
        doors.addWidget(self.compile_choice)

        self.dir_edit = QLineEdit(str(default_install_dir()), page)
        browse = QPushButton("Browse...", page)
        browse.clicked.connect(self._browse)
        where = QHBoxLayout()
        where.addWidget(self.dir_edit, 1)
        where.addWidget(browse)

        self.owner_edit = QLineEdit(OWNER, page)
        self.owner_edit.setPlaceholderText("owner")
        self.repo_edit = QLineEdit(REPO, page)
        self.repo_edit.setPlaceholderText("repo")
        gitbox = QHBoxLayout()
        gitbox.addWidget(self.owner_edit, 1)
        gitbox.addWidget(self.repo_edit, 2)

        self.step = QLabel("", page)
        self.step.setObjectName("splashStep")
        self.step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step.setWordWrap(True)

        # The git source is the one part of this form nobody on the normal
        # path should ever see: the published build comes from this
        # program's own repository, and only the compiler door can be
        # pointed elsewhere - so the two fields wait behind a line that
        # says what opening them is for.
        self.adv_toggle = QPushButton("Advanced: build from another repository",
                                      page)
        self.adv_toggle.setObjectName("advToggle")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setChecked(False)
        self.adv_toggle.clicked.connect(self._toggle_advanced)
        self.git_label = QLabel("Git source (owner / repo)", page)
        self.git_hint = QLabel("Only change these when the build comes from "
                               "somewhere other than this program's own "
                               "repository.", page)
        self.git_hint.setObjectName("splashStep")
        self.git_hint.setWordWrap(True)

        self.start_button = QPushButton("Install", page)
        self.start_button.setObjectName("primaryAction")
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Close", page)
        self.cancel_button.clicked.connect(self._close_or_cancel)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button, 1)
        buttons.addWidget(self.cancel_button)

        grid.addWidget(self.logo)
        grid.addWidget(title)
        grid.addWidget(subtitle)
        grid.addWidget(self.newest)
        grid.addLayout(doors)
        grid.addWidget(QLabel("Install folder", page))
        grid.addLayout(where)
        grid.addWidget(self.adv_toggle)
        grid.addWidget(self.git_label)
        grid.addLayout(gitbox)
        grid.addWidget(self.git_hint)
        grid.addWidget(self.step)
        grid.addLayout(buttons)
        return page

    def _toggle_advanced(self) -> None:
        """Show the git source fields, or fold them back out of the way."""
        show = bool(self.adv_toggle.isChecked())
        for widget in (self.git_label, self.owner_edit, self.repo_edit,
                       self.git_hint):
            widget.setVisible(show)
        self.adjustSize()

    # -- the job view: a card, and the console only for whoever asks --

    def _build_job_page(self) -> QWidget:
        """The face a door wears while it works: one card, console on demand."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        holder = QWidget(page)
        cell = QGridLayout(holder)
        cell.setContentsMargins(0, 0, 0, 0)
        # The console used to be the page's height; with it folded away the
        # card needs the same room to stand in, or the whole face shrinks
        # to a paragraph the moment somebody presses Show details.
        holder.setMinimumHeight(320)

        self.terminal = QPlainTextEdit(holder)
        self.terminal.setObjectName("jobTerm")
        self.terminal.setReadOnly(True)
        self.terminal.setMinimumHeight(300)
        # A whole PyInstaller run can print many thousands of lines; the console
        # only needs the recent tail, so the buffer is capped and old lines fall
        # off the back instead of quietly eating the memory of a long build.
        self.terminal.setMaximumBlockCount(2000)
        cell.addWidget(self.terminal, 0, 0)
        self._details_shown = False
        self.terminal.setVisible(False)

        # The card shares the terminal's cell and is centred over it - the same
        # overlay an update puts its job on - so opening the console slides it
        # in behind a card that never moves, and the card keeps the one thing
        # worth reading at a glance.
        self.job_card = QFrame(holder)
        self.job_card.setObjectName("jobCard")
        self.job_card.setFixedWidth(320)
        cell.addWidget(self.job_card, 0, 0, Qt.AlignmentFlag.AlignCenter)

        inner = QVBoxLayout(self.job_card)
        inner.setContentsMargins(18, 14, 18, 16)
        inner.setSpacing(7)

        self.card_done = QLabel("DONE", self.job_card)
        self.card_done.setObjectName("jobDone")
        self.card_done.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_done.hide()

        self.card_icon = QLabel(self.job_card)
        self.card_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._place_job_icon()

        self.card_title = QLabel("", self.job_card)
        self.card_title.setObjectName("jobName")
        self.card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_title.setWordWrap(True)

        self.card_step = QLabel("", self.job_card)
        self.card_step.setObjectName("jobStep")
        self.card_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_step.setWordWrap(True)

        self.card_progress = QProgressBar(self.job_card)
        self.card_progress.setObjectName("jobBar")
        self.card_progress.setRange(0, 100)
        self.card_progress.setValue(0)
        self.card_progress.setTextVisible(True)
        self.card_progress.setFormat("%p%")

        # The card's one concession to the curious: the console opens under
        # it, or folds back, on a press that says which it is about to do.
        self.details_button = QPushButton("Show details", self.job_card)
        self.details_button.setObjectName("jobDetails")
        self.details_button.clicked.connect(self._toggle_job_details)

        # A bar and the buttons never share the card: what the bar counts up to
        # is the moment the two things worth doing next take its place.
        self.job_actions = QWidget(self.job_card)
        row = QHBoxLayout(self.job_actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.launch_button = QPushButton("Launch app", self.job_actions)
        self.launch_button.setObjectName("primaryAction")
        self.launch_button.clicked.connect(self._launch_app)
        self.job_close_button = QPushButton("Close installer", self.job_actions)
        self.job_close_button.clicked.connect(self._on_job_close)
        row.addWidget(self.launch_button, 1)
        row.addWidget(self.job_close_button)
        self.job_actions.hide()

        inner.addWidget(self.card_done)
        inner.addWidget(self.card_icon)
        inner.addWidget(self.card_title)
        inner.addWidget(self.card_step)
        inner.addWidget(self.card_progress)
        inner.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self.job_actions)

        self.job_card.raise_()
        lay.addWidget(holder)
        return page

    def _toggle_job_details(self) -> None:
        """Open the job's console behind the card, or fold it back away.

        The steps keep being printed to the screen the whole time it is
        hidden, so opening it late loses nothing of the job.
        """
        self._details_shown = not self._details_shown
        self.terminal.setVisible(self._details_shown)
        self.details_button.setText("Hide details" if self._details_shown
                                    else "Show details")

    def _place_job_icon(self) -> None:
        path = find_icon()
        if path is None:
            self.card_icon.hide()
            return
        self.card_icon.setPixmap(QPixmap(str(path)).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    # -- the splash's face --

    def _place_logo(self) -> None:
        path = find_icon()
        if path is None:
            self.logo.hide()
            return
        self.logo.setPixmap(QPixmap(str(path)).scaled(
            104, 104, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _card_sheet(self) -> str:
        colour = PALETTE.get
        return f"""
        QWidget {{ background-color: transparent;
                   color: {colour('text')};
                   font-family: "Segoe UI", sans-serif; font-size: 10pt; }}
        QWidget#splashCard {{
            background-color: {colour('surface')};
            border: 2px solid {colour('accent')};
            border-radius: 8px; }}
        QLabel {{ background-color: transparent; color: {colour('text')}; }}
        QLabel#splashTitle {{ color: {colour('bright')};
                              font-weight: 600; font-size: 15px; }}
        QLabel#splashStep {{ color: {colour('muted')}; font-size: 11px; }}
        QRadioButton {{ background: transparent; color: {colour('text')}; }}
        QRadioButton::indicator {{ width: 14px; height: 14px; }}
        QLineEdit {{ background-color: {colour('base')};
                     border: 1px solid {colour('border')}; border-radius: 4px;
                     padding: 5px; color: {colour('text')}; }}
        QPushButton {{ background-color: {colour('raise')};
                       border: 1px solid {colour('raise_edge')};
                       border-radius: 4px; padding: 6px 12px;
                       color: {colour('text')}; }}
        QPushButton:hover {{ background-color: {colour('hover')};
                             border-color: {colour('accent')}; }}
        QPushButton#advToggle {{ background-color: transparent;
                                 border: none; padding: 2px;
                                 color: {colour('muted')}; }}
        QPushButton#advToggle:checked {{ color: {colour('accent')}; }}
        QPushButton#jobDetails {{ background-color: {colour('raise')};
                                  border: 1px solid {colour('border')};
                                  border-radius: 6px; padding: 3px 14px;
                                  color: {colour('muted')}; }}
        QPushButton#jobDetails:hover {{ border-color: {colour('accent')};
                                         color: {colour('text')}; }}
        QPushButton#primaryAction {{ background-color: {colour('accent')};
                                     color: {colour('bright')};
                                     border: 1px solid {colour('accent_deep')};
                                     font-weight: 600; }}
        QPushButton:disabled {{ color: {colour('disabled_text')};
                                background-color: {colour('disabled_fill')};
                                border-color: {colour('disabled_edge')}; }}
        QProgressBar {{ background-color: {colour('soft')};
                        border: 1px solid {colour('border')};
                        border-radius: 3px; height: 18px; text-align: center;
                        color: {colour('text')}; font-size: 10px; }}
        QProgressBar::chunk {{ background-color: {colour('accent')};
                               border-radius: 3px; margin: 1px; }}
        QPlainTextEdit#jobTerm {{ background-color: {colour('base')};
                        border: 1px solid {colour('border')};
                        border-radius: 6px; color: {colour('text')};
                        font-family: Consolas, monospace; font-size: 10pt; }}
        QFrame#jobCard {{ background-color: rgba(24, 24, 24, 236);
                        border: 1px solid {colour('accent')};
                        border-radius: 10px; }}
        QLabel#jobName {{ color: {colour('bright')}; font-size: 13px;
                        font-weight: 700; }}
        QLabel#jobStep {{ color: {colour('muted')}; font-size: 10px;
                        font-family: Consolas, monospace; }}
        QLabel#jobDone {{ color: {colour('ok')}; font-size: 15px;
                        font-weight: 700; letter-spacing: 3px; }}
        """

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center()
                      - self.rect().center())

    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.LeftButton:
            # Keep the grab as a QPoint offset (global minus this card's top-left):
            # ``globalPosition()`` is a QPointF, and a float reaching ``move`` is
            # a TypeError, not a nudge - which is how a frameless window dies the
            # moment someone drags it.
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:       # noqa: N802 - Qt's name
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._drag = None

    # -- the installer's behaviour --

    def _door_changed(self) -> None:
        """The compile door needs a toolchain; say so before it is opened."""
        self.start_button.setText("Install" if self.install_choice.isChecked()
                                  else "Compile")
        if self.install_choice.isChecked():
            return
        py = which_python()
        if py is None:
            self._say("no Python was found on this machine", bad=True)
            return
        if self._toolchain is not None and self._toolchain[0] == py:
            self._tell(self._toolchain[1], self._toolchain[2])
            return
        if self._probe is not None and self._probe.isRunning():
            if self._probe.py == py:
                return                      # already asking about this one
            self._stop_probe()
        self._say(f"checking what {py} can import...")
        self._probe = ToolchainProbe(py, TOOLCHAIN_MODULES)
        self._probe.answered.connect(self._on_toolchain)
        self._probe.start()

    def _on_toolchain(self, py: str, ok: bool, missing: list) -> None:
        """A probe's answer: kept for the next time the door is opened."""
        self._toolchain = (py, bool(ok), list(missing))
        if self.install_choice.isChecked() or self._worker is not None:
            return            # the door moved on, or a job owns the card now
        self._tell(ok, missing)

    def _tell(self, ok: bool, missing: list) -> None:
        """What the compile door says about the interpreter it found."""
        py = str((self._toolchain or ("", False, []))[0])
        if ok:
            self._say(f"found {py}; it can build here")
            return
        names = [item for item in missing if item in TOOLCHAIN_MODULES]
        if not names:
            # Not a module short: that interpreter would not answer at all, and
            # telling somebody to pip-install into it would only waste their time.
            self._say(f"{py} is not usable here - {' '.join(missing)}", bad=True)
            return
        if names == ["PyInstaller"]:
            hint = f"run '{py} -m pip install pyinstaller'"
        else:
            hint = ("install the app's requirements first "
                    f"('{py} -m pip install -r requirements.txt' from a "
                    "source checkout)")
        self._say(f"{py} is missing {', '.join(names)} - {hint}", bad=True)

    def _stop_probe(self) -> None:
        """End a probe: a QThread deleted while it still runs is a crash."""
        probe = self._probe
        self._probe = None
        if probe is None:
            return
        try:
            probe.answered.disconnect()
        except TypeError:
            pass                                # nothing was ever connected
        if not probe.wait(PROBE_WAIT_MS):
            probe.terminate()
            probe.wait(PROBE_WAIT_MS)
        probe.deleteLater()

    def _start_gprobe(self) -> None:
        """Ask, off the card's thread, what the newest release this machine can
        find is; the answer lands on the form through ``_on_newest``."""
        old = self._gprobe
        self._gprobe = None
        if old is not None:
            try:
                old.answered.disconnect()
            except TypeError:
                pass                            # nothing was ever connected
            if old.isRunning():
                # Still on the network: this thread is not waited on with the
                # interface frozen, and it is not thrown away mid-run either -
                # it keeps itself alive until the answer lands unheard.
                _ABOARD.append(old)

                def _landed(probe: QThread = old) -> None:
                    try:
                        _ABOARD.remove(probe)
                    except ValueError:
                        pass
                    probe.deleteLater()

                old.finished.connect(_landed)
            else:
                old.deleteLater()
        owner = self.owner_edit.text().strip() or OWNER
        repo = self.repo_edit.text().strip() or REPO
        self.newest.setText("checking for the newest release...")
        self._gprobe = GitHubProbe(owner, repo)
        self._gprobe.answered.connect(self._on_newest)
        self._gprobe.start()

    def _on_newest(self, text: str) -> None:
        """A release probe's answer: the newest the setup can find, by name."""
        if self._worker is not None:
            return            # a job owns the card now; the label waits for none
        if not text:
            # Not even the fallback landed (resolve_tag cannot raise, so this is
            # a probe that died trying): the version this setup itself carries
            # is the newest one the machine has any word on.
            text = _readable(APP_VERSION)
        self.newest.setText(f"Newest release I can find: V{text}")

    def _stop_gprobe(self) -> None:
        """End a release probe: a QThread deleted while it still runs is a crash."""
        probe = self._gprobe
        self._gprobe = None
        if probe is None:
            return
        try:
            probe.answered.disconnect()
        except TypeError:
            pass                                # nothing was ever connected
        # The wait covers the longest ask the probe can be inside, so it lands
        # by itself; terminate here would be the quiet crash on the way out.
        if not probe.wait(NEWEST_WAIT_MS):
            probe.terminate()
            probe.wait(NEWEST_WAIT_MS)
        probe.deleteLater()

    def _browse(self) -> None:
        start = self.dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose the install "
                                                  "folder", start)
        if chosen:
            self.dir_edit.setText(chosen)

    def _say(self, text: str, bad: bool = False) -> None:
        self.step.setText(text)
        self.step.setStyleSheet(
            f"color: {PALETTE['warn' if bad else 'muted']};")

    def _reset_job(self, name: str) -> None:
        """Put the job's card up clean: named, folded, the bar busy."""
        self._ok = False
        self._cancel_asked = False
        self._program = None
        self.terminal.clear()
        # Every job starts with the console folded away, whatever the last
        # one left open behind the card.
        self._details_shown = False
        self.terminal.setVisible(False)
        self.details_button.setText("Show details")
        self.card_title.setText(name)
        self.card_step.setText("")
        self.card_step.setStyleSheet("")
        self.card_progress.show()
        self.card_progress.setRange(0, 0)          # busy until a share is known
        self.card_progress.setFormat("")
        self.card_done.hide()
        self.job_actions.hide()
        self._write_line(f"-> {name}")

    def _write_line(self, text: str) -> None:
        self.terminal.appendPlainText(str(text))

    def _job_step(self, text: str) -> None:
        """One step: printed whole on the console, trimmed onto the card."""
        line = str(text)
        self._write_line(line)
        if len(line) > _JOB_STEP_LEN:
            line = line[:_JOB_STEP_LEN - 1].rstrip() + "\u2026"
        self.card_step.setText(line)

    def _job_bar(self, frac: float) -> None:
        if frac is None or frac < 0:
            self.card_progress.setRange(0, 0)      # busy: the bar slides
            self.card_progress.setFormat("")
        else:
            self.card_progress.setRange(0, 100)
            self.card_progress.setFormat("%p%")
            self.card_progress.setValue(max(0, min(100, round(frac * 100))))

    def _start(self) -> None:
        if self._worker is not None:
            return
        target = Path(self.dir_edit.text().strip() or str(default_install_dir()))
        owner = self.owner_edit.text().strip() or OWNER
        repo = self.repo_edit.text().strip() or REPO
        compiling = self.compile_choice.isChecked()
        py: str | None = None
        if compiling:
            py = which_python()
            if py is None:
                self._door_changed()
                return
            if (self._toolchain is not None and self._toolchain[0] == py
                    and not self._toolchain[1]):
                # Asked already, refused already: no reason to start a job whose
                # first step would only say the same thing. The interpreter's own
                # check belongs to the job anyway - see Worker._do_compile.
                self._tell(False, self._toolchain[2])
                return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._say(f"cannot use that folder: {error}", bad=True)
            return

        self._program = target / program_file_name()
        self.start_button.setEnabled(False)
        self._reset_job("Compiling Berry Manager" if compiling
                        else "Installing Berry Manager")
        self.stack.setCurrentIndex(1)
        self.adjustSize()
        self._worker = Worker("compile" if compiling else "install", target,
                              owner, repo, py)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, text: str, frac: float) -> None:
        self._job_step(text)
        self._job_bar(frac)

    def _on_finished(self, ok: bool, text: str) -> None:
        if self._leaving:
            # The person asked to leave and the job was stopped to let them; an
            # outcome drawn over the top of that would only reopen the card.
            return
        # The bar's job is over, so the card wears the outcome and the two
        # things worth doing next in its place: launch, or get out of the way.
        self._write_line(("OK  " if ok else "ERR ") + text)
        self.card_progress.hide()
        self.card_done.setText("DONE" if ok else "NOT DONE")
        self.card_done.setStyleSheet(
            f"color: {PALETTE['ok' if ok else 'warn']};")
        self.card_done.show()
        self.card_step.setText(text)
        self.card_step.setStyleSheet(
            f"color: {PALETTE['ok' if ok else 'warn']};")
        self._ok = ok
        self.launch_button.setVisible(ok)
        self.launch_button.setEnabled(ok)
        self.launch_button.setText("Launch app")
        self.job_close_button.setText("Close installer" if ok else "Back")
        self.job_actions.show()
        self.start_button.setEnabled(True)
        self.start_button.setText("Install again" if ok else
                                  self.start_button.text())
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _launch_app(self) -> None:
        if self._program is None:
            return
        try:
            launch_program(self._program)
        except OSError as error:
            self.card_step.setText(f"could not start it: {error}")
            self.card_step.setStyleSheet(f"color: {PALETTE['warn']};")
            return
        self.launch_button.setEnabled(False)
        self.launch_button.setText("Launched")
        self.card_step.setText(f"started {self._program.name}")
        self.card_step.setStyleSheet(f"color: {PALETTE['ok']};")

    def _on_job_close(self) -> None:
        """A landed install closes the setup; a failed one hands the form back."""
        if self._ok:
            self.close()
            return
        self.stack.setCurrentIndex(0)
        self.adjustSize()
        self._door_changed()

    def _leave(self) -> None:
        """Close the card, stopping the job that is in the way of closing it.

        The first press only asks the job to stop, which is the polite half of
        it. The words on screen promise that a second press gets the person out -
        and that promise used to be a lie, because the second press did the same
        cancelling over again and left the card standing there with nowhere to
        go. So the second press waits a bounded moment for the cancel to land and
        then stops the thread only if that wait ran out: leaving a QThread running
        while its window goes is how the setup dies on the way out instead of
        closing.
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            self._worker = None
            self.close()
            return
        if not self._cancel_asked:
            worker.cancel()
            self._cancel_asked = True
            words = ("the build is still running; press Close again to "
                     "leave anyway")
            if self.stack.currentIndex() == 1:
                self._job_step(words)
            else:
                self._say(words, bad=True)
            return
        self._leaving = True
        self._job_step("leaving now; the job is being stopped")
        worker.cancel()
        if not worker.wait(FORCE_WAIT_MS):
            worker.terminate()
            worker.wait(FORCE_WAIT_MS)
        self._worker = None
        self.close()

    def _close_or_cancel(self) -> None:
        self._leave()

    def _reap_aboard(self) -> None:
        """Wait out every probe this card overtook, so none dies mid-run.

        Retargeting the git fields parks a still-asking probe in ``_ABOARD`` to
        keep it referenced until it lands unheard. On the way out any that has
        not landed would otherwise be destroyed while running, which is a Qt
        abort the crash dialog never sees - so each is given the same patience
        the live one gets, then let go.
        """
        for probe in list(_ABOARD):
            try:
                if not probe.wait(NEWEST_WAIT_MS):
                    probe.terminate()
                    probe.wait(NEWEST_WAIT_MS)
            except RuntimeError:
                pass                        # it landed and was already deleted
            try:
                _ABOARD.remove(probe)
            except ValueError:
                pass

    def closeEvent(self, event) -> None:           # noqa: N802 - Qt's name
        self._stop_probe()
        self._stop_gprobe()
        self._reap_aboard()
        if self._worker is not None and self._worker.isRunning() \
                and not self._leaving:
            event.ignore()
            self._leave()
            return
        event.accept()


def _crash_report(kind, value, trace) -> None:
    """Say what went wrong instead of dying silent.

    A setup window has no console, and PyQt hands an error loose in a slot -
    or a thread's ``run()`` that nothing caught - straight to this hook on
    its way out of the process: without a hook the machine simply loses the
    window and no line is ever read. With one, the traceback goes to stderr
    where a terminal can show it and a dialog says so where there is no
    terminal at all. The dialog is the last courtesy; an error even in
    drawing it must not become an error about the error.
    """
    text = "".join(traceback.format_exception(kind, value, trace))
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except (OSError, AttributeError, ValueError):
        pass                        # a frozen windowed build may have no stderr
    if QApplication.instance() is None:
        return            # no app to draw a dialog on, and trying is its own abort
    try:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Berry Manager Setup")
        box.setText("The setup ran into an error it did not expect.")
        box.setInformativeText(str(value) or (kind or type(None)).__name__)
        box.setDetailedText(text)
        box.exec()
    except Exception:                       # noqa: BLE001 - the report must not
        pass                            # raise: the traceback above is the point


def main() -> int:
    # Every silent death this file has ended in - an error in a slot, a thread
    # nothing caught - is reported through here instead of just closing the
    # window, so what to do about it can actually be read.
    sys.excepthook = _crash_report
    app = QApplication(sys.argv)
    app.setApplicationName("Berry Manager Setup")
    app.setOrganizationName("fligma")
    icon = find_icon()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    try:
        card = InstallerCard()
        card.show()
        return app.exec()
    except Exception:                       # noqa: BLE001 - die saying so
        _crash_report(*sys.exc_info())
        return 1
    finally:
        sys.excepthook = sys.__excepthook__


if __name__ == "__main__":
    raise SystemExit(main())
