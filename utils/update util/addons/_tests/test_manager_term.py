#!/usr/bin/env python3
"""A test for the SSH terminal widget, core/net.py.

The screen is a small xterm emulator the shell drives byte by byte, so the
things that went wrong in 1.6.7 are exactly the things no reading of the source
can prove:

* the cursor has to come back on any output - a full-screen program like nano
  that keeps the alternate screen busy was leaving it caught on the blink's off
  phase, never to be drawn again;
* a resize has to give the grid room for the overlay scrollbar, so a column is
  never painted underneath the bar once history appears beside it;
* the colours have to read on the dark ground, and the SGR mapping has to land
  on the balanced 16-colour set the palette now uses (a bold colour picks its
  bright variant, a 256 or truecolour value is packed the way the cell reads it
  back).

The whole thing is driven offscreen with no SSH and no device: bytes go in via
``feed`` and the model - the pen, the grid, the bar - is read straight back.

Run it on the command line::

    python addons/_tests/test_manager_term.py
"""

from __future__ import annotations

import os
import sys
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager lives in, two levels up from here.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    """One thing the screen has to do; all of them are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


# --- one: the cursor comes back on any output ---

def test_cursor_returns(term: Any) -> None:
    """A write and an alternate-screen entry both put the cursor on-screen."""
    print("\n1. the cursor is not left caught mid-blink")
    screen = term.TerminalScreen()
    screen.feed("\x1b[?1049h")                     # nano takes the alternate screen
    check(screen.alternate is True, "the alternate screen was not entered")
    check(screen._blink_on is True,
          "entering the alternate screen did not light the cursor")
    screen._blink_on = False                        # catch it on the off phase
    screen.feed("hello")
    check(screen._blink_on is True,
          "output on the alternate screen did not bring the cursor back")
    check(screen.cursor_visible is True, "the cursor was hidden by a write")
    # The blink advances on both screens now: the alternate guard is gone.
    screen.alternate = True
    screen.cursor_visible = True
    screen._blink_on = True
    screen._toggle_blink()
    check(screen._blink_on is False,
          "the blink no longer advances on the alternate screen")
    screen._toggle_blink()
    check(screen._blink_on is True, "the blink does not come back around")
    # A program that asks for the cursor to hide is honoured.
    screen.feed("\x1b[?25l")
    check(screen.cursor_visible is False, "'hide cursor' was ignored")
    screen.feed("\x1b[?25h")
    check(screen.cursor_visible is True, "'show cursor' was ignored")
    screen.deleteLater()


# --- two: a resize leaves room for the scrollbar ---

def test_scrollbar_reserved(term: Any) -> None:
    """Once the bar comes over the grid, the grid gives up a column for it."""
    print("\n2. the last column is not painted under the bar")
    screen = term.TerminalScreen()
    screen.alternate = False
    screen.history = []
    screen._bar_shown = False
    screen.resize(640, 480)
    screen._refit()
    no_bar = screen.cols
    check(no_bar >= 2, f"the grid has only {no_bar} columns at a 640 px width")
    # A scroll pushes rows into history, so the bar arrives over the grid.
    screen.history = [term._blank_row(no_bar) for _ in range(100)]
    screen._update_bar()
    with_bar = screen.cols
    check(screen._bar_shown is True, "the bar never registered as shown")
    check(with_bar < no_bar,
          f"the grid kept {with_bar} columns with the bar over it, "
          f"the same as the {no_bar} it had without")
    # And clearing the history hands the column back.
    screen.history = []
    screen._update_bar()
    check(screen._bar_shown is False, "the bar stayed shown once history cleared")
    check(screen.cols >= no_bar,
          f"the wasted column was not returned: {screen.cols} vs {no_bar}")
    screen.deleteLater()


# --- three: the colours and the SGR mapping ---

def test_sgr_colours(term: Any) -> None:
    """256-colour, bold and truecolour all land on the palette the cell reads."""
    print("\n3. the palette and the SGR mapping")
    # The 16-colour set is balanced so every one reads on the dark ground: the
    # darkest entry is still lighter than the background it is drawn on.
    dark = term.QColor(term.DEFAULT_BG).lightness()
    for index, name in enumerate(term.PALETTE16[8:], start=8):
        check(term.QColor(name).lightness() > dark,
              f"the bright colour {index} ({name}) is not lighter than the ground")
    screen = term.TerminalScreen()
    # 38;5;n picks the palette entry n.
    screen.pen = (-1, -1, 0)
    screen.feed("\x1b[38;5;1mA")
    check(screen.pen[0] == 1, f"a 256-colour write left the pen at {screen.pen}")
    check(term._cell_color(screen.pen[0], term.QColor(term.DEFAULT_BG))
          == term.QColor(term.PALETTE16[1]),
          "palette index 1 does not read as the palette's red")
    # 1;31 is bold red: the render swaps a bold 0..7 for its bright variant.
    screen.pen = (-1, -1, 0)
    screen.feed("\x1b[1;31mA")
    check(screen.pen[0] == 1 and (screen.pen[2] & term.BOLD),
          f"bold red set the pen to {screen.pen}")
    bright = term.PALETTE[screen.pen[0] + 8]
    check(bright == term.QColor(term.PALETTE16[9]),
          "a bold base colour does not resolve to the bright variant")
    # 38;2;r;g;b is packed above the palette so the cell can decode it back.
    screen.pen = (-1, -1, 0)
    screen.feed("\x1b[38;2;10;20;30mA")
    expected = 256 + ((10 << 16) | (20 << 8) | 30)
    check(screen.pen[0] == expected,
          f"truecolour packed as {screen.pen[0]}, not {expected}")
    got = term._cell_color(screen.pen[0], term.QColor(term.DEFAULT_BG))
    check((got.red(), got.green(), got.blue()) == (10, 20, 30),
          f"the packed colour decodes to ({got.red()},{got.green()},{got.blue()})")
    # A default foreground (39) is the sentinel, not a palette row.
    screen.feed("\x1b[39mA")
    check(screen.pen[0] == -1, "the default foreground is not the -1 sentinel")
    screen.deleteLater()


# --- four: a shape change clamps the cursor and tells the shell ---

def test_grid_resize(term: Any) -> None:
    """Shrinking the grid keeps the cursor inside it and emits the new size."""
    print("\n4. a grid resize clamps the cursor and emits resized")
    screen = term.TerminalScreen()
    screen.history = []
    screen.resize(640, 480)
    screen._refit()
    wide_cols, wide_rows = screen.cols, screen.rows
    emitted: list[tuple[int, int]] = []
    screen.resized.connect(lambda c, r: emitted.append((int(c), int(r))))
    screen.cr = wide_rows - 1                       # put the cursor in a corner
    screen.cc = wide_cols - 1
    screen.resize(120, 160)                         # a much narrower box
    screen._refit()
    check(screen.cols < wide_cols and screen.rows < wide_rows,
          f"the grid did not shrink: {screen.cols}x{screen.rows} of "
          f"{wide_cols}x{wide_rows}")
    check(screen.cr <= screen.rows - 1 and screen.cc <= screen.cols - 1,
          f"the cursor ran off the shrunk grid: ({screen.cr},{screen.cc})")
    check(emitted and emitted[-1] == (screen.cols, screen.rows),
          f"the shell was told {emitted}, not the new grid size")
    screen.deleteLater()


# --- five: the smoke - a nano-like block, painted ---

def test_paint_smoke(term: Any) -> None:
    """A full-screen program's output paints without raising."""
    print("\n5. a nano-style block feeds, resizes and paints")
    screen = term.TerminalScreen()
    screen.resize(640, 480)
    screen._refit()
    block = ("\x1b[?1049h\x1b[?25l\x1b[H"                # alt screen, clear, home
             "\x1b[1;34mGNU NANO\x1b[0m\r\n"             # a bold blue title
             "\x1b[7m ^G Help ^O Write \x1b[0m\r\n"      # a reverse-video bar
             "\x1b[38;5;2mhello world\x1b[0m\r\n"        # a 256-colour line
             "\x1b[38;2;12;34;56mtruecolour\x1b[0m\r\n"  # a truecolour line
             "\x1b[?25h")                                # cursor back on
    screen.feed(block)
    screen._blink_on = False
    screen.feed("\x1b[1;1H")                     # a redraw nudge: cursor back on
    check(screen._blink_on is True, "a full-screen redraw lost the cursor")
    for size in ((300, 400), (900, 500), (640, 240)):
        screen.resize(*size)
        screen._refit()
    screen.grab()                                # runs paintEvent offscreen
    check(True, "the screen painted a full program without raising")
    screen.feed("\x1b[?1049l")                   # back to the shell's grid
    check(screen.alternate is False, "the alternate screen did not close")
    screen.deleteLater()


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as error:                       # noqa: BLE001 - Qt is optional
        print(f"no Qt here, so the widget cannot be built: {error}")
        return 2
    app = QApplication.instance() or QApplication([])
    try:
        import core.net as term
    except Exception as error:                       # noqa: BLE001
        print(f"core.net could not be imported: {error}")
        return 2
    print("core.net: the SSH terminal screen")
    look(f"palette of {len(term.PALETTE16)} colours, ground {term.DEFAULT_BG}")
    test_cursor_returns(term)
    test_scrollbar_reserved(term)
    test_sgr_colours(term)
    test_grid_resize(term)
    test_paint_smoke(term)
    app.processEvents()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
