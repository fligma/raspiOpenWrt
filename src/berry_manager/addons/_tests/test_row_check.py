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
