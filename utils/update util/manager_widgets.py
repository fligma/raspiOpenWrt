#!/usr/bin/env python3

"""Reusable UI pieces: the themed toggles, the collapsible group, the file tree and the popup dialogs."""


from __future__ import annotations

from PyQt6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
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
    QSpinBox,
    QTabBar,
    QTabWidget,
    QToolTip,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from manager_const import scaled_sheet, zoom_size


class RightPinnedTabBar(QTabBar):
    """A tab bar that keeps the last tab pinned to the far right edge.

    An empty, disabled *gap* tab is widened to soak up all the spare room, so
    the tab that sits just after it - the Settings tab - is pushed against the
    right edge and reads as detached from the reorderable tabs on the left.
    ``set_gap_index`` tells the bar which tab is the spacer; its width is
    recomputed from the bar's own width every time a tab moves or the window
    is resized, so the pinned tab always ends up flush right.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gap_index = -1

    def set_gap_index(self, index: int) -> None:
        self.gap_index = index
        self._relayout()

    def tabSizeHint(self, index: int) -> QSize:
        size = QTabBar.tabSizeHint(self, index)
        if index == self.gap_index and 0 <= index < self.count():
            used = sum(
                QTabBar.tabSizeHint(self, i).width()
                for i in range(self.count()) if i != index
            )
            return QSize(max(0, self.width() - used), size.height())
        return size

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Force the bar to re-run :meth:`tabSizeHint` and re-place every tab.

        QTabBar only recomputes tab sizes when its layout is invalidated; a
        width change alone is not enough once the gap tab is involved. Blinking
        the gap tab's enabled state runs the re-layout and leaves the tab
        disabled (unclickable) exactly as it was."""
        if 0 <= self.gap_index < self.count():
            self.setTabEnabled(self.gap_index, True)
            self.setTabEnabled(self.gap_index, False)


class TabTransportDots(QWidget):
    """Two small status dots for a tab's label: 's' for SSH, 'f' for SFTP.

    Placed beside a tab's text in the tab bar so the live connection state is
    visible from whichever tab is showing. Each dot is coloured by the app's
    shared status-dot style (green connected, red not) and carries a one-letter
    mark, :meth:`set_state` recolouring them from the current transport states.
    """

    def __init__(self, parent=None):
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
        """The pill's size at the zoom the interface is drawn at.

        The switch is painted rather than styled, so no stylesheet can scale it:
        the two hand-written sizes are asked of :mod:`manager_const` instead.
        """
        return zoom_size(self.TRACK_W), zoom_size(self.TRACK_H)

    def __init__(self, text: str = "", parent=None):
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
        """Set the state, and take the painted handle there too.

        A switch whose signals are blocked never hears about its own change of
        mind, and this widget draws its handle from a copy of the state it
        keeps: without moving that copy a rebuild would paint a switched-on
        addon with the handle still on the left, which reads as a checkbox that
        does not work. A plain click keeps its slide - the animation answers
        the ``toggled`` signal, which is only blocked here."""
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

    def paintEvent(self, _event) -> None:
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

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text

    def eventFilter(self, watched, event):
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
    def _over_title(cls, box: QGroupBox, point) -> bool:
        # The same title geometry the app stylesheet spells out, at the zoom the
        # window is currently drawn at.
        left, pad, band = zoom_size(cls.LEFT), zoom_size(cls.PAD), zoom_size(cls.BAND)
        if not 0 <= point.y() <= band:
            return False
        right = left + box.fontMetrics().horizontalAdvance(box.title()) + pad
        return left - pad <= point.x() <= right


def tip_on_title(box: QGroupBox, text: str) -> QGroupBox:
    """Attach `text` to a group box, shown only over its title.

    Use this instead of ``box.setToolTip()``, which covers the whole panel and
    is inherited by every control in it.
    """
    box.setToolTip("")
    box.installEventFilter(_TitleTip(text, box))
    return box


class ToggleGroup(QGroupBox):
    """A settings category headed by a ToggleSwitch instead of the checkable
    group-box checkbox. Keeps the old isChecked/setChecked/toggled interface
    so the Settings code does not have to care which kind it got."""

    def __init__(self, title: str, checked: bool, body: str = "box", tip: str = ""):
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
        if body == "form":
            self.body_layout = QFormLayout(body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
            outer.setContentsMargins(22, 8, 12, 10)
        else:
            self.body_layout = QVBoxLayout(body_widget)
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
                 tip: str = ""):
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
    """Build a file list with the columns, selection mode and look both file
    browsers use. `title` labels the first column, so the local and remote
    trees differ only there."""
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

    def __init__(self, title: str, label: str, items: list[str], parent=None, monospace: bool = False):
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

    def __init__(self, parent=None, tab: dict | None = None, title: str = "Web tab"):
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


class DryRunDialog(QDialog):
    """Read-only report of what a deploy would change, built from a dry-run plan.

    The plan comes straight from SftpWorker.deploy() in dry_run mode, so nothing
    here has to re-derive anything - it only counts and lists.
    """

    def __init__(self, plan: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deploy preview (dry run)")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        unchanged = plan.get("unchanged", [])
        # Headline numbers first; the deletion lines only appear if that mirror
        # direction is actually switched on.
        summary = (
            f"Directories to create: {len(plan['directories'])}\n"
            f"Files to upload (changed): {len(plan['files'])}\n"
        )
        if unchanged:
            summary += f"Unchanged files skipped: {len(unchanged)}\n"
        if plan["would_delete"]:
            summary += f"Server files to remove (not on local): {len(plan['stale_files'])}\n"
            summary += f"Server directories to remove (not on local): {len(plan['stale_dirs'])}\n"
        if plan.get("would_delete_local"):
            summary += f"Local files to remove (not on server): {len(plan.get('local_files', []))}\n"
            summary += f"Local directories to remove (not on server): {len(plan.get('local_dirs', []))}\n"
        if not plan["would_delete"] and not plan.get("would_delete_local"):
            summary += "Mirror cleanup is OFF - nothing will be deleted."
        label = QLabel(summary)
        label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(label)

        # The delete tabs stay visible but empty when mirror cleanup is off.
        tabs = QTabWidget()
        tabs.addTab(self._make_list(plan["directories"]), "New / update dirs")
        tabs.addTab(self._make_list(plan["files"]), "Files to upload")
        if unchanged:
            tabs.addTab(self._make_list(unchanged), "Unchanged (skipped)")
        tabs.addTab(self._make_list(plan["stale_files"] + plan["stale_dirs"]), "Delete on server")
        tabs.addTab(
            self._make_list(plan.get("local_files", []) + plan.get("local_dirs", [])),
            "Delete on local",
        )
        layout.addWidget(tabs, 1)

        note = QLabel("This is a preview. No changes were made. Close this dialog and press Deploy to apply.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #ffb020;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _make_list(items: list[str]) -> QListWidget:
        """A read-only sorted list box for one tab of the preview."""
        widget = QListWidget()
        for item in sorted(items, key=lambda entry: entry.lower()):
            widget.addItem(item)
        return widget
