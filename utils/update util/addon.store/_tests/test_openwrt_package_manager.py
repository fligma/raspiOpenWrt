#!/usr/bin/env python3
"""A test for the openwrt_package_manager addon (for the addon store).

It runs the addon with the recording stub from :mod:`addon_client`, checking:
* the manifest services and api version;
* config declaration for switching between apk and opkg backends;
* tab creation and sub-page / widget structures;
* fetching installed packages using apk (default) and opkg;
* the descriptions folded in from the device's own index, in either backend;
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
    """The recording stub tailored for package manager operations.

    What each command answers is what the device really writes: apk gives one
    ``name-version`` per line for both its list and its search, and its index
    read comes back as JSON records; opkg writes ``name - version`` for what is
    installed and ``name - version - description`` for the feed list.
    """

    #: What the index read answers, in each backend's own shape.
    APK_INDEX = ('[\n  {"name": "base-files", "version": "153-r1",'
                 ' "description": "Release/file version"},\n'
                 '  {"name": "busybox", "version": "1.36.1-r3",'
                 ' "description": "Size optimized toolset"},\n'
                 '  {"name": "htop", "version": "3.3.0-r1",'
                 ' "description": "Interactive process viewer"},\n'
                 '  {"name": "curl", "version": "8.6.0-r0",'
                 ' "description": "Client URL tool"}\n]\n')
    OPKG_INDEX = ("htop - 3.3.0-r1 - Interactive process viewer\n"
                  "curl - 8.6.0-r0 - Client URL tool\n"
                  "nano - 7.2-r0\n")

    def __init__(self, *args: Any, **named: Any) -> None:
        super().__init__(*args, **named)
        self.form: dict = {}
        self.dialog_answer: dict | None = None
        self.question_answer: bool = True

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, fallback=None: self.argument(method, key, arguments, named, fallback)
        if method == "device.run_async":
            command = str(ask("command", ""))
            if command == "apk info -v":
                out = ("base-files-153-r1\nbusybox-1.36.1-r3\n"
                       "kmod-ipt-nat6-5.15.0-r1\n")
            elif command == "opkg list-installed":
                out = "base-files - 153-r1\nbusybox - 1.36.1-r3\n"
            elif command == "apk query --fields name,version,description --format json":
                out = self.APK_INDEX
            elif command == "opkg list":
                out = self.OPKG_INDEX
            elif command.startswith("apk search"):
                out = "htop-3.3.0-r1\ncurl-8.6.0-r0\n"
            elif command.startswith("opkg list"):
                out = "htop - 3.3.0-r1 - Interactive process viewer\n"
            elif command.endswith("update"):
                out = "Updating package list...\nSignature check passed."
            else:
                out = ""
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

    def run_commands(self) -> list:
        """Every command asked for since the last ``clear``, oldest first.

        One click now puts two commands on the wire - the list, then the index
        read that gives its descriptions - so a check reads the run rather than
        the last call alone.
        """
        return [str(arguments[0])
                for where, arguments, _named in self.calls
                if where == "device.run_async"]

    def clear(self) -> None:
        """Forget what has been asked, so the next section starts clean."""
        self.calls = []
        self.form.clear()

    def first_command(self) -> str:
        """The first command asked for since the last ``clear``.

        A list is followed by the index read that describes it, so a check on
        what the click itself sent reads this rather than the last call.
        """
        found = self.run_commands()
        return found[0] if found else ""

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
    check(str(addon.ADDON.get("version") or "") == "1.3",
          f"the published version is 1.3: {addon.ADDON.get('version')!r}")
    check("icon" not in addon.ADDON,
          "the manifest names no picture, so the store picks one by the rule")
    check(addon.TAB_ICON_FILE == "openwrt_package_managericon.png",
          f"the tab's own mark follows {{addon}}icon.png: {addon.TAB_ICON_FILE!r}")
    picture = (ROOT / "addon.store" / "assets" / "openwrt_package_manager"
               / addon.TAB_ICON_FILE)
    check(picture.is_file(), f"{picture.name} is in the store's assets folder")
    addon.init(api)
    check(api.called("config.declare"), "addon must declare its config form")
    fields = api.declared.get("fields", [])
    check(len(fields) == 1 and fields[0].get("key") == "package_manager",
          f"package_manager choice field declared correctly: {fields}")
    check(api.declared.get("defaults", {}).get("package_manager") == "apk",
          "default package manager backend must be set to apk")


def test_package_fetching(addon: Any, api: Api) -> None:
    """What one refresh puts on the wire, and what the table then shows.

    A list of installed packages holds no descriptions in either backend, so a
    refresh is two commands: the list, then the device's own index read. The
    rows only count as parsed when the name and the version land in their own
    columns and the sentence the device keeps lands in the third.
    """
    print("\n2. package fetching (apk default vs opkg mode)")
    fresh_addon(api)
    addon.fetch_installed()
    check(api.run_commands() == ["apk info -v",
                                 "apk query --fields name,version,description --format json"],
          f"apk lists what is installed, then reads its index: {api.run_commands()}")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 3, f"parsed 3 packages successfully under apk: {rows}")
    check(rows and rows[0] == ["base-files", "153-r1", "Release/file version"],
          f"an installed row carries its description: {rows and rows[0]}")
    check(rows and rows[2][:2] == ["kmod-ipt-nat6", "5.15.0-r1"],
          f"a name's own dashes are not read as its version: {rows and rows[2]}")
    said = str(api.last_set("inst_status", "text") or "")
    check("2 with a description" in said, f"the status says how many were described: {said!r}")

    # Switch backend to opkg via config
    fresh_addon(api)
    api.config_values["package_manager"] = "opkg"
    addon.fetch_installed()
    check(api.run_commands() == ["opkg list-installed", "opkg list"],
          f"the other backend lists, then reads the feed list: {api.run_commands()}")
    rows = api.last_set("inst_table", "rows")
    check(rows and len(rows) == 2, f"parsed 2 packages successfully under opkg: {rows}")
    check(rows and [row[0] for row in rows] == ["base-files", "busybox"],
          f"opkg's 'name - version' is cut at the dash: {rows}")
    api.config_values.pop("package_manager", None)


def test_descriptions_and_filter(addon: Any, api: Api) -> None:
    """The filter keeps working over described rows, and a late answer redraws
    whatever the filter is showing - the words arrive on their own schedule."""
    print("\n3. the filter, and a description that lands after the list")
    fresh_addon(api)
    addon.fetch_installed()
    api.clear()
    addon.filter_installed("busy")
    rows = api.last_set("inst_table", "rows")
    check(rows and [row[0] for row in rows] == ["busybox"],
          f"the filter leaves the matching names: {rows}")
    check(rows and rows[0][2] == "Size optimized toolset",
          f"a filtered row keeps its description: {rows}")

    api.clear()
    addon._got_descriptions({"code": 0, "err": "", "out":
                             '[{"name": "busybox", "description": "later words"}]'})
    rows = api.last_set("inst_table", "rows")
    check(rows and rows[0][2] == "later words",
          f"the read redraws the rows on show: {rows}")


def test_search_and_feeds(addon: Any, api: Api) -> None:
    """``apk search`` answers with one ``name-version`` per line: both columns
    come out of that, and the words come from the same index read the installed
    table uses."""
    print("\n4. feed updates and online package search")
    fresh_addon(api)
    addon.update_feeds()
    check(api.run_commands() == ["apk update"],
          f"feed update executes apk update by default: {api.run_commands()}")

    api.clear()
    addon.search_online("htop")
    check(api.first_command() == "apk search htop",
          f"online search executes apk search: {api.first_command()!r}")
    browse_rows = api.last_set("browse_table", "rows")
    check(browse_rows and len(browse_rows) == 2, f"browse results table populated: {browse_rows}")
    check(browse_rows and browse_rows[0][:2] == ["htop", "3.3.0-r1"],
          f"apk's name-version is cut into the two columns: {browse_rows and browse_rows[0]}")
    check(browse_rows and browse_rows[0][2] == "Interactive process viewer",
          f"a feed package carries its description: {browse_rows and browse_rows[0]}")

    fresh_addon(api)
    api.config_values["package_manager"] = "opkg"
    addon.search_online("htop")
    check(api.first_command() == "opkg list 'htop*'",
          f"opkg searches the feed list with a glob: {api.first_command()!r}")
    browse_rows = api.last_set("browse_table", "rows")
    check(browse_rows and browse_rows[0] == ["htop", "3.3.0-r1", "Interactive process viewer"],
          f"opkg writes its description on the same line: {browse_rows and browse_rows[0]}")
    api.config_values.pop("package_manager", None)


def test_widget_actions(addon: Any, api: Api) -> None:
    print("\n5. widget interactions (install and remove)")
    fresh_addon(api)
    
    # Test package removal event by mocking form selection value
    api.form["inst_table"] = ["curl"]
    api.question_answer = True
    addon.on_widget("Package Manager", "btn_remove", "clicked", {})
    check(api.first_command() == "apk del curl",
          f"remove button triggers apk del: {api.first_command()!r}")

    # Test package installation event by mocking form selection value
    api.clear()
    api.form["browse_table"] = ["htop"]
    addon.on_widget("Package Manager", "btn_install", "clicked", {})
    check(api.first_command() == "apk add htop",
          f"install button triggers apk add: {api.first_command()!r}")


def test_quoting(addon: Any, api: Api) -> None:
    """What a user types, and what a table shows, must not reach the device
    shell as shell syntax. The test for this is what the shell would cut the
    command into: shlex.split answers that, so a name that ought to stay one
    word and a name that would break into two commands cannot be confused.
    A plain name quotes to itself, which is why the ordinary command checks
    above are still written plainly and hold the other half."""
    print("\n6. shell punctuation in a name cannot become a second command")
    fresh_addon(api)

    def words() -> list[str]:
        return shlex.split(api.first_command())

    addon.search_online("htop; reboot")
    check(words() == ["apk", "search", "htop; reboot"],
          f"a search term's ';' stays inside its own word: "
          f"{api.first_command()!r}")

    api.clear()
    api.config_values["package_manager"] = "opkg"
    addon.search_online("x'$(id)")
    check(words() == ["opkg", "list", "x'$(id)*"],
          f"a quote in a search term cannot close itself out of its word: "
          f"{api.first_command()!r}")
    api.config_values.pop("package_manager", None)

    api.clear()
    api.form["inst_table"] = ["curl; rm -rf /"]
    addon.on_widget("Package Manager", "btn_remove", "clicked", {})
    check(words() == ["apk", "del", "curl; rm -rf /"],
          f"a name read back from the table is quoted too: "
          f"{api.first_command()!r}")

    api.clear()
    api.form["browse_table"] = ["htop; touch /tmp/pwned"]
    addon.on_widget("Package Manager", "btn_install", "clicked", {})
    check(len(words()) == 3,
          f"install is handed one package word and no more: "
          f"{api.first_command()!r}")


def fresh_addon(api: Api) -> None:
    """Start a section clean: nothing cached on the addon side, nothing recorded."""
    api_module.api = api
    api_module._installed_cache = []
    api_module._browse_cache = []
    api_module._descriptions = {}
    api_module._desc_state = ""
    api_module._filter = ""
    api.clear()


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
    test_descriptions_and_filter(addon, api)
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