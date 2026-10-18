"""Checks that a page gives its room to the widgets that asked for it.

Three layout promises the tabs rest on, and none is visible in a data-only
test. The first two were broken in the same way: a control was left growable
when it should have kept to its own contents, so it ate half the page and the
table under it was pushed to a strip at the bottom.

* a word-wrapped status line takes exactly the height its text needs at the
  width it is given - and takes another line when the pane narrows rather than
  clipping or hoarding the room a splitter below it was asking for;
* a ``scroll`` pane leaves the room its own column asks for *under* its
  contents, which is what pins a form to the top of its pane instead of
  stretching it over the whole height;
* a ``spacer`` claims slack along the line it lies on and never across it:
  a column that ends in a spring hugs its own width inside a row, so the pane
  beside it - a web view filling a tab, say - gets the slack it was promised
  instead of splitting the page fifty-fifty with a rail.

Run it on the command line::

    python addons/_tests/test_page_fill.py
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

#: Long enough to need more than one line once the page is narrowed.
SENTENCE = ("A vault of passwords, kept encrypted on this computer. Nothing here "
            "reaches a device, nothing is phoned home, and the file is only "
            "readable once the master password is typed in for this session.")


def check(condition: object, text: str) -> None:
    """One thing the layout has to do; all are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


def laid_out(api: Any, spec: dict, width: int, height: int):
    """Build the page, show it at this size and let Qt have the last word."""
    body, registry = api.build_spec(spec)
    body.resize(width, height)
    body.show()
    api_app.processEvents()
    body.resize(width, height)
    api_app.processEvents()
    return body, registry


def page(status_text: str) -> dict:
    """The shape every one of these tabs has: a strip of buttons, a status
    line, then a splitter whose left pane carries the list."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [{"kind": "button", "text": caption}
                                     for caption in ("Unlock", "Lock", "Backup")]},
        {"kind": "label", "id": "status", "word_wrap": True, "expand": True,
         "text": status_text},
        {"kind": "splitter", "sizes": [1, 1], "children": [
            {"kind": "column", "children": [
                {"kind": "row", "children": [{"kind": "line", "id": "search",
                                              "expand": True},
                                             {"kind": "button", "text": "+ New"}]},
                {"kind": "table", "id": "list", "columns": ["a", "b", "c"]},
                {"kind": "row", "children": [{"kind": "check", "text": "show"}]},
            ]},
            {"kind": "scroll", "children": [
                {"kind": "column", "stretch": True, "children": [
                    {"kind": "group", "text": "The entry", "layout": "form",
                     "children": [{"kind": "line", "label": "Name"},
                                  {"kind": "line", "label": "User"}]},
                ]},
            ]},
        ]},
    ]}


# --- one: the wrapped line hugs, and the table under it gets the room ---

def test_hug(api: Any) -> None:
    print("\n1. a word-wrapped line takes the height its text needs")
    body, registry = laid_out(api, page(SENTENCE), 1100, 700)
    status = registry["status"]
    table = registry["list"]
    need = status.heightForWidth(status.width())
    look(f"line {status.width()}x{status.height()}, the text needs {need}")
    check(need > 0, "a wrapped label answered nothing for its own height")
    check(abs(status.height() - need) <= 2,
          f"the status line took {status.height()} for {need}px of text")
    check(table.height() > body.height() * 0.6,
          f"the table was left {table.height()} of a {body.height()}px page")

    # Narrow the page: the line buys another row instead of clipping, and the
    # table gives back only what the extra row costs.
    small, registry2 = laid_out(api, page(SENTENCE), 460, 700)
    line = registry2["status"]
    want = line.heightForWidth(line.width())
    look(f"at 460 wide: line {line.width()}x{line.height()}, needs {want}")
    check(line.height() + 2 >= want,
          f"the narrowed page clipped the status line to {line.height()} "
          f"when it needs {want}")
    check(line.height() > status.height(),
          "a much narrower page left the line the same height - it did not wrap")
    check(registry2["list"].height() > 0
          and registry2["list"].height() < table.height(),
          "the table did not make room for the extra line")

    # The words change under the same label - a job reporting progress - and
    # the line grows for them rather than being capped at what it first said.
    api.apply_widget_property(status, "text", SENTENCE * 3)
    body.resize(1100, 700)
    api_app.processEvents()
    grew = status.heightForWidth(status.width())
    look(f"after a longer message: {status.height()} for {grew} needed")
    check(status.height() + 2 >= grew,
          f"a longer message was clipped to {status.height()} needing {grew}")
    check(grew > need, "the longer message needed no more room, so the test "
                       "measured nothing")
    small.deleteLater()
    body.deleteLater()


# --- two: a scrolled column keeps the room it asks for under its contents ---

def test_scroll_stretch(api: Any) -> None:
    print("\n2. a scroll pane leaves the spare room under what it carries")
    form = {"kind": "group", "id": "entry", "text": "The entry", "layout": "form",
            "children": [{"kind": "line", "label": "Name"},
                         {"kind": "line", "label": "User"}]}
    spec = {"kind": "scroll", "children": [
        {"kind": "column", "stretch": True, "children": [form]},
    ]}
    body, registry = laid_out(api, spec, 600, 500)
    box = registry["entry"]
    hint = box.sizeHint().height()
    look(f"pane {body.height()} tall, the form {box.height()} (it wants {hint})")
    check(body.widget().height() > hint * 2,
          "the pane is not taller than its contents, so nothing was stretched")
    check(abs(box.height() - hint) <= 2,
          f"the scrolled form was stretched to {box.height()} instead of the "
          f"{hint} it asked for - the stretch was not left under it")
    body.deleteLater()

    # And the same pane without the promise does stretch, which is what makes
    # the check above say something.
    plain = {"kind": "scroll", "children": [
        {"kind": "column", "children": [form]},
    ]}
    body2, registry2 = laid_out(api, plain, 600, 500)
    box2 = registry2["entry"]
    look(f"without the promise: the form took {box2.height()}")
    check(box2.height() > hint + 20,
          "a column with no stretch claim left its contents at their own "
          "height too - so the promise is not what makes the difference")
    body2.deleteLater()


# --- three: a spring keeps to the line it lies on ---

def test_spring_line(api: Any) -> None:
    print("\n3. a spacer claims slack along its own line only")
    spec = {"kind": "row", "expand": "both", "children": [
        {"kind": "column", "id": "rail", "children": [
            {"kind": "button", "text": ""},
            {"kind": "button", "text": ""},
            {"kind": "spacer"},
        ]},
        {"kind": "label", "id": "page", "text": "the page", "expand": "both"},
    ]}
    body, registry = laid_out(api, spec, 1200, 700)
    rail, pane = registry["rail"], registry["page"]
    look(f"rail {rail.width()} wide (it wants {rail.sizeHint().width()}), "
         f"pane {pane.width()} of {body.width()}")
    check(pane.width() >= body.width() * 0.8,
          f"a rail ending in a spring took {rail.width()} beside a page of "
          f"{pane.width()} - the spring claimed across its own line")
    check(rail.width() <= rail.sizeHint().width() + 24,
          f"the rail grew to {rail.width()} over its own "
          f"{rail.sizeHint().width()}")

    # The same claim across a column: a strip of buttons ending in a spring
    # must not reach up for the height the body under it was promised.
    stack = {"kind": "column", "expand": "both", "children": [
        {"kind": "row", "id": "bar", "children": [
            {"kind": "button", "text": "go"}, {"kind": "spacer"}]},
        {"kind": "label", "id": "below", "text": "body", "expand": "both"},
    ]}
    body2, registry2 = laid_out(api, stack, 700, 500)
    bar = registry2["bar"]
    below = registry2["below"]
    look(f"bar {bar.height()} tall, below {below.height()} of {body2.height()}")
    check(below.height() >= body2.height() * 0.6,
          f"a row ending in a spring claimed the height: the body under it "
          f"was left {below.height()} of {body2.height()}")
    body.deleteLater()
    body2.deleteLater()


api_app: Any = None


def main() -> int:
    global api_app
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as error:                       # noqa: BLE001 - Qt is optional
        print(f"no Qt here, so nothing can be laid out: {error}")
        return 2
    api_app = QApplication.instance() or QApplication([])
    try:
        import addon_api as api
    except Exception as error:                       # noqa: BLE001
        print(f"addon_api could not be imported: {error}")
        return 2
    print("addon_api: wrapped lines hug, scrolled columns leave room, "
          "springs keep to their line")
    test_hug(api)
    test_scroll_stretch(api)
    test_spring_line(api)
    api_app.processEvents()

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
