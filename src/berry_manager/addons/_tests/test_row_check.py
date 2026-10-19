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
