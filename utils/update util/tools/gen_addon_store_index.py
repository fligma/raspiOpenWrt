#!/usr/bin/env python3
"""Build addon.store/index.json from the addons sitting in the store folder.

index.json is the catalog the Berry addon store reads off GitHub. Keeping it by
hand means it drifts the moment an addon is added, renamed or versioned, so this
generates it instead: every ``.py`` in ``addon.store`` becomes an entry, its name,
version, author, description, ``requires`` and ``connection`` read from the
``ADDON`` manifest in the file, its pictures read from
``addon.store/assets/<id>/`` and its test from ``addon.store/_tests/test_<id>.py``
when one is there. The picture that stands for the addon is picked by the name the
addon rules use - ``<id>icon.png`` first, then a name its manifest declares, then
``icon.png``, then whatever single picture the folder holds - so an addon that
follows the rule needs no code and an older one keeps the picture it shipped.
Entries the previous index did not have are marked ``new``; ones whose version
moved are marked ``updated``.

The output is stable: with nothing changed it writes byte-for-byte the same file,
so a build that regenerates it only has something to commit when the catalog
really moved. Run it from the folder, or let CI run it on build::

    python tools/gen_addon_store_index.py [--check]

``--check`` writes nothing and exits non-zero if the file on disk is out of date.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

#: The store folder this sits one level under (``utils/update util``/tools).
STORE_DIR = Path(__file__).resolve().parents[1] / "addon.store"

#: A catalog id is a name Python and PyInstaller can both import.
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")

#: What an addon's own picture is always called: its id, then this. The same
#: rule ``addon_common`` uses when it looks in a program's assets folder, kept
#: here as well because a tool run from CI imports nothing of the app.
ICON_TAIL = "icon.png"

#: A plain name for an addon that has one picture and no standard name.
GENERIC_ICON = "icon.png"

#: Icon suffixes worth passing to PyInstaller, most preferred first.
ICON_SUFFIXES = (".ico", ".icns", ".png")

#: Endings a folder picture may have at all, for a last look for something.
PICTURE_SUFFIXES = ICON_SUFFIXES + (".svg", ".jpg", ".jpeg", ".webp")


def clean_id(value: str) -> str:
    """A file stem turned into the id a catalog and a build both use."""
    return _UNSAFE.sub("_", str(value).strip()) or "addon"


def read_manifest(path: Path) -> dict:
    """The ``ADDON = {...}`` dict of one file, read without importing it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ADDON"
                for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def list_assets(store: Path, addon_id: str) -> list[str]:
    """The asset files under ``addon.store/assets/<id>/``, as paths from that folder."""
    folder = store / "assets" / addon_id
    if not folder.is_dir():
        return []
    return sorted(path.relative_to(folder).as_posix()
                  for path in folder.rglob("*") if path.is_file())


def words(value: Any) -> list[str]:
    """One manifest value written as a list, however it was written in the file.

    ``requires`` is published as a list and an addon may have written it as a
    comma separated line, so both are read the same way here and in the app.
    """
    if isinstance(value, str):
        value = [part for part in re.split(r"[,; ]+", value) if part]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        word = " ".join(str(item or "").split())
        if word and word not in out:
            out.append(word)
    return out


def flat(value: Any) -> str:
    """A manifest sentence on the one line a catalog row and a label both need."""
    return " ".join(str(value or "").split())


def connection_of(manifest: dict) -> str:
    """How one addon reaches a device, as the catalog should say it.

    The manifest's own word wins. An addon that names nothing but does need a
    transport talks over the app's connection - that is what ``requires`` gates
    on - so it is reported as ``app`` rather than as needing no device, and an
    addon needing nothing is ``none``. A word nothing recognises is kept, so a
    fork's own vocabulary still shows.
    """
    said = flat(manifest.get("connection")).lower()
    if said:
        return said
    return "app" if words(manifest.get("requires")) else "none"


def pick_icon(assets: list[str], addon_id: str, declared: str = "") -> str:
    """The asset that stands for one addon, picked by the naming rule.

    The standard name goes first, so an addon that follows the rule needs no
    declaration; then the name its own manifest declares, then the build icons
    under its id, then a plain ``icon.png``, and last the one picture its folder
    holds - which is how an addon that predates the rule keeps the file it has.
    """
    named = {name.lower(): name for name in assets}

    def take(value: Any) -> str:
        return named.get(flat(value).lower(), "")

    wanted = [take(f"{addon_id}{ICON_TAIL}"), take(declared)]
    wanted += [take(f"{addon_id}{suffix}") for suffix in ICON_SUFFIXES]
    wanted.append(take(GENERIC_ICON))
    for item in wanted:
        if item:
            return item
    for suffix in PICTURE_SUFFIXES:
        for name in assets:
            if PurePosixPath(name).suffix.lower() == suffix:
                return name
    return ""


def list_test(store: Path, addon_id: str) -> str:
    """``test_<id>.py`` when the store folder ships one for this addon, else nothing.

    The store runs this test over a freshly downloaded addon before it spends
    time compiling it, so an addon that carries a test gets checked on install.
    """
    name = f"test_{addon_id}.py"
    if (store / "_tests" / name).is_file():
        return name
    return ""


def build_index(store: Path, previous: dict | None = None) -> dict:
    """The whole catalog for one store folder, marked against the previous index."""
    store = Path(store)
    before = {str(entry.get("id")): entry
              for entry in (previous or {}).get("addons", [])
              } if isinstance(previous, dict) else {}
    entries: list[dict] = []
    for path in sorted(store.glob("*.py")):
        if path.name.startswith("_"):
            continue
        manifest = read_manifest(path)
        if not manifest:
            continue
        addon_id = clean_id(path.stem)
        assets = list_assets(store, addon_id)
        entry: dict[str, Any] = {
            "id": addon_id,
            "name": str(manifest.get("name") or addon_id),
            "version": str(manifest.get("version") or ""),
            "author": str(manifest.get("author") or ""),
            "description": flat(manifest.get("description")),
            "filename": path.name,
            # What the addon says it needs, and how it reaches the device. The
            # store shows both, so a person can see before installing whether
            # this works on the machine in front of them or talks to a router.
            "requires": words(manifest.get("requires")),
            "connection": connection_of(manifest),
        }
        if assets:
            entry["assets"] = assets
        icon = pick_icon(assets, addon_id, str(manifest.get("icon") or ""))
        if icon:
            entry["icon"] = icon
        test_name = list_test(store, addon_id)
        if test_name:
            entry["test"] = test_name
        status = mark(entry, before.get(addon_id))
        if status:
            entry["status"] = status
        entries.append(entry)
    return {"addons": entries}


def mark(entry: dict, prior: dict | None) -> str:
    """new when the old catalog did not have it, updated when the version moved."""
    if prior is None:
        return "new"
    if str(prior.get("version") or "") != str(entry.get("version") or ""):
        return "updated"
    return ""


def render(index: dict) -> str:
    """The catalog as tidy, stable JSON with a trailing newline."""
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def load_previous(target: Path) -> dict | None:
    """The index already on disk, for the new/updated marks; nothing if unreadable."""
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    """Generate the catalog, or only report whether the file on disk is current."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, default=STORE_DIR,
                        help="the addon.store folder to read")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; fail if the index is out of date")
    args = parser.parse_args(argv)
    target = args.store / "index.json"
    index = build_index(args.store, load_previous(target))
    text = render(index)
    try:
        # A file nothing can read is not the new one; ``load_previous`` already
        # treats it as nothing, and a check says the same thing out loud.
        current = target.read_text(encoding="utf-8") if target.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    if args.check:
        if text == current:
            print(f"{target.name} is up to date ({len(index['addons'])} addon(s))")
            return 0
        print(f"{target.name} is out of date; run gen_addon_store_index.py")
        return 1
    target.write_text(text, encoding="utf-8")
    changed = [entry["id"] for entry in index["addons"]
               if entry.get("status") in ("new", "updated")]
    print(f"wrote {target} with {len(index['addons'])} addon(s)"
          + (f"; marked {', '.join(changed)}" if changed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
