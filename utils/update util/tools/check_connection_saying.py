#!/usr/bin/env python3
"""Stop a build on a row that would say the wrong thing about its addon.

An addon's row in Settings says which connection it rides, and that saying
comes out of two small decisions: how the word in a manifest's ``connection``
is read, and which of the four sayings wins when an addon's transports, its
being shipped with the program, and its declaration disagree. Both are pure,
and both are quiet - a wrong answer does not crash, it just tells the user
that an addon waits for a connection it never opens.

The catalog the store reads is checked against the same rules, so a published
entry and the row it will build cannot drift apart, and the four addons this
repository ships are read as they really declare.

No window is needed; run it from anywhere::

    python tools/check_connection_saying.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # widgets, no window

#: The folder Berry Manager lives in, one level up from these tools.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import manager_addons                                          # noqa: E402

CATALOG = ROOT / "addon.store" / "index.json"

#: What the store's published addons say, and the picture the catalog should
#: name for each: every one of them now wears its own, under the ``<id>icon.png``
#: rule, in ``addon.store/assets/<id>/`` beside its source.
SHIPPED = {
    "cert_manager": (["ssh"], "app", "cert_managericon.png"),
    "openwrt_logs": (["ssh"], "both", "openwrt_logsicon.png"),
    "openwrt_package_manager": (["ssh", "sftp"], "app",
                                "openwrt_package_managericon.png"),
    # Every shell this addon opens is its own socket, on its own thread, so it
    # is the one published addon that may not ride the app's session at all.
    "multi_ssh": (["ssh"], "own", "multi_sshicon.png"),
}

FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


class _Quiet:
    """The manager's ``log`` as a test needs it: heard, and remembered nowhere."""

    def log(self, *args: Any, **kwargs: Any) -> None:
        pass


def look_rows(manager: Any) -> None:
    print("\n1. which saying a row shows")
    cases = (
        (([], False, "own"), "none", "no transports reaches no device"),
        (([], True, "both"), "none", "no transport beats being shipped"),
        ((["ssh"], True, "own"), "app", "a shipped addon rides the app"),
        ((["ssh"], True, "both"), "app", "a shipped addon gets no switch"),
        ((["ssh"], False, "own"), "own", "own stays own"),
        ((["ssh", "sftp"], False, "both"), "both", "both stays both"),
        ((["ssh"], False, "app"), "app", "app stays app"),
        ((["ssh"], False, ""), "app", "nothing said reads as the app's"),
        ((["ssh"], False, None), "app", "no answer reads as the app's"),
        ((["ssh"], False, "nonsense"), "app", "a word nobody knows falls back"),
        ((["ssh"], False, " BOTH "), "both", "the word is trimmed and lowered"),
    )
    for arguments, want, why in cases:
        got = manager.connection_choice(*arguments)
        check(got == want, f"{why}: {list(arguments)} -> {got!r}")


def look_manifests(manager: Any) -> None:
    print("\n2. what a manifest's word becomes")
    addon = manager.Addon("probe", "check", "", "file")
    for text, want in (("", "none"), ("none", "none"), ("NONE", "none"),
                       ("app", "app"), ("main", "app"), ("own", "own"),
                       ("private", "own"), ("both", "both"), ("BOTH", "both"),
                       ("weird", "keep"), (None, "keep")):
        addon.connection = "keep"
        addon.requires = ()
        manager.AddonManager.apply_manifest(_Quiet(), addon, {"connection": text})
        # A word nobody knows leaves the answer that was there, which for an
        # addon meeting the row for the first time is the app's own connection:
        # a bad declaration does not move a row on its own.
        check(addon.connection == want,
              f"connection {text!r} read as {addon.connection!r}")

    print("\n3. requires only ever keeps the two real transports")
    addon = manager.Addon("probe", "check", "", "file")
    manager.AddonManager.apply_manifest(
        _Quiet(), addon, {"requires": ["SFTP", "ssh", "serial"]})
    check(addon.requires == ("sftp", "ssh"),
          f"requires came out as {addon.requires!r}")
    manager.AddonManager.apply_manifest(_Quiet(), addon, {"requires": "ssh"})
    check(addon.requires == ("ssh",),
          f"a plain string did not become one transport: {addon.requires!r}")
    manager.AddonManager.apply_manifest(_Quiet(), addon, {"requires": []})
    check(addon.requires == (),
          f"an empty list gained a transport: {addon.requires!r}")


def look_catalog(manager: Any) -> None:
    print("\n4. the published catalog, and the row it would build")
    if not CATALOG.is_file():
        check(False, f"there is no catalog at {CATALOG}")
        return
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = {str(row.get("id")): row for row in catalog.get("addons") or []}
    check(set(rows) == set(SHIPPED),
          f"the catalog lists {sorted(rows)}, expected {sorted(SHIPPED)}")
    for name, (requires, connection, icon) in SHIPPED.items():
        row = rows.get(name)
        if row is None:
            check(False, f"{name} is not in the catalog")
            continue
        check(list(row.get("requires") or []) == requires
              and row.get("connection") == connection
              and str(row.get("icon") or "") == icon,
              f"{name} is published as {row.get('requires')} / "
              f"{row.get('connection')} / {str(row.get('icon') or '')!r}")

        # The same words, read the way a row reads them.
        addon = manager.Addon(name, "check", "", "file")
        addon.connection = "app"
        manager.AddonManager.apply_manifest(_Quiet(), addon, row)
        shown = manager.connection_choice(addon.requires, False, addon.connection)
        check(shown == connection,
              f"{name}'s row would say {shown!r} where the catalog says "
              f"{connection!r}")

    # An addon with no picture of its own is the reason the default exists.
    for name, (_requires, _connection, icon) in SHIPPED.items():
        if icon:
            picture = CATALOG.parent / "assets" / name / icon
            where = "is there" if picture.is_file() else "is nowhere to be found"
            check(picture.is_file(), f"{name} names {icon}, which {where}")
        else:
            folder = CATALOG.parent / "assets" / name
            empty = not folder.is_dir() or not any(folder.iterdir())
            saying = ("has nothing, so the store shows the default one"
                      if empty else "holds a picture after all")
            check(empty, f"{name} names no picture and its folder {saying}")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    manager = manager_addons
    look_rows(manager)
    look_manifests(manager)
    look_catalog(manager)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nthe rows say what the manifests and the catalog mean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
