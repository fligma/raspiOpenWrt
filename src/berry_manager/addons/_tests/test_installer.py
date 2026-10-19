#!/usr/bin/env python3
"""A test for berry_manager/installer.py: the setup's two quiet promises.

The setup card is mostly drawing, and the drawing is not what breaks. What
breaks is the two pieces of machinery behind it, and each has taken a machine
down in its own way:

* ``place_build`` is where a downloaded or compiled build becomes the folder the
  user runs. It has one promise: the target ends up holding whole halves of one
  build, never one half of each. A delete-then-copy is the way that promise was
  first kept, and on Windows a folder whose files the running app is holding
  cannot be deleted - with the errors swallowed, the target was left holding the
  new program beside the old ``_internal``: an install that reports success and
  then does not start. So it moves aside under a ``.old`` name, refuses loudly
  when a move is refused, and puts every name back if it cannot finish;
* every wait the setup gives a background thread is a ``QThread.wait`` call, and
  ``QThread.wait`` counts **milliseconds**. A wait written as ``wait(3)`` looks
  like three seconds, is three milliseconds, and gives up on a thread that would
  have landed a second later - then falls through to ``terminate()``, which ends
  a Python thread wherever it happens to be, GIL and all. Both waits here are
  bounded, so the unit is the whole difference between a setup that closes and a
  setup that crashes on the way out.

Nothing here needs the network, GitHub, a compiler or a window: the build is a
few text files in a temporary folder, and the thread is one that sleeps. Run it
from the command line::

    python addons/_tests/test_installer.py
"""

from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and installer live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "installer.py"
FAILURES: list[str] = []

#: Threads this suite leaves running on purpose, kept alive so nothing destroys
#: a QThread that has not finished - and dropped without a bye-bye by the
#: ``os._exit`` at the bottom of the file.
STRAYS: list[object] = []

#: The application object the Qt sections run under, held for the whole life of
#: the process: an unheld ``QApplication([])`` is a temporary that garbage
#: collection takes away again, and every signal emitted after that goes
#: nowhere - a probe that lands but never answers.
APP: list[object] = []

PROGRAM = "berry_manager.exe" if os.name == "nt" else "berry_manager"
INTERNAL = "_internal"


def check(condition: object, text: str) -> None:
    """One thing the setup has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


def tree_of(folder: Path) -> dict:
    """Every file under ``folder``, by posix name, with its whole text."""
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
            for p in folder.rglob("*") if p.is_file()}


def staged_build(base: Path, tag: str, *, program: bool = True,
                 internal: bool = True) -> Path:
    """A build folder as the archive hands it over, each file saying which build."""
    root = base / f"build_{tag}"
    root.mkdir(parents=True, exist_ok=True)
    if program:
        (root / PROGRAM).write_text(f"{tag} program", encoding="utf-8")
    if internal:
        (root / INTERNAL).mkdir(parents=True, exist_ok=True)
        (root / INTERNAL / "one.dll").write_text(f"{tag} library",
                                                 encoding="utf-8")
    return root


def live_build(base: Path, tag: str) -> Path:
    """What is already installed, with one file no new build carries."""
    root = base / f"live_{tag}"
    root.mkdir(parents=True, exist_ok=True)
    (root / PROGRAM).write_text(f"{tag} program", encoding="utf-8")
    (root / INTERNAL).mkdir(parents=True, exist_ok=True)
    (root / INTERNAL / "one.dll").write_text(f"{tag} library", encoding="utf-8")
    (root / INTERNAL / "stale.dll").write_text(f"{tag} only", encoding="utf-8")
    return root


# --- one: what place_build leaves in the install folder ---

def test_the_landing(inst: object) -> None:
    print("\n1. a build lands whole, or not at all")
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        build = staged_build(base, "new")
        target = base / "where"
        inst.place_build(build, target)
        tree = tree_of(target)
        check(tree.get(PROGRAM) == "new program"
              and tree.get(f"{INTERNAL}/one.dll") == "new library",
              f"the build did not land as it was staged: {sorted(tree)}")
        check((target / "addons" / "README.txt").is_file(),
              "the first start has no addons folder to find")
        check(not any(".old" in part for part in tree),
              f"a fresh install littered the target with aside copies: "
              f"{sorted(tree)}")

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        # An install over the top of a running one: the old folder carries a file
        # the new build does not, and that file must not survive into the result.
        target = live_build(base, "old")
        addons = target / "addons"
        addons.mkdir()
        kept = addons / "keepme.py"
        kept.write_text("somebody's addon", encoding="utf-8")
        note = addons / "README.txt"
        note.write_text("my own words", encoding="utf-8")
        inst.place_build(staged_build(base, "new"), target)
        tree = tree_of(target)
        check(tree.get(f"{INTERNAL}/one.dll") == "new library"
              and f"{INTERNAL}/stale.dll" not in tree,
              f"the old libraries were merged instead of replaced: {sorted(tree)}")
        check(kept.is_file() and kept.read_text(encoding="utf-8")
              == "somebody's addon",
              "a replace took away the addons somebody had put in the folder")
        check(note.read_text(encoding="utf-8") == "my own words",
              "a replace wrote over the note that was already there")
        check(not any(".old" in p.relative_to(target).as_posix()
                      for p in target.rglob("*")),
              "the moved-aside copies were left beside the new build")


# --- two: the two ways a swap can be refused ---

def test_a_refused_move(inst: object) -> None:
    print("\n2. what a refused move says, and what it leaves")
    if os.name != "nt":
        # A held file moves happily on unix; the refusal this guards is a
        # Windows one, so the case is told by the Windows runner.
        look("nothing here refuses a rename of a file that is open, so the "
             "held-program case waits for the Windows runner")
    else:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            target = live_build(base, "old")
            held = (target / PROGRAM).open("rb")        # what refuses the rename
            try:
                inst.place_build(staged_build(base, "new"), target)
                check(False, "a program that could not be moved aside let the "
                             "install go ahead anyway")
            except OSError as error:
                check("close Berry Manager and install again" in str(error),
                      f"a refused aside did not say what to do about it: {error}")
            finally:
                held.close()
            tree = tree_of(target)
            check(tree.get(PROGRAM) == "old program"
                  and tree.get(f"{INTERNAL}/one.dll") == "old library"
                  and f"{INTERNAL}/stale.dll" in tree,
                  f"a refused aside did not leave the live build alone: "
                  f"{sorted(tree)}")
            check(not any(".old" in part for part in tree),
                  f"a refused aside left a half copy on disk: {sorted(tree)}")

    with tempfile.TemporaryDirectory() as raw:
        # A staged build that cannot be completed: the program lands and the
        # libraries are not there. Half a build must not be left standing.
        base = Path(raw)
        target = live_build(base, "old")
        broken = staged_build(base, "new", internal=False)
        try:
            inst.place_build(broken, target)
            check(False, "a staged build with no libraries went ahead anyway")
        except OSError as error:
            look(f"what the incomplete build said: {error}")
        tree = tree_of(target)
        check(tree.get(PROGRAM) == "old program"
              and tree.get(f"{INTERNAL}/one.dll") == "old library",
              f"an unfinished swap did not give the old build back: {sorted(tree)}")
        check(not any(".old" in part for part in tree),
              f"the roll back left an aside copy standing: {sorted(tree)}")


# --- three: the unit every background wait is counted in ---

def test_the_waits_are_milliseconds(inst: object) -> None:
    print("\n3. QThread.wait counts milliseconds, and these waits know it")
    for name in ("FORCE_WAIT_MS", "PROBE_WAIT_MS"):
        value = getattr(inst, name, None)
        check(isinstance(value, int) and value >= 1000,
              f"{name} = {value!r} is not a millisecond patience of at least a second")

    offenders = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        for argument in re.findall(r"\.wait\(\s*([^)]*?)\s*\)", line):
            if argument == "" or "_MS" in argument:
                continue
            if re.fullmatch(r"[0-9_]+(\.[0-9]+)?", argument):
                offenders.append(f"{argument} in {line.strip()}")
    check(not offenders,
          "a wait was given a bare number, which QThread reads as milliseconds: "
          + "; ".join(offenders))

    # The contract itself, so the checks above mean something: a thread that
    # finishes in half a second is waited out by a patience counted in
    # milliseconds, and is given up on by the same number read as seconds.
    from PyQt6.QtCore import QThread, QCoreApplication
    from PyQt6.QtWidgets import QApplication

    # a QApplication, not merely a core one: section four draws the crash
    # dialog, and a QMessageBox is a widget - which is a Qt abort on its own.
    # Held in APP, because an application that nothing references is an
    # application that garbage collection ends, signals and all.
    QCoreApplication.instance() or APP.append(QApplication([]))

    class Quick(QThread):
        def run(self) -> None:
            time.sleep(0.5)

    quick = Quick()
    quick.start()
    started = time.monotonic()
    landed = quick.wait(int(inst.FORCE_WAIT_MS))
    took = time.monotonic() - started
    check(landed and 0.4 <= took < inst.FORCE_WAIT_MS / 1000,
          f"wait({inst.FORCE_WAIT_MS}) did not wait a thread that took 0.5 s "
          f"(took {took:.2f} s, answer {landed})")

    class Slow(QThread):
        def run(self) -> None:
            time.sleep(20)

    slow = Slow()
    slow.start()
    STRAYS.append(slow)
    while not slow.isRunning():
        time.sleep(0.02)
    started = time.monotonic()
    short = slow.wait(3)
    took = time.monotonic() - started
    check(not short and took < 0.2,
          f"wait(3) blocked for {took:.2f} s, so a bare small number is not "
          "read as milliseconds after all and the checks above say nothing")
    look("wait(3) came back false at once, which is the trap the checks above "
         "are drawn to catch")


# --- four: the newest release on the form, and the error on the screen ---

def test_the_newest_and_the_notice(inst: object) -> None:
    print("\n4. the setup names the newest release it can find, and names "
          "the error it dies of")
    text = SOURCE.read_text(encoding="utf-8")

    # the form promises the newest before anything is pressed: a probe off
    # the interface thread, a label for its answer, and both wired into the
    # card that opens the door
    check("class GitHubProbe" in text and "answered = pyqtSignal(str)" in text,
          "there is no off-thread probe for the newest release")
    check("self._start_gprobe()" in text and "Newest release I can find" in text,
          "the setup form never shows the newest release it found")
    check("self._stop_gprobe()" in text,
          "a release probe is left running when the card closes - which is a "
          "QThread destroyed mid-run, the crash this file's whole third section "
          "is drawn to catch")
    value = getattr(inst, "NEWEST_WAIT_MS", None)
    check(isinstance(value, int) and value >= 1000,
          f"NEWEST_WAIT_MS = {value!r} is not a millisecond patience of at "
          "least a second")

    # the probe itself: an answer when the catalog answers, the version the
    # setup carries when the network does not, and silence - never a raise -
    # when something no guard expected comes loose, because an error loose
    # in a QThread's run is a dead process
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    QCoreApplication.instance() or APP.append(QApplication([]))
    real_api = inst.github_api_json
    real_cache = dict(inst._tag_cache)
    try:
        inst._tag_cache.clear()

        def offline(owner: str, repo: str, tail: str, timeout: int = 20):
            raise OSError("no network here")

        inst.github_api_json = offline
        got: list = []
        probe = inst.GitHubProbe("fligma", "raspiOpenWrt")
        probe.answered.connect(got.append)
        probe.start()
        landed = probe.wait(int(inst.NEWEST_WAIT_MS))
        # the wait bounds the thread, not the posted answer: let the main
        # thread run what it was sent before reading what came back
        for _ in range(20):
            QCoreApplication.processEvents()
            if got:
                break
            time.sleep(0.05)
        check(landed and got == [inst._readable(inst.APP_VERSION)],
              f"a probe with no network did not fall back to the setup's own "
              f"version: landed={landed}, got={got}")

        inst._tag_cache.clear()

        def impossible(owner: str, repo: str, tail: str, timeout: int = 20):
            raise RuntimeError("an error no guard in the read expects")

        inst.github_api_json = impossible
        got2: list = []
        probe2 = inst.GitHubProbe("fligma", "raspiOpenWrt")
        probe2.answered.connect(got2.append)
        probe2.start()
        landed2 = probe2.wait(int(inst.NEWEST_WAIT_MS))
        for _ in range(20):
            QCoreApplication.processEvents()
            if got2:
                break
            time.sleep(0.05)
        check(landed2 and got2 == [""],
              f"a probe whose read raised did not answer quietly: landed="
              f"{landed2}, got={got2}")
    finally:
        inst.github_api_json = real_api
        inst._tag_cache.clear()
        inst._tag_cache.update(real_cache)

    # the silent death: an error loose in a slot arrives at the hook main()
    # installs, and the hook says what it is - on stderr for a terminal, and
    # in a dialog for the windowed build that has none
    check("sys.excepthook = _crash_report" in text,
          "the setup's main does not install a crash reporter, so an error "
          "in a slot closes the window with no line ever read")
    shown: dict = {}

    class _Box:
        class Icon:                                  # noqa: D106 - Qt's shape
            Critical = "critical"

        def setIcon(self, colour: object) -> None:
            shown["icon"] = colour

        def setWindowTitle(self, title: str) -> None:
            shown["title"] = title

        def setText(self, line: str) -> None:
            shown["text"] = line

        def setInformativeText(self, line: str) -> None:
            shown["info"] = line

        def setDetailedText(self, body: str) -> None:
            shown["detail"] = body

        def exec(self) -> int:
            shown["exec"] = True
            return 0

    real_box = inst.QMessageBox
    real_stderr, sys.stderr = sys.stderr, io.StringIO()
    inst.QMessageBox = _Box
    try:
        try:
            raise ValueError("the slot said so")
        except ValueError:
            inst._crash_report(*sys.exc_info())
        printed = sys.stderr.getvalue()
    finally:
        sys.stderr = real_stderr
        inst.QMessageBox = real_box
    check(shown.get("exec") and "error" in str(shown.get("text"))
          and "the slot said so" in str(shown.get("info"))
          and "ValueError" in str(shown.get("detail")),
          f"the crash report did not put the error on a dialog: {shown}")
    check("ValueError: the slot said so" in printed,
          "the crash report did not write the traceback where a terminal "
          "could read it")


# --- five: the job thread is let go only when it has truly ended ---

def test_the_worker_lifetime(inst: object) -> None:
    """The card must not destroy the job thread while it is still running.

    The Worker says its outcome from inside ``run()``, and the thread lives a
    moment longer while its ``finally`` clears the tree it just unpacked. Letting
    go of the QThread on that outcome - as the installer used to - frees a thread
    that is still running, and Qt aborts the process: seen on Linux as a SIGABRT
    whose fatal frame sits in libQt6Core right after an install that worked. The
    fix names the outcome signal away from ``finished`` (so ``QThread.finished``
    stays free) and hangs the release on that real signal, which fires only once
    ``run()`` has returned - the same shape ``GitHubProbe`` already follows.
    """
    print("\n5. the job thread is released only once it has actually stopped")
    from PyQt6.QtCore import QThread, QCoreApplication, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    QCoreApplication.instance() or APP.append(QApplication([]))

    # The Worker must carry its own ``outcome`` and leave ``finished`` inherited
    # from QThread; a re-added custom ``finished`` would shadow the base one and
    # leave the card with nothing to release on.
    worker_cls = inst.Worker
    check("outcome" in vars(worker_cls) and "finished" not in vars(worker_cls),
          "the Worker shadows QThread.finished with its own signal again, so "
          "the card has no thread-ended signal to release the job on")
    text = SOURCE.read_text(encoding="utf-8")
    check("self._worker.outcome.connect(self._on_finished)" in text,
          "the card no longer reads the job's outcome signal")
    check("self._worker.finished.connect(self._drop_worker)" in text
          and "def _drop_worker" in text,
          "the card does not let go of the job thread on QThread's real finished")
    check("finished = pyqtSignal" not in text,
          "a custom 'finished' signal is back on a QThread subclass, hiding the "
          "one the release is tied to")

    # A thread shaped like the job: it reports, then keeps running through the
    # cleanup window. Reading its state at the moment of the report is exactly
    # where the old release would have destroyed a live thread.
    class JobLike(QThread):
        outcome = pyqtSignal(bool, str)

        def run(self) -> None:
            self.outcome.emit(True, "done")      # said while still running
            time.sleep(0.4)                        # the cleanup window after it

    seen: dict = {}
    job = JobLike()
    job.outcome.connect(lambda ok, words: seen.setdefault("running",
                                                          job.isRunning()))
    job.start()
    for _ in range(40):                            # let the queued report land
        QCoreApplication.processEvents()
        if seen:
            break
        time.sleep(0.02)
    check(seen.get("running") is True,
          "the outcome landed only after the thread had stopped, so this no "
          "longer pins the release to a moment while the thread still runs")
    STRAYS.append(job)
    job.wait(2000)
    check(not job.isRunning(),
          "the job thread never ended, so the release point proves nothing")


def main() -> int:
    try:
        import installer as inst
    except Exception as error:                       # noqa: BLE001
        print(f"installer could not be imported: {error}")
        return 2
    print(f"installer: {SOURCE}")
    test_the_landing(inst)
    test_a_refused_move(inst)
    test_the_waits_are_milliseconds(inst)
    test_the_newest_and_the_notice(inst)
    test_the_worker_lifetime(inst)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    STATUS = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The verdict is printed: go out without walking Qt through the interpreter's
    # shutdown, and without waiting on the sleeper thread started above - see the
    # same note in test_page_fill.py.
    os._exit(STATUS)
