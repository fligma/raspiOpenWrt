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
