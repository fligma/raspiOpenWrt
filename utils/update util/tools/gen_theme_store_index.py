#!/usr/bin/env python3
"""Build themes.store/index.json from the themes sitting in the store folder.

index.json is the catalog the Berry Theme Store reads off GitHub. Keeping it by
hand means it drifts the moment a theme is added, renamed or changed, so this
generates it instead: every ``.theme`` in ``themes.store`` becomes an entry, its
name, version, author and blurb read from the JSON in the file itself. Entries
the previous index did not have are marked ``new``; ones whose version moved are
marked ``updated`` - a theme that wants to be seen as newer says so with its own
``version`` key, because a colour change alone is not something the Theme Store
offers as an update.

The output is stable: with nothing changed it writes byte-for-byte the same
file, so a build that regenerates it only has something to commit when the
catalog really moved. Run it from the folder, or let CI run it on build::

    python tools/gen_theme_store_index.py [--check]

``--check`` writes nothing and exits non-zero if the file on disk is out of date.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

#: The store folder this sits one level under (``utils/update util``/tools).
STORE_DIR = Path(__file__).resolve().parents[1] / "themes.store"

#: A catalog id is a name a table and a download can both use.
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")

#: The ending a theme file always has, the same one the app reads the themes
#: folder with.
THEME_GLOB = "*.theme"


def clean_id(value: str) -> str:
    """A file stem turned into the id a catalog and a listing both use."""
    return _UNSAFE.sub("_", str(value).strip()) or "theme"


def flat(value: Any) -> str:
    """A theme sentence on the one line a catalog row and a label both need."""
    return " ".join(str(value or "").split())


def read_theme(path: Path) -> dict:
    """The JSON object one ``.theme`` file holds; ``{}`` when it holds none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build_index(store: Path, previous: dict | None = None) -> dict:
    """The whole catalog for one store folder, marked against the previous index."""
    store = Path(store)
    before = {str(entry.get("id")): entry
              for entry in (previous or {}).get("themes", [])
              } if isinstance(previous, dict) else {}
    entries: list[dict] = []
    for path in sorted(store.glob(THEME_GLOB), key=lambda one: one.name.lower()):
        if path.name.startswith("_"):
            continue
        data = read_theme(path)
        if not data:
            continue
        theme_id = clean_id(path.stem)
        entry: dict[str, Any] = {
            "id": theme_id,
            "name": str(data.get("name") or path.stem),
            # A theme that does not say its version is the file it always was;
            # the key is not one a ``.theme`` normally carries, so raising it is
            # an author's deliberate "take this one as newer".
            "version": flat(data.get("version")) or "1",
            "author": flat(data.get("author")),
            "blurb": flat(data.get("blurb")),
            "filename": path.name,
        }
        status = mark(entry, before.get(theme_id))
        if status:
            entry["status"] = status
        entries.append(entry)
    return {"themes": entries}


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
                        help="the themes.store folder to read")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; fail if the index is out of date")
    args = parser.parse_args(argv)
    target = args.store / "index.json"
    index = build_index(args.store, load_previous(target))
    text = render(index)
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    if args.check:
        if text == current:
            print(f"{target.name} is up to date ({len(index['themes'])} theme(s))")
            return 0
        print(f"{target.name} is out of date; run gen_theme_store_index.py")
        return 1
    target.write_text(text, encoding="utf-8")
    changed = [entry["id"] for entry in index["themes"]
               if entry.get("status") in ("new", "updated")]
    print(f"wrote {target} with {len(index['themes'])} theme(s)"
          + (f"; marked {', '.join(changed)}" if changed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
