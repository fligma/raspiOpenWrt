#!/usr/bin/env python3
"""A test for the password manager addon (in addon.store).

This addon's promises are the sort that cannot be checked by looking: a file
that must hold nothing readable, a password that must come back only for the
right master, a clipboard that must be wiped but never wiped on the wrong
thing, and a vault that must shut itself. So the test reads the vault file as
bytes, answers the dialogs the addon asks for, and fires its timers by hand
instead of waiting for them.

Nine things get looked at:

* the seal: what opens with the right password, and the ways a tampered file
  refuses to;
* the header: that a file which is not a vault is refused before a master
  password has been spent on it, and without one;
* the generator and the guess-work bar;
* an entry: its id, its blanks filled in, and the label a list row can hand
  back for it;
* a whole session on a real folder: created, saved into, locked, opened again
  with a wrong password and then the right one;
* the clipboard: what is copied, when the timer is armed, and the guard that
  leaves alone anything the addon did not put there;
* the idle clock that locks the vault;
* the tab as Qt builds it, and the bullets that only come off on request;
* the mark beside the tab's label.

No internet, no device and no master password of anybody's is needed. Run it
from the Test panel, or::

    python addon.store/_tests/test_password_manager.py

The Berry addon store also runs this file before it compiles the addon, setting
``RASCONF_ADDON_SOURCE`` to the freshly downloaded ``.py`` so the copy under test
is the one that is about to be built.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402

#: The addon to test: the copy a store install handed over, else the one here.
_HANDED = os.environ.get("RASCONF_ADDON_SOURCE")
SOURCE = Path(_HANDED) if _HANDED else ROOT / "addon.store" / "password_manager.py"
FAILURES: list[str] = []

#: The master password the dialogs answer with, and a wrong one.
MASTER = "correct horse battery"
WRONG = "not the master password"

#: Rounds used instead of the configured 120000: the same code path, and a test
#: that does not spend a second on every save.
ROUNDS = 20000

#: The folder this run keeps its vaults in, made in ``main`` and gone after it.
DATA: Path | None = None

password_manager = None


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- the clocks a test can fire ---

class _Shot:
    """One scheduled job, held back until the test decides its seconds are up."""

    def __init__(self, seconds: Any, callback: Any, repeat: bool = False) -> None:
        self.seconds = float(seconds)
        self.callback = callback
        self.repeat = bool(repeat)
        self.cancelled = False
        self.fired = False

    def cancel(self) -> None:
        """``_Timer.cancel``: the name a compiled addon's handle answers to."""
        self.cancelled = True

    def run(self) -> Any:
        self.fired = True
        return self.callback()


class _Clock:
    """``api.schedule``, held: nothing runs on its own while a test thinks."""

    def __init__(self) -> None:
        self.shots: list[_Shot] = []

    def after(self, seconds: Any, callback: Any) -> _Shot:
        shot = _Shot(seconds, callback)
        self.shots.append(shot)
        return shot

    def every(self, seconds: Any, callback: Any) -> _Shot:
        shot = _Shot(max(0.1, float(seconds)), callback, repeat=True)
        self.shots.append(shot)
        return shot

    def cancel_all(self) -> None:
        for shot in self.shots:
            shot.cancel()
        self.shots.clear()

    def live(self, name: str = "") -> list[_Shot]:
        """The jobs still to run; a repeating one is always still to run."""
        return [shot for shot in self.shots
                if not shot.cancelled and (not shot.fired or shot.repeat)
                and (not name or getattr(shot.callback, "__name__", "") == name)]

    def fire(self, name: str) -> Any:
        """Run the live job called ``name``; a test that found none is a failure."""
        wanted = self.live(name)
        if not wanted:
            FAILURES.append(f"nothing was scheduled as {name!r}")
            return None
        return wanted[-1].run()


# --- the api object the addon is handed ---

class Api(addon_client.StubAPI):
    """The recording stub, with a clipboard, dialogs that answer and a tab that reads back."""

    def __init__(self, *args: Any, answers: list | None = None,
                 questions: list | None = None, **named: Any) -> None:
        super().__init__(*args, **named)
        self.answers = list(answers or [])
        self.questions = list(questions or [])
        self.form: dict = {}
        self.clipboard = ""
        self.copy_works = True
        self.choice = ""
        self.picked = ""
        self.clock = _Clock()
        # The addon arms its own timers; a real thread would fire them while the
        # test was still looking at what had been scheduled.
        self.schedule = self.clock

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        ask = lambda key, none=None: self.argument(method, key, arguments,
                                                   named, none)
        if method == "tabs.set_widget":
            # Write it back into the fake tab, so what the addon sets it can read.
            identifier = str(ask("identifier", ""))
            prop = str(ask("prop", "text"))
            value = ask("value")
            if prop == "value":
                self.form[identifier] = value
            elif prop == "checked":
                self.form[identifier] = bool(value)
            return None
        if method == "tabs.widget_value":
            identifier = str(ask("identifier", ""))
            what = str(ask("what", "value"))
            if identifier not in self.form:
                return None
            value = self.form[identifier]
            if what == "checked":
                return bool(value)
            return value
        if method == "ui.input":
            return self.answers.pop(0) if self.answers else ""
        if method == "ui.question":
            return self.questions.pop(0) if self.questions else True
        if method == "ui.choose":
            return self.choice
        if method == "ui.pick_file":
            return self.picked
        if method == "ui.copy":
            if not self.copy_works:
                return False
            self.clipboard = str(ask("text", "") or "")
            return True
        if method == "ui.clipboard":
            return self.clipboard
        if method == "ui.clipboard_clear":
            # The guard the real clipboard call is given, kept honest here.
            guard = str(ask("only_if", "") or "")
            if guard and self.clipboard != guard:
                return False
            self.clipboard = ""
            return True
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

    def said(self) -> str:
        """The last line put on the tab's status label."""
        return str(self.last_set("status", "text") or "")

    def clip_note(self) -> str:
        """The clipboard line, beside **Show passwords**."""
        return str(self.last_set("clipnote", "text") or "")

    def warnings(self) -> list[str]:
        return [str(self.argument(where, "text", arguments, named, ""))
                for where, arguments, named in self.calls if where == "ui.warning"]

    def log_text(self) -> str:
        return "\n".join(self.logs)


def fresh(answers: list | None = None, config: dict | None = None,
          questions: list | None = None) -> Api:
    """The addon on a stub api, started, with the dialogs and clocks it will get."""
    api = Api(addon_id="password_manager", config=config or {},
              answers=answers, questions=questions)
    password_manager.init(api)
    password_manager.start()
    return api


def empty_folder() -> None:
    """Start the vault again, so a section never reads the last one's file."""
    password_manager.shutdown()
    for item in sorted(DATA.iterdir()):
        if item.is_file() or item.is_symlink():
            item.unlink()
        else:
            shutil.rmtree(item, ignore_errors=True)


def open_vault(config: dict | None = None) -> Api:
    """A vault of this run's own, made through the button the user presses."""
    api = fresh(answers=[MASTER, MASTER],
                config=dict(ROUNDED, **(config or {})))
    password_manager.press_unlock()
    return api


ROUNDED = {"kdf_iterations": ROUNDS}


def seed(api: Api, *pairs: tuple) -> None:
    """Put entries in the vault the way the tab does: new entry, fill it, Save.

    The **＋ New** press matters. Save writes the entry the form is standing on,
    and after a save that is the one it just wrote - so saving twice without
    clearing the form edits the first entry rather than adding a second.
    """
    for name, secret in pairs:
        password_manager.press_add()
        api.form.update({"name": name, "user": "root", "pass": secret,
                         "site": "", "note": ""})
        password_manager.save_entry()


# --- zero: what the store is told ---

def test_the_manifest() -> None:
    print("\n0. what the store is told")
    manifest = password_manager.ADDON
    check(manifest.get("name") == "Password manager", "the addon is not named")
    check(manifest.get("requires") == [],
          f"the manifest asks for a device: {manifest.get('requires')}")
    check(manifest.get("connection") == "none",
          "the manifest does not say there is nothing to connect to")
    check(manifest.get("api") == 1, "the manifest names no api level")
    check("clipboard" in str(manifest.get("description")).lower(),
          "the line the store shows says nothing about the clipboard wipe")
    check(str(manifest.get("description", "")).strip().endswith("."),
          "the store's line about this addon is not a sentence")
    for key, field in password_manager.FIELDS_BY_KEY.items():
        check(key in password_manager.DEFAULTS,
              f"{field.get('label')!r} is a field with no default")
        if field.get("kind") == "number":
            check("minimum" in field and "maximum" in field,
                  f"{field.get('label')!r} is a number with no range")
    check(set(password_manager.DEFAULTS) == set(password_manager.FIELDS_BY_KEY),
          "a config key is saved that the settings form cannot show")


# --- one: the seal, and everything that breaks it ---

def test_the_seal() -> None:
    print("\n1. sealing a vault, and opening it again")
    salt = bytes(range(password_manager.SALT_BYTES))
    nonce = bytes(range(password_manager.NONCE_BYTES))
    plain = b'{"entries": [{"name": "Router"}]}'
    sealed = password_manager.seal(plain, MASTER, ROUNDS, salt=salt, nonce=nonce)
    check(sealed["format"] == password_manager.VAULT_FORMAT
          and sealed["cipher"] == password_manager.CIPHER
          and sealed["kdf"] == password_manager.KDF,
          f"the header does not name the scheme it was written with: {sealed}")
    check(sealed["iterations"] == ROUNDS, "the header lost the round count")
    check(base64.b64encode(plain).decode() not in json.dumps(sealed),
          "the entries are in the file in the clear")
    back = password_manager.open_sealed(
        sealed, base64.b64decode(sealed["payload"]),
        base64.b64decode(sealed["tag"]), salt, nonce, MASTER)
    check(back == plain, "what was sealed does not come back out")

    refused = _why_sealed_refuses(sealed, salt, nonce, WRONG)
    check(bool(refused), "a wrong master password opened the vault")
    check("does not open" in refused,
          f"the refusal does not say that: {refused!r}")

    # One byte of the ciphertext moved: the tag has to fail, not the reading.
    bytes_ = bytearray(base64.b64decode(sealed["payload"]))
    bytes_[0] ^= 0x01
    check(not _opens(sealed, bytes(bytes_), base64.b64decode(sealed["tag"]),
                     salt, nonce, MASTER),
          "a changed ciphertext byte still opened")

    moved = bytearray(base64.b64decode(sealed["tag"]))
    moved[3] ^= 0x40
    check(not _opens(sealed, base64.b64decode(sealed["payload"]), bytes(moved),
                     salt, nonce, MASTER),
          "a changed tag byte still opened")

    # The header is inside the tag too: a file quietly re-stretched to less work
    # must not be decrypted at the attacker's chosen strength.
    check(not _opens(dict(sealed, iterations=max(1000, ROUNDS // 10)),
                     base64.b64decode(sealed["payload"]),
                     base64.b64decode(sealed["tag"]), salt, nonce, MASTER),
          "a header with fewer rounds was accepted")
    check(not _opens(sealed, base64.b64decode(sealed["payload"]),
                     base64.b64decode(sealed["tag"]), salt,
                     bytes(nonce) + b"\x01", MASTER),
          "a nonce that is not the sealed one was accepted")

    first = password_manager.seal(plain, MASTER, ROUNDS)
    second = password_manager.seal(plain, MASTER, ROUNDS)
    check(first["payload"] != second["payload"] and first["salt"] != second["salt"],
          "saving the same entries twice wrote the same file")
    check(password_manager.decrypt_vault(json.dumps(second), MASTER) != [],
          "the second save of the same entries does not open")

    material = password_manager.derive_key(MASTER, salt, ROUNDS)
    check(len(material) == password_manager.KEY_BYTES,
          f"the key is not the two halves it claims: {len(material)} byte(s)")
    check(material[:32] != material[32:], "the keystream and the tag share a key")
    stream = password_manager.keystream(material[:32], nonce, 32)
    check(len(set(stream)) >= 26,
          f"the mask repeats itself inside 32 bytes: {len(set(stream))} distinct")
    other = password_manager.keystream(material[:32], bytes(nonce[:-1] + b"\x01"), 32)
    check(stream != other, "a different nonce gives the same mask, so two saves "
                           "of one password come out alike")


def _opens(header: dict, payload: bytes, tag: bytes, salt: bytes, nonce: bytes,
           master: str) -> bool:
    """Whether the parts open with ``master`` - the yes/no a seal test wants."""
    try:
        password_manager.open_sealed(header, payload, tag, salt, nonce, master)
    except password_manager.AddonError:
        return False
    return True


def _why_sealed_refuses(sealed: dict, salt: bytes, nonce: bytes,
                        master: str) -> str:
    """The complaint the seal makes about a password, or "" if it opens to it."""
    try:
        password_manager.open_sealed(
            sealed, base64.b64decode(sealed["payload"]),
            base64.b64decode(sealed["tag"]), salt, nonce, master)
    except password_manager.AddonError as error:
        return str(error)
    return ""


# --- two: the header, read without a password ---

def test_the_header() -> None:
    print("\n2. telling a vault from a file that is not one")
    text = password_manager.encrypt_vault(MASTER, [{"name": "Router",
                                                   "password": "s3cr3t"}], ROUNDS)
    header = password_manager.vault_header(text)
    check(header["format"] == password_manager.VAULT_FORMAT
          and int(header["iterations"]) == ROUNDS,
          "a vault's own header was not read while locked")
    check("s3cr3t" not in text, "a password reached the vault file in the clear")

    for nothing, why in [("", "nothing at all"), ("   ", "blank space"),
                         ("{not json", "broken JSON"),
                         (json.dumps([1, 2]), "a list rather than a header")]:
        check(bool(_refuses_header(nothing)), f"{why} was not refused as a vault")

    other = json.dumps({"format": "keepass", "cipher": password_manager.CIPHER,
                        "kdf": password_manager.KDF})
    check("not written by this scheme" in _refuses_header(other),
          f"a foreign format was not named: {_refuses_header(other)!r}")
    moved = json.dumps({"format": password_manager.VAULT_FORMAT,
                        "cipher": "aes-gcm", "kdf": password_manager.KDF})
    check("cipher" in _refuses_header(moved),
          f"a scheme this addon does not speak was not refused: "
          f"{_refuses_header(moved)!r}")
    check(not password_manager.master_opens(text, WRONG),
          "the wrong password was said to open it")
    check(password_manager.master_opens(text, MASTER),
          "the right password was not said to open it")
    look(f"a vault of {len(text)} characters, holding no word of the entry")


def _refuses_header(text: str) -> str:
    """The complaint ``vault_header`` makes about a file, or "" if it accepts it."""
    try:
        password_manager.vault_header(text)
    except password_manager.AddonError as error:
        return str(error)
    return ""


# --- three: a made-up password, and what it is worth ---

def test_the_maker() -> None:
    print("\n3. the generator and the bar beside it")
    all_on = {"gen_upper": True, "gen_digits": True, "gen_symbols": True,
              "gen_no_ambiguous": False}
    pool = password_manager.alphabet_of(all_on)
    for wanted in ("a", "Z", "5", "!"):
        check(wanted in pool, f"with every switch on, {wanted!r} is not in the pool")
    letters = {"gen_upper": False, "gen_digits": False, "gen_symbols": False,
               "gen_no_ambiguous": False}
    check(password_manager.alphabet_of(letters) == "abcdefghijklmnopqrstuvwxyz",
          "a pool of lowercase only is not one")

    clean = password_manager.alphabet_of({"gen_upper": True, "gen_digits": True,
                                          "gen_symbols": False,
                                          "gen_no_ambiguous": True})
    for confusing in "Il1O0":
        check(confusing not in clean,
              f"skipping the confusing ones still offered {confusing!r}")

    made = password_manager.make_password(24, pool)
    check(len(made) == 24 and all(character in pool for character in made),
          f"24 draws from the pool came back as {made!r}")
    check(len(set(made) | set(password_manager.make_password(24, pool))) > 12,
          "two draws of 24 characters are the same password")
    check(len(password_manager.make_password(2, pool)) == 6,
          "a length under the floor was not lifted to it")
    check(len(password_manager.make_password(200, pool)) == 64,
          "a length over the ceiling was not held down to it")
    spare = password_manager.make_password(16, "")
    check(len(spare) == 16 and spare.islower(),
          f"an empty pool made nothing rather than falling back: {spare!r}")

    check(password_manager.password_strength("") == (0, "nothing typed"),
          "an empty box is not described as empty")
    check(password_manager.password_strength("abc")[1] == "too short",
          "a three-letter password is not called short")
    weak = password_manager.password_strength("password")[0]
    good = password_manager.password_strength(made)[0]
    check(good > weak, f"the bar rates {weak}% for 'password' and {good}% for a draw")
    check(good >= 90, f"a 24-character draw from the full pool is not near the top: "
                      f"{good}%")
    check(weak >= 1, "a password nobody would use scored nothing at all")
    check(password_manager.pool_size("abcdef") == 26,
          "a pool of lowercase letters is counted wrong")
    check(password_manager.pool_size("Abcdef1!x") >= 63,
          "the classes in a mixed password were not all counted")
    look(f"'password' {weak}%, a 24-character draw {good}%")


# --- four: an entry, and the label a row wears ---

def test_an_entry() -> None:
    print("\n4. one entry, and how the list can point at it")
    blank = password_manager.clean_entry({})
    check(blank["name"] == "(no name)" and len(blank["uid"]) >= 8,
          f"an empty entry is not filled in and stamped: {blank}")
    check(set(blank) == set(("uid", "changed", *password_manager.ENTRY_KEYS)),
          f"an entry holds more or less than the five lines: {sorted(blank)}")
    keep = password_manager.clean_entry({"name": " Mail ",
                                         "password": " leading space"})
    check(keep["name"] == "Mail", "a name was kept with its space")
    check(keep["password"] == " leading space",
          f"a password was trimmed of a space somebody typed: {keep['password']!r}")
    check(password_manager.clean_entry(keep)["uid"] == keep["uid"],
          "saving an entry a second time gave it a new id")

    order = password_manager.sort_entries([{"name": "zebra"}, {"name": "Amazon"},
                                           {"name": "apple"}])
    check([one["name"] for one in order] == ["Amazon", "apple", "zebra"],
          f"the list is not sorted the way a person reads it: "
          f"{[one['name'] for one in order]}")

    router = {"name": "Router", "user": "root", "password": "hunter2",
              "site": "192.168.1.1", "note": "the PIN is 4321"}
    check(password_manager.entry_matches(router, "192.168"),
          "the filter cannot find an address")
    check(password_manager.entry_matches(router, "PIN"),
          "the filter cannot find a note")
    check(not password_manager.entry_matches(router, "hunter"),
          "the filter searches passwords, which puts a secret in a filter box")
    check(password_manager.entry_matches(router, ""), "a blank filter hid everything")

    entries = [{"name": "GitHub", "uid": "aaa"}, {"name": "github", "uid": "bbb"},
               {"name": "Other", "uid": "ccc"}]
    kept, labels = password_manager.label_map(entries)
    check(list(labels) == ["GitHub", "github (2)", "Other"],
          f"two entries of the same name do not get two labels: {list(labels)}")
    check(labels.get("github (2)") == "bbb",
          "the second of two like-named entries is not the one the label points at")
    check(len(kept) == 3, "the list dropped an entry on the way to its labels")


# --- five: a session, on a real folder ---

def test_a_session() -> None:
    print("\n5. a vault made, saved into, locked and opened again")
    empty_folder()
    api = fresh(answers=[MASTER, MASTER], config=ROUNDED)
    check(api.declared.get("fields") == password_manager.FIELDS,
          "the settings form was not declared with these fields")
    check(api.declared.get("defaults") == password_manager.DEFAULTS,
          "the declared defaults are not the addon's own")
    check(not password_manager.is_open(), "the tab came up with the vault open")
    check(bool(api.last_set("lockstate", "text")), "the tab says nothing about its state")
    check(api.asked("tabs.add_tab", "pinned") and api.asked("tabs.add_tab", "icon_only"),
          "the vault tab is not pinned to the bar as furniture")
    check(str(api.last_set("unlock", "text")) == "Create vault",
          f"with no file the button does not say what it does: "
          f"{api.last_set('unlock', 'text')!r}")

    password_manager.set_showing(True)
    password_manager.press_unlock()
    check(password_manager.is_open(), "no vault was created")
    where = Path(DATA) / "vault.json"
    check(where.is_file(), f"the vault was not written at {where}")
    text = where.read_text(encoding="utf-8")
    check(MASTER not in text, "the master password is somewhere in the vault file")
    check(password_manager.vault_header(text)["format"] == password_manager.VAULT_FORMAT,
          "the file written is not the shape the addon reads back")

    seed(api, ("Router", "s3cr3t-pi"), ("GitHub", "gh-token-9"))
    check(len(password_manager.entries()) == 2,
          f"two saves left {len(password_manager.entries())} entry(ies)")
    rows = api.last_set("list", "rows") or []
    check(len(rows) == 2 and rows[0][0] == "GitHub",
          f"the list does not show both, sorted: {rows}")
    check(rows[0][2] == "gh-token-9",
          f"a password is not in its own column while showing: {rows[0]}")
    again = password_manager.decrypt_vault(where.read_text(encoding="utf-8"), MASTER)
    check(sorted(one["name"] for one in again) == ["GitHub", "Router"],
          f"the file on disk does not hold the entries: {[one['name'] for one in again]}")
    check("s3cr3t-pi" not in where.read_text(encoding="utf-8"),
          "a saved password is readable in the vault file")

    print("\n5b. locking it, and the two answers to a password")
    password_manager.press_lock()
    check(not password_manager.is_open(), "**Lock** left the vault open")
    check(not password_manager.session.get("master"), "the master password is still held")
    check(api.last_set("list", "rows") == [], "a locked vault still lists its entries")
    check(not api.last_set("name", "value"), "the form still shows an entry")

    before = len(api.warnings())
    api.answers = [WRONG]
    check(not password_manager.unlock(), "the wrong password opened the vault")
    check(len(api.warnings()) > before, "a wrong password was refused without a word")
    check(any("does not open" in line for line in api.warnings()[before:]),
          f"the refusal does not say what happened: {api.warnings()[before:]}")
    check(not password_manager.is_open(), "a refused unlock left the vault open")
    check(password_manager.session.get("entries") == [], "the entries came in anyway")

    api.answers = [MASTER]
    check(password_manager.unlock(), "the right password did not open the vault")
    check(len(password_manager.entries()) == 2, "the entries did not come back")
    check("unlocked" in api.said(), f"the tab does not say it is open: {api.said()!r}")

    print("\n5c. a note changed while the passwords are hidden")
    password_manager.set_showing(False)
    password_manager.pick("Router")
    saved = str((password_manager.current_entry() or {}).get("password"))
    check(saved == "s3cr3t-pi", f"the picked entry is not the one asked for: {saved!r}")
    check(str(api.form.get("pass")) == password_manager.mask_text(),
          f"the box holds more than bullets while hidden: {api.form.get('pass')!r}")
    api.form["note"] = "changed the PIN in 2026"
    password_manager.save_entry()
    back = password_manager.decrypt_vault(where.read_text(encoding="utf-8"), MASTER)
    router = [one for one in back if one["name"] == "Router"][0]
    check(router["password"] == saved,
          f"saving a note overwrote a password nobody could see: {router['password']!r}")
    check(router["note"] == "changed the PIN in 2026", "the note was not saved")
    check(password_manager.password_from_form() == saved,
          "a hidden form reports the bullets as the password")

    print("\n5d. a new master password over the same entries")
    api.answers = [MASTER, "another master", "another master"]
    password_manager.change_master()
    check(not password_manager.master_opens(where.read_text(encoding="utf-8"), MASTER),
          "the old password still opens the rewritten file")
    check(len(password_manager.decrypt_vault(
        where.read_text(encoding="utf-8"), "another master")) == 2,
          "the entries did not survive the change")
    check("sealed with the new" in api.said(),
          f"a changed master password was not said: {api.said()!r}")

    print("\n5e. a backup is the same ciphertext, and nothing else")
    password_manager.press_backup()
    copies = sorted((Path(DATA) / "backups").glob("*.json"))
    check(len(copies) == 1, f"the backups folder holds {len(copies)} file(s)")
    if copies:
        check("s3cr3t-pi" not in copies[0].read_text(encoding="utf-8"),
              "a backup was written in the clear")
        check(password_manager.master_opens(copies[0].read_text(encoding="utf-8"),
                                            "another master"),
              "a copy of the vault does not open with the password it copies")
    password_manager.shutdown()


# --- six: the clipboard, and the guard on its wipe ---

def test_the_clipboard() -> None:
    print("\n6. copying a password, and taking it back off")
    empty_folder()
    api = open_vault({"clipboard_clear_seconds": 30, "notify_on_copy": True})
    password_manager.set_showing(True)
    seed(api, ("Router", "s3cr3t-pi"))
    password_manager.pick("Router")
    api.clipboard = ""

    password_manager.copy_password()
    check(api.clipboard == "s3cr3t-pi",
          f"the password did not reach the clipboard: {api.clipboard!r}")
    shots = api.clock.live("wipe_clipboard")
    check(len(shots) == 1, f"no timer was armed to take it back: {api.clock.shots}")
    check(shots[0].seconds == 30.0,
          f"the timer is not the configured seconds: {shots[0].seconds}")
    check("30s" in api.clip_note(),
          f"the tab does not say when it comes off: {api.clip_note()!r}")
    check(api.called("ui.notify"), "a copy was not announced")

    api.clock.fire("wipe_clipboard")
    check(api.clipboard == "", "the timer did not clear the clipboard")
    check("wiped" in api.clip_note(),
          f"a wipe was not said as one: {api.clip_note()!r}")
    check("clipboard wiped" in api.log_text(),
          "a wipe was not put in the log")

    # The guard: anything copied since is somebody else's, and stays.
    password_manager.copy_password()
    api.clipboard = "a paragraph the user copied on their own"
    api.clock.fire("wipe_clipboard")
    check(api.clipboard == "a paragraph the user copied on their own",
          "the timer wiped what somebody else had copied")
    check("left alone" in api.clip_note(),
          f"a refused wipe was not explained: {api.clip_note()!r}")

    # A second copy replaces the first timer rather than arming two.
    password_manager.copy_password()
    password_manager.copy_password()
    check(len(api.clock.live("wipe_clipboard")) == 1,
          f"two copies left {len(api.clock.live('wipe_clipboard'))} timers standing")

    # Locking takes back a password that is still sitting there.
    password_manager.copy_password()
    password_manager.press_lock()
    check(api.clipboard == "", "locking left the copied password on the clipboard")

    print("\n6b. what is not a secret, and what is switched off")
    api.clock.shots.clear()
    password_manager.copy_user()
    check("open the vault" in api.said(),
          f"copying from a locked vault was not refused: {api.said()!r}")

    held = open_vault({"clipboard_clear_seconds": 0})
    password_manager.set_showing(True)
    seed(held, ("Mail", "post-123"))
    password_manager.pick("Mail")
    held.clipboard = ""
    held.clock.shots.clear()
    password_manager.copy_user()
    check(held.clipboard == "root",
          f"a user name did not reach the clipboard: {held.clipboard!r}")
    check(not held.clock.live("wipe_clipboard"),
          "a user name was armed for a wipe - it is not a secret")
    check("not a secret" in held.clip_note(),
          f"the tab does not say why it stays: {held.clip_note()!r}")

    held.clipboard = ""
    password_manager.copy_password()
    check(held.clipboard == "post-123", "a copied password did not reach the clipboard")
    check(not held.clock.live("wipe_clipboard"),
          "a timer was armed with the wipe switched off")
    check("never clear it" in held.clip_note(),
          f"0 seconds was not said as leaving it there: {held.clip_note()!r}")

    print("\n6c. the three ways a copy does not happen")
    broken = open_vault()
    password_manager.set_showing(True)
    seed(broken, ("Router", "s3cr3t-pi"))
    password_manager.pick("Router")
    broken.copy_works = False
    broken.clipboard = ""
    password_manager.copy_password()
    check("could not be reached" in broken.said(),
          f"a clipboard that refused was not said so: {broken.said()!r}")
    check(not broken.clock.live("wipe_clipboard"),
          "a timer was armed for a copy that never happened")

    empty = open_vault()
    password_manager.copy_password()
    check("pick an entry" in empty.said(),
          f"no entry picked was not said: {empty.said()!r}")
    check(not empty.called("ui.copy"), "something was copied anyway")

    shut = fresh(config=ROUNDED)
    password_manager.copy_password()
    check("open the vault" in shut.said(),
          f"a locked vault was not said as the reason: {shut.said()!r}")
    check(not shut.called("ui.copy"), "a locked vault put something on the clipboard")
    password_manager.shutdown()


# --- seven: the clock that shuts the vault ---

def test_the_clock() -> None:
    print("\n7. the idle lock")
    empty_folder()
    api = open_vault({"auto_lock_minutes": 5})
    check(bool(api.clock.live("tick")), "the vault started without its lock clock")
    check(not password_manager.lock_due(), "a freshly opened vault is already due")

    password_manager.last_touch = time.monotonic() - 6 * 60
    check(password_manager.lock_due(), "five minutes idle did not make it due")
    api.clock.fire("tick")
    check(not password_manager.is_open(), "the clock did not lock the vault")
    check("locked itself" in api.said(),
          f"an idle lock was not said: {api.said()!r}")
    look(api.said())

    # Nothing pressed since the lock, and the clock must not keep saying it.
    before = api.times("tabs.set_widget")
    api.clock.fire("tick")
    check(api.times("tabs.set_widget") == before,
          "a shut vault is being re-locked every tick")

    empty_folder()
    zero = open_vault({"auto_lock_minutes": 0})
    password_manager.last_touch = time.monotonic() - 86400
    check(not password_manager.lock_due(), "0 minutes still locks the vault")
    zero.clock.fire("tick")
    check(password_manager.is_open(),
          "the clock shut a vault whose own switch says never")
    password_manager.touch()
    check(password_manager.last_touch > time.monotonic() - 2,
          "**touch** did not reset the clock")
    password_manager.shutdown()


# --- eight: the tab, and the bullets ---

def test_the_tab(app: Any) -> None:
    print("\n8. the tab, built by Qt from the spec")
    import addon_api
    from addon_common import merge_config

    values = merge_config(password_manager.DEFAULTS, {})
    widget, registry = addon_api.build_spec(password_manager.tab_spec(values))
    wanted = {"lockstate", "count", "status", "search", "add", "list", "show",
              "clipnote", "name", "user", "pass", "site", "note", "stamp",
              "gen_len", "generate", "strength", "strengthtext", "save",
              "revert", "copy_pass", "copy_user", "delete", "unlock", "lock",
              "chpw", "backup", "restore", "gen_upper", "gen_digits",
              "gen_symbols", "gen_no_ambiguous"}
    missing = sorted(name for name in wanted if name not in registry)
    check(not missing, f"the spec leaves out {missing or 'nothing'}")
    check(widget is not None, "the tab built as nothing")

    table = registry["list"]

    def headings() -> list:
        """What the list calls its own columns - a tree widget has no one call."""
        head = table.headerItem()
        return [head.text(one) if head is not None else ""
                for one in range(table.columnCount())]

    check(table.columnCount() == len(password_manager.LIST_COLUMNS),
          f"the list has {table.columnCount()} column(s), not "
          f"{len(password_manager.LIST_COLUMNS)}")
    check(headings() == list(password_manager.LIST_COLUMNS),
          f"the list's headings are not the four words: {headings()}")
    check("Password" in headings(),
          "the list cannot show a password at all")

    for identifier in ("unlock", "lock", "chpw", "backup", "restore", "add",
                       "save", "revert", "copy_pass", "copy_user", "delete",
                       "generate", "show", "list", "search", "pass"):
        check(bool(registry[identifier].toolTip()),
              f"{identifier} has no tooltip to say what it does")

    check(isinstance(registry["note"], addon_api.QPlainTextEdit),
          "a note is not a box that takes more than one line")
    check(registry["gen_len"].minimum() <= 6 and registry["gen_len"].maximum() >= 64,
          "the length box cannot reach the generator's own limits")
    check(registry["strength"].maximum() == 100, "the bar is not a hundred steps")
    check(not registry["lock"].isEnabled(),
          "the Lock button is live on a tab that starts shut")
    check(not registry["copy_pass"].isEnabled(),
          "the buttons beside an empty form start live")
    check(registry["unlock"].isEnabled() and registry["backup"].isEnabled(),
          "the two things that can be done with no vault are disabled")

    def applies(identifier: str, prop: str, value: Any) -> str:
        try:
            addon_api.apply_widget_property(registry[identifier], prop, value)
        except Exception as error:              # noqa: BLE001 - the point of the check
            return f"{type(error).__name__}: {error}"
        return ""

    for identifier, prop, value in [
            ("status", "text", "unlocked"),
            ("lockstate", "style", "color: #23d18b; font-weight: 600;"),
            ("list", "rows", [["Router", "root", "••••••••", "192.168.1.1"]]),
            ("pass", "value", "s3cr3t-pi"),
            ("pass", "enabled", True),
            ("show", "checked", True),
            ("strength", "value", 80),
            ("strength", "format", ""),
            ("gen_len", "value", 24),
            ("count", "text", "2 entry(ies)")]:
        why = applies(identifier, prop, value)
        check(not why, f"{identifier} takes '{prop}'" + (f": {why}" if why else ""))
    check(registry["list"].topLevelItem(0).text(2) == "••••••••",
          "the bullets in a row are not what is shown")

    unreadable = [name for name in ("name", "user", "pass", "site", "note",
                                    "search", "gen_len", "show", "gen_upper")
                  if addon_api.read_widget_value(
                      registry[name],
                      "checked" if name in ("show", "gen_upper") else "value") is None]
    check(not unreadable, f"these controls give back nothing: {unreadable or 'none'}")

    print("\n8b. the loop the tab runs on: a row clicked, a tick flipped")
    empty_folder()
    api = open_vault()
    calls = len(api.calls)
    password_manager.on_widget("Some other tab", "unlock", "clicked", {})
    check(len(api.calls) == calls, "a control on another tab was answered")

    password_manager.set_showing(True)
    seed(api, ("Router", "s3cr3t-pi"), ("GitHub", "gh-token-9"))
    check(bool(api.last_set("pass", "enabled")),
          "the password box cannot be typed in while passwords are shown")
    password_manager.set_showing(False)
    rows = api.last_set("list", "rows") or []
    check(all(row[2] == password_manager.mask_text() for row in rows),
          f"hidden passwords are readable in the list: {rows}")
    check(str(api.form.get("pass")) == password_manager.mask_text(),
          "the box does not hold bullets while hidden")
    check(not api.last_set("pass", "enabled"),
          "the password box can be typed in while it holds bullets")

    password_manager.on_widget(password_manager.TAB, "list", "selected",
                               {"text": "GitHub", "count": 1})
    check(str((password_manager.current_entry() or {}).get("password"))
          == "gh-token-9", "clicking a row did not put that entry in the form")

    api.choice = "Copy the user name"
    password_manager.on_widget(password_manager.TAB, "list", "context",
                               {"text": "Router"})
    check(api.called("ui.choose"), "a right-click on a row opened no menu")
    check(api.clipboard == "root",
          f"the menu's copy did not run: {api.clipboard!r}")

    # The filter, and the tick box coming back as an event rather than a call.
    password_manager.on_widget(password_manager.TAB, "search", "changed",
                               {"text": "git"})
    check(len(password_manager.row_order) == 1,
          f"the filter left {len(password_manager.row_order)} row(s) standing")
    password_manager.on_widget(password_manager.TAB, "show", "toggled",
                               {"checked": True})
    check(password_manager.showing(), "**Show passwords** as an event did not take")
    rows = api.last_set("list", "rows") or []
    check(rows and rows[0][2] == "gh-token-9",
          f"a filtered list shows no real password: {rows}")
    password_manager.shutdown()


# --- nine: the mark ---

class _ThreeFolders:
    """A stand-in for ``api.paths`` that knows the three places a picture may lie."""

    def __init__(self, addon: Path, assets: Path, data: Path) -> None:
        self._addon = addon
        self._assets = assets
        self._data = data

    @property
    def addon(self) -> Path:
        return self._addon

    @property
    def assets(self) -> Path:
        return self._assets

    @property
    def data(self) -> Path:
        return self._data


def _check_the_lookup() -> None:
    """That the one name is tried in every folder a picture may live in."""
    import addon_common

    work = Path(tempfile.mkdtemp(prefix="pass_icons_"))
    try:
        own = work / "addon" / "assets" / "password_manager"
        program = work / "assets"
        data = work / "_data" / "password_manager"
        own.mkdir(parents=True)
        program.mkdir(parents=True)
        data.mkdir(parents=True)
        name = password_manager.TAB_ICON_FILE
        (own / name).write_bytes(b"own")
        (program / name).write_bytes(b"program")
        (data / name).write_bytes(b"data")
        paths = _ThreeFolders(work / "addon", program, data)
        check(addon_common.find_addon_picture(paths, "password_manager")
              == str(own / name),
              "the addon's own assets folder does not win over the program's")
        (own / name).unlink()
        check(addon_common.find_addon_picture(paths, "password_manager")
              == str(program / name),
              "nothing is worn from the program's assets when the addon has none")
        (program / name).unlink()
        check(addon_common.find_addon_picture(paths, "password_manager")
              == str(data / name),
              "an addon installed as a program does not find the picture kept "
              "beside its settings")
        (data / name).unlink()
        check(addon_common.find_addon_picture(paths, "password_manager") == "",
              "an addon with no picture anywhere was handed a path anyway")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_the_mark(app: Any) -> None:
    """The key: what the addon sends, and where the picture it sends from is."""
    print("\n9. the tab's mark")
    api = fresh(config=ROUNDED)
    check(api.called("tabs.set_icon"), "the addon gave its tab no icon")
    sent = str(api.asked("tabs.set_icon", "icon"))
    check(sent == password_manager.tab_icon(),
          "what the addon sent is not the mark its own tab_icon() settles on")
    drawn = sent.startswith("<svg") and sent.rstrip().endswith("</svg>")
    check(drawn or sent.lower().endswith(".png"),
          "the mark is neither a self-contained SVG document nor a picture file, "
          f"so a compiled copy of this addon could not send it: {sent[:40]!r}")
    if drawn:
        check("#161616" not in sent and "#212121" not in sent,
              "the key is pasted onto a background square instead of being drawn")
    check(password_manager.TAB_ICON_FILE == "password_managericon.png",
          f"the tab's mark follows {{addon}}icon.png: "
          f"{password_manager.TAB_ICON_FILE!r}")
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / password_manager.TAB_ICON_FILE,
                  ROOT / "addon.store" / "assets" / "password_manager"
                  / password_manager.TAB_ICON_FILE]
    shipped = next((one for one in candidates if one.is_file()), candidates[-1])
    check(shipped.is_file(), f"{shipped.name} is in the store's assets folder")
    _check_the_lookup()
    if app is None:
        look("no Qt here, so the drawing itself is not checked")
        return
    import addon_api
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QColor, QPixmap

    icon = addon_api.build_icon(password_manager.TAB_ICON)
    check(icon is not None and not icon.isNull(),
          "the app cannot draw what the addon sent")
    if icon is not None:
        sizes = icon.availableSizes() or [QSize(64, 64)]
        check(max(size.width() for size in sizes) >= 32,
              f"the mark was not rendered at a usable size: {sizes}")
        image = icon.pixmap(sizes[-1]).toImage()
        cover = amber = 0
        for x in range(image.width()):
            for y in range(image.height()):
                colour = image.pixelColor(x, y)
                if colour.alpha() < 40:
                    continue                 # nothing was drawn in this pixel
                cover += 1
                if (colour.red() > 180 and colour.green() > 100
                        and colour.blue() < 90):
                    amber += 1
        total = image.width() * image.height()
        check(cover > total * 0.1, f"the mark drew almost nothing: {cover} of "
                                   f"{total} pixel(s) covered")
        check(amber > cover * 0.4,
              f"hardly any of what was drawn is the key: {amber} amber pixel(s) "
              f"of {cover} drawn")
        look(f"{sizes[-1].width()}px mark, {amber} of {cover} drawn pixel(s) amber")
    password_manager.shutdown()


# --- run it ---

def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live; and a Windows console often speaks
        # cp1252, which has no room for the bullets this tab paints (•), so
        # anything undisplayable becomes "?" rather than stopping the run.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    global password_manager, DATA
    if not SOURCE.is_file():
        print(f"there is no addon at {SOURCE}")
        return 2
    password_manager = addon_client.load_addon(SOURCE)

    # Every vault, backup and half-written file this test makes goes in a folder
    # of its own, which is deleted at the end.
    DATA = Path(tempfile.mkdtemp(prefix="password_manager_"))
    os.environ["RASCONF_ADDON_DATA"] = str(DATA)
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
    except Exception as error:                      # noqa: BLE001 - Qt is optional
        look(f"no Qt here, so the spec itself is not built: {error}")

    print(f"password_manager: the addon at {SOURCE.name}")
    try:
        test_the_manifest()
        test_the_seal()
        test_the_header()
        test_the_maker()
        test_an_entry()
        test_a_session()
        test_the_clipboard()
        test_the_clock()
        if app is not None:
            test_the_tab(app)
        else:
            print("\n8-8b. skipped: no Qt on this machine")
        test_the_mark(app)
    finally:
        os.environ.pop("RASCONF_ADDON_DATA", None)
        shutil.rmtree(DATA, ignore_errors=True)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
