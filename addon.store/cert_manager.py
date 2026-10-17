#!/usr/bin/env python3
"""Certificate manager: read the device's TLS certificate and reissue it.

Adds a ``Certificates`` tab. The certificate and key the web interface uses -
``/etc/ssl/mycert.crt`` and ``/etc/ssl/mycert.key`` by default - are listed and
shown two ways, switched by a small tab on the right: a tidy GUI summary (subject,
issuer, dates, serial, fingerprint, validity) or the raw ``openssl x509 -text``
output. They can be issued again in place over SSH, with the old pair backed up
first and the web server restarted after.

Nothing is installed or hardcoded: the folder, both file names, the subject,
the alternative names, the key type, the lifetime and the follow-up commands
all come from ``addons/_data/cert_manager/config.json``. That file is shown as a
form by **Edit settings** on this addon's tab in the store's Addon manager,
and it is plain JSON, so it can be edited anywhere. Every profile on every device
can have its own, because the addon only ever asks the device the *current
profile* is connected to.

The tab is built from a declarative spec and its buttons report back through
``on_widget``, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab.
"""

from __future__ import annotations

import datetime
import re

# Qt-free half: runs compiled alone and in a windowless test.
from typing import Any

from addon_common import AddonError, config_get, find_addon_picture, merge_config

ADDON = {
    "name": "Certificate manager",
    "version": "1.6",
    "author": "fligma",
    "description": "Shows the certificate on the device as a tidy summary or the raw "
                   "openssl text, and reissues the .crt and .key over SSH, with the "
                   "subject, paths and follow-up commands kept in "
                   "addons/_data/cert_manager/config.json.",
    "requires": ["ssh"],
    # Everything is read and written over the profile's own session.
    "connection": "app",
    "api": 1,
}

TAB = "Certificates"

api = None                  # filled in by init()
working = False             # one device job at a time
follow_up = ""              # what to say once the next read has landed

# --- what can be configured, and its defaults ---

DEFAULTS = {
    "cert_dir": "/etc/ssl",
    "cert_name": "mycert.crt",
    "key_name": "mycert.key",
    "openssl": "openssl",
    "days": 365,
    "key_type": "ec",
    "ec_curve": "prime256v1",
    "rsa_bits": 2048,
    "reuse_key": False,
    "ca_flag": True,
    "backup": True,
    "backup_dir": "",
    # Blank on purpose: words here become the subject, not a hint
    # (see OLD_SUBJECT_WORDS).
    "subject": {
        "C": "",
        "ST": "",
        "L": "",
        "O": "",
        "OU": "",
        "CN": "",
    },
    "san_dns": ["OpenWrt.lan", "localhost"],
    "san_ip": ["192.168.12.1"],
    "use_profile_host": True,
    "key_usage": ["digitalSignature", "nonRepudiation", "keyEncipherment"],
    "extended_key_usage": ["serverAuth"],
    "post_commands": ["/etc/init.d/uhttpd restart"],
    "refresh_on_connect": True,
}

FIELDS = [
    {"key": "cert_dir", "label": "Folder on the device", "kind": "text",
     "hint": "Usually /etc/ssl, /etc/ssl/certs or /etc/uhttpd.crt's own folder"},
    {"key": "cert_name", "label": "Certificate file", "kind": "text",
     "hint": "Written as PEM into that folder"},
    {"key": "key_name", "label": "Private key file", "kind": "text"},
    {"key": "openssl", "label": "openssl command", "kind": "text",
     "hint": "'openssl' works when it is on PATH; give a full path if it is not"},
    {"key": "backup", "label": "Back up the old pair first", "kind": "bool"},
    {"key": "backup_dir", "label": "Backup folder", "kind": "text",
     "hint": "Blank means beside the certificate", "placeholder": "beside the certificate"},

    # Guidance goes in the hint/placeholder, never the value.
    {"key": "subject.C", "label": "Country", "kind": "text",
     "hint": "Two letters and nothing else - GB, NO, DE - or blank. openssl has "
             "room for exactly two characters here and says 'string too long'",
     "placeholder": "GB"},
    {"key": "subject.ST", "label": "State / Province", "kind": "text",
     "placeholder": "Somerset"},
    {"key": "subject.L", "label": "Locality", "kind": "text",
     "placeholder": "Bristol"},
    {"key": "subject.O", "label": "Organization", "kind": "text",
     "placeholder": "Raspberry Pi"},
    {"key": "subject.OU", "label": "Organizational unit", "kind": "text",
     "placeholder": "LAN"},
    {"key": "subject.CN", "label": "Common name", "kind": "text",
     "hint": "The name the browser checks; blank uses the device address",
     "placeholder": "mydevice.lan"},
    {"key": "san_dns", "label": "DNS names", "kind": "list",
     "hint": "Comma separated", "placeholder": "mydevice.lan, localhost"},
    {"key": "san_ip", "label": "IP addresses", "kind": "list",
     "hint": "Comma separated", "placeholder": "192.168.50.1"},
    {"key": "use_profile_host", "label": "Add this profile's address", "kind": "bool",
     "hint": "Puts the host from the connection panel into the alternative names"},

    {"key": "key_type", "label": "Key type", "kind": "choice",
     "choices": ["ec", "rsa"], "hint": "EC is much faster on a Pi"},
    {"key": "ec_curve", "label": "EC curve", "kind": "choice",
     "choices": ["prime256v1", "secp384r1", "secp521r1", "secp224r1"]},
    {"key": "rsa_bits", "label": "RSA size", "kind": "number",
     "minimum": 1024, "maximum": 8192},
    {"key": "days", "label": "Valid for (days)", "kind": "number",
     "minimum": 1, "maximum": 3650},
    {"key": "reuse_key", "label": "Keep the existing key", "kind": "bool",
     "hint": "Issue a new certificate for the key already on the device"},
    {"key": "ca_flag", "label": "Mark it as its own authority", "kind": "bool",
     "hint": "What a self-signed certificate needs; LuCI and uhttpd expect it"},
    {"key": "key_usage", "label": "Key usages", "kind": "list",
     "hint": "Comma separated, as openssl spells them"},
    {"key": "extended_key_usage", "label": "Extended key usages", "kind": "list",
     "hint": "Comma separated; serverAuth is the one for HTTPS"},

    {"key": "post_commands", "label": "Commands to run after", "kind": "multiline",
     "hint": "One per line, run on the device as the profile's user. uhttpd needs a "
             "hangup to pick up a new pair"},
    {"key": "refresh_on_connect", "label": "Show it when SSH comes up", "kind": "bool"},
]

GROUPS = [
    ("Files on the device", ["cert_dir", "cert_name", "key_name", "openssl",
                             "backup", "backup_dir"]),
    ("Who the certificate is for", ["subject.C", "subject.ST", "subject.L", "subject.O",
                                    "subject.OU", "subject.CN", "san_dns", "san_ip",
                                    "use_profile_host"]),
    ("Key and lifetime", ["key_type", "ec_curve", "rsa_bits", "days", "reuse_key",
                          "ca_flag", "key_usage", "extended_key_usage"]),
    ("After reissuing", ["post_commands", "refresh_on_connect"]),
]

FIELDS_BY_KEY = {str(field["key"]): field for field in FIELDS}
KIND_TO_WIDGET = {"text": "line", "number": "spin", "bool": "check",
                  "choice": "combo", "list": "line", "multiline": "multi"}
BUTTONS = (("view", "View current"), ("reissue", "Reissue certificate"),
           ("push", "Save to config"), ("pull", "Reload from config"),
           ("copy", "Save a copy locally"))

#: Filled in below, once the functions exist.
HANDLERS: dict = {}

_UNSAFE = re.compile(r"[\"'`$;|&<>\\\n\r\t]")
_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_DNS = re.compile(r"^[A-Za-z0-9.*_-]+$")
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")

#: The markers :func:`view_script` prints between the parts of its answer, so
#: the GUI summary can split what the device said back into those parts.
_SECTION = re.compile(r"^==\s*(.+?)\s*==\s*$")

#: The subject, in the order openssl writes it, and what each part is called.
SUBJECT_ORDER = ("C", "ST", "L", "O", "OU", "CN")
SUBJECT_LABELS = {"C": "Country", "ST": "State / Province", "L": "Locality",
                  "O": "Organization", "OU": "Organizational unit",
                  "CN": "Common name"}

#: Words an early build kept as subject values, meant as hints. openssl takes
#: them literally and the Country is two letters, so they died on the device.
#: A stored value still one of these counts as the blank it was meant to be.
OLD_SUBJECT_WORDS = {
    "C": {"Country"},
    "ST": {"State", "State / Province"},
    "L": {"City", "Locality"},
    "O": {"Organization", "Orginization"},
    "OU": {"Org Unit", "OrgUnit", "Organizational unit"},
    "CN": {"Hostname.lan", "Common name", "Device"},
}

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QPlainTextEdit { font-family: Consolas, monospace; }
"""

#: The tab's mark: a trophy, since a certificate is what a trophy is for. Sent
#: to the app as an SVG document - plain text, so the icon is the same whether
#: this file is imported into the app or compiled into a program of its own,
#: where there is no Qt to draw with and no picture file beside it.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M7 3h10v6a5 5 0 0 1-10 0z" fill="#ffb020"/>'
    '<path d="M7 4H4v3a3 3 0 0 0 3 3" fill="none" stroke="#ffb020" stroke-width="1.6"/>'
    '<path d="M17 4h3v3a3 3 0 0 1-3 3" fill="none" stroke="#ffb020" stroke-width="1.6"/>'
    '<rect x="11" y="13" width="2" height="4" fill="#ffb020"/>'
    '<path d="M8 17h8v2H8z" fill="#e0e0e0"/>'
    '<path d="M6 20h12v1.8H6z" fill="#e0e0e0"/>'
    '</svg>'
)

#: The picture this addon's own assets folder is expected to hold: its id and
#: then ``icon.png``, the rule every addon follows, so a picture dropped in is
#: worn with nothing here changed.
TAB_ICON_FILE = "cert_managericon.png"


# --- small helpers ---

def widget_id(key: str) -> str:
    """A spec id for a config key: dots are not allowed in one."""
    return str(key).replace(".", "_")


def one_line(value: Any) -> str:
    """One safe line of openssl/path text; shell punctuation dropped on purpose."""
    text = str("" if value is None else value).strip()
    return _UNSAFE.sub("", text.splitlines()[0] if text else "")


def command_line(value: Any) -> str:
    """One shell command, kept as written - these are meant to be run."""
    text = str("" if value is None else value).strip()
    return text.splitlines()[0].replace("\r", "") if text else ""


def clean_list(value: Any) -> list[str]:
    """A config value as a list of clean entries, whatever shape it arrived in."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else re.split(r"[,\n]", str(value))
    out: list[str] = []
    for item in items:
        text = one_line(item)
        if text and text not in out:
            out.append(text)
    return out


def quoted(path: str) -> str:
    return '"' + str(path).replace('"', "") + '"'


def subject_of(values: dict) -> dict:
    """All six subject fields as clean lines in openssl's order, old hint words gone."""
    raw = config_get(values, "subject", {}) or {}
    out: dict[str, str] = {}
    for key in SUBJECT_ORDER:
        text = one_line(config_get(raw, key, config_get(DEFAULTS, f"subject.{key}", "")))
        out[key] = "" if text in OLD_SUBJECT_WORDS[key] else text
    return out


def with_clean_subject(values: dict) -> dict:
    """``values`` with its subject run through :func:`subject_of`, so old hint words show blank."""
    copy = dict(values or {})
    copy["subject"] = subject_of(copy)
    return copy


# --- the tab ---

def control_spec(key: str, values: dict) -> dict:
    """The form row for one config key, prefilled with the current value."""
    field = FIELDS_BY_KEY[key]
    kind = str(field.get("kind") or "text")
    spec = {
        "id": widget_id(key),
        "kind": KIND_TO_WIDGET.get(kind, "line"),
        "label": str(field.get("label") or key),
    }
    if field.get("hint"):
        spec["tooltip"] = str(field["hint"])
    value = config_get(values, key, config_get(DEFAULTS, key))
    if kind == "bool":
        spec["checked"] = bool(value)
    elif kind == "choice":
        choices = [str(choice) for choice in field.get("choices") or []]
        spec["items"] = choices
        spec["value"] = str(value) if value else (choices[0] if choices else "")
    elif kind == "number":
        spec["minimum"] = int(field.get("minimum", 0))
        spec["maximum"] = int(field.get("maximum", 9999))
        try:
            spec["value"] = int(value or 0)
        except (TypeError, ValueError):
            spec["value"] = 0
    elif kind == "list":
        spec["value"] = ", ".join(clean_list(value))
        if field.get("placeholder"):
            spec["placeholder"] = str(field["placeholder"])
    elif kind == "multiline":
        spec["value"] = "\n".join(clean_list(value))
    else:
        spec["value"] = one_line(value)
        if field.get("placeholder"):
            spec["placeholder"] = str(field["placeholder"])
    return spec


def tab_spec(values: dict) -> dict:
    """The whole tab: the form on the left, what the device said on the right."""
    groups = []
    for title, keys in GROUPS:
        groups.append({
            "kind": "group", "text": title, "layout": "form",
            "children": [control_spec(key, values) for key in keys],
        })
    # Two buttons to a row rather than five in a strip: their captions are long
    # enough that a single row asks for more width than this pane is ever given,
    # and a horizontal scrollbar in a form is what makes fields unreadable.
    groups.append({
        "kind": "grid",
        "children": [{"kind": "button", "id": identifier, "text": text,
                      "tooltip": tip, "row": order // 2, "col": order % 2,
                      "on": ["clicked"]}
                     for order, (identifier, text, tip) in enumerate((
            ("view", "View current", "List both files and show the certificate in full"),
            ("reissue", "Reissue certificate", "Write a new .crt and .key on the device"),
            ("push", "Save to config", "Keep these values in addons/_data/cert_manager/config.json"),
            ("pull", "Reload from config", "Put the saved values back into the form"),
            ("copy", "Save a copy locally", "Keep the certificate PEM in the addon's own folder"),
        ))],
    })
    left = {"kind": "scroll", "children": [{"kind": "column", "children": groups,
                                            "stretch": True}]}
    right = {"kind": "column", "children": [
        {"kind": "label", "id": "status", "text": "Nothing read from the device yet.",
         "word_wrap": True, "style": "color: #aaaaaa; padding: 2px 4px;"},
        {"kind": "tabs", "id": "showtabs", "children": [
            {"label": "GUI", "kind": "table", "id": "gui",
             "columns": ["Field", "Value"], "stretch": 1, "col_widths": [110],
             "tooltip": "The certificate as a tidy summary - subject, dates, "
                        "serial, fingerprint and whether it is still valid"},
            {"label": "Text", "kind": "multi", "id": "viewer", "readonly": True,
             "placeholder": "The certificate from the device appears here",
             "tooltip": "What openssl x509 -text said about the file on the device"},
        ]},
    ]}
    return {"kind": "splitter", "children": [left, right]}


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark.

    An addon's own picture is looked for by the standard rule - the id and then
    ``icon.png``, so here ``cert_managericon.png`` - in this addon's own
    ``addon.store/assets/cert_manager`` folder first and then in the program's
    ``assets``, which is why there is no file name to keep in step here: dropping
    the picture in either place is enough.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    return find_addon_picture(paths, "cert_manager") or TAB_ICON


def build_tab() -> None:
    """The tab as a spec; buttons report through ``on_widget``, so compiled and source match.

    It is window furniture, like Settings, the store and the updater: pinned to
    the right of the bar and shown as its mark alone, so the certificate page is
    reached by its icon and never dragged about.
    """
    values = with_clean_subject(api.config.all())
    api.tabs.add_tab(TAB, spec=tab_spec(values), style=TAB_STYLE,
                     pinned=True, icon_only=True)
    api.tabs.set_icon(TAB, tab_icon())


def values_of() -> dict:
    """The form as a config-shaped dict."""
    out: dict = {}
    for key, field in FIELDS_BY_KEY.items():
        parts = str(key).split(".")
        node = out
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = read_control(key, field)
    return out


def read_control(key: str, field: dict) -> Any:
    kind = str(field.get("kind") or "text")
    try:
        raw = api.tabs.widget_value(TAB, widget_id(key),
                                    "checked" if kind == "bool" else "value")
    except AddonError:
        return config_get(DEFAULTS, key)
    if kind == "list":
        return clean_list(raw)
    if kind == "multiline":
        return [one_line(line) for line in str(raw or "").splitlines() if one_line(line)]
    if kind == "number":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(field.get("minimum", 0))
    return raw


def show_form(values: dict) -> None:
    values = with_clean_subject(values)
    for key, field in FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        value = config_get(values, key, config_get(DEFAULTS, key))
        if kind in ("list", "multiline"):
            items = clean_list(value) if kind == "list" else [one_line(line)
                                                             for line in (value or []) if one_line(line)]
            text = ", ".join(items) if kind == "list" else "\n".join(items)
            api.tabs.set_widget(TAB, widget_id(key), "value", text)
        else:
            api.tabs.set_widget(TAB, widget_id(key), "value", value)


def say(text: str, bad: bool = False) -> None:
    api.tabs.set_widget(TAB, "status", "text", str(text))
    api.tabs.set_widget(TAB, "status", "style",
                        "color: #ffb020; padding: 2px 4px;" if bad
                        else "color: #23d18b; padding: 2px 4px;")


def show_output(result: dict) -> None:
    text = str(result.get("out") or "")
    error = str(result.get("err") or "")
    body = text + (("\n" + error) if error.strip() else "")
    api.tabs.set_widget(TAB, "viewer", "text", body.strip() or "(the device gave no output)")
    rows = summary_rows(text)
    api.tabs.set_widget(TAB, "gui", "rows",
                        rows or [["(nothing to show)",
                                 "read the certificate with the button on the left"]])


# --- reading the device ---

def paths(values: dict) -> dict:
    folder = one_line(config_get(values, "cert_dir", DEFAULTS["cert_dir"])) or "/etc/ssl"
    cert = one_line(config_get(values, "cert_name", DEFAULTS["cert_name"])) or "mycert.crt"
    key = one_line(config_get(values, "key_name", DEFAULTS["key_name"])) or "mycert.key"
    backup = one_line(config_get(values, "backup_dir", ""))
    return {
        "dir": folder.rstrip("/") or "/",
        "cert": f"{folder.rstrip('/')}/{cert}",
        "key": f"{folder.rstrip('/')}/{key}",
        "backup": backup.rstrip("/") if backup else "",
        "openssl": one_line(config_get(values, "openssl", "openssl")) or "openssl",
    }


def host_of(values: dict) -> str:
    """The address the current profile talks to, when the addon may use it."""
    if not bool(config_get(values, "use_profile_host", DEFAULTS["use_profile_host"])):
        return ""
    try:
        status = api.session.status()
    except AddonError:
        return ""
    return one_line(status.get("host") or "")


def view_script(values: dict) -> str:
    where = paths(values)
    host = host_of(values)
    lines = [
        "set -e",
        f'CERT={quoted(where["cert"])}',
        f'KEY={quoted(where["key"])}',
        f'OPENSSL={quoted(where["openssl"])}',
        'echo "== files =="',
        'ls -l "$CERT" "$KEY" 2>&1 || true',
        'if [ -f "$CERT" ]; then',
        '  echo',
        '  echo "== summary =="',
        '  "$OPENSSL" x509 -in "$CERT" -noout -subject -issuer -dates -serial 2>&1 || true',
        '  echo',
        '  echo "== fingerprints =="',
        '  "$OPENSSL" x509 -in "$CERT" -noout -fingerprint -sha256 2>/dev/null \\',
        '      || "$OPENSSL" x509 -in "$CERT" -noout -fingerprint 2>&1 || true',
        '  echo',
        '  echo "== still valid? =="',
        '  if "$OPENSSL" x509 -in "$CERT" -noout -checkend 0 2>&1; then',
        '    echo "the certificate has not expired"',
        '  else',
        '    echo "WARNING: the certificate has expired"',
        "  fi",
        f'  if [ -n "{host}" ]; then',
        '    echo',
        f'    echo "== does it cover {host}? =="',
        f'    if "$OPENSSL" x509 -in "$CERT" -noout -text 2>&1 | grep -q "{host}"; then',
        f'      echo "yes, {host} appears in the certificate"',
        '    else',
        f'      echo "no, {host} does not appear in it"',
        '    fi',
        "  fi",
        '  echo',
        '  echo "== full text =="',
        '  "$OPENSSL" x509 -in "$CERT" -noout -text 2>&1 || true',
        "else",
        '  echo "there is no certificate at $CERT yet"',
        'fi',
    ]
    return "\n".join(lines)


def parse_end_date(text: str) -> str:
    """A friendly countdown out of ``notAfter=Nov 11 23:41:41 2027 GMT``."""
    match = re.search(r"notAfter\s*=\s*(.+)", text or "")
    if not match:
        return ""
    stamp = match.group(1).strip()
    for pattern in ("%b %d %H:%M:%S %Y %Z", "%b %d %Y %H:%M %Z"):
        try:
            when = datetime.datetime.strptime(stamp, pattern)
        except ValueError:
            continue
        days = (when - datetime.datetime.now()).days
        if days < 0:
            return f"expired {abs(days)} day(s) ago"
        return f"valid, {days} day(s) left"
    return ""


def split_sections(text: str) -> dict[str, list[str]]:
    """The device's answer cut back into the ``== name ==`` parts :func:`view_script` wrote."""
    parts: dict[str, list[str]] = {}
    name = ""
    for line in str(text or "").splitlines():
        hit = _SECTION.match(line.strip())
        if hit:
            name = hit.group(1).strip().lower()
            parts.setdefault(name, [])
            continue
        if name and line.strip():
            parts[name].append(line.rstrip())
    return parts


#: How the summary keys openssl prints are labelled in the GUI table.
SUMMARY_LABELS = {"subject": "Subject", "issuer": "Issuer",
                  "notbefore": "Valid from", "notafter": "Valid until",
                  "serial": "Serial"}


def summary_rows(text: str) -> list:
    """``text`` (what :func:`view_script` printed) as rows for the GUI table: field, value.

    The sections the script prints are read back one by one, so the summary
    shows only what the device actually answered and a line it did not print
    makes no row. A status that names an expiry is tinted red, the same word the
    countdown in :func:`parse_end_date` uses.
    """
    parts = split_sections(text)
    rows: list = []
    for line in parts.get("files", []):
        rows.append(["File", line.strip()])
    for line in parts.get("summary", []):
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        label = SUMMARY_LABELS.get(key) or (key.replace("_", " ").title() or "Note")
        if key == "notafter":
            countdown = parse_end_date(line)
            if countdown:
                value = f"{value}  ({countdown})"
        rows.append([label, value or "(none)"])
    for line in parts.get("fingerprints", []):
        rows.append(["Fingerprint", line.strip()])
    for line in parts.get("still valid?", []):
        words = line.strip()
        if "expired" in words.lower() or "warning" in words.lower():
            rows.append({"cells": ["Status", words], "color": "#ff6b6b"})
        else:
            rows.append(["Status", words])
    for name, lines in parts.items():
        if name.startswith("does it cover"):
            host = name.replace("does it cover", "").strip().rstrip("?").strip()
            for line in lines:
                rows.append([f"Covers {host}".strip(), line.strip()])
    return rows


def read_device(_checked: bool = False) -> None:
    if api is None:
        return
    values = api.config.all()
    _start("reading the certificate")
    api.device.run_async(view_script(values), on_device_read, timeout=60)


def on_device_read(result: dict) -> None:
    """Show what the device said, and lead with whatever it was doing for."""
    global follow_up
    if api is None:
        return
    _finish()
    show_output(result)
    if int(result.get("code") or 0) != 0:
        follow_up = ""
        say(f"the device answered with code {result.get('code')}", bad=True)
        return
    notice = parse_end_date(result.get("out") or "")
    lead, follow_up = follow_up, ""
    parts = [part for part in (lead, notice or "read from the device") if part]
    say(", ".join(parts),
        bad="expired" in notice or "WARNING" in (result.get("out") or ""))


# --- reissuing ---

def subject_lines(values: dict) -> list[str]:
    """The ``[dn]`` section of the .cnf, blank fields left out."""
    return [f"{key} = {text}" for key, text in subject_of(values).items() if text]


def alt_names(values: dict) -> list[str]:
    entries: list[str] = []
    for name in clean_list(config_get(values, "san_dns", [])):
        entries.append(f"DNS:{name}")
    for address in clean_list(config_get(values, "san_ip", [])):
        entries.append(f"IP:{address}" if _IP.match(address) else f"DNS:{address}")
    host = host_of(values)
    if host:
        entries.append(f"IP:{host}" if _IP.match(host) else f"DNS:{host}")
    seen: list[str] = []
    for entry in entries:
        if entry not in seen:
            seen.append(entry)
    return seen


def openssl_config(values: dict) -> str:
    """The openssl .cnf text: the subject, the extensions and the alt names."""
    lines = ["[req]", "distinguished_name = dn", "x509_extensions = ext", "prompt = no", "", "[dn]"]
    subject = subject_lines(values)
    if not any(line.startswith("CN ") for line in subject):
        host = host_of(values)
        if host:
            subject.append(f"CN = {host}")
    lines.extend(subject or ["CN = rasconf"])
    lines.extend(["", "[ext]",
                  "basicConstraints = critical,CA:{}".format(
                      "TRUE" if bool(config_get(values, "ca_flag", True)) else "FALSE"),
                  "keyUsage = critical," + ",".join(
                      clean_list(config_get(values, "key_usage", DEFAULTS["key_usage"]))
                      or ["digitalSignature"])])
    extended = clean_list(config_get(values, "extended_key_usage",
                                     DEFAULTS["extended_key_usage"]))
    if extended:
        lines.append("extendedKeyUsage = " + ",".join(extended))
    names = alt_names(values)
    if names:
        lines.append("subjectAltName = " + ",".join(names))
    return "\n".join(lines) + "\n"


def key_options(values: dict) -> tuple[str, str]:
    """``(key options, keyout option)`` for the chosen kind of key."""
    kind = str(config_get(values, "key_type", "ec") or "ec").lower()
    if bool(config_get(values, "reuse_key", False)):
        return "-key $KEY", ""
    if kind == "rsa":
        bits = int(config_get(values, "rsa_bits", 2048) or 2048)
        return f"-newkey rsa:{min(max(bits, 1024), 8192)}", '-keyout "$TKEY"'
    curve = one_line(config_get(values, "ec_curve", "prime256v1")) or "prime256v1"
    return (f"-newkey ec -pkeyopt ec_paramgen_curve:{curve} "
            "-pkeyopt ec_param_enc:named_curve"), '-keyout "$TKEY"'


def reissue_script(values: dict) -> str:
    where = paths(values)
    try:
        days = int(config_get(values, "days", 365) or 365)
    except (TypeError, ValueError):
        days = 365
    days = min(max(days, 1), 3650)
    options, keyout = key_options(values)
    make_key = bool(keyout)
    backup = bool(config_get(values, "backup", True))
    folder = where["backup"] or where["dir"]
    lines = [
        "set -e",
        f'DIR={quoted(where["dir"])}',
        f'CERT={quoted(where["cert"])}',
        f'KEY={quoted(where["key"])}',
        'CNF="$DIR/.rasconf_cert.cnf"',
        'TCRT="$DIR/.rasconf_cert.crt"',
        'TKEY="$DIR/.rasconf_cert.key"',
        f'OPENSSL={quoted(where["openssl"])}',
        'echo "== prepare =="',
        'mkdir -p "$DIR"',
        'if [ ! -d "$DIR" ]; then echo "FAILED: no such folder $DIR"; exit 1; fi',
    ]
    if backup:
        lines += [
            'echo "== backup =="',
            f'mkdir -p {quoted(folder)}',
            'STAMP=$(date +%Y%m%d-%H%M%S)',
            'if [ -f "$CERT" ]; then cp -p "$CERT" ' + quoted(folder) + '/"$(basename $CERT).$STAMP"; fi',
            'if [ -f "$KEY" ]; then cp -p "$KEY" ' + quoted(folder) + '/"$(basename $KEY).$STAMP"; fi',
        ]
    if not make_key:
        lines += [
            'echo "== key =="',
            'if [ ! -f "$KEY" ]; then echo "FAILED: $KEY is not there, so there is no key to reuse"; exit 1; fi',
        ]
    lines += [
        'echo "== request =="',
        'rm -f "$TCRT" "$TKEY"',
        'cat > "$CNF" <<RASCONF_CERT_EOF',
        openssl_config(values).rstrip("\n"),
        "RASCONF_CERT_EOF",
    ]
    request = f'"$OPENSSL" req -x509 -nodes -config "$CNF" {options}'
    if keyout:
        request += f" {keyout}"
    request += f' -days {days} -out "$TCRT"'
    lines.append(request + " || { echo 'FAILED: openssl req'; rm -f \"$CNF\"; exit 1; }")
    if make_key:
        lines += [
            'chmod 600 "$TKEY"',
            'mv -f "$TKEY" "$KEY"',
        ]
    lines += [
        'chmod 644 "$TCRT"',
        'mv -f "$TCRT" "$CERT"',
        'rm -f "$CNF"',
        'echo "== new certificate =="',
        '"$OPENSSL" x509 -in "$CERT" -noout -subject -dates -serial',
        'echo "== after =="',
    ]
    commands = [command_line(text) for text in
                (config_get(values, "post_commands", DEFAULTS["post_commands"]) or [])]
    commands = [text for text in commands if text]
    if commands:
        for one in commands:
            lines.append(f'{one} || {{ echo "note: {one} did not succeed"; }}')
    else:
        lines.append("echo '(no follow-up commands configured)'")
    lines.append('echo "DONE"')
    return "\n".join(lines)


def issue_problems(values: dict, host: str = "") -> list[str]:
    """What openssl would refuse about this request, named per field before it is sent."""
    problems: list[str] = []
    subject = subject_of(values)
    country = subject["C"]
    if country and not _COUNTRY.match(country):
        problems.append(
            f"the Country is {country!r}. openssl has room for exactly two "
            "letters there - a code like GB or NO, or nothing at all")
    if not subject["CN"] and not host:
        problems.append(
            "there is no Common name, and no address in the connection panel to "
            "use instead, so nothing would say what this certificate is for")
    for entry in alt_names(values):
        kind, _, item = entry.partition(":")
        if kind == "DNS" and not _DNS.match(item):
            problems.append(f"{item!r} is not a name openssl will write "
                            "- no spaces, no commas")
    return problems


#: What openssl's refusals are usually worth checking, the specific ones first.
FAILURES = (
    ("string too long",
     "a subject field is longer than openssl allows - the Country is exactly "
     "two letters, so the word 'Country' itself does not fit"),
    ("string contains",
     "a subject field holds a character openssl will not encode"),
    ("error making certificate request",
     "openssl would not write that subject - check the Country is two letters "
     "and the rest is plain text"),
    ("unrecognized", "a key usage is not spelled the way openssl spells it"),
    ("invalid", "a name, an address or a usage is not something openssl accepts"),
    ("error loading", "the .cnf the addon wrote was not understood"),
    ("no such file", "the openssl command or the folder is not where the config says"),
    ("can't open", "a file or folder named in the config is not there"),
    ("not found", "the openssl command in the config is not on the device"),
    ("i/o", "the device could not read or write one of the files"),
)


def explain_failure(text: str) -> str:
    """One line saying what to check, out of what the device answered with."""
    body = str(text or "").lower()
    if not body.strip():
        return ""
    for marker, advice in FAILURES:
        if marker in body:
            return advice
    return ""


def reissue(_checked: bool = False) -> None:
    if api is None:
        return
    values = merge_current()
    where = paths(values)
    problems = issue_problems(values, host_of(values))
    if problems:
        say("nothing was issued: " + problems[0], bad=True)
        api.ui.warning("The certificate would be refused",
                       "openssl would not accept this request:\n\n  "
                       + "\n  ".join(problems)
                       + "\n\nFix the fields on the left and press the button again.")
        api.log("reissue refused before it started: " + "; ".join(problems))
        return
    if not api.ui.question(
        "Reissue certificate",
        "A new certificate and key will be written on the device:\n\n"
        f"  {where['cert']}\n  {where['key']}\n\n"
        + ("The old pair is backed up first.\n\n" if bool(config_get(values, "backup", True))
           else "The old pair is NOT backed up.\n\n")
        + "Any client that trusts the old certificate has to trust the new one again.\n\n"
          "Continue?",
        default=False,
    ):
        say("reissue cancelled")
        return
    _start("issuing a new certificate")
    api.device.run_async(reissue_script(values), on_reissued, timeout=180)


def on_reissued(result: dict) -> None:
    """Report the outcome, then read the new certificate back."""
    global follow_up
    if api is None:
        return
    _finish()
    show_output(result)
    if int(result.get("code") or 0) == 0 and "DONE" in str(result.get("out") or ""):
        say("new certificate installed")
        api.log("certificate reissued")
        follow_up = "new certificate installed"
        read_device()
    else:
        body = f"{result.get('out') or ''}\n{result.get('err') or ''}"
        advice = explain_failure(body)
        say("the device refused: " + (advice or "see the output"), bad=True)
        api.log(f"reissue failed with code {result.get('code')}"
                + (f": {advice}" if advice else ""))


def merge_current() -> dict:
    """The saved config with whatever is in the form on top of it."""
    merged = merge_config(DEFAULTS, api.config.stored())
    return with_clean_subject(merge_config(merged, values_of()))


def push_config(_checked: bool = False) -> None:
    if api is None:
        return
    values = with_clean_subject(values_of())
    api.config.update(values)
    say("saved to addons/_data/cert_manager/config.json")
    api.log("config saved from the tab")


def pull_config(_checked: bool = False) -> None:
    if api is None:
        return
    show_form(api.config.all())
    say("form reloaded from the config")


def save_copy(_checked: bool = False) -> None:
    """Keep the certificate PEM in the addon's own folder, for a trust store."""
    if api is None:
        return
    values = merge_current()
    where = paths(values)
    _start("copying the certificate")
    api.device.run_async(
        f'if [ -f {quoted(where["cert"])} ]; then cat {quoted(where["cert"])}; '
        'else echo "there is no certificate there"; exit 1; fi',
        on_copy, timeout=60)


def on_copy(result: dict) -> None:
    if api is None:
        return
    _finish()
    text = str(result.get("out") or "")
    if int(result.get("code") or 0) != 0 or "BEGIN CERTIFICATE" not in text:
        say("no certificate could be read", bad=True)
        return
    name = one_line(api.config.get("cert_name", DEFAULTS["cert_name"])) or "mycert.crt"
    try:
        target = api.paths.data / name
        target.write_text(text.strip() + "\n", encoding="utf-8")
    except OSError as error:
        say(f"the copy could not be written: {error}", bad=True)
        return
    say(f"certificate copied to {target}")
    api.log(f"certificate copied to {target}")


# --- one job at a time ---

def _buttons(usable: bool) -> None:
    """Grey the buttons out while the device is working, through the app."""
    for identifier, _text in BUTTONS:
        try:
            api.tabs.set_widget(TAB, identifier, "enabled", bool(usable))
        except AddonError:
            pass


def _start(text: str) -> None:
    global working
    working = True
    say(f"{text}...")
    _buttons(False)


def _finish() -> None:
    global working
    working = False
    _buttons(True)


# --- hooks the app calls ---

def focus_and_read() -> None:
    """The Tools menu entry: go to the tab and read the device."""
    if api is None:
        return
    api.tabs.activate(TAB)
    read_device()


#: What each button on the tab does, by its spec id.
HANDLERS.update({
    "view": read_device,
    "reissue": reissue,
    "push": push_config,
    "pull": pull_config,
    "copy": save_copy,
})


def init(plugin_api: Any) -> None:
    """Build the tab from the saved config."""
    global api
    api = plugin_api
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Certificate manager - addons/_data/cert_manager/config.json")
    build_tab()
    show_form(values)
    api.ui.menu_item("Show the device's certificate", focus_and_read, menu="Tools")
    api.events.on("ssh_connected", on_connected)


def start() -> None:
    """Quietly fill the viewer in when the device is already reachable."""
    if api is not None and api.device.is_connected():
        read_device()


def shutdown() -> None:
    global api
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A tab control reported an event: run its button, the only route a compiled click takes."""
    if str(tab) != TAB or str(event) != "clicked" or working:
        return
    handler = HANDLERS.get(str(identifier))
    if handler is not None:
        handler()


def on_connected(payload: dict) -> None:
    """Show the certificate when a session comes up, if that is what was asked."""
    if api is None or working:
        return
    if bool(api.config.get("refresh_on_connect", True)):
        read_device()


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: make the form match the file."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    show_form(api.config.all())
    say("config changed in Settings; the form now matches it")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
