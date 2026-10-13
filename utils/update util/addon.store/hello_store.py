#!/usr/bin/env python3
"""A tiny example addon

Adds a ``Hello store`` tab with one button. testfile for addon_store
"""

from __future__ import annotations

from typing import Any

ADDON = {
    "name": "Hello from the store",
    "version": "1.0",
    "author": "fligma",
    "description": "An example addon"
                   "test addon",
    "requires": [],
    "api": 1,
}

TAB = "Hello store"

api = None                  # filled in by init()
greetings = 0


def build_tab() -> None:
    """The tab as a spec: a caption and one button that reports back."""
    spec = {"kind": "column", "children": [
        {"kind": "label", "id": "hello", "text": "Hello from the addon store!",
         "style": "color: #e8e8e8; font-size: 13pt; font-weight: 600;"},
        {"kind": "row", "children": [
            {"kind": "button", "id": "again", "text": "Say it again",
             "on": ["clicked"]},
            {"kind": "spacer"},
            {"kind": "label", "id": "count", "text": "",
             "style": "color: #9a9a9a;"},
        ]},
    ]}
    api.tabs.add_tab(TAB, spec=spec)


def say_again(_checked: bool = False) -> None:
    """Count the presses and change the caption."""
    global greetings
    if api is None:
        return
    greetings += 1
    api.tabs.set_widget(TAB, "hello", "text",
                        f"Hello from the addon store! ({greetings})")
    api.tabs.set_widget(TAB, "count", "text", f"pressed {greetings} time(s)")
    api.log(f"hello said {greetings} time(s)")


def init(plugin_api: Any) -> None:
    """Build the tab."""
    global api
    api = plugin_api
    build_tab()


def start() -> None:
    """Nothing to do beyond what init did."""
    return None


def shutdown() -> None:
    global api
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A button on the tab was clicked: run what its id means."""
    if str(tab) != TAB or str(event) != "clicked":
        return
    if str(identifier) == "again":
        say_again()


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
