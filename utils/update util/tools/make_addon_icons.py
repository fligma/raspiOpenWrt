#!/usr/bin/env python3
"""Write every addon's own tab picture into the folder its icons live in.

Each addon keeps its mark in two shapes: an SVG document held in ``TAB_ICON``,
which the app draws onto the tab bar, and a picture file, which is what the
addon store's card wears and what a built program carries inside itself. The
file is the half a person can change, so it is the half worth having in the
tree - and it has to answer to one name, the rule every lookup already uses:
the addon's id and then ``icon.png``. ``addon_common.find_addon_picture`` looks
for that one name in the addon's own ``assets/<id>`` folder and then in the
program's ``assets``, so a file written here is worn whether the addon is being
read out of ``addon.store`` or installed into a build. This reads the drawing out
of each addon's source and writes it under that name, in the folder that addon's
pictures live in:

* ``addons/<id>.py`` -> ``assets/<id>icon.png``
* ``addon.store/<id>.py`` -> ``addon.store/assets/<id>/<id>icon.png``

Nothing is imported. The file is parsed and ``TAB_ICON`` is read out of it, so an
addon that needs Qt, or a device, or its own config file before it can be
imported still gets its picture. An existing file is left exactly as it is
unless ``--force``, because artwork drawn by hand should never be replaced by
the program's own fallback mark, and ``--check`` writes nothing and only says
which addons hold no picture and exit code 1 when one is missing::

    python tools/make_addon_icons.py [--side 128] [--force] [--check] [id ...]

The raster is square and by default 128 a side: a tab mark is drawn larger than
the bar measures it, so one file stays readable at every zoom, and the field
outside the mark is left transparent, so the picture reads on any theme.
"""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: ``utils/update util``, which the two source folders and the assets folder sit in.
ROOT = Path(__file__).resolve().parents[1]

#: Where each family of addons keeps its source, and where its pictures go.
#: The program's own assets folder serves the addons that ship inside it; a
#: store addon's pictures stay beside its source and travel to whoever installs it.
FAMILIES = (
    (ROOT / "addons", ROOT / "assets"),
    (ROOT / "addon.store", ROOT / "addon.store" / "assets"),
)

#: What a tab picture is always called: the addon's id, then this.
ICON_TAIL = "icon.png"


def icon_name(addon_id: str) -> str:
    """The one file name an addon's own picture is looked up under."""
    return f"{addon_id}{ICON_TAIL}"


def read_tab_icon(path: Path) -> str:
    """The ``TAB_ICON`` literal of one addon file, without importing the file.

    ``ast`` is what makes this safe: the addon's own code never runs, so a module
    that reaches for a device or a config file at import time is no obstacle. A
    drawing written as several adjacent strings is folded by ``literal_eval``,
    which is how the sources hold them; anything the literal reader refuses is
    simply no drawing.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        named = any(isinstance(target, ast.Name) and target.id == "TAB_ICON"
                    for target in node.targets)
        if not named:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            return ""
        return value if isinstance(value, str) else ""
    return ""


def find_addons(wanted: list[str]) -> list[tuple[str, Path, Path]]:
    """``(id, source, where its picture goes)`` for every addon that can have one.

    A folder addon is not walked here: its id is its folder and its picture is
    already under the rule, and this tool is about the addons the program ships
    and the store publishes. Names beginning with ``_`` are skipped, as the
    runtime skips them.
    """
    found: list[tuple[str, Path, Path]] = []
    for source_dir, asset_dir in FAMILIES:
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.glob("*.py")):
            addon_id = path.stem
            if addon_id.startswith("_") or addon_id in {"_tests"}:
                continue
            if wanted and addon_id not in wanted:
                continue
            folder = asset_dir if source_dir == (ROOT / "addons") \
                else asset_dir / addon_id
            found.append((addon_id, path, folder))
    return found


#: The GUI interface, made once and kept: Qt destroys a ``QApplication`` the
#: moment Python drops its last reference, and a ``QPixmap`` built after that
#: takes the program down with it rather than answering.
_APP: Any = None


def render(svg: str, side: int) -> Any:
    """One SVG document as a square transparent-field image; ``None`` if Qt
    cannot draw it, which is said out loud rather than written as a blank file."""
    global _APP
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication

    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    drawn = QPixmap()
    if not drawn.loadFromData(QByteArray(svg.encode("utf-8")), "SVG"):
        return None
    canvas = QImage(int(side), int(side), QImage.Format.Format_ARGB32)
    canvas.fill(QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(0, 0, int(side), int(side), drawn)
    painter.end()
    return canvas


def main(argv: list[str] | None = None) -> int:
    """Write what is missing, and say what every addon is expected to hold."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*",
                        help="only these addons, by their id")
    parser.add_argument("--side", type=int, default=128,
                        help="the picture's width and height (default 128)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report which pictures are missing")
    args = parser.parse_args(argv)

    wanted = [str(item) for item in args.ids]
    side = max(16, int(args.side))
    missing: list[str] = []
    written: list[str] = []
    kept: list[str] = []
    nodrawing: list[str] = []

    for addon_id, source, folder in find_addons(wanted):
        name = icon_name(addon_id)
        target = folder / name
        if target.is_file() and not args.force:
            kept.append(str(target.relative_to(ROOT)))
            continue
        svg = read_tab_icon(source)
        if not svg:
            # An addon with no drawing of its own is not an error: its tab wears
            # the program's default tab picture until somebody puts a file under
            # this name, and the store shows it its placeholder card.
            nodrawing.append(addon_id)
            missing.append(addon_id)
            continue
        if args.check:
            missing.append(addon_id)
            print(f"missing  {target.relative_to(ROOT)} "
                  f"(a drawing is held in {source.name})")
            continue
        image = render(svg, side)
        if image is None:
            print(f"cannot draw {addon_id}: {source.name} holds no SVG Qt reads")
            missing.append(addon_id)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        if not image.save(str(target), "PNG"):
            print(f"could not write {target}")
            missing.append(addon_id)
            continue
        written.append(str(target.relative_to(ROOT)))

    for path in written:
        print(f"wrote    {path}")
    # ``--check`` writes nothing, so nothing was "kept": the file simply is
    # where the rule says it should be.
    kept_word = "there    " if args.check else "kept     "
    for path in kept:
        print(f"{kept_word}{path}")
    for addon_id in nodrawing:
        print(f"no drawing in the source of {addon_id}; its picture is the one "
              f"a person puts there: {icon_name(addon_id)}")
    if args.check and missing:
        print(f"{len(missing)} addon(s) hold no picture file")
        return 1
    if not written:
        print("nothing to write: every addon that can draw one already has its file")
    else:
        print(f"wrote {len(written)} picture(s), kept {len(kept)} that were already there")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
