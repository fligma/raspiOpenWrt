#!/usr/bin/env python3
"""Write the dashboard's pictures out of the picture the program itself wears.

The web interface used to carry artwork of its own, drawn for a page and left
there: a browser tab showing something other than the app that installs it is a
small lie told twice a day. So every one of the page's pictures now comes out of
``assets/icon.png`` - the file the window, the built program and the taskbar all
use - cut to the sizes a browser and a phone actually ask for:

    favicon-16x16.png      a browser tab
    favicon-32x32.png      a retina tab
    favicon.ico            Internet Explorer, and anything that asks the old way
    apple-touch-icon.png   an iPhone or iPad home screen (180)
    android-chrome-*.png   an Android home screen (192 and 512)

The names are not free: they are the ones ``cgi-bin/pages.py`` links to and the
ones a phone writes down when a page is added to a home screen, so a file here is
written under the name the browser already knows or not at all.

``favicon.ico`` is built here rather than saved by Qt, because Qt's ICO writer
wants a ``QIcon`` and picks its own frames, and because the modern form of the
file is simply a directory of PNGs: an ``ICONDIR``, one 16-byte entry per
picture, then the pictures whole. That is a handful of ``struct.pack`` calls and
no new dependency, which matters because this tool has to run in CI on a machine
that holds PyQt6 and nothing else.

Nothing is resized by eye. The source is scaled with the smooth transform, so a
16-pixel tab mark is the whole berry rather than a corner of one.

Run it from ``utils/update util``::

    python tools/make_web_icons.py [--side 512] [--force] [--check]

A file already in place is left alone unless ``--force``. ``--check`` writes
nothing and exits 1 when a file is missing, is the wrong size, or shows a
different picture than the app's icon would produce - which is how a workflow can
hold the page to the program without redrawing anything. It compares what a
browser sees (the decoded pixels, premultiplied, and for an ICO each frame inside
it) rather than the bytes one Qt version happens to write, and it allows a small
mean leeway per pixel, because one platform's smooth downscale rounds the edge of
a stroke a unit or two off another's: artwork drawn on Windows and artwork drawn
on a headless runner are the same artwork even when they are not the same bytes,
and only a genuinely different picture is failed.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path
from typing import Any

# The picture is read, cut and re-encoded through Qt, and a workflow runner has
# no display to open a GUI bar on. Forcing the offscreen platform before any Qt
# import lets the check run where a window could not be drawn, the same way the
# other icon tools do; a person running it by hand still gets it from their own
# desktop, since this only sets the default when nothing has said otherwise.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: ``utils/update util``, which the app's icon sits in.
ROOT = Path(__file__).resolve().parents[1]

#: The one picture everything else is cut from.
SOURCE = ROOT / "assets" / "icon.png"

#: The dashboard's folder. It lives in the repository rather than beside this
#: tool because the tree an install uploads is that one, and the pictures have to
#: be in the tree that goes over.
WEB = ROOT.parents[1] / "src" / "www_rasconf" / "assets"

#: ``(file name, side)`` for each PNG the page asks for, smallest first.
PNG_TARGETS = (
    ("favicon-16x16.png", 16),
    ("favicon-32x32.png", 32),
    ("apple-touch-icon.png", 180),
    ("android-chrome-192x192.png", 192),
    ("android-chrome-512x512.png", 512),
)

#: The frames carried inside ``favicon.ico``. 16 and 32 are what browsers ask for
#: today; 48 is what a Windows desktop tile draws.
ICO_SIZES = (16, 32, 48)

ICO_NAME = "favicon.ico"

#: The name of the PNG that ``--side`` is allowed to redraw at another size. It
#: keeps its name whatever it is drawn at: a phone stores the size it fetched, and
#: the manifest points at the file, not at its measurements.
LARGEST = "android-chrome-512x512.png"


def png_targets(side: int) -> list[tuple[str, int]]:
    """The PNG set to write, with the biggest file drawn at ``side``."""
    out = []
    for name, size in PNG_TARGETS:
        out.append((name, max(16, int(side)) if name == LARGEST else size))
    return out


def app_icon() -> Any:
    """The program's own picture as Qt reads it, or ``None``."""
    from PyQt6.QtGui import QImage

    if not SOURCE.is_file():
        return None
    image = QImage(str(SOURCE))
    return None if image.isNull() else image


def cut(base: Any, side: int) -> Any:
    """One square ARGB raster, the whole source scaled into it.

    A non-square source is trimmed from the middle first, so a mark that grows
    taller than wide is still centred rather than squashed.
    ``SmoothTransformation`` is the difference between a readable 16-pixel mark
    and a block of pixels: the source is 512 a side, so a tab icon is downscaled
    by thirty-two and every part of the drawing has to reach the result.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    square = base
    wide, tall = base.width(), base.height()
    if wide != tall:
        short = min(wide, tall)
        square = base.copy(int((wide - short) / 2), int((tall - short) / 2),
                           short, short)
    image = square.scaled(int(side), int(side),
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def png_of(image: Any) -> bytes:
    """One ``QImage`` as PNG bytes, without touching the disk."""
    from PyQt6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt refused to write a PNG")
    return bytes(buffer.data())


def pixels(image: Any) -> bytes:
    """The picture's own bytes, as premultiplied ARGB, whatever format it held.

    Rows go in whole, padding and all: two images of one width have one row
    length, so the padding cannot make the same drawing read as a different one.
    Premultiplied rather than straight, because a transparent pixel is then zero
    on every channel no matter what colour was stored under it - so the empty
    corners of a mark cannot drift for the wrong reason when two platforms read
    the same file.
    """
    flat = image.convertToFormat(image.Format.Format_ARGB32_Premultiplied)
    total = flat.bytesPerLine() * flat.height()
    return bytes(flat.constBits().asstring(total))


#: How far apart two rasters may be, per byte on average, and still be called
#: one picture. A smooth downscale lands a unit or two off between one Qt build
#: and another on the edge of a stroke; a genuinely different icon is worth tens.
SLACK = 8.0


def _close(first: Any, second: Any) -> bool:
    """Whether two images are the same picture, give or take a platform's rounding.

    The check is a mean distance per byte over the whole premultiplied raster,
    not an equality of bytes: this tool's own output is held to, but a runner's
    Qt is allowed to round a scaled edge differently than the machine that drew
    the file did. Two faithful downscales of one source are nowhere near
    ``SLACK`` apart; the program's icon and any other picture are far above it.
    """
    a = pixels(first)
    b = pixels(second)
    if len(a) != len(b):
        return False
    if not a:
        return True
    total = sum(x - y if x >= y else y - x for x, y in zip(a, b))
    return total / len(a) <= SLACK


def ico_of(pictures: list) -> bytes:
    """PNG-compressed ``.ico``: the directory header, the entries, then frames.

    ``pictures`` is ``(side, png bytes)`` per frame, biggest last. A width or a
    height of 256 is written as 0, which is how the format says "larger than a
    byte can hold"; the rest is lengths and offsets, and offsets are counted from
    the front of the file.
    """
    head = struct.pack("<HHH", 0, 1, len(pictures))
    entries = b""
    body = b""
    offset = len(head) + 16 * len(pictures)
    for side, png in pictures:
        byte = 0 if int(side) >= 256 else int(side)
        entries += struct.pack("<BBBBHHII", byte, byte, 0, 0, 1, 32,
                               len(png), offset)
        offset += len(png)
        body += png
    return head + entries + body


def build(base: Any, size: int) -> bytes:
    """The bytes one PNG target should hold."""
    return png_of(cut(base, size))


def build_ico(base: Any) -> bytes:
    """The bytes ``favicon.ico`` should hold: every frame, in order."""
    return ico_of([(side, build(base, side)) for side in ICO_SIZES])


def same_png(path: Path, base: Any, size: int) -> str:
    """``""`` when the file shows this picture, else the reason it does not."""
    from PyQt6.QtGui import QImage

    if not path.is_file():
        return "is not there"
    if path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        return "is not a PNG"
    have = QImage(str(path))
    if have.isNull():
        return "is not a picture a browser can read"
    if have.width() != size or have.height() != size:
        return f"is {have.width()}x{have.height()}, not {size}x{size}"
    want = cut(base, size)
    if not _close(want, have):
        return "is not the program's icon"
    return ""


def same_ico(path: Path, base: Any) -> str:
    """The same question of an ICO, asked of every frame inside it.

    The file is taken apart rather than handed to Qt because an image reader
    returns the first frame and nothing else, and the point of the check is that
    each size the browser may ask for is really in there.
    """
    if not path.is_file():
        return "is not there"
    from PyQt6.QtGui import QImage

    data = path.read_bytes()
    if len(data) < 6:
        return "is too short to be an ICO"
    _reserved, kind, count = struct.unpack("<HHH", data[:6])
    if kind != 1:
        return "is not an icon file but a bitmap group"
    if count != len(ICO_SIZES):
        return f"holds {count} frame(s), not the {len(ICO_SIZES)} this writes"
    for index, side in enumerate(ICO_SIZES):
        entry = data[6 + 16 * index: 22 + 16 * index]
        if len(entry) < 16:
            return "has a frame whose entry is cut off"
        wide, high, _colors, _reserved, _planes, _bits, length, start = \
            struct.unpack("<BBBBHHII", entry)
        if (wide or 256) != side or (high or 256) != side:
            return f"frame {index + 1} is {(wide or 256)}x{(high or 256)}"
        frame = data[start:start + length]
        image = QImage()
        if not image.loadFromData(frame, "PNG"):
            return f"frame {index + 1} holds no PNG Qt can read"
        want = cut(base, side)
        if not _close(want, image):
            return f"frame {index + 1} is not the program's icon"
    return ""


def report(path: Path, why: str) -> str:
    """One line of output about one file, named the way a person looks for it."""
    word = "same     " if not why else "drift    "
    text = str(path.relative_to(WEB.parent.parent))
    return f"{word}{text}" + (f": {why}" if why else "")


def main(argv: list[str] | None = None) -> int:
    """Write what the page does not have, and say what it has to have."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--side", type=int, default=512,
                        help=f"the size {LARGEST} is drawn at (default 512)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report what has drifted")
    args = parser.parse_args(argv)

    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")

    if not SOURCE.is_file():
        print(f"there is no program icon at {SOURCE}")
        return 2

    from PyQt6.QtWidgets import QApplication

    # Held in a name that lives as long as the reading and re-encoding does:
    # Qt destroys a ``QApplication`` the moment Python drops its last reference,
    # exactly the way ``make_addon_icons`` keeps its own in ``_APP``.
    app = QApplication.instance() or QApplication([])     # a GUI bar, no window
    base = app_icon()
    if base is None:
        print(f"{SOURCE} cannot be read as a picture")
        return 2

    drift = 0
    written = 0
    kept = 0

    for name, size in png_targets(args.side):
        path = WEB / name
        why = same_png(path, base, size) if args.check else ""
        if args.check:
            print(report(path, why))
            drift += 1 if why else 0
            continue
        if path.is_file() and not args.force:
            kept += 1
            print(report(path, ""))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build(base, size))
        written += 1
        print(f"wrote    {path.relative_to(WEB.parent.parent)} at {size}x{size}")

    ico = WEB / ICO_NAME
    if args.check:
        why = same_ico(ico, base)
        print(report(ico, why))
        drift += 1 if why else 0
    elif ico.is_file() and not args.force:
        kept += 1
        print(report(ico, ""))
    else:
        ico.parent.mkdir(parents=True, exist_ok=True)
        ico.write_bytes(build_ico(base))
        written += 1
        print(f"wrote    {ico.relative_to(WEB.parent.parent)} "
              f"({len(ICO_SIZES)} frames)")

    if args.check:
        if drift:
            print(f"{drift} of the dashboard's picture(s) do not match "
                  f"{SOURCE.relative_to(ROOT)}")
            return 1
        print("the dashboard wears the program's icon in every picture it links to")
        return 0
    if written:
        print(f"wrote {written} picture(s), kept {kept} that were already there")
    else:
        print("nothing written: the dashboard already has its pictures "
              "(--force to draw them again)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
