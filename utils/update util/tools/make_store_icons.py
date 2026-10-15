#!/usr/bin/env python3
"""Write the pictures the program itself wears into its assets folder.

Three of these marks are drawn by the interface at run time when no file is
beside it - the four tiles any unnamed tab wears, the shell the terminal tab
wears, the cog the pinned Settings tab wears - and this writes those same
drawings out as files, so what ships and what stands in for it cannot disagree:
the painters come out of :mod:`manager_widgets` itself, in the 32-unit space the
bar paints them into. The fourth is the store's own default - the tile an addon
with no picture of its own is shown with - drawn here in the program's colours
because no run-time code has any need to draw it.

An addon's own mark is not this tool's business: ``tools/make_addon_icons.py``
writes those out of each addon's ``TAB_ICON``, under ``<id>icon.png``, in the
folder that addon is read from.

Nothing here is needed at runtime; run it from this folder::

    python tools/make_store_icons.py [--side 128] [--force] [--check]

A file already in place is left alone unless ``--force``, because the point is to
hand somebody a picture to redraw, not to overwrite the one they redrew.
``--check`` writes nothing and names whatever is missing, exiting 1 - which is how
a workflow can hold the folder to the rule without drawing anything.

Each raster is square and 128 a side by default, with the field outside the mark
left transparent, so one file reads the same at every zoom and on any theme.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, Qt              # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPolygonF      # noqa: E402
from PyQt6.QtGui import QPainter, QPen                 # noqa: E402
from PyQt6.QtWidgets import QApplication               # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: The program's own three colours.
BACK = "#161616"
RIM = "#c51a4a"
OK = "#23d18b"
LINE = "#8a8a8a"

#: The square each mark is drawn into, and the stroke it is drawn with at that
#: size. Everything is scaled from these, so a bigger tile gets a proportionally
#: thicker line and stays legible when the tab asks it for 18px.
SIDE = 512
STROKE = 22

#: Where the tile starts inside the canvas, in the same 512 units as the marks.
INSET = 26


def _canvas(size: int) -> tuple[QImage, float, QPainter]:
    """A transparent square, with the scale one draw pass should work in."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return image, size / SIDE, painter


def _tile(painter: QPainter, scale: float, size: int) -> None:
    """The rounded near-black tile every mark sits on, with a crimson rim."""
    inset = INSET * scale
    side = size - 2 * inset
    painter.setBrush(QColor(BACK))
    painter.setPen(QPen(QColor(RIM), 10 * scale))
    painter.drawRoundedRect(QRectF(inset, inset, side, side),
                            18 * scale, 18 * scale)


def _pen(painter: QPainter, color: str, scale: float, weight: float = 1.0) -> None:
    """One round-capped stroke of the tile's own weight, scaled with it."""
    painter.setPen(QPen(QColor(color), STROKE * weight * scale,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _at(point: tuple[float, float], scale: float) -> QPointF:
    """One 512-unit coordinate, moved onto the tile being drawn."""
    inset = INSET * scale
    return QPointF(inset + point[0] * scale, inset + point[1] * scale)


def _line(painter: QPainter, a: tuple, b: tuple, scale: float) -> None:
    painter.drawLine(_at(a, scale), _at(b, scale))


def _shape(painter: QPainter, points: list, scale: float) -> None:
    """An open polyline through 512-unit coordinates, as one stroke."""
    painter.drawPolyline(QPolygonF([_at(point, scale) for point in points]))


def _dot(painter: QPainter, center: tuple, scale: float, radius: float) -> None:
    spot = _at(center, scale)
    painter.drawEllipse(spot, radius * scale, radius * scale)


def draw_default(size: int = SIDE) -> QImage:
    """A plugin tile: a square with one corner broken off, and its dot.

    The missing corner is where the piece would join another, and the green dot is
    the one thing every addon with a picture of its own shows differently.
    """
    image, scale, painter = _canvas(size)
    _tile(painter, scale, size)
    _pen(painter, LINE, scale, 0.9)
    _shape(painter, [(150, 150), (150, 360), (360, 360), (360, 250),
                     (300, 250), (300, 150), (150, 150)], scale)
    _pen(painter, RIM, scale, 0.7)
    for x in (200, 260):                     # the two pins below the piece
        _line(painter, (x, 360), (x, 410), scale)
    _pen(painter, OK, scale, 0.6)
    _dot(painter, (330, 190), scale, 22)
    painter.end()
    return image


def draw_packages(size: int = SIDE) -> QImage:
    """An open crate with a list of what is in it, ticked off line by line.

    The three lines with ticks are what a package manager reads and writes; the
    crate is where they came from; the flaps standing open are that it is a store.

    No file is written from it any more: OpenWrt Package Manager 1.3 put its own
    mark under the ``<id>icon.png`` rule, and ``tools/make_addon_icons.py`` draws
    that one out of the addon's own ``TAB_ICON``. It is kept because the tile it
    sits on is the shape a hand-drawn card wants, and a fork that names a picture
    in its manifest can still be drawn from here.
    """
    image, scale, painter = _canvas(size)
    _tile(painter, scale, size)
    _pen(painter, LINE, scale, 0.85)
    _shape(painter, [(140, 220), (140, 380), (370, 380), (370, 220)], scale)
    _shape(painter, [(140, 220), (100, 150), (200, 150), (255, 220)], scale)
    _shape(painter, [(370, 220), (410, 150), (310, 150), (255, 220)], scale)
    _pen(painter, RIM, scale, 0.7)
    for y in (265, 310, 350):                # what is in the crate, as lines
        _line(painter, (215, y), (335, y), scale)
    _pen(painter, OK, scale, 0.6)
    for y in (265, 310, 350):                # each one answered for with a tick
        _shape(painter, [(170, y - 12), (190, y + 10), (205, y - 18)], scale)
    painter.end()
    return image


def draw_interface_mark(paint: Any, size: int = 128) -> QImage:
    """One of the bar's own paintings, written out as a file of ``size`` a side.

    The painter is the very function ``manager_widgets`` falls back to when no
    asset is beside it, called in the 32-unit space it was written for and scaled
    up from there - so the shipped picture and the stand-in are one drawing seen
    at two sizes rather than two drawings that have to be kept alike. The colours
    come from the theme in force, which in a fresh checkout is the built-in one.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))       # the tool runs from its own folder
    from PyQt6.QtGui import QColor, QImage, QPainter

    canvas = QImage(int(size), int(size), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 32.0, size / 32.0)
    paint(painter)
    painter.end()
    return canvas


def _interface_targets(size: int) -> list:
    """The three marks the window paints itself, paired with where each goes.

    Read here rather than at import time: a machine with no Qt installed still
    has no business failing a tool it never runs, and ``--check`` needs the paths
    long before anything is drawn.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import manager_widgets

    return [
        (ROOT / "assets" / "defaulttabicon.png",
         lambda: draw_interface_mark(manager_widgets.paint_default_tab, size)),
        (ROOT / "assets" / "terminalicon.png",
         lambda: draw_interface_mark(manager_widgets.paint_terminal_tab, size)),
        (ROOT / "assets" / "settingsicon.png",
         lambda: draw_interface_mark(manager_widgets.paint_settings_tab, size)),
    ]


def main(argv: list[str] | None = None) -> int:
    """Write what the assets folder does not hold, and say what it should hold."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--side", type=int, default=128,
                        help="the picture's width and height (default 128)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report which pictures are missing")
    args = parser.parse_args(argv)
    size = max(16, int(args.side))

    targets = [(ROOT / "assets" / "storedefaultaddon.png",
                lambda: draw_default(size))] + _interface_targets(size)
    if args.check:
        missing = [str(path.relative_to(ROOT)) for path, _draw in targets
                   if not path.is_file()]
        for name in missing:
            print(f"missing  {name}")
        if missing:
            print(f"{len(missing)} of the program's own pictures are not in assets")
            return 1
        print(f"the program's own {len(targets)} picture(s) are all in assets")
        return 0

    # Held in a name that lives as long as the drawing does: Qt destroys a
    # ``QApplication`` the moment Python drops its last reference, exactly the
    # way ``make_addon_icons`` keeps its own in ``_APP``.
    app = QApplication.instance() or QApplication([])   # a GUI bar, no window
    made = 0
    kept = 0
    for path, drawer in targets:
        if path.is_file() and not args.force:
            kept += 1
            print(f"kept     {path.relative_to(ROOT)}")
            continue
        image = drawer()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            print(f"FAILED: {path} could not be written")
            return 1
        made += 1
        print(f"wrote    {path.relative_to(ROOT)} at {image.width()}x{image.height()}")
    print(f"{made} written, {kept} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
