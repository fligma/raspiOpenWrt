#!/usr/bin/env python3
"""OpenWrt Package Manager Addon
Audits installed packages, updates repositories, and manages software via apk (default) or opkg.
"""

from __future__ import annotations
from typing import Any
from addon_common import AddonError

ADDON = {
    "name": "OpenWrt Package Manager",
    "version": "1.0",
    "author": "fligma",
    "api": 1,
    "requires": ["ssh", "sftp"],
    "description": "Audits installed OpenWrt packages and manages software installation via apk or opkg.",
}

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
_installed_cache = []

def init(plugin_api: Any) -> None:
    global api
    api = plugin_api
    api.config.declare(fields=CONFIG_FIELDS, defaults=CONFIG_DEFAULTS, title="Package Manager Settings")
    api.tabs.add_tab("Package Manager", spec=TAB_SPEC)
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

def fetch_installed() -> None:
    backend = _pm_backend()
    api.ui.notify(f"Fetching installed packages using {backend}...")
    
    cmd = "apk info -v" if backend == "apk" else "opkg list-installed"
    api.device.run_async(cmd, on_done=_got_installed)

def _got_installed(result: dict) -> None:
    global _installed_cache
    if int(result.get("code", -1)) != 0:
        api.ui.error("Package Manager", "Failed to list installed packages.")
        return
    
    backend = _pm_backend()
    lines = str(result.get("out", "")).splitlines()
    _installed_cache = []
    rows = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if backend == "opkg":
            parts = line.split(" - ", 1)
            if len(parts) == 2:
                name, ver = parts[0].strip(), parts[1].strip()
                _installed_cache.append((name, ver))
                rows.append([name, ver, ""])
        else:
            # apk info -v output parsing (typically package-version)
            parts = line.rsplit("-", 2)
            if len(parts) >= 2:
                # Basic heuristic for apk info -v splitting name vs version
                name = "-".join(parts[:-2]) if len(parts) > 2 else parts[0]
                ver = f"{parts[-2]}-{parts[-1]}" if len(parts) > 2 else parts[1]
                if not name:
                    name = line
                    ver = ""
                _installed_cache.append((name, ver))
                rows.append([name, ver, ""])
            else:
                _installed_cache.append((line, ""))
                rows.append([line, "", ""])
            
    api.tabs.set_widget("Package Manager", "inst_table", "rows", rows)
    api.tabs.set_widget("Package Manager", "inst_status", "text", f"Loaded {len(_installed_cache)} installed packages ({backend}).")

def filter_installed(query: str) -> None:
    query = query.lower()
    rows = []
    for name, ver in _installed_cache:
        if query in name.lower():
            rows.append([name, ver, ""])
    api.tabs.set_widget("Package Manager", "inst_table", "rows", rows)

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
    
    cmd = f"apk search {term}" if backend == "apk" else f"opkg list '{term}*'"
    api.device.run_async(cmd, on_done=_got_search)

def _got_search(result: dict) -> None:
    if int(result.get("code", -1)) != 0:
        api.ui.error("Search", "Feed search failed.")
        return
    backend = _pm_backend()
    lines = str(result.get("out", "")).splitlines()
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if backend == "opkg":
            parts = line.split(" - ", 1)
            if len(parts) == 2:
                name, desc = parts[0].strip(), parts[1].strip()
                rows.append([name, "", desc])
            else:
                rows.append([line, "", ""])
        else:
            # apk search output typically lists package names or package-version
            rows.append([line, "", ""])
            
    api.tabs.set_widget("Package Manager", "browse_table", "rows", rows)
    api.ui.notify(f"Found {len(rows)} matching packages.")

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
            cmd = f"apk del {pkg_name}" if backend == "apk" else f"opkg remove {pkg_name}"
            api.device.run_async(cmd, on_done=lambda r: fetch_installed())
    elif identifier == "btn_install" and event == "clicked":
        selected = api.tabs.widget_value("Package Manager", "browse_table", "selected")
        if not selected:
            api.ui.notify("Select an online package to install first.")
            return
        pkg_name = selected[0].split()[0] # Handle potential version suffixes in apk search
        if api.ui.question("Confirm Install", f"Do you want to install '{pkg_name}' via {backend}?"):
            cmd = f"apk add {pkg_name}" if backend == "apk" else f"opkg install {pkg_name}"
            api.device.run_async(cmd, on_done=lambda r: api.ui.notify(f"Installation of {pkg_name} completed."))