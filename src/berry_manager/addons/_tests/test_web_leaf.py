"""Checks what a ``web`` leaf does with an address it has been handed.

An addon that builds a device's page into its own tab has to be able to point at
it before anybody has looked, and to look away again without losing it. Four
promises, none of which a data-only test can see:

* an address given to a leaf that is not on show is held until the moment it is,
  so a tab nobody has opened costs no fetch and no renderer;
* an address the leaf is showing already is not asked for again, so an addon that
  puts its own form over the page and the page back again does not bounce the
  person out of the session they had reached;
* the leaf answers ``url``, ``title``, ``pending`` and ``loading``, and takes the
  moves ``back``, ``forward``, ``reload`` and ``stop`` - and none of those asks
  falls through to a text widget's methods, which a page view does not have;
* a control that shows no page is refused with the reason rather than doing
  nothing quietly, and a build without QtWebEngine says so in the leaf itself.

The view is stood in for by a widget that keeps the calls made to it. A real
``QWebEngineView`` starts a render process, and no test on an offscreen runner
should be paying for that or betting its exit code on a sandbox - so the leaf is
only ever *built* here in a build that has no QtWebEngine in it, where what comes
out is a one-line notice and no browser is involved.

Run it on the command line::

    python addons/_tests/test_web_leaf.py
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

#: An address that looks like the dashboard this leaf was written for.
FIRST = "http://127.0.0.1:8989/cgi-bin/index.py"
SECOND = "https://192.168.1.1/cgi-bin/index.py"

api: Any = None                  # addon_api, imported once Qt is about
api_app: Any = None              # the QApplication every widget hangs off

try:                                         # the whole file is Qt's business
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
except Exception as _error:                  # noqa: BLE001 - checked in main()
    QWidget = QLabel = None
    NO_QT = str(_error)
else:
    NO_QT = ""


def check(condition: object, text: str) -> None:
    """One thing the leaf has to do; all are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


class _Address:
    """Qt's QUrl, as far as the leaf cares: it is only asked to say itself."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def toString(self) -> str:
        return self._text


class FakeView(QWidget):
    """A stand for the page view: it keeps what it was asked to do and fetches
    nothing, so what is measured is the hold-and-fire bookkeeping."""

    def __init__(self) -> None:
        super().__init__()
        self.loads: list[str] = []
        self.moves: list[str] = []
        self._at = _Address("")

    def load(self, url: Any) -> None:
        text = url.toString() if hasattr(url, "toString") else str(url)
        self.loads.append(text)
        self._at = _Address(text)

    def url(self) -> _Address:
        return self._at

    def title(self) -> str:
        return "Berry - Dashboard"

    def isLoading(self) -> bool:
        return False

    def reload(self) -> None:
        self.moves.append("reload")

    def back(self) -> None:
        self.moves.append("back")

    def forward(self) -> None:
        self.moves.append("forward")

    def stop(self) -> None:
        self.moves.append("stop")


def leaf() -> tuple:
    """A web leaf as ``_make_leaf`` builds one - the view with its holder on it -
    in a window that is on screen while the leaf itself is not."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    view = FakeView()
    view._addon_web_load = api._WebLeafLoad(view)
    layout.addWidget(view)
    view.hide()                       # the tab was built and never opened
    holder.show()
    api_app.processEvents()
    return view, holder


# --- one: an address waits for the leaf to be looked at ---

def test_hold() -> None:
    print("\n1. a leaf nobody has seen holds its address")
    view, holder = leaf()
    check(api.read_widget_value(view, "url") == "",
          "a leaf that was never given an address answered one")
    api.apply_widget_property(view, "url", FIRST)
    check(view.loads == [], f"the page was fetched while hidden: {view.loads}")
    hold = view._addon_web_load
    check(hold.pending() == FIRST, f"the address was not held: {hold.pending()!r}")
    # What an addon reads back is the address it is going to rather than the blank
    # the view holds, since a tab that has never been opened still has to be able
    # to say where it stands.
    check(api.read_widget_value(view, "url") == FIRST,
          "a held address is not read back as the leaf's own")
    check(api.read_widget_value(view, "text") == FIRST,
          "the address is not read back as its text")
    check(api.read_widget_value(view, "pending") == FIRST,
          "the held address is not answered for 'pending'")
    # Asking twice for the same thing is one ask, not two loads queued up.
    api.apply_widget_property(view, "url", FIRST)
    api.apply_widget_property(view, "url", FIRST)
    check(hold.pending() == FIRST, "the same address was queued over itself")

    # Show it: the held address goes, once, and is no longer held.
    view.show()
    api_app.processEvents()
    check(view.loads == [FIRST], f"the page was not fetched on being shown: "
                                 f"{view.loads}")
    check(hold.pending() == "", "the address was kept held after it went")
    check(api.read_widget_value(view, "url") == FIRST,
          "a loaded page does not read its own address back")

    # And the leaf keeps it: asked again now, it is not fetched again. This is the
    # half that stops an addon's own form from bouncing a logged-in person out.
    api.apply_widget_property(view, "url", FIRST)
    check(view.loads == [FIRST], f"the page was reloaded for the same address: "
                                 f"{view.loads}")
    api.apply_widget_property(view, "url", SECOND)
    check(view.loads == [FIRST, SECOND], f"a new address was not gone to: "
                                         f"{view.loads}")
    check(api.read_widget_value(view, "url") == SECOND,
          "the leaf still read the address it had left")
    holder.deleteLater()


# --- two: what the leaf answers, and what it refuses ---

def test_reads_and_moves() -> None:
    print("\n2. the leaf's own questions")
    view, holder = leaf()
    api.apply_widget_property(view, "url", FIRST)
    check(api.read_widget_value(view, "title") == "Berry - Dashboard",
          "the page's title was not answered")
    check(api.read_widget_value(view, "loading") is False,
          "'loading' is not a plain yes or no")
    check(api.read_widget_value(view, "nonsense") is None,
          "a question the leaf cannot answer was made up for it")
    for prop in ("back", "forward", "reload", "stop"):
        api.apply_widget_property(view, prop, "")
    check(view.moves == ["back", "forward", "reload", "stop"],
          f"the moves did not reach the view: {view.moves}")
    # An address that is nothing at all is not a fetch for a blank.
    view._addon_web_load.goto("   ")
    check(view.loads == [], "blank was taken for an address to go to")
    holder.deleteLater()

    print("\n2b. a control that shows no page")
    label = QLabel("Just a label")
    for prop in ("url", "reload", "back"):
        try:
            api.apply_widget_property(label, prop, FIRST)
            check(False, f"'{prop}' on a plain label was accepted quietly")
        except api.AddonError as error:
            check("web leaf" in str(error),
                  f"'{prop}' did not say what it belongs to: {error}")
    check(api.read_widget_value(label, "text") == "Just a label",
          "a label's own text was taken over by the leaf's reading")


# --- three: the leaf a spec actually builds ---

def test_the_build() -> None:
    print("\n3. what the spec's ``web`` kind builds")
    if api.webengine_view() is not None:
        look("QtWebEngine is here, so the kind builds a real view and this test "
             "does not build one - a view means a render process, which an "
             "offscreen runner has no business starting. The notice below is "
             "what a build without the module gives.")
        return
    body, registry = api.build_spec({"kind": "column", "children": [
        {"kind": "web", "id": "page", "visible": False, "expand": True},
    ]})
    widget = registry.get("page")
    check(widget is not None and not api._is_web_leaf(widget),
          f"without QtWebEngine the kind should be a plain notice, not "
          f"{type(widget).__name__ if widget else 'nothing'}")
    if widget is None:
        return
    text = str(api.read_widget_value(widget, "text") or "")
    check("QtWebEngine" in text,
          f"the notice does not say what is missing: {text!r}")
    try:
        api.apply_widget_property(widget, "url", FIRST)
        check(False, "an address was accepted by a leaf that cannot show one")
    except api.AddonError as error:
        check("web leaf" in str(error), f"the refusal said nothing useful: {error}")
    body.deleteLater()


def main() -> int:
    global api, api_app
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if QWidget is None:
        print(f"no Qt here, so no leaf can be built: {NO_QT}")
        return 2
    api_app = make_app()
    try:
        import addon_api
    except Exception as error:                       # noqa: BLE001
        print(f"addon_api could not be imported: {error}")
        return 2
    api = addon_api
    print(f"addon_api: a web leaf, QtWebEngine "
          f"{'missing' if api.webengine_view() is None else 'here'}")
    test_hold()
    test_reads_and_moves()
    test_the_build()
    api_app.processEvents()

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


def make_app() -> Any:
    """The one application, made the way the app's own tests make it."""
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv[:1])


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
