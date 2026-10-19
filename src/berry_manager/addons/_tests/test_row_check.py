"""A test for tickable table rows in addon_api.py - the store's switches.

The v1.6.8 addon store puts a check box on each installed row so a person can
switch an addon on or off from the list itself. That path is painted, not just
computed: the row delegate draws every cell by hand, tick included, and the
first paint only happens when the tab actually shows. An app that dies the
moment the store tab opens is exactly what a data-only test cannot see, so
this walks the genuine Qt route - build the table as the store declares it,
lay rows exactly as the store shapes them, show the widget and make it paint -
and proves the three things the feature rests on:

* a ticked, tinted, pictured row paints without raising (the crash itself:
  the delegate has no ``style()`` of its own to ask for a brush);
* a tick flipped by hand comes back as one ``toggled`` event carrying the
  row's label, its state and its whole line;
* a repaint sets ticks behind the silent flag and is never reported.

Run it on the command line::

    python addons/_tests/test_row_check.py
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
    """One thing the tickable rows have to do; all are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


def _store_table(api: Any, events: list) -> Any:
    """The store's list table, spec line for line from its tab_spec()."""
    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "table", "id": "list",
            "columns": ["addon", "version", "status"],
            "stretch": [0], "col_widths": [0, 70, 130], "font_size": 11,
            "checkbox": 0, "multi": True,
            "on": ["selected", "context", "toggled"]}
    widget, _registry = api.build_spec(spec, events=emit)
    return widget


def _store_rows() -> list[dict]:
    """Rows exactly as the store's show_entries() shapes them: a ticked one,
    an unticked one, a plain not-installed one, one wearing a picture beside
    the tick, and one whose picture cannot be read."""
    picture = os.path.join(ROOT, "assets", "icon.png")
    return [
        {"cells": ["Berry Terminal\u2713", "1.2", "installed"],
         "color": "#3fbf5f", "check": True},
        {"cells": ["Logs", "1.0", "installed (off)"],
         "color": "#9a9a9a", "check": False},
        {"cells": ["Theme Manager", "2.0", "not installed"],
         "color": "#ffb020"},
        {"cells": ["Pictured row", "1.0", "installed"],
         "color": "#3fbf5f", "check": True, "icon": picture},
        {"cells": ["Missing picture", "1.0", "new"], "icon": "no_such.png"},
    ]


# --- one: a ticked row paints, which is where the app used to die ---

def test_paint(app: Any, api: Any) -> None:
    print("\n1. ticked, tinted and pictured rows paint without raising")
    from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

    events: list = []
    table = _store_table(api, events)
    check(table is not None, "the store's table spec built")
    rows = _store_rows()
    api.apply_widget_property(table, "rows", rows)
    check(table.topLevelItemCount() == len(rows),
          f"only {table.topLevelItemCount()} of {len(rows)} rows landed")
    # A tab holder is what makes the delegate paint: rows laid down while the
    # widget is hidden are first drawn the moment the tab shows.
    holder = QTabWidget()
    pane = QWidget()
    QVBoxLayout(pane).addWidget(table)
    holder.addTab(pane, "store")
    holder.resize(600, 300)
    holder.show()
    app.processEvents()
    table.grab()                       # runs the delegate over every cell
    app.processEvents()
    check(True, "the delegate painted every tick without raising")
    holder.close()


# --- two: a hand-flipped tick is reported once, with the whole row ---

def test_flip(api: Any) -> None:
    print("\n2. a tick flipped by hand comes back as one toggled event")
    events: list = []
    table = _store_table(api, events)
    api.apply_widget_property(table, "rows", _store_rows())
    events.clear()
    table.topLevelItem(0).setCheckState(0, api.Qt.CheckState.Unchecked)
    flips = [item for item in events if item[1] == "toggled"]
    check(len(flips) == 1, f"a flip reported {len(flips)} events, not one")
    if flips:
        ident, _event, payload = flips[0]
        look(f"{ident}: {payload}")
        check(ident == "list" and payload.get("text") == "Berry Terminal\u2713",
              f"the flip named the wrong row: {payload.get('text')!r}")
        check(payload.get("checked") is False, "an untick was reported as ticked")
        check(payload.get("cells") == ["Berry Terminal\u2713", "1.2", "installed"],
              f"the flip lost the row's line: {payload.get('cells')}")
    # A second tick flips too, and comes back saying it was put on.
    events.clear()
    table.topLevelItem(1).setCheckState(0, api.Qt.CheckState.Checked)
    flips = [item for item in events if item[1] == "toggled"]
    check(len(flips) == 1 and flips[0][2].get("checked") is True,
          f"ticking a row reported {flips}")


# --- three: a repaint is never mistaken for a user's flip ---

def test_silent_repaint(api: Any) -> None:
    print("\n3. laying rows down sets ticks without reporting them")
    events: list = []
    table = _store_table(api, events)
    events.clear()                       # forget the build itself
    api.apply_widget_property(table, "rows", _store_rows())
    flips = [item for item in events if item[1] == "toggled"]
    check(not flips, f"a repaint reported {len(flips)} tick flips")
    check(table.topLevelItem(0).checkState(0) == api.Qt.CheckState.Checked,
          "the repaint did not put the stored tick back on the row")
    check(table.topLevelItem(1).checkState(0) == api.Qt.CheckState.Unchecked,
          "a switched-off addon came back ticked")
    # Qt's default item flags always carry ItemIsUserCheckable, so "wears no
    # switch" is not a flag to read - it is the tick's data: the delegate only
    # draws a box where CheckStateRole answers with something, and a row that
    # never said "check" carries nothing.
    plain = table.model().index(2, 0)
    check(plain.data(api.Qt.ItemDataRole.CheckStateRole) is None,
          "a not-installed row carries a tick state and would be drawn "
          "wearing a switch")


# --- four: the store's ACTUAL shelf is a grid of tiles, not a table ---

def _store_grid(api: Any, events: list) -> Any:
    """The store's list shelf exactly as its tab_spec() declares it: a grid.

    This is the widget the store really builds - ``kind: list, grid: True`` -
    a QListWidget whose tiles are QListWidgetItem, not the QTreeWidget the
    sections above stand in for. A QListWidgetItem's ``setCheckState`` and
    ``checkState`` take NO column, and a v1.6.x store drove them as if they
    were table rows, so an install died on the final repaint with
    ``setCheckState(self, state): argument 1 has unexpected type 'int'``.
    """
    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "list", "id": "list", "grid": True,
            "icon_size": 64, "tile_width": 150, "spacing": 10,
            "font_size": 10, "multi": True, "checkbox": 0,
            "on": ["selected", "context", "toggled"]}
    widget, _registry = api.build_spec(spec, events=emit)
    return widget


def test_grid_repaint(app: Any, api: Any) -> None:
    print("\n4. grid tiles laying a tick down do not raise (the install crash)")
    from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

    events: list = []
    grid = _store_grid(api, events)
    check(grid is not None, "the store's grid spec built")
    events.clear()                       # forget the build itself
    rows = _store_rows()
    raised = ""
    try:
        api.apply_widget_property(grid, "rows", rows)
    except Exception as error:                    # noqa: BLE001 - the crash itself
        raised = f"{type(error).__name__}: {error}"
    check(not raised,
          f"laying a ticked tile down raised, which is the install crash: "
          f"{raised!r}")
    if raised:
        return
    check(grid.count() == len(rows),
          f"only {grid.count()} of {len(rows)} tiles landed")
    # A repaint sets ticks behind the silent flag: nothing is reported.
    flips = [item for item in events if item[1] == "toggled"]
    check(not flips, f"a tile repaint reported {len(flips)} tick flips")
    # The ticks the store said are the ticks the tiles wear.
    check(grid.item(0).checkState() == api.Qt.CheckState.Checked,
          "a ticked tile was not laid down ticked")
    check(grid.item(1).checkState() == api.Qt.CheckState.Unchecked,
          "a switched-off tile came back ticked")
    # Show it and paint it, which is where a compiled store actually dies.
    holder = QTabWidget()
    pane = QWidget()
    QVBoxLayout(pane).addWidget(grid)
    holder.addTab(pane, "store")
    holder.resize(600, 400)
    holder.show()
    app.processEvents()
    grid.grab()
    app.processEvents()
    check(True, "the tiles painted after a ticked repaint without raising")
    holder.close()


def test_grid_flip(api: Any) -> None:
    print("\n5. a tile flipped by hand comes back as one toggled event")
    events: list = []
    grid = _store_grid(api, events)
    api.apply_widget_property(grid, "rows", _store_rows())
    events.clear()
    grid.item(0).setCheckState(api.Qt.CheckState.Unchecked)
    flips = [item for item in events if item[1] == "toggled"]
    check(len(flips) == 1,
          f"flipping a tile reported {len(flips)} events, not one (a tile's "
          "itemChanged carries no column)")
    if flips:
        ident, _event, payload = flips[0]
        look(f"{ident}: {payload}")
        check(ident == "list" and payload.get("text") == "Berry Terminal\u2713",
              f"the flip named the wrong tile: {payload.get('text')!r}")
        check(payload.get("checked") is False, "an unticked tile read as ticked")
        check(payload.get("cells") == ["Berry Terminal\u2713", "1.2", "installed"],
              f"the flip lost the tile's line: {payload.get('cells')}")


# --- six: the taskbar primitives a desktop addon leans on ---

def test_taskbar_primitives(api: Any) -> None:
    """Horizontal flow, a pinned height and small icons on a plain list.

    BerryVM's taskbar is a ``kind: list`` strip, and a Qt list without help
    is a 192px column with no room for marks: the bar swallowed half the
    desktop and every button came up blank. The spec now carries
    ``flow: horizontal`` and ``icon_size``, and ``height`` / ``icon_size``
    can be re-set later; this drives the real primitives offscreen.
    """
    print("\n6. a taskbar list flows sideways, pins its height and wears icons")
    from PyQt6.QtWidgets import QListView
    from PyQt6.QtCore import QSize

    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "list", "id": "tasks", "flow": "horizontal",
            "icon_size": 16, "on": ["selected"]}
    bar, _registry = api.build_spec(spec, events=emit)
    check(bar is not None, "the taskbar spec built")
    check(bar.flow() == QListView.Flow.LeftToRight,
          "flow: horizontal did not lay the list sideways")
    check(bar.isWrapping() is False,
          "a horizontal list still wraps, so the strip would grow a second row")
    check(bar.iconSize().width() == api.zoom_size(16),
          f"icon_size 16 was not put on the plain list: {bar.iconSize()}")
    # The two late properties a scale change re-lays: a pinned height (a
    # list's own sizeHint insists on 192px however it is nudged) and a
    # fresh icon edge.
    api.apply_widget_property(bar, "height", 30)
    want_h = api.zoom_size(30)
    check(bar.minimumHeight() == want_h and bar.maximumHeight() == want_h,
          f"height 30 did not pin the bar: {bar.minimumHeight()}/"
          f"{bar.maximumHeight()}, wanted {want_h}")
    api.apply_widget_property(bar, "icon_size", 20)
    check(bar.iconSize() == QSize(api.zoom_size(20), api.zoom_size(20)),
          f"icon_size 20 did not reach the bar: {bar.iconSize()}")
    # Items that name an icon become buttons that show it: a bare svg text
    # is what the addon hands, and the item must come back with a picture.
    mark = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
            '<rect width="16" height="16" fill="#3fbf5f"/></svg>')
    api.apply_widget_property(bar, "items", [
        {"text": "Notepad", "icon": mark}, "plain text"])
    check(bar.count() == 2, f"only {bar.count()} taskbar buttons landed")
    check(bar.item(0).icon().isNull() is False,
          "a taskbar item named an icon but came up blank")
    check(bar.item(1).icon().isNull(),
          "a plain string item grew an icon it was never given")


# --- seven: a chromeless window's body can be made see-through ---

def test_window_body_name(api: Any) -> None:
    """The frame's inner body widget is named, so a sheet can reach it.

    A desktop layer paints its wallpaper on the frame, but the frame keeps
    an ordinary QWidget body inside, and the app-wide ``QWidget { background }
    "#121212" }`` pours dark paint through it over the whole desktop - which
    is how BerryVM's Bliss gradient came out black. The body now answers to
    ``#addonWindowBody`` so the addon's own sheet can make it transparent.
    """
    print("\n7. a window frame's body answers to addonWindowBody")
    from PyQt6.QtWidgets import QWidget

    spec = {"kind": "windows", "id": "desk", "children": [
        {"id": "icons", "title": "icons", "chrome": False,
         "x": 0, "y": 0, "width": 200, "height": 200,
         "spec": {"kind": "label", "text": "layer"}},
    ]}
    canvas, _registry = api.build_spec(spec)
    check(canvas is not None, "the desktop canvas built with its layer")
    # The canvas paints its wallpaper from a stylesheet rule, and Qt only
    # fills a bare QWidget subclass's background when it carries this
    # attribute - without it the desktop came out black.
    from PyQt6.QtCore import Qt
    check(canvas.testAttribute(Qt.WidgetAttribute.WA_StyledBackground) is True,
          "the desktop canvas does not paint its stylesheet background, so "
          "a wallpaper set on it never shows")
    frame = canvas._addon_windows["frames"].get("icons")
    check(frame is not None, "the chromeless window did not open")
    if frame is None:
        return
    body = frame.findChild(QWidget, "addonWindowBody")
    check(body is not None,
          "the frame holds no body named addonWindowBody for a sheet to reach")


# --- eight: the zoom ladder walks by fives, fifty to a hundred and twenty ---

def test_zoom_ladder() -> None:
    """One press, one small predictable move.

    The old ladder jumped by tens, then fifteens and twenties, and ran to
    200% where nothing fits; zooming felt random and hard to undo. The rungs
    are now even fives from 50 to 120, and ``step_zoom`` walks them.
    """
    print("\n8. the zoom ladder is even fives from 50 to 120")
    from core import const

    check(const.UI_ZOOM_STEPS == tuple(range(50, 121, 5)),
          f"ladder is not 50..120 by fives: {const.UI_ZOOM_STEPS}")
    check(const.UI_ZOOM_DEFAULT in const.UI_ZOOM_STEPS,
          "the default zoom fell off the ladder")
    check(const.UI_ZOOM_MIN == 50 and const.UI_ZOOM_MAX == 120,
          f"the ends read {const.UI_ZOOM_MIN}..{const.UI_ZOOM_MAX}")
    check(const.step_zoom(100, +1) == 105,
          f"a step up from 100 gave {const.step_zoom(100, +1)}")
    check(const.step_zoom(100, -1) == 95,
          f"a step down from 100 gave {const.step_zoom(100, -1)}")
    check(const.step_zoom(50, -1) == 50, "zooming out did not clamp at 50")
    check(const.step_zoom(120, +1) == 120, "zooming in did not clamp at 120")
    # An off-ladder stored value (an old profile) snaps to the nearest rung.
    check(const.step_zoom(150, 0) == 120,
          f"an old 150 zoom did not clamp down: {const.step_zoom(150, 0)}")


# --- nine: the icon cache answers twice from one decoding ---

def test_icon_cache(api: Any) -> None:
    """A repaint that re-asks for a mark gets the same QIcon back.

    A taskbar or a store shelf lays the same pictures down on every
    refresh, and decoding and scaling the same SVG each time is repaint
    work nobody asked for. The cache must hand back the very same icon -
    not a re-decoded look-alike - and what it hands back must still paint
    at the size the old code built.
    """
    print("\n9. the same icon source builds one mark, kept between calls")
    mark = ('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            '<rect width="32" height="32" fill="#2b8aa8"/></svg>')
    first = api.build_icon(mark)
    check(first is not None, "the svg text did not build an icon at all")
    if first is None:
        return
    check(api.build_icon(mark) is first,
          "a second build of the same source decoded again instead of caching")
    shot = first.pixmap(64, 64)
    check(shot is not None and shot.isNull() is False
          and shot.width() <= 64 and shot.height() <= 64,
          f"the cached icon no longer paints whole: {shot}")
    check(api.build_icon("") is None and api.build_icon(None) is None,
          "a blank source grew an icon it was never given")


# --- ten: a cached config read stays true to its file ---

def test_config_cache() -> None:
    """The parse cache is keyed on the file's own stamp, not on hope.

    Every addon's ``config.get`` funnels through ``read_config_cached``,
    which remembers the parsed file between calls. The bargain is that a
    hand edit between two reads still comes back fresh, that the answer
    handed out is a copy the caller may merge into, and that a write
    through the API drops the old reading at once.
    """
    print("\n10. the config cache follows the file, hand edits included")
    import tempfile
    from pathlib import Path

    import addon_common

    with tempfile.TemporaryDirectory(prefix="rowcheck-config-") as folder:
        path = Path(folder) / "config.json"
        addon_common.write_config_json(path, {"theme": "bliss"})
        check(addon_common.read_config_cached(path) == {"theme": "bliss"},
              "the first read of a fresh config did not come back")
        check(addon_common.read_config_cached(path) == {"theme": "bliss"},
              "the cached second read changed the answer")
        # A hand edit, not through the API: different bytes, a different
        # size, and the next read has to see them.
        path.write_text('{"theme": "classic", "extra": 1}\n',
                        encoding="utf-8")
        check(addon_common.read_config_cached(path)
              == {"theme": "classic", "extra": 1},
              "a hand-edited config did not reach the next read")
        # The answer is a copy: merging into it must not poison the cache
        # for the next caller, who expects the file's own words.
        held = addon_common.read_config_cached(path)
        held["theme"] = "poisoned"
        check(addon_common.read_config_cached(path)
              == {"theme": "classic", "extra": 1},
              "the cached reading was shared out, not copied")
        addon_common.write_config_json(path, {"theme": "zune"})
        check(addon_common.read_config_cached(path) == {"theme": "zune"},
              "a write through the API left an old reading in place")


# --- eleven: a free-movement grid seats tiles by name and reports a drop ---

def test_grid_movement_and_rename(api: Any) -> None:
    """The desktop icon layer's own primitives: draggable tiles, inline
    rename, and a saved position on each row.

    BerryVM's icon layer is a ``kind: list`` grid built with
    ``movement: free`` and ``renamable: True``. Qt's default is a Static
    grid that snaps every tile back; the free movement lets the person
    drag one anywhere, and the tile's row can carry a ``pos`` that gets
    it put back where it was. A rename is fired through ``itemChanged``
    as a display-role mismatch; the event the addon wants is ``renamed``,
    and cells are the tile's shown caption."""
    print("\n11. a free-movement grid drags, renames and lands at a ``pos``")
    from PyQt6.QtWidgets import QListView, QListWidget
    from PyQt6.QtCore import Qt

    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "list", "id": "icons", "grid": True,
            "icon_size": 32, "tile_width": 60, "spacing": 6,
            "movement": "free", "renamable": True,
            "on": ["activated", "moved", "renamed"]}
    grid, _registry = api.build_spec(spec, events=emit)
    check(grid is not None, "the free-movement grid spec built")
    check(grid.movement() == QListView.Movement.Free,
          "movement: free did not put the list in free mode")
    triggers = grid.editTriggers()
    check(bool(triggers & QListWidget.EditTrigger.DoubleClicked),
          "a renamable grid does not accept a double-click edit")
    rows = [{"cells": ["Notepad"], "name": "Notepad", "pos": [40, 30]},
            {"cells": ["My Computer"], "name": "My Computer"}]
    api.apply_widget_property(grid, "rows", rows)
    check(grid.count() == 2, f"only {grid.count()} tiles landed")
    # ItemIsEditable rides every row when the grid was built renamable.
    first = grid.item(0)
    check(bool(first.flags() & Qt.ItemFlag.ItemIsEditable),
          "a renamable grid laid an un-renamable tile")
    # The ``pos`` is honoured: item 0 sits at (40*zoom, 30*zoom) on the
    # viewport; item 1 has no pos and is left to Qt's cascade.
    want_x = api.zoom_size(40)
    want_y = api.zoom_size(30)
    # The tile's own seat is read back through the view's rect - Qt has
    # no direct item->pos() in the Python binding, and the rect's
    # top-left is where the drop landed.
    rect = grid.visualItemRect(first)
    check(abs(rect.x() - want_x) <= api.zoom_size(4)
          and abs(rect.y() - want_y) <= api.zoom_size(4),
          f"pos [40, 30] did not reach the tile: got ({rect.x()}, "
          f"{rect.y()}), wanted roughly ({want_x}, {want_y})")
    # A live re-seat via the ``positions`` prop: a rebuild is not needed.
    api.apply_widget_property(grid, "positions",
                              {"My Computer": [10, 20]})
    second = grid.item(1)
    rect2 = grid.visualItemRect(second)
    check(abs(rect2.x() - api.zoom_size(10)) <= api.zoom_size(4)
          and abs(rect2.y() - api.zoom_size(20)) <= api.zoom_size(4),
          f"positions prop did not re-seat My Computer: ({rect2.x()}, "
          f"{rect2.y()})")


# --- twelve: window minimize, maximize, restore and attention ---

def test_window_min_max_attention(api: Any) -> None:
    """The three frame commands a taskbar drives, plus the highlight.

    A desktop's bar does more than raise; the person expects a minimize,
    a maximize, and a way to bring the frame back. The canvas answers to
    ``minimize_window``, ``toggle_maximize``, ``restore_window`` and
    ``attention_window`` through ``_window_command``; the frame reports
    the first three back so the taskbar's row can follow."""
    print("\n12. a frame can minimize, maximize and be flagged")
    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "windows", "id": "desk",
            "on": ["minimized", "maximized", "restored"],
            "children": [
                {"id": "w1", "title": "Window one",
                 "x": 10, "y": 10, "width": 200, "height": 120,
                 "spec": {"kind": "label", "text": "hi"}},
            ]}
    canvas, _registry = api.build_spec(spec, events=emit)
    check(canvas is not None, "the desktop canvas built")
    frame = canvas._addon_windows["frames"].get("w1")
    check(frame is not None, "the child window did not open")
    if frame is None:
        return
    # The min / max buttons exist and are named the way the sheet wants.
    bar = frame.findChild(api.QtCore.QObject, "addonWindowBar") \
        if hasattr(api, "QtCore") else None
    from PyQt6.QtWidgets import QPushButton
    btn_min = frame.findChild(QPushButton, "btnMin")
    btn_max = frame.findChild(QPushButton, "btnMax")
    btn_close = frame.findChild(QPushButton, "btnClose")
    check(btn_min is not None and btn_max is not None
          and btn_close is not None,
          "a frame's title bar does not carry btnMin/btnMax/btnClose")
    # The commands run through the same path the addon uses.
    events.clear()
    api._window_command(canvas, "minimize_window", {"id": "w1"})
    check(frame.isHidden(), "minimize_window did not hide the frame")
    check(any(e[1] == "minimized" for e in events),
          f"the minimize did not report back: {events}")
    api._window_command(canvas, "restore_window", {"id": "w1"})
    # The canvas is offscreen, so ``isVisible`` stays False even after a
    # restore; what actually flips is the frame's own hidden flag.
    check(not frame.isHidden(), "restore_window did not show the frame again")
    check(any(e[1] == "restored" for e in events),
          "the restore did not report back")
    # A maximize grows the frame to the canvas's viewport.
    api._window_command(canvas, "toggle_maximize", {"id": "w1"})
    check(any(e[1] == "maximized" for e in events),
          "the maximize did not report maximized")
    inside = canvas.viewport().size() if hasattr(canvas, "viewport") \
        else canvas.size()
    check(frame.width() >= inside.width() - 4
          and frame.height() >= inside.height() - 4,
          f"the maximized frame ({frame.width()}x{frame.height()}) is "
          f"smaller than the viewport ({inside.width()}x{inside.height()})")
    # Attention is a state flag the sheet can paint from.
    api._window_command(canvas, "attention_window",
                        {"id": "w1", "on": True})
    check(frame.property("attention") is True,
          "attention_window on=True did not set the frame's property")
    api._window_command(canvas, "attention_window",
                        {"id": "w1", "on": False})
    check(frame.property("attention") in (False, None),
          "attention_window off left the frame flagged")


# --- thirteen: a shortcut prop installs a QShortcut and fires the event ---

def test_shortcut_prop(api: Any) -> None:
    """An addon's ``Alt+Tab`` reaches its dispatcher through the canvas.

    BerryVM puts ``shortcuts: [{"keys": "Alt+Tab", "event": "shortcut"}]``
    on its ``windows`` canvas; the host builds a ``QShortcut`` for each
    entry and connects it to the addon's event callback. The context is
    ``WindowShortcut`` - fires only while the app has focus - and no
    OS-level grab is offered, so this is what an addon can actually see."""
    print("\n13. a windows canvas installs Alt+Tab and reports the cycle")
    # QShortcut lives in QtGui since Qt6 moved it out of QtWidgets.
    from PyQt6.QtGui import QShortcut
    from PyQt6.QtCore import Qt

    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "windows", "id": "desk",
            "on": ["shortcut"],
            "shortcuts": [{"keys": "Alt+Tab", "event": "shortcut"}],
            "children": []}
    canvas, _registry = api.build_spec(spec, events=emit)
    check(canvas is not None, "the canvas with a shortcut built")
    shorts = canvas.findChildren(QShortcut)
    check(len(shorts) >= 1,
          f"Alt+Tab never became a QShortcut: {shorts}")
    if not shorts:
        return
    shot = shorts[0]
    check(shot.context() == Qt.ShortcutContext.WindowShortcut,
          "the Alt+Tab shortcut does not use the WindowShortcut context")
    # Fire it by hand: the callback the addon registered gets its event.
    shot.activated.emit()
    names = [e[1] for e in events if e[0] == "desk"]
    check("shortcut" in names,
          f"firing Alt+Tab did not reach the dispatcher: {events}")


# --- fourteen: a list prop flips item backgrounds for a taskbar flash ---

def test_list_attention_prop(api: Any) -> None:
    """``attention: ["Notepad"]`` tints one button; ``attention: []`` clears.

    The bar's blink is a colour swap rather than a rebuild, so a taskbar
    can flash without disturbing the row's height or icon."""
    print("\n14. a horizontal list flashes a named row via ``attention``")
    from PyQt6.QtGui import QBrush

    events: list = []
    spec = {"kind": "list", "id": "tasks", "flow": "horizontal",
            "icon_size": 16, "on": ["selected"]}
    bar, _registry = api.build_spec(spec, events=lambda *a: events.append(a))
    api.apply_widget_property(bar, "items", [
        {"text": "Notepad"}, {"text": "My Computer"}])
    # Nothing named: no item wears a special brush.
    api.apply_widget_property(bar, "attention", [])
    first = bar.item(0)
    check(first.background() is None or not first.background().isOpaque()
          or first.data(api.Qt.ItemDataRole.BackgroundRole) is None,
          "a row was tinted before the attention list named it")
    api.apply_widget_property(bar, "attention", ["Notepad"])
    check(first.data(api.Qt.ItemDataRole.BackgroundRole) is not None,
          "attention: [Notepad] left the row without a background brush")
    brush = first.background()
    check(isinstance(brush, QBrush) and brush.color().isValid(),
          "the tinted row's background did not read as a colour brush")
    # Clearing it drops the tint again.
    api.apply_widget_property(bar, "attention", [])
    check(first.data(api.Qt.ItemDataRole.BackgroundRole) is None,
          "attention: [] did not clear the tint")


# --- fifteen: a window can be resized from any edge, not just its corner ---

def test_resize_all_edges(api: Any) -> None:
    """Eight resize handles let the hand drag a frame from any side.

    A frame used to be sized only from its bottom-right corner; now four sides
    and four corners each carry a :class:`_WindowEdge`. The check reads the
    handles off a freshly built frame and drives ``resize_from`` directly, the
    way a drag would, to prove the opposite edge stays pinned and the minimum
    size is honoured."""
    print("\n15. a window is resizable from all eight edges")
    spec = {"kind": "windows", "id": "desk",
            "on": ["resized", "restored", "maximized"],
            "children": [
                {"id": "w1", "title": "Window one",
                 "x": 40, "y": 40, "width": 300, "height": 200,
                 "spec": {"kind": "label", "text": "hi"}},
            ]}
    canvas, _registry = api.build_spec(spec)
    frame = canvas._addon_windows["frames"].get("w1")
    check(frame is not None, "the window opened")
    if frame is None:
        return
    check(len(frame._grips) == 8,
          f"a resizable framed window has {len(frame._grips)} resize "
          f"handles, not 8")
    check(set(frame._edge_map or {}) >= {"l", "r", "t", "b", "lt", "rt",
                                         "lb", "rb"},
          "the eight handles do not cover all four sides and corners")
    floor = api.zoom_size(api.WINDOW_MIN)
    # Drag the left edge 50px right: narrower by 50, origin moved right by 50,
    # the right edge pinned where it was.
    geom = (40, 40, 300, 200)
    frame.resize_from("l", geom, 50, 0)
    check(frame.width() == 300 - 50,
          f"a left drag of 50 gave width {frame.width()}")
    check(frame.x() == 40 + 50,
          f"a left drag did not move the origin with it: x={frame.x()}")
    # Drag the bottom edge down: taller, top stays put.
    frame.resize_from("b", geom, 0, 30)
    check(frame.height() == 200 + 30,
          f"a bottom drag of 30 gave height {frame.height()}")
    check(frame.y() == 40,
          f"a bottom drag moved the top: y={frame.y()}")
    # A right drag past nothing shrinks the window, but never under the floor.
    frame.resize_from("r", geom, -400, 0)
    check(frame.width() == floor,
          f"crushing the right edge did not stop at the minimum "
          f"({frame.width()} vs floor {floor})")


# --- sixteen: dragging a maximized window's bar restores it under the hand ---

def test_drag_restore(api: Any) -> None:
    """An un-maximize by a drag keeps the grab point under the cursor.

    Grabbing a full-screen window's title bar and pulling it back down should
    return the frame to the size it had before, with the spot of the bar the
    hand closed on carried across to the same fraction of the now-shorter bar
    - so the window follows the cursor instead of snapping its corner to it.
    """
    print("\n16. dragging a maximized bar un-maximizes to the grab point")
    from PyQt6.QtCore import QPoint

    # The pure math first: grab the dead centre of a 800-wide maximized bar at
    # global x 400, restore to 300 wide -> the window's left goes to 250 so the
    # centre of the new bar sits under the cursor at 400.
    x, y = api._drag_restore_point(400, 10, 0.5, 10, 300, 200, 0, 0)
    check(x == 250 and y == 0,
          f"the centre grab did not translate to the shorter bar: "
          f"({x}, {y})")
    # A grab 3/4 along the wide bar lands 3/4 along the narrow one.
    x3, _ = api._drag_restore_point(600, 10, 0.75, 10, 300, 200, 0, 0)
    check(x3 == 600 - 0.75 * 300,
          f"a right-of-centre grab misplaced the window: x={x3}")

    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "windows", "id": "desk",
            "on": ["maximized", "restored"],
            "children": [
                {"id": "w1", "title": "Window one",
                 "x": 30, "y": 30, "width": 220, "height": 140,
                 "spec": {"kind": "label", "text": "hi"}},
            ]}
    canvas, _registry = api.build_spec(spec, events=emit)
    frame = canvas._addon_windows["frames"].get("w1")
    if frame is None:
        check(False, "the window did not open for the drag test")
        return
    api._window_command(canvas, "toggle_maximize", {"id": "w1"})
    check(frame._maximized is True, "the frame did not maximize")
    check(frame._prev_geom is not None, "maximize lost the previous size")
    prev_w, prev_h = frame._prev_geom[2], frame._prev_geom[3]
    events.clear()
    # Un-maximize the way the bar's drag does: grab the centre, pull.
    press = frame.mapToGlobal(QPoint(0, 0))
    grab = (0.5, 8)
    frame.restore_for_drag(press, grab)
    check(frame._maximized is False, "restore_for_drag left it maximized")
    check(frame._prev_geom is None, "restore_for_drag kept the old geometry")
    check(frame.width() == max(api.zoom_size(api.WINDOW_MIN), prev_w)
          and frame.height() == max(api.zoom_size(api.WINDOW_MIN), prev_h),
          f"the frame did not return to its pre-max size: "
          f"{frame.width()}x{frame.height()} vs {prev_w}x{prev_h}")
    check(any(e[1] == "restored" for e in events),
          "the drag un-maximize did not report a restore")


# --- seventeen: pushing a bar to a desktop edge names a snap zone ---

def test_snap_zone(api: Any) -> None:
    """The pure snap rule an Aero-style drag leans on.

    :func:`snap_zone` takes the cursor's place inside the canvas, the canvas'
    own room and how near a side counts as against it, and answers with the
    rectangle a window would land in - or ``None`` for an ordinary drop. It
    touches no Qt, so the whole half/quarter/maximise rule is checked here
    offscreen."""
    print("\n17. a bar pushed to an edge names the zone it lands in")
    room_w, room_h, edge = 1000, 800, 24
    left = api.snap_zone(0, 400, room_w, room_h, edge)
    check(left == {"x": 0, "y": 0, "w": 500, "h": 800, "max": False},
          f"a left-edge drop takes the left half: {left}")
    right = api.snap_zone(999, 400, room_w, room_h, edge)
    check(right == {"x": 500, "y": 0, "w": 500, "h": 800, "max": False},
          f"a right-edge drop takes the right half: {right}")
    top = api.snap_zone(500, 0, room_w, room_h, edge)
    check(top == {"x": 0, "y": 0, "w": 1000, "h": 800, "max": True},
          f"a top-edge drop maximises: {top}")
    corner = api.snap_zone(0, 0, room_w, room_h, edge)
    check(corner == {"x": 0, "y": 0, "w": 500, "h": 400, "max": False},
          f"a top-left corner takes that quarter: {corner}")
    corner_r = api.snap_zone(999, 0, room_w, room_h, edge)
    check(corner_r == {"x": 500, "y": 0, "w": 500, "h": 400, "max": False},
          f"a top-right corner takes its quarter: {corner_r}")
    check(api.snap_zone(500, 400, room_w, room_h, edge) is None,
          "a drop in the middle of the desktop is not a snap")
    check(api.snap_zone(0, 400, 0, 0, edge) is None,
          "a room of nothing snaps to nothing")
    check(api.snap_zone(0, 400, room_w, room_h, 0) is None,
          "a zero edge width never reaches an edge")


# --- eighteen: a canvas leaf is driven through commands and saved ---

def test_canvas(app: Any, api: Any) -> None:
    """The paint surface an addon drives with ``set_widget`` commands.

    The leaf paints from the mouse on its own, but tools, colour, width,
    clear, undo and file IO all arrive as commands, and its working size is
    read back. This builds one, drives every command the Paint addon uses,
    saves a real PNG to disk and reads it back with QImage to prove the file
    is a picture - then undoes."""
    print("\n18. a canvas leaf takes its commands and saves a PNG")
    import tempfile
    from PyQt6.QtGui import QImage

    widget, _registry = api.build_spec({"kind": "canvas", "id": "c1",
                                        "tool": "pen", "color": "#00ff00",
                                        "width": 5})
    check(isinstance(widget, api._PaintCanvas),
          "a canvas spec built a paint surface")
    widget.show()
    app.processEvents()
    widget.resize(160, 120)
    app.processEvents()
    api.apply_widget_property(widget, "tool", "rect")
    check(widget._tool == "rect", "the tool command reached the surface")
    api.apply_widget_property(widget, "tool", "sparkle")
    check(widget._tool == "rect", "an unknown tool is refused, not taken")
    api.apply_widget_property(widget, "color", "#ff0000")
    check(widget._color.name() == "#ff0000",
          "the colour command reached the surface")
    api.apply_widget_property(widget, "width", 9)
    check(widget._width == 9, "the width command reached the surface")
    size = api.read_widget_value(widget, "size")
    check(isinstance(size, dict) and size.get("w") == 160
          and size.get("h") == 120,
          f"the surface reads its working size back: {size}")
    fd, png = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    if os.path.exists(png):
        os.unlink(png)
    api.apply_widget_property(widget, "clear", "#0000ff")
    api.apply_widget_property(widget, "save", png)
    wrote = os.path.isfile(png) and os.path.getsize(png) > 0
    check(wrote, "Save wrote a PNG to the named path")
    if wrote:
        reloaded = QImage(png)
        check(not reloaded.isNull() and reloaded.width() == 160
              and reloaded.height() == 120,
              f"the file is a readable PNG at the surface's size: "
              f"{reloaded.width()}x{reloaded.height()}")
        os.unlink(png)
    api.apply_widget_property(widget, "undo", True)
    check(True, "Undo ran without raising")
    widget.close()


# --- nineteen: a player leaf builds, or falls back gracefully ---

def test_player(api: Any) -> None:
    """The audio leaf a Media Player drives - or its polite absence.

    A ``player`` leaf is built on QtMultimedia; where that module is missing
    the leaf is a plain notice, the same bargain a ``web`` leaf strikes
    without QtWebEngine. Either way the commands it is handed and the reads
    taken from it must never raise, so playback is not exercised here - only
    that the leaf exists in one of its two shapes and answers its reads."""
    print("\n19. a player leaf builds, and answers its reads")
    widget, _registry = api.build_spec({"kind": "player", "id": "p1",
                                        "volume": 60})
    check(widget is not None, "the player spec built a widget")
    leaf = getattr(widget, "_addon_player", None)
    if isinstance(leaf, dict):
        check(isinstance(widget, api._MediaLeaf),
              "with QtMultimedia the leaf is a real media player")
        for prop, value in (("open", "/no/such/track.mp3"), ("volume", 40),
                            ("mute", True), ("stop", True), ("seek", 1000)):
            api.apply_widget_property(widget, prop, value)
        state = api.read_widget_value(widget, "state")
        check(state in ("stopped", "playing", "paused", "loading"),
              f"the leaf answers a state word: {state!r}")
        position = api.read_widget_value(widget, "position")
        check(isinstance(position, int),
              f"and its position in milliseconds: {position!r}")
        check(isinstance(api.read_widget_value(widget, "duration"), int),
              "and its duration in milliseconds")
    else:
        notice = str(api.read_widget_value(widget, "text") or "")
        check("QtMultimedia" in notice or "not available" in notice,
              f"without QtMultimedia the leaf is a plain notice: {notice!r}")
    raised = ""
    try:
        api.read_widget_value(widget, "state")
        api.read_widget_value(widget, "position")
        api.apply_widget_property(widget, "play", True)
    except Exception as error:                       # noqa: BLE001 - the crash itself
        raised = f"{type(error).__name__}: {error}"
    check(not raised,
          f"a player's reads and commands must never raise: {raised!r}")


# --- twenty: a windows canvas reports its own room ---

def test_desktop_area(app: Any, api: Any) -> None:
    """The canvas' ``area`` read: the box a cascade or a tile fits into.

    An addon that lays its windows out needs the desktop's own room, not
    just the frames inside it, so a ``windows`` canvas answers ``area`` with
    its current size in real pixels. This builds a desktop, sizes it, and
    reads the room back beside the window list."""
    print("\n20. a windows desktop reads its area, not only its frames")
    events: list = []

    def emit(identifier: str, event: str, payload: dict) -> None:
        events.append((identifier, event, payload))

    spec = {"kind": "windows", "id": "desk", "on": ["activated"],
            "children": [
                {"id": "w1", "title": "one",
                 "spec": {"kind": "label", "text": "hi"}},
            ]}
    canvas, _registry = api.build_spec(spec, events=emit)
    canvas.show()
    app.processEvents()
    canvas.resize(800, 600)
    app.processEvents()
    area = api.read_widget_value(canvas, "area")
    check(isinstance(area, dict) and area.get("width") == 800
          and area.get("height") == 600,
          f"the desktop reports its own room: {area}")
    check(api.read_widget_value(canvas, "windows") == ["w1"],
          "and still lists the windows it holds")
    canvas.close()


# --- twenty-one: a restart asks on the loop, not inside the caller's trip ---

def test_app_restart_deferred(app: Any, api: Any) -> None:
    """An addon asks for a restart through ``api.session``, which runs on the
    interface thread while the addon's own worker waits for the answer. Closing
    the window there tears the addons down around that request - the caller
    among them - so the shutdown waits on a thread that is only let go once the
    call returns: the window freezes half-shut, never relaunches, and the next
    hand-opened copy stands beside it as a second window. ``request_app_restart``
    now sets the restart and hands the close to the next turn of the loop."""
    print("\n21. a restart asks on the loop, not inside the caller's round trip")
    from core.views import take_restart_request

    def holder(win):
        return type("_Api", (), {"win": win})()

    class _Win:
        def __init__(self, answer: bool) -> None:
            self.calls = 0
            self._answer = answer

        def close(self) -> bool:
            self.calls += 1
            return self._answer

    take_restart_request()                    # clear any flag another section left

    # No window to close: refused outright, nothing queued.
    check(api.SessionAPI(holder(None)).request_app_restart() is False,
          "a restart with no window to close is refused")
    check(take_restart_request() is False,
          "a refused restart owes nothing to the entry point")

    # A window that closes: the ask is accepted, the close is deferred to the
    # loop rather than run here, and a restart stays owed.
    win = _Win(True)
    check(api.SessionAPI(holder(win)).request_app_restart() is True,
          "a restart with a window is accepted")
    check(win.calls == 0,
          f"the close ran inside the call ({win.calls}), not on the loop")
    app.processEvents()
    check(win.calls == 1,
          f"the deferred close ran {win.calls} times on the loop, want once")
    check(take_restart_request() is True,
          "a good close leaves a restart owed to the entry point")

    # A close turned down on the tick (an abort question answered "cancel")
    # withdraws the restart, so nothing relaunches later by accident.
    win2 = _Win(False)
    check(api.SessionAPI(holder(win2)).request_app_restart() is True,
          "the ask is accepted before the loop finds the close refused")
    app.processEvents()
    check(win2.calls == 1, "the refused close still happened once")
    check(take_restart_request() is False,
          "a close turned down withdraws the restart")


# --- twenty-two: a local shell spawned on a terminal, no SSH in sight ---

def test_local_shell(api: Any) -> None:
    """``spawn`` points a terminal screen at the machine's own shell.

    Stage A gives an addon a real OS process behind a ``terminal`` leaf without
    an SSH connection. Two things have to hold and neither can lean on a live
    ``cmd``/``sh`` child (flaky, and absent on a headless run), so they are
    driven from the outside:

    * the real :class:`core.net.LocalShellWorker`'s line editor - what it echoes,
      how it honours backspace, and the first-word alias expansion it runs on the
      way out - is exercised by feeding bytes straight to ``_type`` with a fake
      ``proc``, so no subprocess is started;
    * :func:`addon_api._terminal_spawn` wires a worker to the screen exactly the
      way a far shell is wired (``connected``->ready, ``received``->the screen,
      ``disconnected``->closed), seen through a fake worker factory; and the
      local/SSH gate split means ``spawn`` never asks the device, and a local
      shell's ``send`` bypasses ``_authorize_terminal`` where an SSH shell's
      would not."""
    print("\n22. spawn: a local shell on a terminal, and the SSH gate split")
    import core.net as net
    from PyQt6.QtCore import QObject, pyqtSignal

    # --- one: the worker's line editor, driven by hand, no child process ---

    class _Stdin:
        def __init__(self) -> None:
            self.written: list = []

        def write(self, text: str) -> None:
            self.written.append(text)

        def flush(self) -> None:
            pass

    class _Proc:
        def __init__(self) -> None:
            self.stdin = _Stdin()

        def poll(self) -> None:
            return None                 # still running: _submit writes through

    worker = net.LocalShellWorker({"cmd": "shell", "cwd": "/tmp",
                                   "aliases": {"l": "ll", "ll": "ls -al"}})
    check(getattr(worker, "is_local", False) is True,
          "a LocalShellWorker is tagged local, so the gate can tell it apart")
    echoed: list = []
    worker.received.connect(lambda chunk: echoed.append(chunk))
    worker.proc = _Proc()
    # "ll" reaches the shell fully expanded, chained l -> ll -> ls -al.
    worker._type("l -x\r")
    check(worker.proc.stdin.written == ["ls -al -x\n"],
          f"the alias expands the first word once, args ride along: "
          f"{worker.proc.stdin.written}")
    check(echoed == ["l", " ", "-", "x", "\r\n"],
          f"every typed key is echoed, then a newline on Enter: {echoed}")
    # Backspace eats the last character and tells the screen to rub it out.
    echoed.clear()
    worker.proc = _Proc()
    worker._type("abc\x7f\x7f\r")
    check(worker.proc.stdin.written == ["a\n"],
          f"two backspaces leave only 'a' to submit: "
          f"{worker.proc.stdin.written}")
    check(echoed.count("\b \b") == 2,
          f"each backspace rubs one character off the screen: {echoed}")
    # A lone Enter still goes through, so the shell prints its own prompt.
    worker.proc = _Proc()
    worker._type("\r")
    check(worker.proc.stdin.written == ["\n"],
          "an empty line is still submitted, not swallowed")

    # --- two: a fake worker factory, so spawn's wiring can be watched ---

    class FakeWorker(QObject):
        connected = pyqtSignal()
        received = pyqtSignal(str)
        failed = pyqtSignal(str)
        disconnected = pyqtSignal()
        is_local = True

        def __init__(self, settings, cols=80, rows=24, parent=None) -> None:
            super().__init__(parent)
            self.settings = dict(settings or {})
            self.state = {"connected": False, "host": "localhost",
                          "port": 0, "username": "",
                          "cwd": str(self.settings.get("cwd") or "")}
            self.started = False
            self.sent: list = []

        def isRunning(self) -> bool:            # noqa: N802 - Qt's own name
            return self.started

        def start(self) -> None:
            self.started = True

        def send(self, text: str) -> None:
            self.sent.append(text)

        def send_line(self, text: str) -> None:
            self.sent.append(text)

        def resize(self, cols: int, rows: int) -> None:
            pass

        def stop(self) -> None:
            self.started = False

    class FakeShell:
        """A stand-in connection for the gate tests: only what the gate and
        ``_terminal_send`` read - ``is_local``, ``isRunning`` and ``send``."""

        def __init__(self, local: bool) -> None:
            self.is_local = local
            self.state = {"connected": True}
            self.sent: list = []

        def isRunning(self) -> bool:            # noqa: N802 - Qt's own name
            return True

        def send(self, text: str) -> None:
            self.sent.append(text)

        def send_line(self, text: str) -> None:
            self.sent.append(text)

    class FakeDevice:
        def __init__(self) -> None:
            self.guarded: list = []

        def _guard(self, kind: str, uses_main: bool = True) -> None:
            self.guarded.append(kind)

        def _allow_own(self) -> None:
            pass

        def _mark(self, kind: str) -> None:
            pass

    class FakeApi:
        def __init__(self) -> None:
            self.device = FakeDevice()

    def make_terminal() -> Any:
        events: list = []

        def emit(identifier: str, event: str, payload: dict) -> None:
            events.append((identifier, event, payload))

        screen, _registry = api.build_spec(
            {"kind": "terminal", "id": "term",
             "on": ["ready", "closed", "failed"]}, events=emit)
        return screen, events

    original = net.LocalShellWorker
    try:
        net.LocalShellWorker = FakeWorker

        # The spawn wires the worker's three news signals onto the screen.
        screen, events = make_terminal()
        api._terminal_spawn(screen, {"cmd": "shell", "cwd": "/home/u"})
        shell = (screen._addon_terminal or {}).get("shell")
        check(isinstance(shell, FakeWorker),
              "spawn puts a worker on the terminal's state")
        check(shell.started is True, "and starts it")
        check("Starting local shell" in api._terminal_text(screen),
              "the screen says a local shell is starting")
        shell.received.emit("hello\n")
        check("hello" in api._terminal_text(screen),
              "what the worker sends is fed straight to the screen")
        shell.connected.emit()
        check(any(event == "ready" for _id, event, _p in events),
              "a connected worker reports ready to the addon")
        shell.disconnected.emit()
        check(any(event == "closed" for _id, event, _p in events),
              "and a dropped one reports closed")
        check((screen._addon_terminal or {}).get("shell") is None,
              "the closed shell is unhooked from the terminal")

        # spawn is not SSH-gated: the whole set_widget path reaches no device.
        screen2, _ev2 = make_terminal()
        fake_api = FakeApi()
        handled = api._write_control(fake_api, screen2, "spawn",
                                     {"cmd": "shell"})
        check(handled is True
              and (screen2._addon_terminal or {}).get("shell") is not None,
              "spawn ran the worker up through set_widget")
        check(fake_api.device.guarded == [],
              f"spawn never asks the device for SSH permission: "
              f"{fake_api.device.guarded}")

        # A local shell's typing bypasses _authorize_terminal too.
        screen3, _ev3 = make_terminal()
        local = FakeShell(local=True)
        screen3._addon_terminal["shell"] = local
        fake_api3 = FakeApi()
        api._write_control(fake_api3, screen3, "send", "dir")
        check(fake_api3.device.guarded == [],
              f"typing into a local shell skips the SSH gate: "
              f"{fake_api3.device.guarded}")
        check(local.sent == ["dir"],
              "and the words still reach the shell")

        # The contrast that makes the split mean something: an SSH-backed
        # shell's send is gated exactly as before.
        screen4, _ev4 = make_terminal()
        remote = FakeShell(local=False)
        remote.is_local = False
        screen4._addon_terminal["shell"] = remote
        fake_api4 = FakeApi()
        api._write_control(fake_api4, screen4, "send", "ls")
        check(fake_api4.device.guarded == ["ssh"],
              f"an SSH shell's send passes the gate: "
              f"{fake_api4.device.guarded}")
    finally:
        net.LocalShellWorker = original


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as error:                       # noqa: BLE001 - Qt is optional
        print(f"no Qt here, so the rows cannot be painted: {error}")
        return 2
    app = QApplication.instance() or QApplication([])
    try:
        import addon_api as api
    except Exception as error:                       # noqa: BLE001
        print(f"addon_api could not be imported: {error}")
        return 2
    print("addon_api: tickable table rows, painted and reported")
    test_paint(app, api)
    test_flip(api)
    test_silent_repaint(api)
    test_grid_repaint(app, api)
    test_grid_flip(api)
    test_taskbar_primitives(api)
    test_window_body_name(api)
    test_zoom_ladder()
    test_icon_cache(api)
    test_config_cache()
    test_grid_movement_and_rename(api)
    test_window_min_max_attention(api)
    test_shortcut_prop(api)
    test_list_attention_prop(api)
    test_resize_all_edges(api)
    test_drag_restore(api)
    test_snap_zone(api)
    test_canvas(app, api)
    test_player(api)
    test_desktop_area(app, api)
    test_app_restart_deferred(app, api)
    test_local_shell(api)
    app.processEvents()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    STATUS = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The verdict is printed: go out now, without walking Qt through the
    # interpreter's shutdown. Its destructors run there in whatever order the
    # garbage collection picks, and an offscreen widget suite has been seen to
    # raise an access violation on a busy machine after all checks passed -
    # which is how a green run turns into a 0xC0000005.
    os._exit(STATUS)
