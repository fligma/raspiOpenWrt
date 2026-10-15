"""A test for core.addon.install_build - replacing an old shape, not dying on it.

A Linux user's store log ended with the batch refusing to install at all:

    the batch stopped after 0 of 3: it could not be put in the addons
    folder: .../addons/openwrt_logs is a file, not the folder this build needs

On a machine with no executable suffix the one-file program is the bare
``addons/<id>`` and the folder build wants the very same name, so the old flat
program sat on the spot the fresh folder needed and the installer raised
instead of clearing it. This drives the real ``install_build`` - the store's
own tests stand it in for, so nothing else covered it - over a throwaway
addons folder, with the suffix patched to both platforms' answers, and proves
a fresh build now lands whatever the older install of the same id looked like:

* a loose extensionless program does not block a folder build (the log's own
  failure), and the folder build's program ends up ``addon.bin``;
* a loose ``<id>.exe`` and an old folder of the same id both go before a
  folder build, and the returned program keeps its own name;
* a one-file build replaces an old folder of the same id outright;
* an id that names no addon at all is refused before anything is deleted.

Run it on the command line::

    python addons/_tests/test_install_build.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # import only, no window

#: The folder Berry Manager lives in, two levels up from here.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    """One thing an install has to do; all are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


def _install(dev: Any, addons: Path, suffix: str, staged: Path,
             addon_id: str, onefile: bool) -> Path:
    """One real ``install_build``, over this case's folder and platform shape."""
    old = (dev.ADDONS_DIR, dev.ADDON_EXECUTABLE_SUFFIX)
    dev.ADDONS_DIR, dev.ADDON_EXECUTABLE_SUFFIX = addons, suffix
    try:
        return dev.install_build(staged, addon_id, onefile)
    finally:
        dev.ADDONS_DIR, dev.ADDON_EXECUTABLE_SUFFIX = old


def _folder_build(dist: Path, addon_id: str) -> Path:
    """A PyInstaller --onedir result: the program loose inside dist/<id>/."""
    built = dist / addon_id
    built.mkdir(parents=True, exist_ok=True)
    program = built / addon_id
    program.write_text("fresh", encoding="utf-8")
    (built / "base_library.zip").write_text("x", encoding="utf-8")
    return program


def _onefile_build(dist: Path, addon_id: str, suffix: str) -> Path:
    """A --onefile result: one program straight in dist/."""
    dist.mkdir(parents=True, exist_ok=True)
    program = dist / f"{addon_id}{suffix}"
    program.write_text("fresh", encoding="utf-8")
    return program


# --- one: the log's own failure, an extensionless file blocking the folder ---

def test_flat_blocks_folder(dev: Any) -> None:
    print("\n1. a loose extensionless program no longer blocks a folder build")
    with tempfile.TemporaryDirectory() as where:
        tmp, addons = Path(where), Path(where) / "addons"
        addons.mkdir()
        (addons / "widget").write_text("the old flat program", encoding="utf-8")
        made = _install(dev, addons, "", _folder_build(tmp / "dist", "widget"),
                        "widget", False)
        check(made == addons / "widget" / "addon.bin",
              f"the folder build did not land as addon.bin: {made}")
        check(made.is_file() and made.read_text(encoding="utf-8") == "fresh",
              f"the installed program is not the fresh build: {made}")
        check((addons / "widget" / "base_library.zip").is_file(),
              "the folder build's neighbours were not copied with it")
        look(f"addons/widget/ now holds {sorted(p.name for p in (addons / 'widget').iterdir())}")


# --- two: a folder replacing itself, on the name both shapes share ---

def test_folder_over_folder(dev: Any) -> None:
    print("\n2. an old folder of the same id is replaced, not argued with")
    with tempfile.TemporaryDirectory() as where:
        tmp, addons = Path(where), Path(where) / "addons"
        old = addons / "widget"
        old.mkdir(parents=True)
        (old / "stale.dll").write_text("x", encoding="utf-8")
        made = _install(dev, addons, "", _folder_build(tmp / "dist", "widget"),
                        "widget", False)
        check(made.is_file(), f"no program came back: {made}")
        check(not (old / "stale.dll").exists(),
              "the old folder's contents were left beside the new build")
        check((old / "addon.bin").is_file(),
              "the replacement is not a folder build")


# --- three: the Windows shapes, loose exe and folder both clearing the way ---

def test_windows_shapes(dev: Any) -> None:
    print("\n3. a folder build clears a loose .exe and an old folder of the id")
    with tempfile.TemporaryDirectory() as where:
        tmp, addons = Path(where), Path(where) / "addons"
        addons.mkdir()
        (addons / "widget.exe").write_text("old flat", encoding="utf-8")
        (addons / "widget").mkdir()
        (addons / "widget" / "stale.exe").write_text("x", encoding="utf-8")
        made = _install(dev, addons, ".exe",
                        _folder_build(tmp / "dist", "widget"), "widget", False)
        check(made == addons / "widget" / "widget",
              f"a suffix-wearing folder build renamed something: {made}")
        check(not (addons / "widget.exe").exists(),
              "the old loose .exe was left to be a second same-id program")
        check(not (addons / "widget" / "stale.exe").exists(),
              "the old folder survived beside the new one")
        look(f"installed as {made}")

    print("\n4. a one-file build replaces an old folder of the id")
    with tempfile.TemporaryDirectory() as where:
        tmp, addons = Path(where), Path(where) / "addons"
        (addons / "widget").mkdir(parents=True)
        (addons / "widget" / "stale.exe").write_text("x", encoding="utf-8")
        staged = _onefile_build(tmp / "dist", "widget", ".exe")
        made = _install(dev, addons, ".exe", staged, "widget", True)
        check(made == addons / "widget.exe" and made.is_file(),
              f"the one-file build did not land loose: {made}")
        check(not (addons / "widget").exists(),
              "the folder that was in the one-file build's way stayed")


# --- four: an id that names nothing is refused before anything is deleted ---

def test_no_id(dev: Any) -> None:
    print("\n5. an empty or dot id is refused, and the addons folder is unharmed")
    with tempfile.TemporaryDirectory() as where:
        addons = Path(where) / "addons"
        addons.mkdir()
        (addons / "keep_me.txt").write_text("x", encoding="utf-8")
        for bad in ("", ".", ".."):
            try:
                _install(dev, addons, ".exe", addons / "keep_me.txt", bad, True)
                check(False, f"an id of {bad!r} was installed instead of refused")
            except OSError as error:
                look(f"{bad!r}: {error}")
        check((addons / "keep_me.txt").is_file(),
              "refusing a bad id still took something down")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        import core.addon as dev
    except Exception as error:                       # noqa: BLE001 - the app modules are optional
        print(f"core.addon could not be imported here: {error}")
        return 2
    print("core.addon: install_build replaces the old shape of an id")
    test_flat_blocks_folder(dev)
    test_folder_over_folder(dev)
    test_windows_shapes(dev)
    test_no_id(dev)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
