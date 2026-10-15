#!/usr/bin/env python3
"""OpenWrt Package Manager Addon
Audits installed packages, updates repositories, and manages software via apk (default) or opkg.
Each table carries the package's own description, read off the device: ``opkg
list`` for the legacy backend, ``apk info -a`` for the modern one.
"""

from __future__ import annotations
import json
import shlex
from typing import Any
from addon_common import AddonError, find_addon_picture

#: Every word this addon sends to a device shell that came from anywhere other
#: than the addon itself - the search box, a name a table cell is showing -
#: goes through this first. A command string is handed to the remote shell
#: exactly as written, so one of ';', '$(' or a backquote in a search term
#: would otherwise be run on the router as a second command. Same rule the
#: files addon keeps (``addons/berry.py``).
SHQ = shlex.quote

ADDON = {
    "name": "OpenWrt Package Manager",
    "version": "1.3",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh", "sftp"],
    # Every command runs over the profile's own session; none is opened here.
    "connection": "app",
    # The picture is published under the standard name - the id and then
    # ``icon.png`` - so the manifest names none and the store finds it by rule.
    "description": "Audits installed OpenWrt packages and manages software installation via apk or opkg.",
}

#: The tab's mark when the assets folder carries no picture: a package box.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M12 2.6 20.4 6.4v11.2L12 21.4 3.6 17.6V6.4Z" fill="#3a3a3a"'
    ' stroke="#c51a4a" stroke-width="1.2"/>'
    '<path d="M3.6 6.4 12 10.2l8.4-3.8" fill="none" stroke="#ffb020"'
    ' stroke-width="1.3"/>'
    '<path d="M12 10.2v11.2" fill="none" stroke="#ffb020" stroke-width="1.3"/>'
    '<path d="M7.4 4.4 15.8 8.2" fill="none" stroke="#23d18b"'
    ' stroke-width="1.3"/>'
    '</svg>'
)

#: The picture this addon's own assets folder is expected to hold: its id and
#: then ``icon.png``, the rule every addon follows, so there is no name to keep
#: in step here - dropping the picture in is enough.
TAB_ICON_FILE = "openwrt_package_managericon.png"


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark.

    One name is looked for - ``openwrt_package_managericon.png`` - in this addon's
    own ``addon.store/assets/openwrt_package_manager`` folder and then in the
    program's, so an addon that follows the rule needs nothing here beyond its id.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api in a bare import
        paths = None
    return find_addon_picture(paths, "openwrt_package_manager") or TAB_ICON

CONFIG_FIELDS = [
    {
        "key": "package_manager",
        "label": "Package Manager Backend",
        "kind": "choice",
        "choices": ["apk", "opkg"],
        "hint": "Default package manager backend: apk (modern OpenWrt) or opkg (legacy)."
    }
]

CONFIG_DEFAULTS = {
    "package_manager": "apk"
}

TAB_ID = "pkg_manager_tabs"

INSTALLED_SPEC = {
    "kind": "column",
    "label": "Installed Packages",
    "children": [
        {"kind": "row", "children": [
            {"kind": "label", "text": "Filter:"},
            {"kind": "line", "id": "inst_search", "placeholder": "Search installed...", "on": ["changed", "returned"]},
            {"kind": "button", "id": "btn_refresh_inst", "text": "Refresh List", "on": ["clicked"]},
            {"kind": "button", "id": "btn_remove", "text": "Remove Selected", "on": ["clicked"]},
        ]},
        {"kind": "table", "id": "inst_table", "columns": ["Package Name", "Version", "Description"], "stretch": 2, "col_widths": [180, 100]},
        {"kind": "label", "id": "inst_status", "text": "Ready."},
    ],
}

BROWSE_SPEC = {
    "kind": "column",
    "label": "Install Software",
    "children": [
        {"kind": "row", "children": [
            {"kind": "button", "id": "btn_update_feeds", "text": "1. Update Feeds (Update)", "on": ["clicked"]},
            {"kind": "label", "id": "feed_status", "text": ""},
        ]},
        {"kind": "row", "children": [
            {"kind": "label", "text": "Search Feeds:"},
            {"kind": "line", "id": "browse_search", "placeholder": "e.g. luci-app-*, curl, htop", "on": ["returned"]},
            {"kind": "button", "id": "btn_search", "text": "Search Online", "on": ["clicked"]},
            {"kind": "button", "id": "btn_install", "text": "Install Selected", "on": ["clicked"]},
        ]},
        {"kind": "table", "id": "browse_table", "columns": ["Package Name", "Version", "Description"], "stretch": 2, "col_widths": [180, 100]},
    ],
}

TAB_SPEC = {
    "kind": "tabs",
    "id": TAB_ID,
    "children": [INSTALLED_SPEC, BROWSE_SPEC],
}

api = None
_installed_cache = []      # (name, version) as the device listed what it runs
_browse_cache = []         # (name, version, description) as a feed search answered
_descriptions = {}         # package name -> the sentence the device keeps for it
_desc_state = ""           # '', 'out' while a read is running, 'done' once filled
_filter = ""               # the text the installed table is showing only

def init(plugin_api: Any) -> None:
    global api
    api = plugin_api
    api.config.declare(fields=CONFIG_FIELDS, defaults=CONFIG_DEFAULTS, title="Package Manager Settings")
    api.tabs.add_tab("Package Manager", spec=TAB_SPEC)
    api.tabs.set_icon("Package Manager", tab_icon())
    api.events.on("tab_changed", _on_tab_changed)
    api.log("Package Manager addon initialized.")

def shutdown() -> None:
    api.tabs.close("Package Manager")

def _pm_backend() -> str:
    if api is None:
        return "apk"
    try:
        val = api.config.get("package_manager", "apk")
        return str(val).strip().lower() or "apk"
    except Exception:
        return "apk"

def _on_tab_changed(_payload: Any) -> None:
    try:
        if api.session.current_tab() == "Package Manager":
            if not _installed_cache:
                fetch_installed()
    except Exception:
        pass

def split_apk(line: str) -> tuple:
    """One ``name-version`` answer cut where the version starts.

    A package name may hold dashes of its own (``kmod-ipt-nat6``) and so may a
    version (``1.2.3-r1``), so the cut is the first dash with a digit behind it:
    a name cannot start a segment with one, a version always does. A line with
    no such dash is taken as a bare name.
    """
    for index, char in enumerate(line):
        if char == "-" and line[index + 1:index + 2].isdigit():
            return line[:index].strip(), line[index + 1:].strip()
    return line, ""


def parse_descriptions(text: str) -> dict:
    """The ``{name: description}`` map off one backend's dump.

    ``opkg list`` writes ``name - version - description`` on a line; apk's
    ``--format json`` is an array of records or one record per line, so both
    shapes are read. Anything that cannot be placed by a name is left out - a
    description on the wrong package is worse than none.
    """
    out: dict[str, str] = {}
    body = str(text or "")
    if _pm_backend() == "opkg":
        for line in body.splitlines():
            parts = [part.strip() for part in line.split(" - ", 2)]
            if len(parts) == 3 and parts[0] and parts[2]:
                out[parts[0]] = parts[2]
        return out
    records: list = []
    stripped = body.strip()
    try:
        data = json.loads(stripped)
    except ValueError:
        for line in stripped.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    else:
        records = data if isinstance(data, list) else [data]
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        summary = " ".join(str(record.get("description") or "").split())
        if name and summary:
            out[name] = summary
    return out


def description_of(name: str) -> str:
    """What the device says about one package; blank until it has been read."""
    return str(_descriptions.get(name) or "")


def fetch_installed() -> None:
    backend = _pm_backend()
    api.ui.notify(f"Fetching installed packages using {backend}...")
    global _desc_state
    _desc_state = ""            # a fresh list is worth a fresh read of the text
    cmd = "apk info -v" if backend == "apk" else "opkg list-installed"
    api.device.run_async(cmd, on_done=_got_installed)

def _got_installed(result: dict) -> None:
    global _installed_cache
    if int(result.get("code", -1)) != 0:
        api.ui.error("Package Manager", "Failed to list installed packages.")
        return

    backend = _pm_backend()
    _installed_cache = []
    for line in str(result.get("out", "")).splitlines():
        line = line.strip()
        if not line:
            continue
        if backend == "opkg":
            # "opkg list-installed" writes "name - version".
            parts = [part.strip() for part in line.split(" - ", 1)]
            _installed_cache.append((parts[0],
                                     parts[1] if len(parts) > 1 else ""))
        else:
            # "apk info -v" writes one "name-version" per line.
            _installed_cache.append(split_apk(line))
    show_installed()
    fetch_descriptions()

def installed_rows(query: str = "") -> list:
    """The installed list as table rows, filtered by name and carrying each
    package's description once the device has given one."""
    needle = str(query or "").lower()
    rows = []
    for name, ver in _installed_cache:
        if needle and needle not in name.lower():
            continue
        rows.append([name, ver, description_of(name)])
    return rows

def show_installed() -> None:
    """Redraw the installed table and say what it holds."""
    api.tabs.set_widget("Package Manager", "inst_table", "rows",
                        installed_rows(_filter))
    have = sum(1 for name, _ver in _installed_cache if description_of(name))
    api.tabs.set_widget(
        "Package Manager", "inst_status", "text",
        f"Loaded {len(_installed_cache)} installed packages ({_pm_backend()}); "
        f"{have} with a description.")

def filter_installed(query: str) -> None:
    global _filter
    _filter = str(query or "")
    show_installed()

def fetch_descriptions() -> None:
    """Read the sentences the device keeps for its packages, once per list.

    Neither backend puts a description in the list of what is installed: apk
    keeps them in its repository index and opkg in its feed list, so the names
    come from one command and the words from another. One answer covers every
    package the device knows about - installed or not, in either table - so this
    is asked for at most once between refreshes, and a device that will not
    answer simply leaves the column blank.
    """
    global _desc_state
    if _desc_state:
        return
    _desc_state = "out"
    cmd = ("opkg list" if _pm_backend() == "opkg"
           else "apk query --fields name,version,description --format json")
    api.device.run_async(cmd, on_done=_got_descriptions, timeout=90)

def _got_descriptions(result: dict) -> None:
    """Fold the descriptions in and redraw both tables."""
    global _desc_state
    _desc_state = ""
    if int(result.get("code", -1)) != 0:
        return
    found = parse_descriptions(str(result.get("out", "")))
    if not found:
        return
    _descriptions.update(found)
    _desc_state = "done"
    show_installed()
    show_browse()

def update_feeds() -> None:
    backend = _pm_backend()
    api.ui.notify(f"Updating package lists via {backend} update...")
    cmd = "apk update" if backend == "apk" else "opkg update"
    api.device.run_async(cmd, on_done=lambda res: api.ui.notify(f"Feeds update finished (code {res.get('code')})"))

def search_online(term: str) -> None:
    term = term.strip()
    if not term:
        api.ui.warning("Search", "Enter a search pattern (e.g. htop or luci-*)")
        return
    backend = _pm_backend()
    api.ui.notify(f"Searching feeds for '{term}' using {backend}...")
    
    cmd = (f"apk search {SHQ(term)}" if backend == "apk"
           else f"opkg list {SHQ(term + '*')}")
    api.device.run_async(cmd, on_done=_got_search)

def _got_search(result: dict) -> None:
    global _browse_cache
    if int(result.get("code", -1)) != 0:
        api.ui.error("Search", "Feed search failed.")
        return
    backend = _pm_backend()
    _browse_cache = []
    for line in str(result.get("out", "")).splitlines():
        line = line.strip()
        if not line:
            continue
        if backend == "opkg":
            # "opkg list" answers with name - version - description.
            parts = [part.strip() for part in line.split(" - ", 2)]
            if len(parts) == 3:
                _browse_cache.append((parts[0], parts[1], parts[2]))
            elif len(parts) == 2:
                _browse_cache.append((parts[0], parts[1], ""))
            else:
                _browse_cache.append((line, "", ""))
        else:
            # "apk search" answers with one "name-version" per line, and the
            # words for it come from the same index read as everything else.
            name, ver = split_apk(line)
            _browse_cache.append((name, ver, ""))
    for name, _ver, summary in _browse_cache:
        if summary:
            _descriptions[name] = summary
    show_browse()
    api.ui.notify(f"Found {len(_browse_cache)} matching packages.")
    fetch_descriptions()

def browse_rows() -> list:
    """The last search as table rows, the index read's words preferred."""
    rows = []
    for name, ver, summary in _browse_cache:
        rows.append([name, ver, description_of(name) or summary])
    return rows

def show_browse() -> None:
    api.tabs.set_widget("Package Manager", "browse_table", "rows", browse_rows())

def _done_change(result: dict, verb: str, pkg_name: str) -> None:
    """Tell the user how the install or removal went, then pace a refresh of
    the installed list on a timer instead of chaining it into this reply."""
    code = int(result.get("code", -1))
    if code != 0:
        reason = str(result.get("err", "")).strip() or f"code {code}"
        api.ui.error("Package Manager", f"{verb} of {pkg_name} failed: {reason}")
        return
    api.ui.notify(f"{verb} of {pkg_name} completed.")
    api.schedule.after(0.5, fetch_installed)

def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    backend = _pm_backend()
    if identifier == "btn_refresh_inst" and event == "clicked":
        fetch_installed()
    elif identifier == "inst_search" and event in ("changed", "returned"):
        filter_installed(payload.get("text", ""))
    elif identifier == "btn_update_feeds" and event == "clicked":
        update_feeds()
    elif identifier == "btn_search" and event == "clicked":
        term = api.tabs.widget_value("Package Manager", "browse_search", "text")
        search_online(term)
    elif identifier == "browse_search" and event == "returned":
        search_online(payload.get("text", ""))
    elif identifier == "btn_remove" and event == "clicked":
        selected = api.tabs.widget_value("Package Manager", "inst_table", "selected")
        if not selected:
            api.ui.notify("Select a package from the table first.")
            return
        pkg_name = selected[0]
        if api.ui.question("Confirm Removal", f"Are you sure you want to remove '{pkg_name}' via {backend}?"):
            cmd = (f"apk del {SHQ(pkg_name)}" if backend == "apk"
                   else f"opkg remove {SHQ(pkg_name)}")
            api.device.run_async(cmd, on_done=lambda r, name=pkg_name: _done_change(r, "Removal", name))
    elif identifier == "btn_install" and event == "clicked":
        selected = api.tabs.widget_value("Package Manager", "browse_table", "selected")
        if not selected:
            api.ui.notify("Select an online package to install first.")
            return
        pkg_name = selected[0].split()[0] # Handle potential version suffixes in apk search
        if api.ui.question("Confirm Install", f"Do you want to install '{pkg_name}' via {backend}?"):
            cmd = (f"apk add {SHQ(pkg_name)}" if backend == "apk"
                   else f"opkg install {SHQ(pkg_name)}")
            api.device.run_async(cmd, on_done=lambda r, name=pkg_name: _done_change(r, "Installation", name))