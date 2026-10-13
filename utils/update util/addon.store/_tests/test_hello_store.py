#!/usr/bin/env python3
"""A test for the hello_store addon, the store's own example.

hello_store is the tiny addon the store ships as a sample: one tab, a caption,
and a button that counts its presses and rewords the caption. There is no device
and no config to speak of, so this is mostly about the two things every store
addon has to get right - a manifest the app can read, and a tab that builds from
a spec and answers its widgets back through ``on_widget``.

What gets looked at:

* the manifest and the api version;
* the tab it declares really carries the caption, the button and the count, and
  the button reports back on ``clicked``;
* pressing the button counts, rewords the caption and the tally, and logs;
* a press on the wrong tab, the wrong widget or the wrong event does nothing.

The Berry addon store runs this file before it compiles hello_store, setting
``RASCONF_ADDON_SOURCE`` to the freshly downloaded ``.py`` so the copy under test
is the one that is about to be built. No internet, no device, no Qt window. Run
it from the Test panel, or::

    python addon.store/_tests/test_hello_store.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the copy a store install handed over, else the one in the
#: store folder beside this test.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "hello_store.py"
FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- reading back what the addon sent to its tab ---

def spec_ids(node: Any, out: dict) -> dict:
    """Every ``{"id": ...}`` in a spec, remembered by id, walking the whole tree."""
    if isinstance(node, dict):
        if "id" in node:
            out[str(node["id"])] = node
        for child in node.get("children") or []:
            spec_ids(child, out)
    elif isinstance(node, list):
        for child in node:
            spec_ids(child, out)
    return out


class Api(addon_client.StubAPI):
    """The recording stub, with a way to read one control's last set value."""

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None

    def add_tab_spec(self) -> Any:
        entry = self.last("tabs.add_tab")
        if entry is None:
            return None
        _where, _arguments, named = entry
        return named.get("spec")


# --- the checks ---

def test_the_manifest(addon: Any) -> None:
    print("\n1. the manifest and the new api")
    check(isinstance(addon.ADDON, dict) and addon.ADDON.get("name"),
          f"the addon must carry a manifest with a name: {addon.ADDON!r}")
    check(str(addon.ADDON.get("api")) == "1", "the addon must stay on api version 1")


def test_the_tab(addon: Any, api: Api) -> None:
    print("\n2. the tab really builds from its spec")
    addon.init(api)
    check(api.called("tabs.add_tab"), "init did not add its tab")
    names = [api.argument("tabs.add_tab", "name", a, k)
             for w, a, k in api.calls if w == "tabs.add_tab"]
    check(names == [addon.TAB], f"the addon opened {names}; one tab is what it owes")
    ids = spec_ids(api.add_tab_spec(), {})
    for wanted in ("hello", "again", "count"):
        check(wanted in ids, f"the spec never carries the id {wanted!r}")
    button = ids.get("again") or {}
    check(button.get("kind") == "button" and "clicked" in (button.get("on") or []),
          f"the button does not report back on clicked: {button!r}")


def test_pressing_the_button(addon: Any, api: Api) -> None:
    print("\n3. pressing the button counts and rewords")
    addon.init(api)
    addon.greetings = 0
    api.calls.clear()
    addon.on_widget(addon.TAB, "again", "clicked", {})
    check(addon.greetings == 1, f"one press should count one, got {addon.greetings}")
    hello = api.last_set("hello", "text")
    check(hello and "Hello from the addon store!" in str(hello) and "(1)" in str(hello),
          f"the caption should greet and count: {hello!r}")
    count = api.last_set("count", "text")
    check(count and "pressed 1" in str(count), f"the tally should read one: {count!r}")
    check(any("said 1" in line for line in api.logs),
          f"the press should be logged: {api.logs!r}")
    addon.on_widget(addon.TAB, "again", "clicked", {})
    check(addon.greetings == 2, f"a second press should count two, got {addon.greetings}")


def test_the_wrong_press(addon: Any, api: Api) -> None:
    print("\n4. a press that is not the button's does nothing")
    addon.init(api)
    addon.greetings = 0
    addon.on_widget(addon.TAB, "count", "clicked", {})       # not the button's id
    addon.on_widget("Some other tab", "again", "clicked", {})  # not this tab
    addon.on_widget(addon.TAB, "again", "pressed", {})       # not a click
    check(addon.greetings == 0,
          f"the button reacted to a press that was not its own: {addon.greetings}")


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)    # the Test pane wants it live
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    addon = addon_client.load_addon(SOURCE)
    print(f"hello_store: the addon at {SOURCE.name}")
    test_the_manifest(addon)
    test_the_tab(addon, Api(addon_id="hello_store"))
    test_pressing_the_button(addon, Api(addon_id="hello_store"))
    test_the_wrong_press(addon, Api(addon_id="hello_store"))

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
