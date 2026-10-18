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

    QCoreApplication.instance() or QCoreApplication([])

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
