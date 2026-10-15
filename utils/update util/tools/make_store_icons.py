#!/usr/bin/env python3
"""Draw the two pictures the store and the package manager are shipped with.

Both are flat marks in the program's own colours - near-black behind, the crimson
of the group titles, the green of a thing that is working - drawn at any size from
one pass so they can be made again bigger, or nudged, rather than edited in a
bitmap. Nothing here is needed at runtime; run it from this folder::

    python tools/make_store_icons.py

and it writes:

* ``assets/storedefaultaddon.png`` - what an addon with no picture of its own is
  shown with, in the list and on the pane beside it;
* ``addon.store/assets/openwrt_package_manager/openwrtpkgmgr.png`` - the picture
  OpenWrt Package Manager declares as its own ``icon``, which is why it is not
  named ``openwrt_package_managericon.png`` like everything else.

Both are square PNGs with a transparent field outside the rounded tile, so they
read the same on any background the interface is wearing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


#: The file each mark is written to, and the function that draws it.
TARGETS = (
    (ROOT / "assets" / "storedefaultaddon.png", draw_default),
    (ROOT / "addon.store" / "assets" / "openwrt_package_manager"
     / "openwrtpkgmgr.png", draw_packages),
)


def main() -> int:
    QApplication.instance() or QApplication([])   # a GUI bar, no window
    made = 0
    for path, drawer in TARGETS:
        image = drawer()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            print(f"FAILED: {path} could not be written")
            return 1
        made += 1
        print(f"wrote {path} at {image.width()}x{image.height()}")
    print(f"{made} icon(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
