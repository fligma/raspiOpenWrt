#!/usr/bin/env python3

"""The terminal of the SSH tab: a small xterm-style screen the shell can drive.

The widget keeps a grid of character cells and understands the escape
sequences full-screen programs actually use - cursor addressing, scroll
regions, the alternate screen that nano and friends switch to, colours, and
the "where is your cursor?" query a program waits on before it draws. Keys
typed while it has focus go out as the raw bytes a real terminal would send,
so ``^G`` in nano is ``^G`` on the device.

Nothing here knows about SSH or paramiko: the widget talks to the rest of
the app through four signals - ``send_data`` for every byte the shell asked
for or the user typed, ``resized`` when its grid changes shape, ``bell`` and
``title_changed`` for the polite noise - and through ``feed()`` for whatever
arrives from the device.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QApplication, QScrollBar, QSizePolicy, QWidget

# The colours. Sixteen named entries start the palette, the usual 6x6x6 cube
# and grey ramp follow, and the two defaults match the rest of the app.
DEFAULT_FG = "#e0e0e0"
DEFAULT_BG = "#1e1e1e"
SELECTION_COLOR = QColor(70, 130, 200, 100)
PALETTE16 = ["#000000", "#cd0000", "#00cd00", "#cdcd00", "#0000ee", "#cd00cd",
             "#00cdcd", "#e5e5e5", "#7f7f7f", "#ff0000", "#00ff00", "#ffff00",
             "#5c5cff", "#ff00ff", "#00ffff", "#ffffff"]

HISTORY_MAX = 2000          # rows kept above the screen when nothing is full-screen

# Cell attributes, packed into one int per cell.
BOLD, DIM, ITALIC, UNDERLINE, BLINK, REVERSE, HIDDEN, STRIKE = (1, 2, 4, 8, 16, 32, 64, 128)

# Colours inside a cell: -1 means "whatever the default is", 0..255 a palette
# index, and anything above that one packed rrggbb value with 256 added.


def _palette() -> list[QColor]:
    """The standard 256-colour table."""
    colors = [QColor(name) for name in PALETTE16]
    steps = (0, 95, 135, 175, 215, 255)
    for red in steps:
        for green in steps:
            for blue in steps:
                colors.append(QColor(red, green, blue))
    for level in range(24):
        grey = 8 + level * 10
        colors.append(QColor(grey, grey, grey))
    return colors


PALETTE = _palette()


def _cell_color(value: int, default: QColor) -> QColor:
    if value < 0:
        return default
    if value < 256:
        return PALETTE[value]
    packed = value - 256
    return QColor((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF)


def _blank_cell(fg: int = -1, bg: int = -1, flags: int = 0):
    return (" ", fg, bg, flags)


def _blank_row(width: int) -> list:
    return [_blank_cell() for _ in range(width)]


class TerminalScreen(QWidget):
    """One cell grid, one cursor, and the escape brain that keeps them right."""

    send_data = pyqtSignal(str)          # bytes for the shell: keys, replies
    resized = pyqtSignal(int, int)       # new (columns, rows) for the PTY
    bell = pyqtSignal()
    title_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._font = QFont("Consolas", 10)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._metrics = QFontMetrics(self._font)
        self._cw = max(1, self._metrics.horizontalAdvance("W"))
        self._ch = max(1, self._metrics.height())
        self._ascent = self._metrics.ascent()

        self.cols, self.rows = 80, 24
        self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
        self.history: list[list] = []
        self.cr = self.cc = 0             # cursor, row then column
        self.pen = (-1, -1, 0)            # fg, bg, attribute bits
        self.saved_cursor = (0, 0, -1, -1, 0)
        self.top_margin, self.bottom_margin = 0, self.rows - 1
        self.tabs = set(range(8, self.cols, 8))
        self.wrap_next = False
        self.origin_mode = False
        self.autowrap = True
        self.app_cursor = False
        self.cursor_visible = True
        self.bracketed_paste = False
        self.alternate = False
        self._main_state = None           # everything the alternate screen displaced

        # Parser: one character at a time, so a chunk may end mid-sequence.
        self._state = "ground"
        self._collect = ""
        self._intermediate = ""
        self._private = ""
        self._osc_after_escape = False

        self._back = 0                    # rows of history currently showing
        self._bar = QScrollBar(Qt.Orientation.Vertical, self)
        self._bar.setVisible(False)
        self._bar.valueChanged.connect(self._scroll_to)

        self._blink_on = True
        self._blink = QTimer(self)
        self._blink.setInterval(500)
        self._blink.timeout.connect(self._toggle_blink)

        self._select_from: tuple[int, int] | None = None
        self._select_to: tuple[int, int] | None = None
        self.title = ""

    # ------------------------------------------------------------------
    # size and fonts
    # ------------------------------------------------------------------

    def grid_size(self) -> tuple[int, int]:
        return self.cols, self.rows

    def set_font(self, font: QFont) -> None:
        """Adopt a font (the app's Consolas + size rule) and recompute cells."""
        self._font = QFont(font)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._metrics = QFontMetrics(self._font)
        self._cw = max(1, self._metrics.horizontalAdvance("W"))
        self._ch = max(1, self._metrics.height())
        self._ascent = self._metrics.ascent()
        self._refit()

    def font_size(self) -> int:
        return self._font.pointSize()

    def sizeHint(self) -> QSize:
        return QSize(self.cols * self._cw, self.rows * self._ch)

    def resizeEvent(self, event) -> None:
        self._refit()
        super().resizeEvent(event)

    def _refit(self) -> None:
        cols = max(2, self.width() // self._cw)
        rows = max(2, self.height() // self._ch)
        if (cols, rows) == (self.cols, self.rows):
            return
        self._resize_grid(cols, rows)
        self.resized.emit(self.cols, self.rows)

    def _resize_grid(self, cols: int, rows: int) -> None:
        """Carry the grid across a shape change, keeping what still fits."""
        moved = [(_row + [_blank_cell() for _ in range(cols)])[:cols]
                 for _row in self.lines[:rows]]
        moved += [_blank_row(cols) for _ in range(max(0, rows - len(self.lines)))]
        self.lines = moved
        self.cols, self.rows = cols, rows
        self.bottom_margin = rows - 1
        self.tabs = set(range(8, cols, 8))
        self.cr = min(self.cr, rows - 1)
        self.cc = min(self.cc, cols - 1)
        self._back = min(self._back, len(self.history))
        self._update_bar()
        self.update()

    # ------------------------------------------------------------------
    # feeding output
    # ------------------------------------------------------------------

    def feed(self, text: str) -> None:
        """One chunk of device output. The view follows it if it was at bottom."""
        for ch in text:
            self._step(ch)
        if self._back:
            self._back = 0
            self._bar.setValue(0)
        self.update()

    def write_local(self, text: str) -> None:
        """A message from the app itself, drawn exactly like device output."""
        self.feed(text)

    def clear(self) -> None:
        """Screen and history away; the shell is where the user put it."""
        self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
        self.history.clear()
        self.cr = self.cc = 0
        self._back = 0
        self._update_bar()
        self.update()

    # the parser, one character at a time ---------------------------------

    def _step(self, ch: str) -> None:
        state = self._state
        if state == "ground":
            self._ground(ch)
        elif state == "escape":
            self._escape(ch)
        elif state == "csi":
            self._csi_char(ch)
        elif state == "osc":
            self._osc_char(ch)
        elif state == "charset":       # ESC ( B and friends: swallow one more
            self._state = "ground"

    def _ground(self, ch: str) -> None:
        code = ord(ch)
        if ch == "\x1b":
            self._state = "escape"
        elif ch == "\n":
            self._line_feed()
        elif ch == "\r":
            self.cc = 0
            self.wrap_next = False
        elif ch == "\x08":
            self.cc = max(0, self.cc - 1)
            self.wrap_next = False
        elif ch == "\x09":
            self._tab()
        elif ch == "\x07":
            self.bell.emit()
        elif ch in ("\x0b", "\x0c"):
            self._line_feed()
        elif code < 32:
            pass                       # any other control byte: quietly ignored
        else:
            self._put(ch)

    def _escape(self, ch: str) -> None:
        if ch == "[":
            self._state = "csi"
            self._collect, self._intermediate, self._private = "", "", ""
        elif ch == "]":
            self._state = "osc"
            self._collect = ""
            self._osc_after_escape = False
        elif ch in ("(", ")", "#", "*", "+"):
            self._state = "charset"    # character set selection: understood, unused
        elif ch == "7":
            self.saved_cursor = (self.cr, self.cc, *self.pen)
            self._state = "ground"
        elif ch == "8":
            self.cr, self.cc, self.pen = (self.saved_cursor[0], self.saved_cursor[1]), \
                                          (self.saved_cursor[2], self.saved_cursor[3], self.saved_cursor[4])
            self.wrap_next = False
            self._state = "ground"
        elif ch == "D":                # IND: down one line, scrolling if needed
            self._line_feed()
            self._state = "ground"
        elif ch == "E":                # NEL: down and back to the margin
            self._line_feed()
            self.cc = 0
            self._state = "ground"
        elif ch == "M":                # RI: up one line, scrolling the other way
            self._reverse_index()
            self._state = "ground"
        elif ch == "c":                # soft reset: a fresh screen is enough
            self.clear()
            self.pen = (-1, -1, 0)
            self._state = "ground"
        else:
            self._state = "ground"     # = and > (keypad modes) change nothing here

    def _csi_char(self, ch: str) -> None:
        code = ord(ch)
        if 0x40 <= code <= 0x7E:
            self._csi_run(ch)
            self._state = "ground"
        elif 0x30 <= code <= 0x3F:
            if ch == "?" and not self._collect and not self._private:
                self._private = "?"
            else:
                self._collect += ch
        elif 0x20 <= code <= 0x2F:
            self._intermediate += ch
        else:                          # stray control byte inside a sequence
            self._state = "ground"
            self._ground(ch)

    def _osc_char(self, ch: str) -> None:
        if self._osc_after_escape:
            self._osc_after_escape = False
            if ch == "\\":
                self._osc_run()
                self._state = "ground"
                return
            # not a string terminator after all; carry on collecting
        if ch == "\x07":
            self._osc_run()
            self._state = "ground"
        elif ch == "\x1b":
            self._osc_after_escape = True
        elif len(self._collect) < 4096:
            self._collect += ch

    def _osc_run(self) -> None:
        head, _, body = self._collect.partition(";")
        if head in ("0", "1", "2"):    # icon and window title: keep the title
            self.title = body
            self.title_changed.emit(body)

    # ------------------------------------------------------------------
    # the CSI dictionary
    # ------------------------------------------------------------------

    def _numbers(self) -> list[int]:
        out = []
        for piece in self._collect.split(";"):
            digits = "".join(c for c in piece if c.isdigit())
            out.append(int(digits) if digits else 0)
        return out

    def _at(self, params: list[int], index: int, default: int = 1) -> int:
        return params[index] if index < len(params) and params[index] else default

    def _csi_run(self, final: str) -> None:
        params = self._numbers()
        private = self._private == "?"
        if self._intermediate == " " and final == "q":     # DECSCUSR cursor shape
            return
        if final == "H" or final == "f":                   # CUP / HVP
            self._move(self._at(params, 0) - 1, self._at(params, 1) - 1, address=True)
        elif final == "A":
            self._move(self.cr - max(1, params[0] if params else 1), self.cc)
        elif final == "B":
            self._move(self.cr + max(1, params[0] if params else 1), self.cc)
        elif final == "C":
            self._move(self.cr, self.cc + max(1, params[0] if params else 1))
        elif final == "D":
            self._move(self.cr, self.cc - max(1, params[0] if params else 1))
        elif final == "d":                                 # VPA
            self._move(self._at(params, 0) - 1, self.cc, address=True)
        elif final == "G" or final == "`":                 # HPA / HPB
            self._move(self.cr, self._at(params, 0) - 1, address=True)
        elif final == "J":
            self._erase_display(self._at(params, 0, 0))
        elif final == "K":
            self._erase_line(self._at(params, 0, 0))
        elif final == "L":
            self._insert_lines(self._at(params, 0))
        elif final == "M":
            self._delete_lines(self._at(params, 0))
        elif final == "P":
            self._delete_chars(self._at(params, 0))
        elif final == "@":
            self._insert_chars(self._at(params, 0))
        elif final == "X":
            self._erase_chars(self._at(params, 0))
        elif final == "S":
            self._scroll_region(-self._at(params, 0))
        elif final == "T":
            self._scroll_region(self._at(params, 0))
        elif final == "r":                                 # DECSTBM scroll region
            top = self._at(params, 0, 1) - 1
            bottom = self._at(params, 1, self.rows) - 1
            if top < bottom and bottom < self.rows:
                self.top_margin, self.bottom_margin = top, bottom
                self._move(self.top_margin, 0, address=True)
                self.saved_cursor = (self.cr, self.cc, *self.pen)
        elif final == "m":
            self._sgr(params)
        elif final == "h" or final == "l":
            self._set_mode(private, final == "h", params)
        elif final == "n" and not private:                 # DSR
            if params and params[0] == 6:
                self.send_data.emit(f"\x1b[{self.cr + 1};{self.cc + 1}R")
        elif final == "c":
            # DA: answers keep old-school programs from waiting forever.
            if private:
                self.send_data.emit("\x1b[?62;1;2;6;9c")
            elif params and params[0] == 0 and self._collect.startswith(">"):
                self.send_data.emit("\x1b[>1;95;0c")       # DA2
            else:
                self.send_data.emit("\x1b[?1;2c")          # DA1, a VT102 with options
        elif final == "s" and not private:
            self.saved_cursor = (self.cr, self.cc, *self.pen)
        elif final == "u" and not private:
            self.cr, self.cc = self.saved_cursor[0], self.saved_cursor[1]
            self.pen = self.saved_cursor[2:]
        elif final == "t":
            pass                                           # window manipulation: ignored
        elif final == "Z":
            self._back_tabs(self._at(params, 0))

    def _set_mode(self, private: bool, on: bool, params: list[int]) -> None:
        if not private:
            return                                          # IRM etc.: not emulated
        for mode in params or [0]:
            if mode == 1:
                self.app_cursor = on                        # application cursor keys
            elif mode == 6:
                self.origin_mode = on
                self._move(self.top_margin if on else 0, 0, address=True)
            elif mode == 7:
                self.autowrap = on
            elif mode == 12:
                pass                                        # blink, already fast enough
            elif mode == 25:
                self.cursor_visible = on
            elif mode == 1048:
                if on:
                    self.saved_cursor = (self.cr, self.cc, *self.pen)
                else:
                    self.cr, self.cc = self.saved_cursor[0], self.saved_cursor[1]
            elif mode in (47, 1047, 1049):
                self._alternate(on, full=mode == 1049)
            elif mode == 2004:
                self.bracketed_paste = on
            # mouse modes (1000..1016) and the rest: noted, not forwarded

    def _alternate(self, on: bool, full: bool) -> None:
        """The screen nano lives on: a fresh grid, the shell's grid kept aside."""
        if on and not self.alternate:
            saved = (self.lines, self.cr, self.cc, self.top_margin, self.bottom_margin,
                     self.saved_cursor, self.wrap_next)
            keep_modes = (self.app_cursor, self.autowrap, self.bracketed_paste)
            self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
            self.cr = self.cc = 0
            self.top_margin, self.bottom_margin = 0, self.rows - 1
            self.saved_cursor = (0, 0, -1, -1, 0)
            self.wrap_next = False
            if full:
                self.saved_cursor = (self.cr, self.cc, *self.pen)
            self._main_state = (saved, keep_modes)
            self.alternate = True
        elif on is False and self.alternate and self._main_state is not None:
            (saved, keep_modes) = self._main_state
            (self.lines, self.cr, self.cc, self.top_margin, self.bottom_margin,
             self.saved_cursor, self.wrap_next) = saved
            (self.app_cursor, self.autowrap, self.bracketed_paste) = keep_modes
            self._main_state = None
            self.alternate = False
        self._bar.setVisible(not self.alternate and bool(self.history))
        self.update()

    # ------------------------------------------------------------------
    # screen surgery
    # ------------------------------------------------------------------

    def _move(self, row: int, col: int, address: bool = False) -> None:
        if address and self.origin_mode:
            row += self.top_margin
        self.cr = max(0, min(row, self.rows - 1))
        self.cc = max(0, min(col, self.cols - 1))
        self.wrap_next = False

    def _tab(self) -> None:
        stop = next((t for t in sorted(self.tabs) if t > self.cc), self.cols - 1)
        self.cc = min(stop, self.cols - 1)
        self.wrap_next = False

    def _back_tabs(self, count: int) -> None:
        for _ in range(max(1, count)):
            earlier = [t for t in self.tabs if t < self.cc]
            self.cc = max(0, earlier[-1] if earlier else 0)

    def _put(self, ch: str) -> None:
        if self.wrap_next:
            self.cc = 0
            self._line_feed()
        if self.bottom_margin < self.rows - 1 and self.cr > self.bottom_margin:
            self.cr = self.bottom_margin
        elif self.origin_mode and self.cr < self.top_margin:
            self.cr = self.top_margin
        self.lines[self.cr][self.cc] = (ch, *self.pen)
        if self.cc + 1 >= self.cols:
            if self.autowrap:
                self.cc = self.cols - 1
                self.wrap_next = True
        else:
            self.cc += 1

    def _line_feed(self) -> None:
        if self.cr == self.bottom_margin:
            self._scroll_up(1, force=True)
        elif self.cr < self.rows - 1:
            self.cr += 1

    def _reverse_index(self) -> None:
        if self.cr == self.top_margin:
            self._scroll_down(1, force=True)
        elif self.cr > 0:
            self.cr -= 1

    def _scroll_up(self, count: int, force: bool = False) -> None:
        """Roll the region up: lines leave at the top, blanks arrive at the bottom."""
        top, bottom = self.top_margin, self.bottom_margin
        count = min(count, bottom - top + 1)
        gone = [row[:] for row in self.lines[top:top + count]]
        for _ in range(count):
            del self.lines[top]
            self.lines.insert(bottom, _blank_row(self.cols))
        if top == 0 and not self.alternate and (force or bottom == self.rows - 1):
            self.history.extend(gone)
            if len(self.history) > HISTORY_MAX:
                del self.history[:len(self.history) - HISTORY_MAX]
            self._update_bar()

    def _scroll_down(self, count: int, force: bool = False) -> None:
        top, bottom = self.top_margin, self.bottom_margin
        count = min(count, bottom - top + 1)
        for _ in range(count):
            del self.lines[bottom]
            self.lines.insert(top, _blank_row(self.cols))

    def _scroll_region(self, delta: int) -> None:
        """SU (positive) scrolls content up, SD down, inside the margins."""
        if delta > 0:
            self._scroll_up(delta)
        elif delta < 0:
            self._scroll_down(-delta)

    def _insert_lines(self, count: int) -> None:
        if not (self.top_margin <= self.cr <= self.bottom_margin):
            return
        for _ in range(min(count, self.bottom_margin - self.cr + 1)):
            del self.lines[self.bottom_margin]
            self.lines.insert(self.cr, _blank_row(self.cols))

    def _delete_lines(self, count: int) -> None:
        if not (self.top_margin <= self.cr <= self.bottom_margin):
            return
        for _ in range(min(count, self.bottom_margin - self.cr + 1)):
            del self.lines[self.cr]
            self.lines.insert(self.bottom_margin, _blank_row(self.cols))

    def _insert_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        blank = _blank_cell()
        for _ in range(min(count, self.cols - self.cc)):
            row.insert(self.cc, blank)
            row.pop()

    def _delete_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        for _ in range(min(count, self.cols - self.cc)):
            del row[self.cc]
            row.append(_blank_cell())

    def _erase_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        blank = _blank_cell()
        for col in range(self.cc, min(self.cc + max(1, count), self.cols)):
            row[col] = blank

    def _erase_line(self, mode: int) -> None:
        # Erased cells take the pen's background, so a reverse-video line that
        # clears "to the end" stays inverted right to the margin.
        blank = _blank_cell(bg=self.pen[1])
        row = self.lines[self.cr]
        if mode == 0:
            for col in range(self.cc, self.cols):
                row[col] = blank
        elif mode == 1:
            for col in range(0, min(self.cc + 1, self.cols)):
                row[col] = blank
        else:
            self.lines[self.cr] = [_blank_cell(bg=self.pen[1]) for _ in range(self.cols)]

    def _erase_display(self, mode: int) -> None:
        blank = _blank_cell(bg=self.pen[1])
        if mode == 0:
            self._erase_line(0)
            for row in self.lines[self.cr + 1:]:
                row[:] = [blank] * self.cols
        elif mode == 1:
            self._erase_line(1)
            for row in self.lines[:self.cr]:
                row[:] = [blank] * self.cols
        else:
            self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
            if mode == 3:
                self.history.clear()
                self._update_bar()

    def _blank_cell(self, fg: int = -1, bg: int = -1, flags: int = 0):
        return _blank_cell(fg, bg, flags)

    # ------------------------------------------------------------------
    # colours and attributes
    # ------------------------------------------------------------------

    def _sgr(self, params: list[int]) -> None:
        fg, bg, flags = self.pen
        index = 0
        values = params or [0]
        while index < len(values):
            code = values[index]
            if code == 0:
                fg, bg, flags = -1, -1, 0
            elif code == 1:
                flags |= BOLD
            elif code == 2:
                flags |= DIM
            elif code == 3:
                flags |= ITALIC
            elif code == 4:
                flags |= UNDERLINE
            elif code == 5 or code == 6:
                flags |= BLINK
            elif code == 7:
                flags |= REVERSE
            elif code == 8:
                flags |= HIDDEN
            elif code == 9:
                flags |= STRIKE
            elif code == 21:
                flags &= ~UNDERLINE
            elif code in (22, 24):
                flags &= ~(BOLD | DIM) if code == 22 else ~UNDERLINE
            elif code == 23:
                flags &= ~ITALIC
            elif code == 27:
                flags &= ~REVERSE
            elif code == 28:
                flags &= ~HIDDEN
            elif code == 29:
                flags &= ~STRIKE
            elif 30 <= code <= 37:
                fg = code - 30
            elif code == 38:
                fg, index = self._extended_color(values, index, fg)
            elif code == 39:
                fg = -1
            elif 40 <= code <= 47:
                bg = code - 40
            elif code == 48:
                bg, index = self._extended_color(values, index, bg)
            elif code == 49:
                bg = -1
            elif 90 <= code <= 97:
                fg = code - 82                       # bright 8..15
            elif 100 <= code <= 107:
                bg = code - 92
            # 53 overline and the like: noticed, not drawn
            index += 1
        self.pen = (fg, bg, flags)

    def _extended_color(self, values: list[int], index: int, current: int) -> tuple[int, int]:
        """38/48 with its sub-parameters; returns the new pen value and cursor."""
        kind = values[index + 1] if index + 1 < len(values) else 5
        if kind == 5:
            number = values[index + 2] if index + 2 < len(values) else 0
            return number, index + 2
        if kind == 2:
            red = values[index + 2] if index + 2 < len(values) else 0
            green = values[index + 3] if index + 3 < len(values) else 0
            blue = values[index + 4] if index + 4 < len(values) else 0
            return 256 + (red << 16 | green << 8 | blue), index + 4
        return current, index

    # ------------------------------------------------------------------
    # scrolling the view back into history
    # ------------------------------------------------------------------

    def _update_bar(self) -> None:
        room = 0 if self.alternate else len(self.history)
        self._bar.setMaximum(room)
        self._bar.setVisible(room > 0)
        self._bar.setGeometry(self.width() - self._bar.sizeHint().width() - 2, 2,
                              self._bar.sizeHint().width(), self.height() - 4)
        if self._back > room:
            self._back = room
        self._bar.setValue(self._back)

    def _scroll_to(self, value: int) -> None:
        self._back = value
        self.update()

    def wheelEvent(self, event) -> None:
        if not self.history or self.alternate:
            return
        steps = 3 * (1 if event.angleDelta().y() < 0 else -1)
        self._back = max(0, min(len(self.history), self._back + steps))
        self._bar.setValue(self._back)
        self.update()
        event.accept()

    def _visible_rows(self) -> list:
        """The lines under the window, history first when scrolled back."""
        history = [] if self.alternate else self.history
        total = history + self.lines
        end = len(total) - self._back
        start = max(0, end - self.rows)
        shown = total[start:end]
        return [_blank_row(self.cols) for _ in range(self.rows - len(shown))] + shown

    def _view_top_index(self) -> int:
        history = [] if self.alternate else self.history
        total = history + self.lines
        return max(0, len(total) - self._back - self.rows)

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(DEFAULT_BG))
        rows = self._visible_rows()
        view_top = self._view_top_index()
        fonts: dict[int, QFont] = {}
        for row_index, row in enumerate(rows):
            y = row_index * self._ch
            col = 0
            while col < len(row):
                cell = row[col]
                fg, bg, flags = cell[1], cell[2], cell[3]
                run = col
                while run + 1 < len(row) and (row[run + 1][1], row[run + 1][2], row[run + 1][3]) == (fg, bg, flags):
                    run += 1
                # background first, then the glyphs of the run in its font;
                # reverse video swaps the two, defaults crossing over
                if flags & REVERSE:
                    bg_color = _cell_color(fg, QColor(DEFAULT_FG))
                    text_color = _cell_color(bg, QColor(DEFAULT_BG))
                else:
                    bg_color = _cell_color(bg, QColor(DEFAULT_BG))
                    text_color = _cell_color(fg, QColor(DEFAULT_FG))
                    if flags & BOLD and 0 <= fg < 8:
                        text_color = PALETTE[fg + 8]      # bold 0..7 picks the bright colour
                    if flags & DIM:
                        text_color = text_color.darker(150)
                if bg_color != QColor(DEFAULT_BG):
                    painter.fillRect(QRect(col * self._cw, y, (run - col + 1) * self._cw, self._ch), bg_color)
                text = "".join(row[c][0] for c in range(col, run + 1)).rstrip()
                if flags & HIDDEN:
                    text = ""
                if text:
                    font = fonts.get(flags)
                    if font is None:
                        font = self._font_for(flags, fonts)
                    painter.setFont(font)
                    painter.setPen(text_color)
                    painter.drawText(QRect(col * self._cw, y, (run - col + 1) * self._cw, self._ch),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                                     text)
                col = run + 1
        self._paint_selection(painter, view_top, rows)
        self._paint_cursor(painter)
        painter.end()

    def _font_for(self, flags: int, cache: dict) -> QFont:
        font = QFont(self._font)
        font.setBold(bool(flags & BOLD))
        font.setItalic(bool(flags & ITALIC))
        font.setUnderline(bool(flags & UNDERLINE))
        font.setStrikeOut(bool(flags & STRIKE))
        cache[flags] = font
        return font

    def _paint_selection(self, painter: QPainter, view_top: int, rows: list) -> None:
        if self._select_from is None or self._select_to is None:
            return
        first, last = self._selection_span()
        for absolute in range(first[0], last[0] + 1):
            row_index = absolute - view_top
            if not (0 <= row_index < len(rows)):
                continue
            start_col = 0
            end_col = self.cols - 1
            if absolute == first[0]:
                start_col = first[1]
            if absolute == last[0]:
                end_col = last[1]
            if start_col > end_col:
                continue
            painter.fillRect(QRect(start_col * self._cw, row_index * self._ch,
                                   (end_col - start_col + 1) * self._cw, self._ch),
                             SELECTION_COLOR)

    def _paint_cursor(self, painter: QPainter) -> None:
        if not self.cursor_visible or self._back:
            return
        if not self._blink_on and self.hasFocus():
            return
        rect = QRect(self.cc * self._cw, self.cr * self._ch, self._cw, self._ch)
        if self.hasFocus():
            # a solid block in the cell's own colours, so it reads on any background
            cell = self.lines[self.cr][self.cc]
            if cell[3] & REVERSE:
                fill, glyph = _cell_color(cell[2], QColor(DEFAULT_BG)), _cell_color(cell[1], QColor(DEFAULT_FG))
            else:
                fill, glyph = _cell_color(cell[1], QColor(DEFAULT_FG)), _cell_color(cell[2], QColor(DEFAULT_BG))
            painter.fillRect(rect, fill)
            painter.setPen(glyph)
            painter.drawText(rect.adjusted(1, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, cell[0])
        else:
            painter.setPen(QColor(DEFAULT_FG))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_from = self._select_to = self._cell_at(event.position().toPoint())
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._select_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._select_to = self._cell_at(event.position().toPoint())
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        cell = self._cell_at(event.position().toPoint())
        absolute = self._view_top_index() + cell[0]
        history = [] if self.alternate else self.history
        total = history + self.lines
        if 0 <= absolute < len(total):
            row = total[absolute]
            col = cell[1]
            if not row[col][0].strip():
                return
            start = end = col
            word = lambda ch: ch.isalnum() or ch in "._-/"
            while start > 0 and word(row[start - 1][0]):
                start -= 1
            while end + 1 < len(row) and word(row[end + 1][0]):
                end += 1
            self._select_from = (cell[0], start)
            self._select_to = (cell[0], end)
            self.update()

    def _cell_at(self, point) -> tuple[int, int]:
        row = max(0, min(self.rows - 1, point.y() // self._ch))
        col = max(0, min(self.cols - 1, point.x() // self._cw))
        return (int(row), int(col))

    def _selection_span(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Selected cells as (absolute row, col) ends, first always before last."""
        view_top = self._view_top_index()
        a = (view_top + self._select_from[0], self._select_from[1])
        b = (view_top + self._select_to[0], self._select_to[1])
        if a > b:
            a, b = b, a
        return a, b

    def selected_text(self) -> str:
        if self._select_from is None or self._select_to is None:
            return ""
        first, last = self._selection_span()
        history = [] if self.alternate else self.history
        total = history + self.lines
        out: list[str] = []
        for absolute in range(first[0], last[0] + 1):
            if not (0 <= absolute < len(total)):
                continue
            row = total[absolute]
            start = first[1] if absolute == first[0] else 0
            end = last[1] if absolute == last[0] else len(row) - 1
            out.append("".join(row[col][0] for col in range(start, end + 1)).rstrip())
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)

    # ------------------------------------------------------------------
    # typing
    # ------------------------------------------------------------------

    def focusInEvent(self, event) -> None:
        self._blink_on = True
        self._blink.start()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._blink.stop()
        self._blink_on = True
        self.update()
        super().focusOutEvent(event)

    def _toggle_blink(self) -> None:
        if self.cursor_visible and not self.alternate:
            self._blink_on = not self._blink_on
            self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        # Clipboard keys carry the shift so they cannot eat ^C and ^V.
        if ctrl and shift and key == Qt.Key.Key_C:
            text = self.selected_text()
            if text:
                QApplication.clipboard().setText(text)
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert):
            self._paste(QApplication.clipboard().text())
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock):
            return

        data = self._key_bytes(key, mods, event.text())
        if data is None:
            return
        if self._back:
            self._back = 0
            self._bar.setValue(0)
        self.send_data.emit(data)

    def _paste(self, text: str) -> None:
        if not text:
            return
        if self.bracketed_paste:
            self.send_data.emit("\x1b[200~" + text.replace("\r\n", "\r").replace("\n", "\r") + "\x1b[201~")
        else:
            self.send_data.emit(text.replace("\r\n", "\r").replace("\n", "\r"))

    def _key_bytes(self, key: int, mods, text: str) -> str | None:
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        modifier_code = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return "\r"
        if key == Qt.Key.Key_Tab:
            return "\x1b[Z" if shift else "\t"
        if key == Qt.Key.Key_Backtab:
            return "\x1b[Z"
        if key == Qt.Key.Key_Backspace:
            return "\x08" if ctrl else "\x7f"
        if key == Qt.Key.Key_Escape:
            return "\x1b\x1b" if alt else "\x1b"

        arrows = {Qt.Key.Key_Left: "D", Qt.Key.Key_Right: "C",
                  Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B"}
        if key in arrows:
            letter = arrows[key]
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}{letter}"
            return (f"\x1bO{letter}" if self.app_cursor else f"\x1b[{letter}")
        if key == Qt.Key.Key_Home:
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}H"
            return "\x1bOH" if self.app_cursor else "\x1b[H"
        if key == Qt.Key.Key_End:
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}F"
            return "\x1bOF" if self.app_cursor else "\x1b[F"
        simple = {Qt.Key.Key_Insert: "2", Qt.Key.Key_Delete: "3",
                  Qt.Key.Key_PageUp: "5", Qt.Key.Key_PageDown: "6"}
        if key in simple:
            if modifier_code > 1:
                return f"\x1b[{simple[key]};{modifier_code}~"
            return f"\x1b[{simple[key]}~"
        function = {Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ",
                    Qt.Key.Key_F3: "\x1bOR", Qt.Key.Key_F4: "\x1bOS",
                    Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~",
                    Qt.Key.Key_F7: "\x1b[18~", Qt.Key.Key_F8: "\x1b[19~",
                    Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~",
                    Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~"}
        if key in function:
            return function[key]

        # Ctrl+letter is the control byte whatever Qt reports as text, and it
        # must be settled before the fall-through below (nano's ^G, ^X, ^K...).
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key - Qt.Key.Key_A + 1)
        if ctrl:
            literal = {Qt.Key.Key_BracketLeft: "\x1b", Qt.Key.Key_BracketRight: "\x1d",
                       Qt.Key.Key_Backslash: "\x1c", Qt.Key.Key_Space: "\x00",
                       Qt.Key.Key_Question: "\x7f"}
            if key in literal:
                return literal[key]

        if text:
            ch = text[0]
            if ctrl and len(text) == 1 and 32 <= ord(ch) < 127:
                return "\x00" if ch == " " else chr(ord(ch) & 0x1F)
            if ord(ch) >= 32:
                return ("\x1b" + ch) if alt else ch
        return None
