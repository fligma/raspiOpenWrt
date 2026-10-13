#!/usr/bin/env python3
"""A test for the openwrt_package_manager addon (for the addon store).

It runs the addon with the recording stub from :mod:`addon_client`, checking:
* the manifest services and api version;
* config declaration for switching between apk and opkg backends;
* tab creation and sub-page / widget structures;
* fetching installed packages using apk (default) and opkg;
* updating feeds, searching online, and handling widget events[cite: 5].

Run it from the Test panel, or on the command line::

    python addon.store/_tests/test_openwrt_package_manager.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the store install source or local tree[cite: 6].
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "openwrt_package_manager.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = ROOT / "addons" / "openwrt_package_manager.py"

FAILURES: list[str] = []
api_module = None


def check(condition: object, text: str) -> None:
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    print(f"       {text}")


class Api(addon_client.StubAPI):
    """The recording stub tailored for package manager operations."""

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.form: dict = {}
        self.dialog_answer: dict | None = None
        self.question_answer: bool = True

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments, named, fallback)
        if method == "device.run_async":
            command = str(ask("command", ""))
            out = ""
            if "info -v" in command:
                out = "base-files-153-r1\nbusybox-1.36.1-r3\nkernel-6.6.21-1\n"
            elif "list-installed" in command:
                out = "base-files - 153-r1\nbusybox - 1.36.1-r3\n"
            elif "update" in command:
                out = "Updating package list...\nSignature check passed."
            elif "search" in command or "list" in command:
                out = "htop - 3.3.0-r1 - Interactive process viewer\ncurl - 8.6.0-r0 - Client url tool"
            result = {"code": 0, "out": out, "err": ""}
            callback = ask("on_done")
            if callable(callback):
                callback(result)
            return result
        if method == "tabs.widget_value":
            identifier = ask("identifier", "")
            return self.form.get(identifier)
        if method == "ui.question":
            return self.question_answer
        return super().default(method, arguments, named)

    def last_set(self, identifier: str, prop: str) -> Any:
        for where, arguments, _named in reversed(self.calls):
            if (where == "tabs.set_widget" and len(arguments) >= 4
                    and arguments[1] == identifier and arguments[2] == prop):
                return arguments[3]
        return None


def test_manifest_and_config(addon: Any, api: Api) -> None:
    print("\n1. manifest and config defaults")
    check(addon.ADDON.get("requires") == ["ssh", "sftp"],
          f"requires ssh and sftp services: {addon.ADDON.get('requires')!r}[cite: 5]")
    check(str(addon.ADDON.get("api")) == "1", "addon api version is 1[cite: 5]")
    addon.init(api)
    check(api.called("config.declare"), "addon must declare its config form[cite: 5]")
    fields = api.declared.get("fields", [])
    check(len(fields) == 1 and fields[0].get("key") == "package_manager",
          f"package_manager choice field declared correctly: {fields}[cite: 5]")
    check(api.declared.get("defaults", {}).get("package_manager") == "apk",
          "default package manager backend must be set to apk[cite: 5]")


def test_package_fetching(addon: Any, api: Api) -> None:
    print("\n2. package fetching (apk default vs opkg mode)")
    fresh_addon(api)
    addon.fetch_installed()
    check(api.asked("device.run_async", "command") == "apk info -v",
          f"default fetch command uses apk info -v: {api.asked('device.run_async', 'command')!r}[cite: 5]")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 3, f"parsed 3 packages successfully under apk: {rows}[cite: 5]")

    # Switch backend to opkg via config
    api.config_values["package_manager"] = "opkg"
    addon.fetch_installed()
    check(api.asked("device.run_async", "command") == "opkg list-installed",
          f"switched backend runs opkg list-installed: {api.asked('device.run_async', 'command')!r}[cite: 5]")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 2, f"parsed 2 packages successfully under opkg: {rows}[cite: 5]")
    api.config_values.pop("package_manager", None)


def test_search_and_feeds(addon: Any, api: Api) -> None:
    print("\n3. feed updates and online package search")
    fresh_addon(api)
    addon.update_feeds()
    check(api.asked("device.run_async", "command") == "apk update",
          f"feed update executes apk update by default: {api.asked('device.run_async', 'command')!r}[cite: 5]")

    addon.search_online("htop")
    check(api.asked("device.run_async", "command") == "apk search htop",
          f"online search executes apk search: {api.asked('device.run_async', 'command')!r}[cite: 5]")
    browse_rows = api.last_set("browse_table", "rows")
    check(browse_rows and len(browse_rows) == 2, f"browse results table populated: {browse_rows}[cite: 5]")


def test_widget_actions(addon: Any, api: Api) -> None:
    print("\n4. widget interactions (install and remove)")
    fresh_addon(api)
    
    # Test package removal event
    addon._installed_cache = [("curl", "8.6.0")]
    addon.on_widget("Package Manager", "inst_table", "selected", {"texts": ["curl"]})
    api.question_answer = True
    addon.on_widget("Package Manager", "btn_remove", "clicked", {})
    check(api.asked("device.run_async", "command") == "apk del curl",
          f"remove button triggers apk del: {api.asked('device.run_async', 'command')!r}[cite: 5]")

    # Test package installation event
    api.results["tabs.widget_value"] = ["htop - 3.3.0"]
    addon.on_widget("Package Manager", "btn_install", "clicked", {})
    check(api.asked("device.run_async", "command") == "apk add htop",
          f"install button triggers apk add: {api.asked('device.run_async', 'command')!r}[cite: 5]")


def fresh_addon(api: Api) -> None:
    api_module.api = api
    api_module._installed_cache = []


def main() -> int:
    global api_module, FAILURES
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}[cite: 5, 6]")
        return 2
    addon = addon_client.load_addon(SOURCE)
    api_module = addon

    print(f"openwrt_package_manager: testing addon at {SOURCE.name}[cite: 5, 6]")
    api = Api(addon_id="openwrt_package_manager")
    test_manifest_and_config(addon, api)
    test_package_fetching(addon, api)
    test_search_and_feeds(addon, api)
    test_widget_actions(addon, api)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall package manager checks passed[cite: 5]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())