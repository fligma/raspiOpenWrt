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

Run from anywhere::

    pip install pyflakes
    python tools/check_names.py            # the whole app
    python tools/check_names.py addons     # or named files or folders
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

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


def main(arguments: list[str]) -> int:
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
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
        printed, errors = io.StringIO(), io.StringIO()
        check(text + "\n", str(path), Reporter(printed, errors))
        for line in printed.getvalue().splitlines() + errors.getvalue().splitlines():
            if any(mark in line for mark in FATAL_MARKS):
                # The app's own folder is left off the reported name, so the
                # line reads the way a traceback does from the working folder.
                try:
                    line = line.replace(str(path), str(path.relative_to(ROOT)))
                except ValueError:
                    pass
                bad.append(line)

    print(f"checked {len(files)} module(s) for names that do not exist")
    for line in bad:
        print(f"  {line}")
    if bad:
        print(f"\n{len(bad)} name(s) would raise the moment they are reached; "
              "nothing was built")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
