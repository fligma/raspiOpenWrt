#!/usr/bin/env python3
"""Stop a build on a guide that links to a page nobody wrote.

The Addon Dev tab's **Docs** pane is built off the running app: its guides come
from a fixed table of markdown, its reference is read off ``addon_api``, and the
two are stitched together by links an addon author clicks - ``guide:config`` to
jump to another page, ``api:tabs.add_tab`` to jump to a call. A link to a page
that was renamed, or never added, does not raise: the pane shows the words and
the click does nothing, which is only ever noticed by whoever tries it. This
tries all of them before anything is built.

The templates the **New addon...** button writes are read too. They are text
inside a ``string.Template``, so a mistake in one is a syntax error in a file
the author only compiles after the app has handed it to them; here the template
is filled in and parsed, which costs nothing and catches it first.

No window is needed; run it from anywhere::

    pip install -r requirements.txt      # Qt is imported, no screen is opened
    python tools/check_guide_links.py
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # widgets, no window

#: The folder Berry Manager lives in, one level up from these tools.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.addon               # noqa: E402

FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look_guides(dev: Any) -> list[str]:
    """Every guide renders, and no two share a key or a title."""
    print("\n1. the guides themselves")
    keys: list[str] = []
    titles = set()
    for key, title, text in dev.GUIDES:
        keys.append(key)
        page = dev._html(text)
        check(bool(page.strip()), f"{key} renders to something to read")
        check(key not in keys[:-1], f"{key} is a key no other guide has")
        check(title not in titles, f"{title!r} is a title no other guide has")
        titles.add(title)
    print(f"     {len(keys)} guide(s): {', '.join(keys)}")
    return keys


def look_links(dev: Any, keys: list[str]) -> None:
    """Every ``guide:`` and ``api:`` link in a guide lands somewhere real."""
    print("\n2. where the links inside them go")
    pages, calls = dev.api_counts()
    print(f"     the reference has {calls} name(s) across {pages} page(s)")
    for key, _title, text in dev.GUIDES:
        for match in dev._LINK_RE.finditer(text):
            target = match.group(2)
            if target.startswith("guide:"):
                check(target[6:] in keys,
                      f"{key} links to {target}, which is a guide")
            elif target.startswith("api:"):
                found = dev.api_entry(target[4:])
                check(found is not None,
                      f"{key} links to {target}, which the reference has")

    print("\n3. the page each namespace opens on")
    for addr, guide in sorted(dev.GUIDE_FOR_NAMESPACE.items()):
        check(guide in keys, f"api.{addr} sends the reader to {guide!r}, "
                             "which is a guide")


def look_templates(dev: Any) -> None:
    """The files 'New addon...' writes would still be readable by Python."""
    print("\n4. the templates an author is handed")
    source = dev._ADDON_SOURCE.substitute(
        id="probe", name="Probe", title="Probe", tab="Probe",
        menu="Probe", version="1.0", author="author",
        description="a probe addon")
    _parses(source, "the addon template")
    test = dev._TEST_SOURCE.substitute(id="probe", tab="Probe")
    _parses(test, "the test template")
    launcher = dev._LAUNCHER.substitute(id="probe", module="probe")
    _parses(launcher, "the launcher template")


def _parses(source: str, what: str) -> None:
    try:
        ast.parse(source)
        check(True, f"{what} parses as written")
    except SyntaxError as error:
        check(False, f"{what} does not parse: line {error.lineno}: {error.msg}")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not core.addon.api_reference():
        print("addon_api could not be imported, so the reference is empty "
              "and every api: link would be reported missing")
        return 1
    keys = look_guides(core.addon)
    look_links(core.addon, keys)
    look_templates(core.addon)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nthe guides, their links and the templates all line up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
