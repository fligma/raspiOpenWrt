#!/usr/bin/env python3
"""A test for the cert_manager addon (in addon.store, or addons if re-shipped).

The certificate addon's whole job is one openssl request written into a shell
heredoc, and openssl is strict about what may go in it - stricter than anything
here can check by eye. So this looks at the text that reaches the device, at the
subject that used to sit in a config file of its own accord, and at what the tab
says when the device answers with the error it answers with.

Four things get looked at:

* the subject as openssl sees it: what an old config file's words turn into,
  which fields end up in the ``.cnf`` and which are left out;
* the check that refuses a request openssl would refuse, before a key has been
  generated on the far side of an SSH session to find out;
* the script: the heredoc, the key options for each kind of key, the backup,
  the modes, the follow-up commands, and no trace of the old placeholder words;
* the tab as Qt builds it, and what the two failure routes leave in the status
  line - the one caught here, and the one only the device can catch;
* the mark beside the tab's label: an SVG document the addon holds as text, and
  what the app can and cannot turn into an icon.

No internet and no device are needed. Run it from the Test panel, or::

    python addon.store/_tests/test_cert_manager.py

The Berry addon store also runs this file before it compiles the addon, setting
``RASCONF_ADDON_SOURCE`` to the freshly downloaded ``.py`` so the copy under test
is the one that is about to be built.
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
#: tree - the store folder first, the addons folder if it was ever re-shipped.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "cert_manager.py"
if not _HANDED and not SOURCE.is_file():
    SOURCE = ROOT / "addons" / "cert_manager.py"
FAILURES: list[str] = []

#: The address the fake connection panel reports, which a blank Common name and
#: the alternative names both fall back to.
HOST = "192.168.50.1"

#: What the first released version of this addon wrote into a config file that
#: nobody had filled in, and what the device then said. The words were hints;
#: openssl read them as the subject, and a country name holds two characters.
LEGACY_SAVED = {
    "cert_dir": "/etc/ssl",
    "cert_name": "mycert.crt",
    "key_name": "mycert.key",
    "subject": {"C": "Country", "ST": "State", "L": "City",
                "O": "Orginization", "OU": "OrgUnit", "CN": "Device"},
    "san_dns": ["knet3b.lan"],
    "san_ip": ["192.168.50.1"],
    "key_type": "rsa",
    "rsa_bits": 2048,
    "days": 365,
}

#: The same certificate asked for properly.
PROPER = {
    "cert_dir": "/etc/ssl",
    "subject": {"C": "GB", "ST": "Somerset", "L": "Bristol",
                "O": "Raspberry Pi", "OU": "LAN", "CN": "knet3b.lan"},
    "san_dns": ["knet3b.lan", "localhost"],
    "san_ip": ["192.168.50.1"],
    "key_type": "ec",
    "days": 365,
}

#: Verbatim from a device that was given LEGACY_SAVED: the key got made, and
#: then the request was refused for a length openssl has no room for.
ASN1_REFUSAL = {
    "code": 1,
    "out": ("== prepare ==\n== backup ==\n== request ==\nFAILED: openssl req\n\n"
            ".+..+....+........+.++++++++++\n-----\n"
            "Error making certificate request\n"),
    "err": ("309D84907F000000:error:06800097:asn1 encoding routines:"
            "ASN1_mbstring_ncopy:string too long:crypto/asn1/a_mbstr.c:118:maxsize=2"),
}

ISSUED = {"code": 0,
          "out": "== prepare ==\n== backup ==\n== request ==\n== new certificate ==\n"
                 "subject=C = GB, CN = knet3b.lan\nnotAfter=Oct 11 12:00:00 2027 GMT\n"
                 "== after ==\nDONE\n",
          "err": ""}


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- the api object the addon is handed ---

class Api(addon_client.StubAPI):
    """The recording stub, with a device that answers and a form that holds."""

    def __init__(self, *args: Any, answer: Any = ISSUED, host: str = "192.168.50.1", **named: Any) -> None:
        super().__init__(*args, **named)
        self.device_answer = answer
        self.host = host
        self.form: dict = {}
        self.commands: list[str] = []

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        if method == "device.run_async":
            command = self.argument(method, "command", arguments, named, "")
            callback = self.argument(method, "on_done", arguments, named)
            self.commands.append(str(command))
            answer = self.device_answer
            if "x509 -in" in str(command) and "req" not in str(command):
                answer = {"code": 0, "out": "subject=CN = knet3b.lan\n"
                                            "notAfter=Oct 11 12:00:00 2027 GMT\n",
                          "err": ""}
            if callable(callback):
                callback(answer)                # the app answers on the GUI thread
            return answer
        if method == "tabs.widget_value":
            identifier = self.argument(method, "identifier", arguments, named, "")
            what = self.argument(method, "what", arguments, named, "value")
            if identifier not in self.form:
                return None
            value = self.form[identifier]
            if what == "checked":
                return bool(value)
            if isinstance(value, (list, tuple)):
                return ", ".join(str(item) for item in value)
            return value
        if method == "session.status":
            return {"host": self.host, "port": 22, "username": "root",
                    "ssh_connected": True, "sftp_connected": True, "busy": False}
        return super().default(method, arguments, named)

    def sets(self, identifier: str, prop: str | None = None) -> list:
        """What the addon sent to one control on its tab, in order."""
        out = []
        for where, arguments, _named in self.calls:
            if where == "tabs.set_widget" and arguments[1:2] == (identifier,):
                if prop is None or arguments[2:3] == (prop,):
                    out.append(arguments[3] if len(arguments) > 3 else None)
        return out

    def last_set(self, identifier: str, prop: str) -> Any:
        values = self.sets(identifier, prop)
        return values[-1] if values else None

    def warnings(self) -> list[str]:
        return [str(entry[2].get("text") or (entry[1][1] if len(entry[1]) > 1 else ""))
                for entry in self.calls if entry[0] == "ui.warning"]

    def log_text(self) -> str:
        return "\n".join(self.logs)


def as_form(cert: dict) -> dict:
    """``cert`` as the tab would hold it, keyed by widget id."""
    import addon_common

    out: dict = {}
    for key, field in cert_manager.FIELDS_BY_KEY.items():
        kind = str(field.get("kind") or "text")
        value = addon_common.config_get(cert, key,
                                        addon_common.config_get(cert_manager.DEFAULTS, key))
        if kind in ("list", "multiline"):
            value = ", ".join(str(item) for item in (value or []))
        out[cert_manager.widget_id(key)] = value
    return out


def fresh(cert: dict, answer: Any = ISSUED, host: str = HOST, saved: dict | None = None) -> Api:
    """The addon on a stub api, its form filled in as ``cert`` says."""
    api = Api(addon_id="cert_manager", config=dict(saved or {}),
              answer=answer, host=host)
    api.form = as_form(cert)
    cert_manager.init(api)
    return api


# --- one: the subject that reaches openssl ---

def test_the_subject() -> None:
    print("\n1. the subject, as it goes into the .cnf")
    lines = cert_manager.subject_lines(LEGACY_SAVED)
    check(not lines, f"an unfilled old config still writes a subject: {lines}")
    text = cert_manager.openssl_config(LEGACY_SAVED)
    for word in ("Country", "State", "City", "Orginization", "OrgUnit", "Device"):
        check(f"= {word}" not in text, f"'{word}' is still offered to openssl")
    check(f"CN = {HOST}" in text,
          "with every subject field blank the name should fall back to the "
          "address in the connection panel")

    lines = cert_manager.subject_lines(PROPER)
    check(lines == ["C = GB", "ST = Somerset", "L = Bristol", "O = Raspberry Pi",
                   "OU = LAN", "CN = knet3b.lan"],
          f"a real subject is not written in openssl's order: {lines}")

    part = dict(PROPER)
    part["subject"] = {"C": "NO", "CN": "knet3b.lan", "L": ""}
    check(cert_manager.subject_lines(part) == ["C = NO", "CN = knet3b.lan"],
          "a partly filled subject keeps the blanks out and the order in")

    odd = cert_manager.with_clean_subject({"subject": {"C": "Country", "O": "Café局域网"}})
    check(odd["subject"]["C"] == "" and odd["subject"]["O"] == "Café局域网",
          "cleaning the subject also touched a value somebody really typed")


# --- two: the check that refuses what openssl would refuse ---

def test_the_check() -> None:
    print("\n2. refusing a request openssl would refuse")
    problems = cert_manager.issue_problems(LEGACY_SAVED, HOST)
    check(not problems,
          f"the config from the bug report should read as blanks, but: {problems}")

    typed = cert_manager.issue_problems(
        {"subject": {"C": "Country name here", "CN": "knet3b.lan"}}, HOST)
    check(len(typed) == 1 and "Country" in typed[0] and "two" in typed[0],
          f"a country somebody typed themselves was not refused plainly: {typed}")
    look(f"it says: {typed[0] if typed else 'nothing'}")

    check(not cert_manager.issue_problems(PROPER, HOST),
          "a proper subject was refused")
    check(not cert_manager.issue_problems({"subject": {"ST": "Somerset"}}, HOST),
          "a certificate with nothing but an address in it is fine - the name comes "
          "from the connection panel")

    no_name = cert_manager.issue_problems({"subject": {"C": "GB"}}, "")
    check(any("Common name" in text for text in no_name),
          f"no Common name and no host was let through: {no_name}")

    for country in ("GB", "gb", "NO"):
        check(not [text for text in cert_manager.issue_problems(
            {"subject": {"C": country, "CN": "x.lan"}}, "") if "Country" in text],
            f"{country!r} was refused as a country code")
    for country in ("UK1", "G", "United Kingdom", "N O"):
        problems = cert_manager.issue_problems({"subject": {"C": country, "CN": "x.lan"}}, "")
        check(any("Country" in text for text in problems),
              f"{country!r} was not refused as a country code")

    spaced = cert_manager.issue_problems(
        dict(PROPER, san_dns=["knet3b lan"]), HOST)
    check(any("knet3b" in text for text in spaced),
          f"a name with a space in it was let through: {spaced}")


# --- three: the script that goes over SSH ---

def test_the_script() -> None:
    print("\n3. the script the device runs")
    script = cert_manager.reissue_script(cert_manager.with_clean_subject(PROPER))
    check("cat > \"$CNF\" <<RASCONF_CERT_EOF" in script, "no heredoc for the .cnf")
    check("[req]" in script and "prompt = no" in script and "[dn]" in script,
          "the .cnf is not the shape openssl wants")
    check("CN = knet3b.lan" in script and "C = GB" in script,
          "the subject did not reach the .cnf")
    check("subjectAltName = DNS:knet3b.lan,DNS:localhost,IP:192.168.50.1" in script,
          "the alternative names did not reach the .cnf")
    check("basicConstraints = critical,CA:TRUE" in script, "the CA flag is missing")
    check("req -x509 -nodes" in script, "the request is not the one for a "
                                        "self-signed certificate")
    check("-newkey ec -pkeyopt ec_paramgen_curve:prime256v1" in script,
          "the EC key options are not what openssl takes")
    check(script.rstrip().endswith("echo \"DONE\""), "the script does not end by "
                                                     "saying it is finished")
    check("/etc/init.d/uhttpd restart" in script, "the follow-up command was dropped")

    rsa = cert_manager.reissue_script(dict(PROPER, key_type="rsa", rsa_bits=4096))
    check("-newkey rsa:4096 -keyout \"$TKEY\"" in rsa, "an RSA key is not asked for")
    reused = cert_manager.reissue_script(dict(PROPER, reuse_key=True))
    check("-key $KEY" in reused and "-newkey" not in reused,
          "keeping the existing key still generated a new one")
    small = cert_manager.reissue_script(dict(PROPER, key_type="rsa", rsa_bits=80))
    check("rsa:1024" in small, "a size under the floor was not lifted to it")

    filled = cert_manager.reissue_script(LEGACY_SAVED)
    for word in ("Country", "Orginization", "Device"):
        check(f"= {word}\n" not in filled, f"'{word}' was still written to the device")
    look(f"{len(filled.splitlines())} line(s) of shell")


# --- four: what a refusal on the device becomes ---

def test_the_failure() -> None:
    print("\n4. what the device's answer becomes")
    advice = cert_manager.explain_failure(ASN1_REFUSAL["out"] + ASN1_REFUSAL["err"])
    check("Country" in advice and "two letters" in advice,
          f"the asn1 complaint was not recognised: {advice!r}")
    look(f"the status line says: {advice}")
    check(cert_manager.explain_failure("openssl: not found").startswith("the openssl command"),
          "a missing openssl was not noticed")
    check(cert_manager.explain_failure("") == "", "an empty answer was explained anyway")
    check(cert_manager.explain_failure("subject=C = GB, CN = knet3b.lan") == "",
          "a line that means nothing was given advice about")

    api = fresh({"subject": {"C": "United Kingdom", "CN": "knet3b.lan"}},
                saved=LEGACY_SAVED)
    before = len(api.commands)
    cert_manager.reissue()
    check(not api.called("device.run_async"),
          "a seven-letter country was sent to the device anyway")
    check(not api.called("ui.question"), "the user was asked to confirm a refusal")
    said = str(api.last_set("status", "text"))
    check("Country" in said, f"the tab does not say why: {said!r}")
    check(any("reissue refused" in line for line in api.logs),
          "a refusal was not put in the log")
    look(f"the tab says: {said}")
    check(len(api.commands) == before, "something still ran on the device")

    print("\n4b. the same old file, with its words treated as blanks")
    legacy = fresh(LEGACY_SAVED, saved=LEGACY_SAVED)
    cert_manager.reissue()
    asked = [command for command in legacy.commands if "req -x509" in command]
    check(len(asked) == 1, f"the cleaned-up config did not reach the device: "
                           f"{len(legacy.commands)} command(s)")
    check(asked and "= Country" not in asked[0] and f"CN = {HOST}" in asked[0],
          "what went over SSH still held the old words, or lost the name")
    check("new certificate installed" in str(legacy.last_set("status", "text")),
          f"a config that only needed cleaning was not issued: "
          f"{legacy.last_set('status', 'text')!r}")
    look(f"the tab says: {legacy.last_set('status', 'text')}")

    api2 = fresh(PROPER, answer=ASN1_REFUSAL)
    cert_manager.reissue()
    check(any("req -x509" in command for command in api2.commands),
          "a proper request never reached the device")
    said = str(api2.last_set("status", "text"))
    check("two letters" in said, f"a device refusal was reported without advice: {said!r}")
    look(f"the tab says: {said}")

    api3 = fresh(PROPER, saved=PROPER)
    cert_manager.reissue()
    check("new certificate installed" in str(api3.last_set("status", "text")),
          f"a good run was not called good: {api3.last_set('status', 'text')!r}")
    cert_manager.shutdown()


# --- five: the tab, and the config it leaves behind ---

def test_the_tab(app: Any) -> None:
    print("\n5. the tab, built by Qt from the spec")
    import addon_api

    spec = cert_manager.tab_spec(cert_manager.with_clean_subject(LEGACY_SAVED))
    widget, registry = addon_api.build_spec(spec)
    wanted = {cert_manager.widget_id(key) for key in cert_manager.FIELDS_BY_KEY}
    wanted |= {"status", "viewer"} | {name for name, _text in cert_manager.BUTTONS}
    check(not wanted - set(registry),
          f"the spec leaves out {sorted(wanted - set(registry)) or 'nothing'}")
    check(registry["viewer"].isReadOnly(), "the viewer can be typed into")
    check(registry["subject_C"].text() == ""
          and registry["subject_ST"].text() == ""
          and registry["subject_CN"].text() == "",
          "the subject boxes still start as the old words")
    check(registry["subject_C"].placeholderText() == "GB",
          f"the Country box has no hint about its two letters: "
          f"{registry['subject_C'].placeholderText()!r}")
    check("two letters" in registry["subject_C"].toolTip().lower(),
          "the Country tooltip does not mention the limit")
    check(registry["subject_CN"].placeholderText() == "knet3b.lan",
          "the Common name box has no example")
    missing_tip = [name for name, _text in cert_manager.BUTTONS
                   if not registry[name].toolTip()]
    check(not missing_tip, f"these buttons have no tooltip: {missing_tip or 'none'}")
    check(bool(registry["days"].maximum()), "the lifetime box has no range")
    check(registry["key_type"].currentText() in ("ec", "rsa"),
          "the key type is not a choice of the two openssl takes")

    def applies(identifier: str, prop: str, value: Any) -> str:
        try:
            addon_api.apply_widget_property(registry[identifier], prop, value)
        except Exception as error:              # noqa: BLE001 - the point of the check
            return f"{type(error).__name__}: {error}"
        return ""

    for identifier, prop, value in [("status", "text", "hello"),
                                    ("status", "style", "color: #ffb020; padding: 2px 4px;"),
                                    ("viewer", "text", "subject=CN = knet3b.lan"),
                                    ("reissue", "enabled", False),
                                    ("subject_C", "value", "NO")]:
        why = applies(identifier, prop, value)
        check(not why, f"{identifier} takes '{prop}'" + (f": {why}" if why else ""))
    unreadable = [key for key, field in cert_manager.FIELDS_BY_KEY.items()
                  if addon_api.read_widget_value(
                      registry[cert_manager.widget_id(key)],
                      "checked" if str(field.get("kind")) == "bool" else "value") is None]
    check(not unreadable, f"these controls give back nothing: {unreadable or 'none'}")

    print("\n6. saving the form fixes what the old file held")
    api = fresh(LEGACY_SAVED, saved=LEGACY_SAVED)
    cert_manager.push_config()
    entry = api.last("config.update")
    subject = (entry[1][0] if entry else {}).get("subject") or {}
    check(subject.get("C") == "" and subject.get("CN") == "",
          f"the words came back out of the form as values: {subject}")
    check(set(subject) == set(cert_manager.SUBJECT_ORDER),
          f"a saved subject is not the whole subject: {sorted(subject)}")
    cert_manager.shutdown()


# --- seven: the mark beside the tab's label ---

def test_the_mark(app: Any) -> None:
    """The trophy: what the addon sends, and what the app makes of it."""
    print("\n7. the tab's mark")
    api = fresh(PROPER, saved=PROPER)
    check(api.called("tabs.set_icon"), "the addon gave its tab no icon")
    sent = str(api.asked("tabs.set_icon", "icon"))
    check(sent == cert_manager.TAB_ICON,
          "what the addon sent is not its own TAB_ICON")
    check(sent.startswith("<svg") and sent.rstrip().endswith("</svg>"),
          "the icon is not a self-contained SVG document, so a compiled copy of "
          "this addon could not send it")
    check("#161616" not in sent and "#212121" not in sent,
          "the trophy is pasted onto a background square instead of being drawn")
    if app is None:
        look("no Qt here, so the drawing itself is not checked")
        return
    import addon_api
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QColor, QPixmap

    icon = addon_api.build_icon(cert_manager.TAB_ICON)
    check(icon is not None and not icon.isNull(),
          "the app cannot draw what the addon sent")
    if icon is not None:
        sizes = icon.availableSizes() or [QSize(64, 64)]
        check(max(size.width() for size in sizes) >= 32,
              f"the mark was not rendered at a usable size: {sizes}")
        image = icon.pixmap(sizes[-1]).toImage()
        gold = sum(1 for x in range(image.width()) for y in range(image.height())
                   if image.pixelColor(x, y).red() > 180
                   and image.pixelColor(x, y).green() > 100
                   and image.pixelColor(x, y).blue() < 90)
        check(gold > image.width() * image.height() * 0.05,
              f"hardly anything was drawn: {gold} gold pixel(s) of "
              f"{image.width() * image.height()}")
        look(f"{sizes[-1].width()}px mark, {gold} gold pixel(s) in it")

    for nothing in ("", "   ", None, "not-a-file.png", "<svg><broken",
                    str(Path(__file__))):
        check(addon_api.build_icon(nothing) is None,
              f"{str(nothing)!r} was taken for an icon")

    # A picture file works too, which is what an addon with an icon.png uses.
    folder = Path(api.paths.data)
    picture = folder / "probe_mark.png"
    canvas = QPixmap(24, 24)
    canvas.fill(QColor("#c51a4a"))
    canvas.save(str(picture))
    try:
        check(addon_api.build_icon(str(picture)) is not None,
              "a real PNG by absolute path was not read")
        check(addon_api.build_icon(picture.name, folder) is not None,
              "a real PNG beside the addon's own folder was not read by name")
    finally:
        picture.unlink(missing_ok=True)
    cert_manager.shutdown()


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)    # the Test pane wants it live
    global cert_manager
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    cert_manager = addon_client.load_addon(SOURCE)
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the spec itself is not built: {error}")

    print(f"cert_manager: the addon at {SOURCE.name}")
    # host_of() asks the connection panel, so even the plain text of the .cnf
    # needs an api to be standing there; this one reports HOST and nothing else.
    cert_manager.init(Api(addon_id="cert_manager", host=HOST))
    test_the_subject()
    test_the_check()
    test_the_script()
    test_the_failure()
    if app is not None:
        test_the_tab(app)
    else:
        print("\n5-6. skipped: no Qt on this machine")
    test_the_mark(app)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


cert_manager = None

if __name__ == "__main__":
    raise SystemExit(main())
