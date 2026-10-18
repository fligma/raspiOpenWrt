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
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

# --- what this setup is, and where the release it belongs to lives ---

#: The one place the version is written is ``core/const.py``; the release packer
#: reads that same line, so this is kept equal to it and names the tag this setup
#: was published under. A build made from source reads the live value instead.
#: It is the program's byte-packed hex form (the release decimal would call
#: ``3.1.0``), so ``release_tag`` below lands on the same ``berry0x3.0x1.0x0``
#: tag the workflow publishes.
APP_VERSION = "0x3.0x1.0x0"
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
    """The GitHub tag this setup was published under: ``berry`` + the version."""
    return f"{TAG_PREFIX}{APP_VERSION}"


def asset_name() -> str:
    """The archive name the release carries for this system."""
    return f"berry_manager-{system_name()}.zip"


# --- the one thing that touches the network ---

def fetch_github(tail: str, dest: Path, report, owner: str, repo: str) -> None:
    """GET one address from GitHub, following the redirect, into ``dest``.

    Both fetches - the release archive and the source snapshot - go through
    here, which is the single place anything leaves the machine. GitHub answers
    these links by pointing somewhere else, so the redirect is followed by hand,
    one hop at a time, and a private token (when the environment carries one) is
    sent to the ``github.com`` hop only and never on to the file server that
    finally hands the bytes over. ``report`` is called with
    ``(bytes_done, bytes_total)`` as they arrive.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    host, path = "github.com", f"/{owner.strip('/')}/{repo.strip('/')}{tail}"
    hops = 0
    while True:
        conn = http.client.HTTPSConnection(host, timeout=30)
        headers = {"User-Agent": f"berry-setup/{APP_VERSION}",
                   "Accept": "application/octet-stream"}
        # The one-time signature GitHub redirects to carries its own access in
        # the URL, so a token is sent to the github.com hop only.
        if token and host.endswith("github.com"):
            headers["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = int(response.status)
        if status in (301, 302, 303, 307, 308):
            location = response.getheader("Location") or ""
            response.read()
            conn.close()
            hops += 1
            if not location or hops > 8:
                raise OSError("the download kept being pointed elsewhere")
            parts = urlsplit(location)
            if parts.scheme != "https" or not parts.netloc:
                raise OSError(f"{location} is not an https address")
            host, path = parts.netloc, (parts.path or "/")
            if parts.query:
                path = f"{path}?{parts.query}"
            continue
        if status != 200:
            body = response.read(400)
            conn.close()
            raise OSError(f"GitHub refused with {status}: "
                          f"{body.decode('utf-8', 'replace')[:200]}")
        length = response.getheader("Content-Length") or ""
        total = int(length) if length.isdigit() else 0
        done = 0
        dest.write_bytes(b"")
        with dest.open("ab") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                report(done, total)
        conn.close()
        if done == 0:
            raise OSError("the download answered with nothing at all")
        return


def download_release(dest: Path, report, owner: str, repo: str) -> None:
    """Fetch this release's archive for the running system into ``dest``."""
    tail = (f"/releases/download/{release_tag()}/{asset_name()}")
    fetch_github(tail, dest, report, owner, repo)


def download_source(dest: Path, report, owner: str, repo: str) -> None:
    """Fetch the source snapshot this release was tagged from into ``dest``.

    A compile reads the program's own ``src/berry_manager`` off GitHub rather
    than carrying a private copy inside the setup, so the source it builds is
    exactly the one the release names - and the setup stays a small file.
    """
    tail = f"/archive/refs/tags/{release_tag()}.tar.gz"
    fetch_github(tail, dest, report, owner, repo)


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


def place_build(build_root: Path, target: Path) -> None:
    """Move an unpacked build to where the user asked for it, and make it run.

    The program and ``_internal`` come over together; the drop-in ``addons``
    folder is added if the archive did not carry one, so the first start has the
    folder the program looks for already beside it. Anything of the same name
    already at the target is taken first - this is a fresh install, not a merge.
    """
    target.mkdir(parents=True, exist_ok=True)
    program = program_file_name()
    for name in (program, INTERNAL_DIR):
        source = build_root / name
        landing = target / name
        if landing.exists():
            if landing.is_dir():
                shutil.rmtree(landing, ignore_errors=True)
            else:
                landing.unlink(missing_ok=True)
        shutil.copytree(source, landing) if source.is_dir() \
            else shutil.copy2(source, landing)
    if os.name != "nt":
        try:
            (target / program).chmod(0o755)
        except OSError:
            pass
    addons = target / "addons"
    addons.mkdir(exist_ok=True)
    readme = addons / "README.txt"
    if not readme.exists():
        readme.write_text(ADDONS_README, encoding="utf-8")


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
                                text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return False, [f"could not run {py}: {error}"]
    if answer.returncode != 0:
        return False, [f"{py} would not start: "
                       f"{answer.stderr.strip()[:200] or 'unknown error'}"]
    missing = [line.strip() for line in answer.stdout.splitlines()
               if line.strip()]
    return not missing, missing


def run_compile(source: Path, target: Path, py: str, report,
                cancel_flag) -> None:
    """Build the program from ``source`` with PyInstaller into ``target``.

    Everything happens in a throwaway copy, so the source tree is never touched
    and the leftovers a compile makes (``build/``, ``dist/``, the ``.spec``, the
    staging folders) go with that copy when the finished build has been moved.
    The flags mirror the workflow exactly, so a local build and a published one
    are the same program.
    """
    work = Path(tempfile.mkdtemp(prefix="berry_build_"))
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
                                   encoding="utf-8", errors="replace")
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

    def cancel(self) -> None:
        self._cancel["cancel"] = True

    def run(self) -> None:                        # noqa: D401 - QThread contract
        try:
            if self.mode == "install":
                self._do_install()
            else:
                self._do_compile()
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            self.finished.emit(False, str(error) or error.__class__.__name__)

    def _do_install(self) -> None:
        archive = Path(tempfile.gettempdir()) / asset_name()
        staging = Path(tempfile.mkdtemp(prefix="berry_install_"))
        try:
            self.progress.emit(f"fetching {asset_name()} from the release", -1)

            def on_bytes(done: int, total: int) -> None:
                frac = (done / total) if total else -1
                size = f"{done / 1_048_576:.1f} MB"
                if total:
                    size += f" of {total / 1_048_576:.1f} MB"
                self.progress.emit(f"downloading {size}", frac)

            download_release(archive, on_bytes, self.owner, self.repo)
            build_root = extract_release(archive, staging,
                                         lambda t: self.progress.emit(t, -1))
            self.progress.emit("putting the build in place", -1)
            place_build(build_root, self.target)
            self.finished.emit(True, f"Berry Manager {APP_VERSION} is "
                                     f"installed at {self.target}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                archive.unlink()
            except OSError:
                pass

    def _do_compile(self) -> None:
        assert self.python is not None
        work = Path(tempfile.mkdtemp(prefix="berry_source_"))
        try:
            tarball = work / "source.tar.gz"
            self.progress.emit(f"fetching the source from "
                               f"{self.owner}/{self.repo}", -1)

            def on_bytes(done: int, total: int) -> None:
                frac = (done / total) if total else -1
                size = f"{done / 1024} KB"
                if total:
                    size += f" of {total / 1024} KB"
                self.progress.emit(f"downloading source {size}", frac)

            download_source(tarball, on_bytes, self.owner, self.repo)
            source = extract_source(tarball, work,
                                    lambda t: self.progress.emit(t, -1))
            tarball.unlink(missing_ok=True)
            run_compile(source, self.target, self.python,
                        lambda text: self.progress.emit(text, -1),
                        self._cancel)
            self.finished.emit(True, f"a local build of Berry Manager "
                                     f"{APP_VERSION} is installed at "
                                     f"{self.target}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --- the card ---

class InstallerCard(QWidget):
    """The window: the splash's look, and the two doors under it.

    It opens as the boot card does - logo, name, and a bar under a line of what
    is happening - and adds only what an installer needs: a choice of door, the
    folder it will write to, and the buttons to start and to close.
    """

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._worker: Worker | None = None
        self._drag: tuple[int, int] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("splashCard")
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(30, 24, 30, 20)
        body.setSpacing(9)

        self.logo = QLabel(card)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._place_logo()

        title = QLabel(f"Berry Manager V{APP_VERSION} - setup", card)
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Install the published build, or compile one here.",
                          card)
        subtitle.setObjectName("splashStep")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        doors = QHBoxLayout()
        self.install_choice = QRadioButton("Install release", card)
        self.compile_choice = QRadioButton("Compile on this PC", card)
        self.install_choice.setChecked(True)
        doors.addWidget(self.install_choice)
        doors.addStretch(1)
        doors.addWidget(self.compile_choice)

        self.dir_edit = QLineEdit(str(default_install_dir()), card)
        browse = QPushButton("Browse...", card)
        browse.clicked.connect(self._browse)
        where = QHBoxLayout()
        where.addWidget(self.dir_edit, 1)
        where.addWidget(browse)

        self.owner_edit = QLineEdit(OWNER, card)
        self.owner_edit.setPlaceholderText("owner")
        self.repo_edit = QLineEdit(REPO, card)
        self.repo_edit.setPlaceholderText("repo")
        gitbox = QHBoxLayout()
        gitbox.addWidget(self.owner_edit, 1)
        gitbox.addWidget(self.repo_edit, 2)

        self.step = QLabel("", card)
        self.step.setObjectName("splashStep")
        self.step.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.bar = QProgressBar(card)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Install", card)
        self.start_button.setObjectName("primaryAction")
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Close", card)
        self.cancel_button.clicked.connect(self._close_or_cancel)
        buttons.addWidget(self.start_button, 1)
        buttons.addWidget(self.cancel_button)

        body.addWidget(self.logo)
        body.addWidget(title)
        body.addWidget(subtitle)
        body.addLayout(doors)
        body.addWidget(QLabel("Install folder", card))
        body.addLayout(where)
        body.addWidget(QLabel("Git source (owner / repo)", card))
        body.addLayout(gitbox)
        body.addWidget(self.step)
        body.addWidget(self.bar)
        body.addLayout(buttons)

        self.install_choice.toggled.connect(self._door_changed)
        self.setFixedWidth(440)
        self.setStyleSheet(self._card_sheet())
        self.adjustSize()
        self._center_on_screen()
        self._door_changed()

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
        """

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center()
                      - self.rect().center())

    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().x() - self.x(),
                          event.globalPosition().y() - self.y())

    def mouseMoveEvent(self, event) -> None:       # noqa: N802 - Qt's name
        if self._drag is not None:
            self.move(event.globalPosition().x() - self._drag[0],
                      event.globalPosition().y() - self._drag[1])

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._drag = None

    # -- the installer's behaviour --

    def _door_changed(self) -> None:
        """The compile door needs a toolchain; say so before it is opened."""
        self.start_button.setText("Install" if self.install_choice.isChecked()
                                  else "Compile")
        if not self.install_choice.isChecked():
            py = which_python()
            if py is None:
                self._say("no Python was found on this machine", bad=True)
                return
            ok, missing = python_has(py, ("PyInstaller", "PyQt6", "paramiko",
                                          "keyring"))
            if not ok:
                hint = (f"run '{py} -m pip install -y pyinstaller" if
                        "PyInstaller" in missing else
                        f"install the app's requirements first "
                        f"('{py} -m pip install -r requirements.txt' from a "
                        "source checkout)")
                self._say(f"{py} is missing {', '.join(missing)} - {hint}",
                          bad=True)
            else:
                self._say(f"found {py}; it can build here")

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

    def _set_bar(self, frac: float) -> None:
        if frac is None or frac < 0:
            self.bar.setRange(0, 0)               # busy: the bar slides
            self.bar.setFormat("")
        else:
            self.bar.setRange(0, 100)
            self.bar.setFormat("%p%")
            self.bar.setValue(max(0, min(100, round(frac * 100))))

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
            ok, missing = python_has(py, ("PyInstaller", "PyQt6", "paramiko",
                                          "keyring"))
            if not ok:
                self._say(f"{py} is missing {', '.join(missing)}", bad=True)
                return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._say(f"cannot use that folder: {error}", bad=True)
            return

        self.start_button.setEnabled(False)
        self._set_bar(-1)
        self._say("working...")
        self._worker = Worker("compile" if compiling else "install", target,
                              owner, repo, py)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, text: str, frac: float) -> None:
        self._say(text)
        self._set_bar(frac)

    def _on_finished(self, ok: bool, text: str) -> None:
        self._set_bar(1.0 if ok else -1)
        if ok:
            self.bar.setRange(0, 100)
            self.bar.setFormat("done")
        self._say(text, bad=not ok)
        self.start_button.setEnabled(True)
        self.start_button.setText("Install again" if ok else
                                  self.start_button.text())
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _close_or_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._say("cancelling...")
            return
        self.close()

    def closeEvent(self, event) -> None:           # noqa: N802 - Qt's name
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            self._worker.cancel()
            self._say("the build is still running; press Close again to "
                      "leave anyway")
            return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Berry Manager Setup")
    app.setOrganizationName("fligma")
    icon = find_icon()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))
    card = InstallerCard()
    card.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
