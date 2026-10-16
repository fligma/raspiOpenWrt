#!/usr/bin/env python3
"""Stop a build on names that do not exist.

Every module under this folder is read with pyflakes, and only the answers that
mean a crash are kept: a name nothing binds, an import of something that is not
there, a file that does not parse. The rest of what pyflakes says - an import
nobody uses, a value assigned and left - is left to a developer's own linter,
because failing a build on those would only teach people to ignore the build.

Why this is a build step and not a code review habit: Berry Manager is a Qt
program, and a Python exception raised where Qt called in does not print and
carry on - it takes the window down with it. A released build did exactly that
on opening the About dialog, over one line written for a trailing ``.format()``
that never reached it::

    f'GitHub: <a href="{repo}" ...>'      # no ``repo`` anywhere: NameError

That is visible without running anything, so it is looked for that way here,
before either program is built and long before either is published.

The files the Addon Dev tab writes are read the same way once they are filled
in. A ``string.Template`` is text to an editor and text to ``ast``, so a name
missing from the launcher it renders stays invisible until a compiled addon dies
on the spot with a ``NameError`` - which is what happened when the launcher lost
its imports and every built addon failed with it. Rendering the template and
reading the result costs nothing and sees it.

Run from anywhere::

    pip install pyflakes
    python tools/check_names.py            # the whole app
    python tools/check_names.py addons     # or named files or folders
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # templates, no window

#: Folders that hold nothing the program runs from.
SKIP_DIRS = {"__pycache__", ".venv", ".git", "node_modules", "_build",
             "_data", "dist", "build"}

#: What pyflakes says when a file cannot possibly run as it stands. Matched on
#: the printed text because the wording of these messages is the one part of
#: pyflakes that has stayed stable across its versions.
FATAL_MARKS = ("undefined name", "does not exist", "syntax error",
               "expected an indented block", "unexpected indent",
               "unterminated", "duplicate argument", "starred")

ROOT = Path(__file__).resolve().parent.parent

#: The three templates the Addon Dev tab hands an author, filled with a probe
#: addon; each is read for names afterwards.
TEMPLATE_PROBES = (
    ("_ADDON_SOURCE", {"id": "probe", "name": "Probe", "title": "Probe",
                        "tab": "Probe", "menu": "Probe", "version": "1.0",
                        "author": "author", "description": "a probe addon"}),
    ("_TEST_SOURCE", {"id": "probe", "tab": "Probe"}),
    ("_LAUNCHER", {"id": "probe", "module": "probe"}),
)


def sources(arguments: list[str]) -> list[Path]:
    """The files to read: those named, or every module under the app's folder."""
    if not arguments:
        return sorted(path for path in ROOT.rglob("*.py")
                      if not set(path.parts) & SKIP_DIRS)
    found: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            found += sorted(item for item in path.rglob("*.py")
                            if not set(item.parts) & SKIP_DIRS)
        else:
            found.append(path)
    return found


def fatal_lines(text: str, label: str) -> list[str]:
    """What pyflakes says about ``text`` that means it cannot run as it stands."""
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    printed, errors = io.StringIO(), io.StringIO()
    check(text + "\n", label, Reporter(printed, errors))
    return [line for line in printed.getvalue().splitlines()
            + errors.getvalue().splitlines()
            if any(mark in line for mark in FATAL_MARKS)]


def rendered_templates() -> list[str]:
    """Names missing from the files the Addon Dev tab writes, once filled in.

    Quiet when the app's own modules cannot be imported here at all: that is a
    checkout with something wrong in it, which the pass over the real files has
    already said about.
    """
    sys.path.insert(0, str(ROOT))
    try:
        import core.addon as addon_module
    except Exception as error:                       # noqa: BLE001 - optional
        print(f"  note: templates were not read (the app did not import: {error})")
        return []
    bad: list[str] = []
    for name, fields in TEMPLATE_PROBES:
        template = getattr(addon_module, name, None)
        if template is None:
            bad.append(f"core/addon.py has no {name} to fill in")
            continue
        try:
            source = template.substitute(**fields)
        except (KeyError, ValueError) as error:
            bad.append(f"{name}: cannot be filled in ({error})")
            continue
        bad += [f"{name} renders: {line}" for line in
                fatal_lines(source, f"<{name}>")]
    return bad


def main(arguments: list[str]) -> int:
    try:
        from pyflakes.api import check                 # noqa: F401 - presence only
    except ImportError:
        print("pyflakes is not installed here; run: pip install pyflakes")
        return 2

    files = sources(arguments)
    bad: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            bad.append(f"{path}: cannot be read: {error}")
            continue
        for line in fatal_lines(text, str(path)):
            # The app's own folder is left off the reported name, so the
            # line reads the way a traceback does from the working folder.
            try:
                line = line.replace(str(path), str(path.relative_to(ROOT)))
            except ValueError:
                pass
            bad.append(line)

    bad += rendered_templates()

    print(f"checked {len(files)} module(s) and "
          f"{len(TEMPLATE_PROBES)} template(s) for names that do not exist")
    for line in bad:
        print(f"  {line}")
    if bad:
        print(f"\n{len(bad)} name(s) would raise the moment they are reached; "
              "nothing was built")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
