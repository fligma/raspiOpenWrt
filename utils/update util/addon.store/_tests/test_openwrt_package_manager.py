#!/usr/bin/env python3
"""A test for the openwrt_package_manager addon (for the addon store).

It runs the addon with the recording stub from :mod:`addon_client`, checking:
* the manifest services and api version;
* config declaration for switching between apk and opkg backends;
* tab creation and sub-page / widget structures;
* fetching installed packages using apk (default) and opkg;
* updating feeds, searching online, and handling widget interactions;
* the search term and package names being quoted before they reach the shell.

Run it from the Test panel, or on the command line::

    python addon.store/_tests/test_openwrt_package_manager.py
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the store install source or local tree.
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
          f"requires ssh and sftp services: {addon.ADDON.get('requires')!r}")
    check(str(addon.ADDON.get("api")) == "1", "addon api version is 1")
    addon.init(api)
    check(api.called("config.declare"), "addon must declare its config form")
    fields = api.declared.get("fields", [])
    check(len(fields) == 1 and fields[0].get("key") == "package_manager",
          f"package_manager choice field declared correctly: {fields}")
    check(api.declared.get("defaults", {}).get("package_manager") == "apk",
          "default package manager backend must be set to apk")


def test_package_fetching(addon: Any, api: Api) -> None:
    print("\n2. package fetching (apk default vs opkg mode)")
    fresh_addon(api)
    addon.fetch_installed()
    check(api.asked("device.run_async", "command") == "apk info -v",
          f"default fetch command uses apk info -v: {api.asked('device.run_async', 'command')!r}")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 3, f"parsed 3 packages successfully under apk: {rows}")

    # Switch backend to opkg via config
    api.config_values["package_manager"] = "opkg"
    addon.fetch_installed()
    check(api.asked("device.run_async", "command") == "opkg list-installed",
          f"switched backend runs opkg list-installed: {api.asked('device.run_async', 'command')!r}")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 2, f"parsed 2 packages successfully under opkg: {rows}")
    api.config_values.pop("package_manager", None)


def test_search_and_feeds(addon: Any, api: Api) -> None:
    print("\n3. feed updates and online package search")
    fresh_addon(api)
    addon.update_feeds()
    check(api.asked("device.run_async", "command") == "apk update",
          f"feed update executes apk update by default: {api.asked('device.run_async', 'command')!r}")

    addon.search_online("htop")
    check(api.asked("device.run_async", "command") == "apk search htop",
          f"online search executes apk search: {api.asked('device.run_async', 'command')!r}")
    browse_rows = api.last_set("browse_table", "rows")
    check(browse_rows and len(browse_rows) == 2, f"browse results table populated: {browse_rows}")


def test_widget_actions(addon: Any, api: Api) -> None:
    print("\n4. widget interactions (install and remove)")
    fresh_addon(api)
    
    # Test package removal event by mocking form selection value
    api.form["inst_table"] = ["curl"]
    api.question_answer = True
    addon.on_widget("Package Manager", "btn_remove", "clicked", {})
    check(api.asked("device.run_async", "command") == "apk del curl",
          f"remove button triggers apk del: {api.asked('device.run_async', 'command')!r}")

    # Test package installation event by mocking form selection value
    api.form["browse_table"] = ["htop - 3.3.0"]
    addon.on_widget("Package Manager", "btn_install", "clicked", {})
    check(api.asked("device.run_async", "command") == "apk add htop",
          f"install button triggers apk add: {api.asked('device.run_async', 'command')!r}")


def test_quoting(addon: Any, api: Api) -> None:
    """What a user types, and what a table shows, must not reach the device
    shell as shell syntax. The test for this is what the shell would cut the
    command into: shlex.split answers that, so a name that ought to stay one
    word and a name that would break into two commands cannot be confused.
    A plain name quotes to itself, which is why the ordinary command checks
    above are still written plainly and hold the other half."""
    print("\n5. shell punctuation in a name cannot become a second command")
    fresh_addon(api)

    def words() -> list[str]:
        return shlex.split(api.asked("device.run_async", "command"))

    addon.search_online("htop; reboot")
    check(words() == ["apk", "search", "htop; reboot"],
          f"a search term's ';' stays inside its own word: "
          f"{api.asked('device.run_async', 'command')!r}")

    api.config_values["package_manager"] = "opkg"
    addon.search_online("x'$(id)")
    check(words() == ["opkg", "list", "x'$(id)*"],
          f"a quote in a search term cannot close itself out of its word: "
          f"{api.asked('device.run_async', 'command')!r}")
    api.config_values.pop("package_manager", None)

    api.form["inst_table"] = ["curl; rm -rf /"]
    addon.on_widget("Package Manager", "btn_remove", "clicked", {})
    check(words() == ["apk", "del", "curl; rm -rf /"],
          f"a name read back from the table is quoted too: "
          f"{api.asked('device.run_async', 'command')!r}")

    api.form["browse_table"] = ["htop; touch /tmp/pwned - 3.3.0"]
    addon.on_widget("Package Manager", "btn_install", "clicked", {})
    check(len(words()) == 3,
          f"install is handed one package word and no more: "
          f"{api.asked('device.run_async', 'command')!r}")


def fresh_addon(api: Api) -> None:
    api_module.api = api
    api_module._installed_cache = []
    api.form.clear()


def main() -> int:
    global api_module, FAILURES
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the marks these tabs paint (● ○ ✓),
        # so anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    addon = addon_client.load_addon(SOURCE)
    api_module = addon

    print(f"openwrt_package_manager: testing addon at {SOURCE.name}")
    api = Api(addon_id="openwrt_package_manager")
    test_manifest_and_config(addon, api)
    test_package_fetching(addon, api)
    test_search_and_feeds(addon, api)
    test_widget_actions(addon, api)
    test_quoting(addon, api)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall package manager checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())