#!/usr/bin/env python3

"""Reusable UI pieces: the themed toggles, the collapsible group, the file tree and the popup dialogs."""


from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QPointF,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionTab,
    QTabBar,
    QTabWidget,
    QToolTip,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from manager_const import find_asset, scaled_sheet, zoom_size


# --- tab captions and the pictures a crowded bar falls back on ---

#: What a tab spends on itself besides its text - the sheet's side padding,
#: its borders and the small gap before the icon - counted at 100% zoom.
_TAB_CHROME = 34
#: Narrower than this a caption could not be read anyway (100% zoom): below
#: it the tab drops its text entirely and wears its icon alone.
_TAB_MIN_TEXT = 24

#: The pictures this module paints itself, made once and kept: the bar
#: refits on every resize, and repainting the same glyphs each time is waste.
#: File-backed marks live here too, so an unreadable asset is tried once.
_DRAWN_ICONS: dict[str, QIcon] = {}

#: Every tab picture is painted at least this wide (at 100% zoom), whatever
#: the bar's own icon size measures. The tab keeps the size the icon size
#: gives it - only the drawn mark grows, so a bigger picture never means a
#: bigger tab.
_TAB_ICON_DRAW = 24


def tab_icon_side(bar: QTabBar) -> int:
    """The edge a tab's picture is drawn at: the bar's icon size, but never
    smaller than the comfortable mark. Zoomed with the rest of the bar."""
    return max(int(bar.iconSize().width()), zoom_size(_TAB_ICON_DRAW))


def _drawn_icon(key: str, paint: Callable[[QPainter], None], filename: str = "") -> QIcon:
    """One tab picture, made once and remembered; a shipped asset wins, the paint glyph is the fallback so a bare checkout still draws something."""
    icon = _DRAWN_ICONS.get(key)
    if icon is None:
        path = find_asset(filename) if filename else None
        if path is not None:
            icon = QIcon(str(path))
        else:
            canvas = QPixmap(32, 32)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint(p)
            p.end()
            icon = QIcon(canvas)
        _DRAWN_ICONS[key] = icon
    return icon


def default_tab_icon() -> QIcon:
    """The mark for any tab that arrives without one: the shipped defaulttabicon.png, else four rounded tiles in the bar's grey with one accent corner."""
    def paint(p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for n, (x, y) in enumerate(((5.0, 5.0), (18.5, 5.0),
                                    (5.0, 18.5), (18.5, 18.5))):
            p.setBrush(QColor("#c51a4a") if n == 0 else QColor("#9db4c8"))
            p.drawRoundedRect(QRectF(x, y, 8.5, 8.5), 2.2, 2.2)
    return _drawn_icon("default_tab", paint, "defaulttabicon.png")


def terminal_tab_icon() -> QIcon:
    """The picture the SSH terminal tab wears: the shipped terminalicon.png, else a drawn shell frame with a raspberry prompt."""
    def paint(p: QPainter) -> None:
        frame = QPen(QColor("#9db4c8"))
        frame.setWidthF(2.4)
        p.setPen(frame)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(4.0, 6.0, 24.0, 20.0), 3.0, 3.0)
        prompt = QPen(QColor("#c51a4a"))
        prompt.setWidthF(2.2)
        prompt.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(prompt)
        p.drawLine(QPointF(8.5, 12.5), QPointF(12.5, 16.0))
        p.drawLine(QPointF(12.5, 16.0), QPointF(8.5, 19.5))
        p.drawLine(QPointF(14.5, 19.5), QPointF(20.0, 19.5))
    return _drawn_icon("terminal_tab", paint, "terminalicon.png")


def settings_tab_icon() -> QIcon:
    """The cog the pinned Settings tab always wears: a rim with eight teeth, kept at every width because Settings is never squeezed away."""
    def paint(p: QPainter) -> None:
        pen = QPen(QColor("#d6d6d6"))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # A thick rim with short stubs standing out of it: at 18px anything
        # longer reads as a sun, not a cog.
        p.drawEllipse(QRectF(9.0, 9.0, 14.0, 14.0))
        for step in range(8):
            angle = math.radians(step * 45)
            dx, dy = math.cos(angle), math.sin(angle)
            p.drawLine(QPointF(16 + 8.2 * dx, 16 + 8.2 * dy),
                       QPointF(16 + 11.2 * dx, 16 + 11.2 * dy))
    return _drawn_icon("settings_tab", paint)


def tab_caption(bar: QTabBar, index: int) -> str:
    """The full name a tab carries: the caption stored in its data (kept whole even when the bar elides the text), or its plain text."""
    try:
        stored = bar.tabData(index)
    except (IndexError, RuntimeError):
        stored = None
    if stored is not None and str(stored) != "":
        return str(stored)
    return bar.tabText(index)


#: Air between a tab's icon and its caption, and the give that keeps an
#: elided caption off the capsule's edge - both before zoom.
_TAB_LABEL_GAP = 6
_TAB_LABEL_SLACK = 4


def _draw_centered_tab_label(opt: QStyleOptionTab, painter: QPainter, bar: QTabBar) -> None:
    """Draw one tab's icon and caption as one centred block, holding out room for the side buttons; the styled bar would pin the icon left and read off-balance."""
    rect = opt.rect
    left = bar.tabButton(opt.tabIndex, QTabBar.ButtonPosition.LeftSide)
    right = bar.tabButton(opt.tabIndex, QTabBar.ButtonPosition.RightSide)
    pad = zoom_size(2)
    if left is not None and left.isVisible():
        rect = rect.adjusted(left.width() + pad, 0, 0, 0)
    if right is not None and right.isVisible():
        rect = rect.adjusted(0, 0, -(right.width() + pad), 0)
    has_icon = not opt.icon.isNull()
    # The mark draws bigger than the bar measures its icons (see
    # _TAB_ICON_DRAW): a clearer picture without a taller or wider tab.
    draw_side = tab_icon_side(bar) if has_icon else 0
    icon_w = draw_side
    gap = zoom_size(_TAB_LABEL_GAP) if has_icon and opt.text else 0
    # (QStyleOptionTab's font copy is invisible to PyQt; the bar's own font
    # is what initStyleOption put in there.)
    font = bar.font()
    metrics = QFontMetrics(font)
    avail = max(0, rect.width() - icon_w - gap - zoom_size(_TAB_LABEL_SLACK))
    text = metrics.elidedText(opt.text, bar.elideMode(), avail)
    text_w = min(avail, metrics.horizontalAdvance(text))
    total = icon_w + gap + text_w
    x = rect.left() + max(0, (rect.width() - total) // 2)
    middle = rect.center().y()
    painter.save()
    painter.setFont(font)
    if has_icon:
        pix = opt.icon.pixmap(QSize(draw_side, draw_side))
        painter.drawPixmap(x, middle - pix.height() // 2, pix)
        x += icon_w + gap
    if text:
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        if not enabled:
            color = QColor("#666666")
        elif opt.state & QStyle.StateFlag.State_Selected:
            color = QColor("#e0e0e0")
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            color = QColor("#ffffff")
        else:
            color = QColor("#aaaaaa")
        painter.setPen(QPen(color))
        painter.drawText(x, rect.top(), text_w + zoom_size(_TAB_LABEL_SLACK),
                         rect.height(),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         text)
    painter.restore()


class RightPinnedTabBar(QTabBar):
    """A tab bar that keeps the last tab pinned to the far right edge.

    An empty, disabled *gap* tab is widened to soak up all the spare room, so
    the tab that sits just after it - the Settings tab - is pushed against the
    right edge and reads as detached from the reorderable tabs on the left.
    ``set_gap_index`` tells the bar which tab is the spacer; its width is
    recomputed from the bar's own width every time a tab moves or the window
    is resized, so the pinned tab always ends up flush right.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gap_index = -1
        self._refitting = False

    def set_gap_index(self, index: int) -> None:
        old = self.gap_index
        self.gap_index = index
        # Whatever the old index points at now, it is not the gap any more -
        # and only the gap may sit disabled on this bar. Hand the tab that
        # inherited the position (a reorder can leave one sitting exactly
        # where the blink last struck) its clicks back.
        if 0 <= old < self.count() and old != index:
            self.setTabEnabled(old, True)
        self.refit()

    def tabSizeHint(self, index: int) -> QSize:
        """Equalise the movable tabs to the widest, stretch the gap to soak spare room, and leave Settings at its own width."""
        natural = QTabBar.tabSizeHint(self, index)
        if not (0 <= self.gap_index < self.count()):
            return natural              # nothing pinned yet: leave tabs natural
        count = self.count()
        settings_index = self.gap_index + 1
        if index == self.gap_index:
            used = sum(self.tabSizeHint(i).width() for i in range(count)
                       if i != index)
            return QSize(max(0, self.width() - used), natural.height())
        if index == settings_index:
            return natural
        movable = [i for i in range(count)
                   if i != self.gap_index and i != settings_index]
        if movable:
            widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
            # Never let the movable tabs eat the room reserved for Settings:
            # the squeeze aims captions at an equal share, and this caps the
            # drawn width at that share even if the aim comes out a little
            # wide. The label paints centred into whatever rect results.
            share = max(1, (self.width() - 2
                            - QTabBar.tabSizeHint(self, settings_index).width())
                        // len(movable))
            return QSize(min(widest, share), natural.height())
        return natural

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.refit()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Paint every tab as the sheet's capsule plus a centred label; the bar takes over painting so the icon-and-caption block centres as one piece."""
        painter = QPainter(self)
        style = self.style()
        opt = QStyleOptionTab()
        for i in range(self.count()):
            if not self.tabRect(i).isValid():
                continue
            self.initStyleOption(opt, i)
            style.drawControl(QStyle.ControlElement.CE_TabBarTabShape,
                              opt, painter, self)
            _draw_centered_tab_label(opt, painter, self)
        painter.end()

    def refit(self) -> None:
        """Re-measure the bar and shorten the captions that no longer fit, dropping a tab to its icon when even an ellipsis leaves nothing readable."""
        if self._refitting:
            return
        self._refitting = True
        try:
            self._refit()
        finally:
            self._refitting = False
        self._relayout()

    def _refit(self) -> None:
        if not (0 <= self.gap_index < self.count()):
            return                      # nothing pinned yet: measure later
        if self.gap_index != self.count() - 2:
            # The pinned pair is out of date - a tab went in or came out and
            # the owner has not re-pinned yet. Squeezing on the stale index
            # could treat the real Settings tab as a movable one, and
            # Settings must never be touched: leave the texts alone.
            return
        settings_index = self.gap_index + 1
        movable = [i for i in range(self.count())
                   if i != self.gap_index and i != settings_index]
        if not movable:
            return
        # Only the gap ever sits disabled here; if a stale blink greyed a
        # real tab out earlier (see _relayout), this is where it heals.
        if not self.isTabEnabled(settings_index):
            self.setTabEnabled(settings_index, True)
        for i in movable:
            if not self.isTabEnabled(i):
                self.setTabEnabled(i, True)
        # Every tab wears something, even when its name comes off later.
        if self.tabIcon(settings_index).isNull():
            self.setTabIcon(settings_index, default_tab_icon())
        for i in movable:
            if self.tabIcon(i).isNull():
                self.setTabIcon(i, default_tab_icon())
            # Measure from the full captions, whatever an earlier pass made
            # of them; the real names live in the tab's data, untouched.
            caption = tab_caption(self, i)
            if self.tabText(i) != caption:
                self.setTabText(i, caption)
        reserve = QTabBar.tabSizeHint(self, settings_index).width()
        room = max(0, self.width() - 2 - reserve)
        if self.width() < zoom_size(50):
            return   # the bar has no real width yet (window still hidden
                     # or being built): restore only, never squeeze
        widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
        if widest * len(movable) <= room:
            return                      # everything still fits in full
        per = max(1, room // len(movable))
        metrics = QFontMetrics(self.font())
        # The chrome constant aims the caption at a share of the bar; the
        # sheet's real padding is never exactly that, so each pass measures
        # what the elided captions actually cost and shaves the next aim by
        # the overage. Two or three rounds settle; the loop cap is a brake.
        # The drawn mark can be wider than the bar's measured icon, and the
        # caption has to clear the picture as painted, not as measured.
        text_room = per - tab_icon_side(self) - zoom_size(_TAB_CHROME)
        for _ in range(4):
            icon_only = text_room < zoom_size(_TAB_MIN_TEXT)
            for i in movable:
                caption = tab_caption(self, i)
                if icon_only:
                    self.setTabText(i, "")
                else:
                    text = metrics.elidedText(caption, Qt.TextElideMode.ElideRight,
                                              max(8, text_room))
                    self.setTabText(i, str(text))
                # Only a tab whose name was cut down explains itself on hover,
                # so one that still shows everything keeps whatever tooltip it
                # already had (the SSH tab's connection title among them).
                self.setTabToolTip(i, caption)
            if icon_only:
                break
            widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
            if widest <= per:
                break
            text_room -= widest - per

    def _relayout(self) -> None:
        """Force the bar to re-run tabSizeHint by blinking the gap tab's enabled state - only when the index provably is the gap."""
        if 0 <= self.gap_index < self.count() and self.gap_index == self.count() - 2:
            self.setTabEnabled(self.gap_index, True)
            self.setTabEnabled(self.gap_index, False)


class RightPinnedTabWidget(QTabWidget):
    """The main tab widget, announcing every add and remove.

    The pinned Settings tab depends on a gap whose index is stored in the bar;
    adding or removing a tab to the left of that gap shifts every later index,
    so the owner has to re-pin. This Qt build exposes no ``countChanged`` signal
    on the tab bar (only ``tabRemoved``), so the widget fires its own
    :data:`tabCountChanged` from the ``addTab``/``insertTab``/``removeTab``/
    ``clear`` calls the app actually makes. Qt never adds a tab to itself, so
    wrapping those Python entry points catches every change in the count."""

    tabCountChanged = pyqtSignal(int)

    def _announce(self) -> None:
        self.tabCountChanged.emit(self.count())

    def _capture(self, index: int, label: object) -> None:
        """Store the tab's full name in its data the moment it appears, so a squeezed label never reaches name lookups."""
        if 0 <= index < self.count() and str(label or "") != "":
            self.tabBar().setTabData(index, str(label))

    def addTab(self, widget: QWidget, label: str = "") -> int:
        index = super().addTab(widget, label)
        self._capture(index, label)
        self._announce()
        return index

    def insertTab(self, index: int, widget: QWidget, label: str = "") -> int:
        placed = super().insertTab(index, widget, label)
        self._capture(placed, label)
        self._announce()
        return placed

    def setTabText(self, index: int, text: str) -> None:
        super().setTabText(index, text)
        # A rename is a new caption, not a shortened one: refresh the data
        # and let the bar re-measure what every tab can now afford.
        if 0 <= index < self.count():
            self.tabBar().setTabData(index, str(text))
        self.tabBar().refit()

    def removeTab(self, index: int) -> None:
        super().removeTab(index)
        self._announce()

    def clear(self) -> None:
        super().clear()
        self._announce()


class TabTransportDots(QWidget):
    """Two small status dots for a tab's label: 's' for SSH, 'f' for SFTP.

    Placed beside a tab's text in the tab bar so the live connection state is
    visible from whichever tab is showing. Each dot is coloured by the app's
    shared status-dot style (green connected, red not) and carries a one-letter
    mark, :meth:`set_state` recolouring them from the current transport states.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        # The dots are rebuilt whenever the zoom changes, so reading the zoom in
        # force here is enough to keep them in step with the tab's own text.
        layout.setContentsMargins(zoom_size(3), 0, zoom_size(3), 0)
        layout.setSpacing(zoom_size(2))
        self._dots: dict[str, tuple[QLabel, QLabel]] = {}
        for transport, letter in (("ssh", "s"), ("sftp", "f")):
            dot = QLabel("\u25CF")
            dot.setObjectName("statusDotOffline")
            dot.setToolTip(f"{transport.upper()}: not connected")
            mark = QLabel(letter)
            mark.setStyleSheet(scaled_sheet("color: #aaaaaa; font-size: 8pt;"))
            layout.addWidget(dot)
            layout.addWidget(mark)
            self._dots[transport] = (dot, mark)

    def set_state(self, states: dict[str, bool]) -> None:
        """Recolour each dot from the app's live transport states."""
        for transport, (dot, _mark) in self._dots.items():
            online = bool(states.get(transport))
            name = "statusDotOnline" if online else "statusDotOffline"
            if dot.objectName() != name:
                dot.setObjectName(name)
                dot.style().unpolish(dot)
                dot.style().polish(dot)
            dot.setToolTip(f"{transport.upper()}: "
                           + ("connected" if online else "not connected"))


class ToggleSwitch(QCheckBox):
    """A pill-shaped on/off switch that replaces checkboxes in Settings,
    painted in the raspberry accent colours of the app theme."""

    TRACK_W = 40
    TRACK_H = 20

    def _track(self) -> tuple[int, int]:
        """The pill's size at the current zoom; painted not styled, so the hand-written sizes are asked of manager_const."""
        return zoom_size(self.TRACK_W), zoom_size(self.TRACK_H)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._handle = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"handle_pos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._handle)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        """Set the state and move the painted handle too, so a blocked-signals rebuild still shows the switch on."""
        if self.signalsBlocked():
            self._anim.stop()
            self._handle = 1.0 if bool(checked) else 0.0
            self.update()
        super().setChecked(bool(checked))

    def _get_handle(self) -> float:
        return self._handle

    def _set_handle(self, value: float) -> None:
        self._handle = float(value)
        self.update()

    handle_pos = pyqtProperty(float, fget=_get_handle, fset=_set_handle)

    def minimumSizeHint(self) -> QSize:
        fm = self.fontMetrics()
        track_w, track_h = self._track()
        width = track_w
        if self.text():
            width += zoom_size(8) + fm.horizontalAdvance(self.text())
        return QSize(width, max(track_h, fm.height()) + zoom_size(4))

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track_w, track_h = self._track()
        track_top = (self.height() - track_h) / 2
        if on:
            track, border = QColor("#c51a4a"), QColor("#8f1236")
        else:
            track, border = QColor("#2b2b2b"), QColor("#4a4a4a")
        if not enabled:
            track.setAlpha(120)
            border.setAlpha(120)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(track))
        # The handle is a fixed share of the track - 65% of the radius, which is
        # what the old hand-written 3.5 px inset came to - so it keeps its shape
        # at every zoom instead of growing a border of its own.
        radius = track_h / 2
        painter.drawRoundedRect(
            QRectF(0.5, track_top + 0.5, track_w - 1, track_h - 1), radius, radius
        )
        handle_r = radius * 0.65
        travel = track_w - track_h
        center_x = radius + self._handle * travel
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5f5f5") if enabled else QColor("#888888"))
        painter.drawEllipse(
            QRectF(center_x - handle_r, track_top + radius - handle_r, handle_r * 2, handle_r * 2)
        )
        if self.text():
            color = self.palette().color(self.palette().ColorRole.WindowText)
            if not enabled:
                color.setAlpha(140)
            painter.setPen(QPen(color))
            gap = zoom_size(8)
            text_rect = QRectF(track_w + gap, 0, self.width() - track_w - gap, self.height())
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )


class _TitleTip(QObject):
    """Answers tool-tip events for a group box, but only over its title.

    A QGroupBox is handed the tool-tip event for every pixel it covers, and any
    child without a tool-tip of its own inherits the parent's, so a note set on
    the panel followed the mouse across all the options inside it. The box is
    left with no tool-tip and this filter shows the text only while the pointer
    rests on the words of the title, which sit on the top border.
    """

    LEFT = 12     # the "left: 12px" of QGroupBox::title in the app stylesheet
    PAD = 6       # and its "padding: 0 6px"
    BAND = 24     # the title is drawn across the border, so a thin strip

    def __init__(self, text: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.text = text

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip or not isinstance(watched, QGroupBox):
            return False
        # A tool-tip event is a QHelpEvent, not a mouse event: it carries no
        # globalPosition(), only pos(), which is already relative to the box the
        # event was sent to. So the local point is taken as it stands and the
        # global one the tool-tip wants is the box's own mapping of it.
        where = event.pos()
        if self._over_title(watched, where):
            QToolTip.showText(watched.mapToGlobal(where), self.text, watched)
        else:
            QToolTip.hideText()
        return True

    @classmethod
    def _over_title(cls, box: QGroupBox, point: QPoint) -> bool:
        # The same title geometry the app stylesheet spells out, at the zoom the
        # window is currently drawn at.
        left, pad, band = zoom_size(cls.LEFT), zoom_size(cls.PAD), zoom_size(cls.BAND)
        if not 0 <= point.y() <= band:
            return False
        right = left + box.fontMetrics().horizontalAdvance(box.title()) + pad
        return left - pad <= point.x() <= right


def tip_on_title(box: QGroupBox, text: str) -> QGroupBox:
    """Attach text to a group box, shown only over its title (box.setToolTip covers the whole panel)."""
    box.setToolTip("")
    box.installEventFilter(_TitleTip(text, box))
    return box


class ToggleGroup(QGroupBox):
    """A settings category headed by a ToggleSwitch instead of the checkable
    group-box checkbox. Keeps the old isChecked/setChecked/toggled interface
    so the Settings code does not have to care which kind it got."""

    def __init__(self, title: str, checked: bool, body: str = "box", tip: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 12, 10)
        outer.setSpacing(6)
        header = QHBoxLayout()
        caption = QLabel(title)
        caption.setStyleSheet("color: #c51a4a; font-weight: 600;")
        header.addWidget(caption)
        header.addStretch(1)
        self.switch = ToggleSwitch(parent=self)
        self.switch.setChecked(bool(checked))
        header.addWidget(self.switch)
        if tip:
            # The heading and its switch, never the group itself: a tool-tip on
            # the panel would pop up wherever the mouse rested, including over
            # every option the body holds. See _TitleTip for the same rule on
            # panels that have no switch.
            caption.setToolTip(tip)
            self.switch.setToolTip(tip)
        outer.addLayout(header)
        body_widget = QWidget()
        self.body_widget = body_widget
        if body == "form":
            self.body_layout = QFormLayout(self.body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
            outer.setContentsMargins(22, 8, 12, 10)
        else:
            self.body_layout = QVBoxLayout(self.body_widget)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(8)
        body_widget.setVisible(self.switch.isChecked())
        self.switch.toggled.connect(body_widget.setVisible)
        outer.addWidget(body_widget)
        # Old checkable-groupbox interface used across the Settings tab.
        self.toggled = self.switch.toggled
        self.isChecked = self.switch.isChecked
        self.setChecked = self.switch.setChecked


class CollapseGroup(QGroupBox):
    """A settings category whose contents fold away behind its heading.

    :class:`ToggleGroup` switches what it holds on and off; this one only decides
    whether you can see it, and starts folded so a group nobody looks at daily
    takes up no room. The heading is a flat tool button carrying the arrow Qt
    uses in a tree: pointing right while closed, down while open.
    """

    def __init__(self, title: str, collapsed: bool = True, body: str = "box",
                 tip: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 12, 10)
        outer.setSpacing(6)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(not collapsed)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setAutoRaise(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setStyleSheet(
            # Every state flat: the app sheet paints a QToolButton as a raised
            # box and its :checked state in the accent colour, which would turn
            # an open category into a button. A sheet on the widget itself
            # outranks the app's, so the heading reads as a caption with an
            # arrow, in the same crimson as any other category title.
            "QToolButton, QToolButton:hover, QToolButton:pressed,"
            " QToolButton:checked, QToolButton:selected {"
            " background-color: transparent; border: none; padding: 0; }"
            "QToolButton { color: #c51a4a; font-weight: 600; }"
        )
        if tip:
            # Only the heading is asked about: the body holds the information the
            # note describes, and a tool-tip on the panel would follow the mouse
            # across all of it (see _TitleTip for the same rule on plain groups).
            self.header.setToolTip(tip)
        self.header.toggled.connect(self._set_open)
        self._set_arrow(self.header.isChecked())
        outer.addWidget(self.header)
        self.body_widget = QWidget()
        if body == "form":
            self.body_layout = QFormLayout(self.body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
        else:
            self.body_layout = QVBoxLayout(self.body_widget)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(8)
        self.body_widget.setVisible(self.header.isChecked())
        outer.addWidget(self.body_widget)
        self.isOpen = self.header.isChecked
        self.setOpen = self.header.setChecked

    def _set_arrow(self, open_: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if open_
                                 else Qt.ArrowType.RightArrow)

    def _set_open(self, open_: bool) -> None:
        self._set_arrow(open_)
        self.body_widget.setVisible(open_)


def make_file_tree(title: str) -> QTreeWidget:
    """A file list with the columns, selection mode and look both file browsers use; title labels the first column."""
    tree = QTreeWidget()
    tree.setHeaderLabels([title, "Size", "Modified"])
    tree.setRootIsDecorated(True)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
    tree.setColumnWidth(1, 90)
    tree.setColumnWidth(2, 140)
    tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)
    return tree


class StringListDialog(QDialog):
    """Editor for a list of strings (used for ignore patterns and post-deploy commands).

    Values are only read back through values() when the dialog is accepted, and
    blank lines are dropped there rather than blocked on entry.
    """

    def __init__(self, title: str, label: str, items: list[str], parent: QWidget | None = None, monospace: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.list_widget = QListWidget()
        for item in items:
            self.list_widget.addItem(item)
        if monospace:
            font = QFont("Consolas", 10)
            font.setStyleHint(QFont.StyleHint.Monospace)
            self.list_widget.setFont(font)
        layout.addWidget(self.list_widget)
        # Add / edit / remove act on the current or selected rows.
        row = QHBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self.add_item)
        edit_button = QPushButton("Edit selected")
        edit_button.clicked.connect(self.edit_selected)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        row.addWidget(add_button)
        row.addWidget(edit_button)
        row.addWidget(remove_button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_item(self) -> None:
        text, ok = QInputDialog.getText(self, "Add entry", "Value:")
        if ok and text.strip():
            self.list_widget.addItem(text.strip())

    def edit_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "Edit entry", "Value:", text=item.text())
        if ok:
            item.setText(text.strip())

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list[str]:
        """The list as typed, minus anything that is only whitespace."""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]


class TabEditDialog(QDialog):
    """Editor for one configurable web tab (display name, ip, port, path)."""

    def __init__(self, parent: QWidget | None = None, tab: dict | None = None, title: str = "Web tab") -> None:
        super().__init__(parent)
        tab = tab or {}
        self.setWindowTitle(title)
        layout = QFormLayout(self)
        self.name_input = QLineEdit(str(tab.get("name", "")))
        self.name_input.setPlaceholderText("e.g. LuCI")
        layout.addRow("Display name", self.name_input)
        self.ip_input = QLineEdit(str(tab.get("ip", "")))
        self.ip_input.setPlaceholderText("IP or hostname (leave blank to use profile host)")
        self.ip_input.setToolTip(
            "Address this tab opens. Empty means follow the connected profile's host, so the "
            "tab updates whenever you switch profiles."
        )
        layout.addRow("IP / host", self.ip_input)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        try:
            self.port_input.setValue(int(tab.get("port", 80)))
        except (TypeError, ValueError):
            self.port_input.setValue(80)
        layout.addRow("Port", self.port_input)
        self.path_input = QLineEdit(str(tab.get("path", "/")))
        self.path_input.setPlaceholderText("/")
        layout.addRow("Path", self.path_input)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItems(["http", "https"])
        self.scheme_combo.setCurrentText(str(tab.get("scheme", "http")).lower() or "http")
        layout.addRow("Protocol", self.scheme_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict:
        path = self.path_input.text().strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        return {
            "name": self.name_input.text().strip(),
            "ip": self.ip_input.text().strip(),
            "port": self.port_input.value(),
            "path": path,
            "scheme": self.scheme_combo.currentText(),
        }


# --- the colour wheel: what api.ui.pick_color and a "color" field open ---

#: The hue/saturation picture at full value, built once per size and kept -
#: repainting 40 000 pixels on every drag would be pure waste.
_WHEEL_DISCS: dict[int, QPixmap] = {}


class _ColorWheel(QWidget):
    """An HSV disc: the angle is the hue, the distance from the centre the
    saturation; brightness comes from the slider beside it.

    The value is a black veil laid over the full-strength disc with
    ``SourceAtop`` - the same maths HSV does behind it - and that blend
    leaves the corners outside the circle transparent."""

    def __init__(self, parent: QWidget | None = None, side: int = 200) -> None:
        super().__init__(parent)
        self._side = zoom_size(side)
        self.setFixedSize(self._side, self._side)
        self._hue = 0.0        # 0..1
        self._sat = 0.0        # 0..1
        self._value = 1.0      # 0..1
        self.on_change = None  # callback, handed the new QColor

    def set_hsv(self, hue: float, sat: float, value: float) -> None:
        self._hue, self._sat, self._value = hue, sat, value
        self.update()

    def hsv(self) -> tuple:
        return self._hue, self._sat, self._value

    def set_colour(self, colour: QColor) -> None:
        """Point the wheel at a colour; a grey one keeps the current hue."""
        hue, sat, value, _a = colour.getHsvF()
        if hue < 0.0:
            hue = self._hue
        self.set_hsv(hue, max(0.0, sat), max(0.0, value))

    def colour(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._sat, self._value)

    @staticmethod
    def _disc(side: int) -> QPixmap:
        cached = _WHEEL_DISCS.get(side)
        if cached is not None:
            return cached
        img = QImage(side, side, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        radius = side / 2.0
        for y in range(side):
            dy = radius - y - 0.5              # centre-based, up positive
            for x in range(side):
                dx = x + 0.5 - radius
                dist = math.hypot(dx, dy)
                if dist > radius:
                    continue
                hue = math.degrees(math.atan2(dy, dx)) % 360.0
                sat = min(1.0, dist / radius)
                img.setPixelColor(x, y, QColor.fromHsvF(hue / 360.0, sat, 1.0))
        disc = QPixmap.fromImage(img)
        _WHEEL_DISCS[side] = disc
        return disc

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self._side / 2.0
        painter.drawPixmap(0, 0, self._disc(self._side))
        if self._value < 1.0:
            painter.setCompositionMode(QPainter.CompositionMode.SourceAtop)
            painter.fillRect(self.rect(), QColor(
                0, 0, 0, int(round(255 * (1.0 - self._value)))))
            painter.setCompositionMode(QPainter.CompositionMode.SourceOver)
        angle = self._hue * 2.0 * math.pi
        mx = radius + radius * self._sat * math.cos(angle)
        my = radius - radius * self._sat * math.sin(angle)
        ring = max(6.0, self._side * 0.045)
        pen = QPen(QColor("#ffffff") if self._value < 0.6 else QColor("#202020"))
        pen.setWidthF(max(1.5, self._side / 100.0))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(mx, my), ring, ring)

    def _pick(self, pos: QPointF) -> None:
        radius = self._side / 2.0
        dx = pos.x() + 0.5 - radius
        dy = radius - pos.y() - 0.5
        self._hue = (math.degrees(math.atan2(dy, dx)) % 360.0) / 360.0
        self._sat = min(1.0, math.hypot(dx, dy) / radius)
        self.update()
        if callable(self.on_change):
            self.on_change(self.colour())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position())


class ColorWheelDialog(QDialog):
    """A colour wheel with the hex line beside it - and the hex fills itself.

    Turning the wheel, dragging the marker or moving the brightness slider
    writes ``#rrggbb`` into the line; typing a hex moves the wheel to it.
    ``get_colour`` is the one-call form: the hex back, or ``""`` when the
    dialog was cancelled."""

    def __init__(self, initial: str = "", parent: QWidget | None = None,
                 title: str = "Choose a colour") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        start = QColor(str(initial)) if str(initial or "").strip() else QColor()
        if not start.isValid():
            start = QColor("#ffb020")

        layout = QHBoxLayout(self)
        self.wheel = _ColorWheel(self)
        self.wheel.on_change = self._from_wheel
        layout.addWidget(self.wheel)

        side = QVBoxLayout()
        row = QHBoxLayout()
        self.value_slider = QSlider(Qt.Orientation.Vertical)
        self.value_slider.setRange(0, 255)
        self.value_slider.setToolTip("Brightness")
        self.value_slider.valueChanged.connect(self._from_slider)
        row.addWidget(self.value_slider)
        info = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setMinimumHeight(zoom_size(36))
        info.addWidget(self.preview)
        info.addWidget(QLabel("Hex:"))
        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("#rrggbb")
        self.hex_input.textChanged.connect(self._from_hex)
        info.addWidget(self.hex_input)
        info.addStretch(1)
        row.addLayout(info, 1)
        side.addLayout(row, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        side.addWidget(buttons)
        layout.addLayout(side, 1)

        self._syncing = False
        hue, sat, value, _a = start.getHsvF()
        self.wheel.set_hsv(max(0.0, hue), max(0.0, sat), max(0.0, value))
        self._syncing = True
        self.value_slider.setValue(int(round(value * 255)))
        self.hex_input.setText(start.name())
        self._syncing = False
        self._show(start)

    # --- the three controls keeping each other current ---

    def _show(self, colour: QColor) -> None:
        self.preview.setStyleSheet(scaled_sheet(
            f"QLabel {{ background: {colour.name()};"
            " border: 1px solid #555555; border-radius: 6px; }"))

    def _push(self, colour: QColor) -> None:
        """One control moved: bring the hex line and the preview along."""
        self._syncing = True
        self.hex_input.setText(colour.name())
        self._syncing = False
        self._show(colour)

    def _from_wheel(self, colour: QColor) -> None:
        self._syncing = True
        self.value_slider.setValue(int(round(colour.valueF() * 255)))
        self._syncing = False
        self._push(colour)

    def _from_slider(self, value: int) -> None:
        hue, sat, _old = self.wheel.hsv()
        colour = QColor.fromHsvF(hue, sat, max(0.0, value / 255.0))
        self.wheel.set_hsv(hue, sat, colour.valueF())
        if not self._syncing:
            self._push(colour)

    def _from_hex(self, text: str) -> None:
        if self._syncing:
            return
        colour = QColor(str(text).strip())
        if not colour.isValid():
            return                      # half-typed hex: wait, don't fight
        self.wheel.set_colour(colour)
        self._syncing = True
        self.value_slider.setValue(int(round(colour.valueF() * 255)))
        self._syncing = False
        self._show(colour)

    # --- the answer ---

    def colour(self) -> str:
        return self.wheel.colour().name()

    @staticmethod
    def get_colour(initial: str = "", parent: QWidget | None = None,
                   title: str = "Choose a colour") -> str:
        box = ColorWheelDialog(initial, parent, title)
        return box.colour() if box.exec() else ""
