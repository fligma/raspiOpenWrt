#!/usr/bin/env python3
"""A test for the cursor-following, fading tool-tip in manager_widgets.py.

The old behaviour was Qt's own ``QToolTip``: it pops on at a fixed spot after a
pause, does not move with the pointer, and is inconsistent about appearing. The
replacement is one application event filter that draws a bubble which fades in
and follows. Offscreen there is no real pointer to walk, so what can be proved
without a window is what matters:

* installing gives the window a single keeper and puts it on the application;
* a widget carrying text gets the bubble and the native tool-tip is swallowed
  (the filter accepts the event), and taking it away works;
* a widget with no text of its own is left to Qt, so nothing is eaten that the
  built-in machinery still needs (menus, untipped children);
* a group box still shows its note only over the title, and it now rides the
  same bubble rather than the old fixed one.

Run it on the command line::

    python addons/_tests/test_smart_tip.py
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
    """One thing the keeper has to do; all of them are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


def _tooltip_event(x: int = 4, y: int = 4):
    """A hover tip event at a local point; the global half is not read for text."""
    from PyQt6.QtCore import QEvent, QPoint
    from PyQt6.QtGui import QHelpEvent

    return QHelpEvent(QEvent.Type.ToolTip, QPoint(x, y), QPoint(x, y))


# --- one: installing hands the window one keeper ---

def test_installed(app: Any) -> None:
    print("\n1. the window gets one cursor-following keeper")
    from manager_widgets import install_smart_tooltips, smart_tip

    engine = install_smart_tooltips(app)
    check(smart_tip() is engine, "smart_tip() does not report the keeper it made")
    check(type(engine).__name__ == "_SmartTip",
          f"install returned a {type(engine).__name__}, not the keeper")
    check(not engine.showing(), "a fresh keeper already shows a bubble")


# --- two: a widget with text gets the bubble, native is swallowed ---

def test_text_widget(app: Any) -> None:
    print("\n2. a widget with a note gets the bubble")
    from PyQt6.QtWidgets import QLabel

    from manager_widgets import smart_tip

    engine = smart_tip()
    label = QLabel("hover me")
    label.setToolTip("A helpful note")
    handled = app.sendEvent(label, _tooltip_event())
    check(handled is True, "the tip event was not taken over (native still shows)")
    check(engine.current_text() == "A helpful note",
          f"the bubble says {engine.current_text()!r}, not the note")
    check(engine.showing(), "the bubble did not come up over the widget")
    engine.dismiss()
    check(not engine.showing(), "dismiss left the bubble on screen")
    label.deleteLater()


# --- three: nothing to say is left to Qt ---

def test_no_text(app: Any) -> None:
    print("\n3. a widget without a note is not intercepted")
    from PyQt6.QtWidgets import QLabel

    from manager_widgets import smart_tip

    engine = smart_tip()
    plain = QLabel("no tooltip here")
    app.sendEvent(plain, _tooltip_event())
    check(not engine.showing(),
          "a widget with nothing to say still raised a bubble - menus and "
          "children would lose their native tip")
    plain.deleteLater()


# --- four: a group box note rides the bubble, over the title only ---

def test_group_title(app: Any) -> None:
    print("\n4. a group box shows its title note through the bubble")
    from PyQt6.QtWidgets import QGroupBox

    from manager_widgets import smart_tip, tip_on_title

    engine = smart_tip()
    box = QGroupBox("Options")
    tip_on_title(box, "only over the title")
    box.resize(300, 200)

    # The title words sit on the top border; a point there shows the note.
    over = _tooltip_event(x=14, y=2)
    app.sendEvent(box, over)
    check(engine.current_text() == "only over the title",
          f"the title note did not reach the bubble: {engine.current_text()!r}")
    check(engine.showing(), "hovering the title did not raise the bubble")

    # Well inside the panel the note is gone, so it never follows across options.
    inside = _tooltip_event(x=150, y=120)
    app.sendEvent(box, inside)
    check(not engine.showing(), "the note stayed up over the group's body")

    # The pure geometry agrees with what the bubble showed.
    from PyQt6.QtCore import QPoint
    from manager_widgets import _TitleTip

    check(_TitleTip._over_title(box, QPoint(14, 2)) is True,
          "_over_title says the title point is not over the title")
    check(_TitleTip._over_title(box, QPoint(150, 120)) is False,
          "_over_title says a body point is over the title")
    box.deleteLater()


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as error:                       # noqa: BLE001 - Qt is optional
        print(f"no Qt here, so the keeper cannot be built: {error}")
        return 2
    app = QApplication.instance() or QApplication([])
    print("manager_widgets: the cursor-following, fading tool-tip")
    test_installed(app)
    test_text_widget(app)
    test_no_text(app)
    test_group_title(app)
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
