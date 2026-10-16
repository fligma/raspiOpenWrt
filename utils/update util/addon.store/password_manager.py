#!/usr/bin/env python3
"""Password manager: one encrypted vault on this machine, and a tab to work it.

Adds a **Passwords** tab. Every entry - a name, the user it belongs to, its
password, the site or service it is for and a note - lives in one file,
``addons/_data/password_manager/vault.json``, on the computer Berry Manager runs
on. Nothing about it is ever sent anywhere: the addon opens no connection and
needs no device, which is why its manifest says ``"requires": []`` and
``"connection": "none"``.

The file holds no readable password. The entries are written as JSON, that JSON
is masked by a keystream and sealed with a tag: a PBKDF2-HMAC-SHA256 key derived
from the master password and a random salt, an HMAC-SHA256 counter keystream
built from half of that key, and an encrypt-then-MAC tag over the header, the
salt, the nonce and the ciphertext built from the other half. Only the salt, the
nonce, the ciphertext and the tag are put on disk. This is built out of
``hashlib``, ``hmac`` and ``secrets`` for one specific reason: an addon the store
compiles carries the standard library, PyQt6, ``paramiko`` and ``keyring`` and
nothing else, so a vault that needed ``cryptography`` would be unreadable in a
build that never bundled it. The format field in the file names the scheme it was
written with, so a later version can move the scheme without leaving anybody's
old vault unreadable. A file that was tampered with fails its tag, and so does a
wrong master password: that is the only tell, and it is enough, because both
answers are "nothing was read".

The master password is asked for once and held for the session, in a module
variable, and **Lock** puts what it was holding back to nothing. While the vault
is locked the list shows no entries at all - the bytes on disk are the only thing
the tab has to work with. What the tab does with a secret once it is open:

* a password reads as bullets until **Show passwords** is ticked, and the
  password box in the form is only typeable while it is shown, so nobody edits
  an entry blind;
* **Copy password** puts the password on the clipboard with ``api.ui.copy`` and
  starts a timer for **Clipboard clears after** seconds. When it fires the
  clipboard is wiped only if it still holds exactly what the addon put there, so
  whatever was copied since is left alone;
* after **Lock after** minutes with nothing pressed on this tab, the vault locks
  itself and says so in the status line.

The tab is a declarative spec and every control reports back through
``on_widget``, so the same file works imported into the app and compiled into a
program of its own by the Addon Dev tab.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import string
import time
from pathlib import Path
from typing import Any

# Qt-free half: runs compiled alone and in a windowless test.
from addon_common import AddonError, config_get, find_addon_picture, merge_config

ADDON = {
    "name": "Password manager",
    "version": "1.0",
    "author": "fligma",
    "description": "Keeps logins in one encrypted file on this computer - no device, "
                   "no cloud - unlocked for the session with a master password, with "
                   "a searchable list, bullets until you ask to see them, a generator, "
                   "and a copied password taken back off the clipboard after the "
                   "seconds you configured.",
    # Nothing here reaches a device: the vault is a file on this machine.
    "requires": [],
    "connection": "none",
    "api": 1,
}

TAB = "Passwords"

#: How often the auto-lock clock is looked at, in seconds. A quarter of a minute
#: is often enough to lock within a minute of the switch saying, and rare enough
#: to be nothing in the log.
LOCK_TICK_SECONDS = 15

api = None                  # filled in by init()

# --- what can be configured, and its defaults ---

DEFAULTS = {
    "vault_name": "vault.json",
    "mask": "••••••••",
    # 0 means "leave it there", which is what somebody who finds the wipe
    # surprising will switch it to; it is not a way of saying "no timer".
    "clipboard_clear_seconds": 20,
    "auto_lock_minutes": 5,
    "notify_on_copy": True,
    "show_by_default": False,
    "gen_length": 20,
    "gen_upper": True,
    "gen_digits": True,
    "gen_symbols": True,
    "gen_no_ambiguous": True,
    "kdf_iterations": 120000,
    "backups_keep": 5,
}

FIELDS = [
    {"key": "vault_name", "label": "Vault file", "kind": "text",
     "hint": "Kept in addons/_data/password_manager/. It holds ciphertext only, "
             "so its name is nothing to be careful about",
     "placeholder": "vault.json"},
    {"key": "mask", "label": "Bullets shown instead of a password", "kind": "text",
     "hint": "What the list and the password box hold while passwords are hidden"},
    {"key": "clipboard_clear_seconds", "label": "Clipboard clears after (seconds)",
     "kind": "number", "minimum": 0, "maximum": 3600,
     "hint": "A copied password is wiped from the clipboard after this many "
             "seconds, and only if nothing else has been copied since. 0 leaves "
             "it on the clipboard"},
    {"key": "auto_lock_minutes", "label": "Lock after (minutes idle)",
     "kind": "number", "minimum": 0, "maximum": 240,
     "hint": "Idle on this tab means no press, no click in the list and no typed "
             "character. 0 never locks the vault by itself"},
    {"key": "notify_on_copy", "label": "Say where the copied text went", "kind": "bool",
     "hint": "One line in the status line and a second in the clipboard box, so a "
             "copy is never silently wiped later"},
    {"key": "show_by_default", "label": "Start with passwords shown", "kind": "bool",
     "hint": "Off means the list and the form hold bullets until the box is ticked"},
    {"key": "gen_length", "label": "Generated password length", "kind": "number",
     "minimum": 6, "maximum": 64},
    {"key": "gen_upper", "label": "Generated: upper case", "kind": "bool"},
    {"key": "gen_digits", "label": "Generated: digits", "kind": "bool"},
    {"key": "gen_symbols", "label": "Generated: symbols", "kind": "bool"},
    {"key": "gen_no_ambiguous", "label": "Generated: skip confusing characters",
     "kind": "bool",
     "hint": "Leaves out l, I, 1, O, 0 and the like, for a password that has to be "
             "typed somewhere by hand"},
    {"key": "kdf_iterations", "label": "Key stretching rounds", "kind": "number",
     "minimum": 20000, "maximum": 2000000,
     "hint": "How hard guessing the master password is per try. It is written into "
             "the vault when the vault is written, so changing it here changes the "
             "next save and not the file already on disk"},
    {"key": "backups_keep", "label": "Backups to keep", "kind": "number",
     "minimum": 0, "maximum": 50,
     "hint": "In addons/_data/password_manager/backups, newest first. 0 writes none"},
]

FIELDS_BY_KEY = {str(field["key"]): field for field in FIELDS}

#: The entries, in the order the form lists them.
ENTRY_KEYS = ("name", "user", "password", "site", "note")

#: What the tab is made of, by spec id. Filled in below, once the functions exist.
HANDLERS: dict = {}

# --- the vault's shape on disk ---

VAULT_FORMAT = "berry-vault"
VAULT_VERSION = 1
CIPHER = "ctr-hmac-sha256"
KDF = "pbkdf2-hmac-sha256"

#: PBKDF2 gives out 64 bytes: 32 for the keystream and 32 for the tag. They are
#: separate keys on purpose - a stream cipher and its own MAC must not share one.
KEY_BYTES = 64
SALT_BYTES = 16
NONCE_BYTES = 16
BLOCK_BYTES = 32               # one HMAC-SHA256 answer, and one keystream block

_MIN_MASTER = 6

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# --- small helpers ---

def widget_id(key: str) -> str:
    """A spec id for a config key: dots are not allowed in one."""
    return str(key).replace(".", "_")


def one_line(value: Any) -> str:
    """One line of display text, with the control characters that break a table gone."""
    text = str("" if value is None else value)
    return " ".join(text.splitlines()[:1]).strip()


def clean_text(value: Any) -> str:
    """An entry field, kept exactly as typed apart from the line breaks in it.

    A password is kept character for character, spaces included - a site that
    was given a leading space wants that space back. Only the newline a stray
    Enter leaves behind is dropped, because these fields are one line each.
    """
    text = str("" if value is None else value)
    return text.replace("\r", "").replace("\n", " ")


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def mask_text() -> str:
    """The bullets the list and the password box hold while nothing is shown."""
    text = one_line(cfg("mask", DEFAULTS["mask"])) or DEFAULTS["mask"]
    return text


def cfg(key: str, fallback: Any = None) -> Any:
    """One config value, read safely enough to be called before ``init`` finishes."""
    if api is None:
        return config_get(DEFAULTS, key, fallback)
    try:
        return api.config.get(key, config_get(DEFAULTS, key, fallback))
    except AddonError:
        return config_get(DEFAULTS, key, fallback)


def number(key: str, fallback: int) -> int:
    """A config number, held inside the range the form itself would allow."""
    field = FIELDS_BY_KEY.get(key) or {}
    value = as_int(cfg(key, fallback), fallback)
    low, high = as_int(field.get("minimum"), value), as_int(field.get("maximum"), value)
    return max(low, min(high, value))


# --- the key, the keystream and the tag ---

def _b64(raw: bytes) -> str:
    return base64.b64encode(bytes(raw)).decode("ascii")


def _unb64(text: Any) -> bytes:
    """Base64 back to bytes; anything that is not base64 is not a vault."""
    try:
        return base64.b64decode(str(text or ""), validate=True)
    except (ValueError, TypeError) as error:
        raise AddonError(f"the vault file is not the shape it claims to be ({error})")


def derive_key(master: str, salt: bytes, iterations: int) -> bytes:
    """``KEY_BYTES`` of key material out of the master password and the salt.

    PBKDF2-HMAC-SHA256 is in ``hashlib``, and stretching is the whole point: at a
    hundred thousand rounds a guess costs about a tenth of a second, which turns
    a dictionary of a million guesses from an afternoon into a week.
    """
    if not salt:
        raise AddonError("the vault holds no salt to work from")
    rounds = max(1000, as_int(iterations, DEFAULTS["kdf_iterations"]))
    return hashlib.pbkdf2_hmac("sha256", str(master or "").encode("utf-8"),
                               bytes(salt), rounds, KEY_BYTES)


def keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """``length`` bytes of mask, from HMAC-SHA256 over the nonce and a counter.

    A block is ``HMAC(enc_key, nonce || counter)``, and the counters run 0, 1, 2
    ... The nonce is what makes two saves of the same password come out as two
    different files, and the counter is what makes the two saves' own blocks
    differ - a keystream that repeated inside one message would hand back the
    difference between two plaintexts for nothing.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"),
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, mask: bytes) -> bytes:
    return bytes(one ^ other for one, other in zip(data, mask))


def _context(iterations: int, salt: bytes, nonce: bytes) -> bytes:
    """The bytes the tag is calculated over besides the ciphertext.

    The header goes in as well as the body, because a file whose ``iterations``
    or ``cipher`` had been quietly rewritten must not be decrypted at the
    attacker's chosen strength either.
    """
    head = (f"{VAULT_FORMAT}|{VAULT_VERSION}|{CIPHER}|{KDF}|"
            f"{as_int(iterations)}|").encode("utf-8")
    return head + bytes(salt) + bytes(nonce)


def seal(plain: bytes, master: str, iterations: int,
         salt: bytes | None = None, nonce: bytes | None = None) -> dict:
    """The header-and-ciphertext parts of a vault, out of ``plain``.

    ``salt`` and ``nonce`` are only for a test that has to be able to say what
    the bytes will be; nothing that runs from the tab passes them.
    """
    salt = bytes(salt or secrets.token_bytes(SALT_BYTES))
    nonce = bytes(nonce or secrets.token_bytes(NONCE_BYTES))
    material = derive_key(master, salt, iterations)
    masked = _xor(bytes(plain), keystream(material[:BLOCK_BYTES], nonce, len(plain)))
    tag = hmac.new(material[BLOCK_BYTES:],
                   _context(iterations, salt, nonce) + masked, hashlib.sha256).digest()
    return {"format": VAULT_FORMAT, "version": VAULT_VERSION, "cipher": CIPHER,
            "kdf": KDF, "iterations": as_int(iterations), "salt": _b64(salt),
            "nonce": _b64(nonce), "tag": _b64(tag), "payload": _b64(masked),
            "written": time.strftime("%Y-%m-%dT%H:%M:%S")}


def open_sealed(header: dict, cipher: bytes, tag: bytes, salt: bytes, nonce: bytes,
                master: str) -> bytes:
    """The plaintext behind a sealed header, or an ``AddonError`` saying it will not open."""
    material = derive_key(master, salt, header.get("iterations"))
    asked = hmac.new(material[BLOCK_BYTES:],
                     _context(header.get("iterations"), salt, nonce) + cipher,
                     hashlib.sha256).digest()
    if not hmac.compare_digest(asked, tag):
        raise AddonError("that master password does not open this vault")
    return _xor(cipher, keystream(material[:BLOCK_BYTES], nonce, len(cipher)))


def encrypt_vault(master: str, entries: list, iterations: int | None = None) -> str:
    """The whole vault file as text: one JSON header, and the entries inside it sealed."""
    body = json.dumps({"entries": [clean_entry(entry) for entry in entries or []],
                       "written": time.strftime("%Y-%m-%dT%H:%M:%S")},
                      indent=2, ensure_ascii=False)
    header = seal(body.encode("utf-8"), master,
                  as_int(iterations, number("kdf_iterations",
                                            DEFAULTS["kdf_iterations"])))
    return json.dumps(header, indent=2, ensure_ascii=False) + "\n"


def vault_header(text: str) -> dict:
    """The JSON header of a vault file, checked for being one, without any password.

    This is what lets the tab say "there is a vault here, and it was stretched
    with N rounds" while it is locked, and refuse a file that is plainly not one
    before a master password has been spent on it.
    """
    try:
        data = json.loads(str(text or ""))
    except ValueError as error:
        raise AddonError(f"the vault file does not read as JSON ({error})")
    if not isinstance(data, dict):
        raise AddonError("the vault file is not a vault")
    if str(data.get("format") or "") != VAULT_FORMAT:
        raise AddonError("the vault file was not written by this scheme "
                         f"(it says {str(data.get('format') or 'nothing')!r})")
    if str(data.get("cipher") or "") != CIPHER or str(data.get("kdf") or "") != KDF:
        raise AddonError("the vault file names a cipher or a key stretching this "
                         "addon does not speak")
    return data


def decrypt_vault(text: str, master: str) -> list:
    """The entries inside a vault file, or an ``AddonError`` saying why not.

    One tampered byte anywhere - in the salt, the header, the ciphertext - fails
    the tag, so a broken file is never mistaken for an empty vault.
    """
    header = vault_header(text)
    salt = _unb64(header.get("salt"))
    nonce = _unb64(header.get("nonce"))
    cipher = _unb64(header.get("payload"))
    tag = _unb64(header.get("tag"))
    plain = open_sealed(header, cipher, tag, salt, nonce, master)
    try:
        body = json.loads(plain.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise AddonError(f"the vault opened into nothing readable ({error})")
    entries = body.get("entries") if isinstance(body, dict) else body
    if not isinstance(entries, list):
        raise AddonError("the vault holds no list of entries")
    return [clean_entry(entry) for entry in entries]


def master_opens(text: str, master: str) -> bool:
    """Whether ``master`` is the one for the vault in ``text``; the way to ask without opening it."""
    try:
        decrypt_vault(text, master)
    except AddonError:
        return False
    return True


# --- one entry ---

def clean_entry(raw: Any) -> dict:
    """One entry as the vault keeps it: five lines of text, an id and a stamp."""
    source = raw if isinstance(raw, dict) else {}
    out = {"uid": one_line(source.get("uid")) or secrets.token_hex(8)}
    for key in ENTRY_KEYS:
        # Everything but the password is trimmed: " Mail " and "Mail" are one
        # entry, and a list that shows both looks like a bug even when the two
        # names sort together. A password is never trimmed - see ``clean_text``.
        text = clean_text(source.get(key))
        out[key] = text if key == "password" else text.strip()
    if not out["name"]:
        out["name"] = "(no name)"
    out["changed"] = one_line(source.get("changed")) or time.strftime(
        "%Y-%m-%d %H:%M")
    return out


def entry_matches(entry: dict, needle: str) -> bool:
    """Whether a search word is in an entry's name, user, site or note.

    The password is deliberately not searched: a box that matched on it would put
    a secret in a filter, and the point of the list is to find the entry you
    remember the *name* of.
    """
    if not needle:
        return True
    word = needle.lower()
    return any(word in str(entry.get(key) or "").lower()
               for key in ("name", "user", "site", "note"))


def sort_entries(entries: list) -> list:
    """By name, case ignored, so ``amazon`` sits next to ``Amazon`` rather than at the end."""
    return sorted(entries or [], key=lambda entry: str(entry.get("name") or "").lower())


def label_map(entries: list) -> tuple[list[dict], dict[str, str]]:
    """The entries in list order, and the map from what row one shows to its id.

    Two entries can genuinely have the same name, and the list has to be able to
    answer a click on either of them, so a repeated name wears a count and the
    label is the only key the app can hand back.
    """
    labels: dict[str, str] = {}
    seen: dict[str, int] = {}
    out: list[dict] = []
    for entry in entries or []:
        base = str(entry.get("name") or "(no name)")
        count = seen.get(base.lower(), 0) + 1
        seen[base.lower()] = count
        label = base if count == 1 else f"{base} ({count})"
        labels[label] = str(entry.get("uid") or "")
        out.append(entry)
    return out, labels


# --- a made-up password, and how good it is ---

SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"

#: The shapes that get mixed up when a password has to be read out loud or typed
#: from a screen onto a phone.
AMBIGUOUS = set("Il1lJjO0o|`'\";,.:i")


def alphabet_of(values: dict) -> str:
    """The characters the generator may use, from the four switches."""
    pool = string.ascii_lowercase
    if bool(config_get(values, "gen_upper", DEFAULTS["gen_upper"])):
        pool += string.ascii_uppercase
    if bool(config_get(values, "gen_digits", DEFAULTS["gen_digits"])):
        pool += string.digits
    if bool(config_get(values, "gen_symbols", DEFAULTS["gen_symbols"])):
        pool += SYMBOLS
    if bool(config_get(values, "gen_no_ambiguous", DEFAULTS["gen_no_ambiguous"])):
        pool = "".join(character for character in pool if character not in AMBIGUOUS)
    return pool


def make_password(length: int, pool: str) -> str:
    """``length`` draws from ``pool``, taken with ``secrets`` rather than ``random``.

    ``random`` is seeded from the clock and is meant for shuffling a deck, not
    for picking a password; ``secrets`` is the operating system's own randomness.
    A pool nobody switched on is empty, and an empty pool makes no password, so
    the lowercase letters are used in that case rather than an empty string that
    would read as a password of nothing.
    """
    pool = str(pool or "") or string.ascii_lowercase
    wanted = max(6, min(64, as_int(length, DEFAULTS["gen_length"])))
    return "".join(secrets.choice(pool) for _ in range(wanted))


def pool_size(text: str) -> int:
    """How many characters ``text`` shows a knowledge of, as the guesser would count them."""
    body = str(text or "")
    if not body:
        return 0
    size = 0
    if any(character in string.ascii_lowercase for character in body):
        size += 26
    if any(character in string.ascii_uppercase for character in body):
        size += 26
    if any(character.isdigit() for character in body):
        size += 10
    if any(character in SYMBOLS for character in body):
        size += len(SYMBOLS)
    if any(not (character.isalnum() or character in SYMBOLS)
           for character in body):
        # A space, a tab or a letter no keyboard class covers: give it a hundred,
        # which is the order of how much more a guesser has to think about.
        size += 100
    return size or 26


def password_strength(text: str) -> tuple[int, str]:
    """The bar's percent and the word beside it, from length and character classes.

    ``len * log2(pool)`` is the guesses a full sweep of that space is, in bits,
    and the word chosen is what that many bits is worth against an attacker who
    can spend a lot: under 40 bits is a phone PIN, 60 is a database that leaked,
    90 is nothing anybody is going to sweep. It is an estimate of a *random*
    password's size and nothing more - ``correct horse battery staple`` scores
    badly here and is a better password than most that score well - which is why
    the box says "estimate" rather than promising anything.
    """
    body = str(text or "")
    if not body:
        return 0, "nothing typed"
    bits = len(body) * math.log2(pool_size(body))
    percent = max(1, min(100, int(round(bits / 90.0 * 100.0))))
    if bits < 40:
        word = "easy to guess"
    elif bits < 55:
        word = "short"
    elif bits < 70:
        word = "fair"
    elif bits < 90:
        word = "strong"
    else:
        word = "very strong"
    if len(body) < 8:
        word = "too short"
    return percent, word


# --- the vault on disk ---

def safe_name(value: Any, fallback: str) -> str:
    """A file name somebody wrote in a config box, made safe for the folder it goes in."""
    text = one_line(value) or str(fallback)
    return _UNSAFE_NAME.sub("_", text).strip(".") or str(fallback)


def vault_path() -> Path:
    """The one file this addon keeps: ciphertext, salt, nonce and tag."""
    name = safe_name(cfg("vault_name", DEFAULTS["vault_name"]),
                     DEFAULTS["vault_name"])
    return Path(api.paths.data) / name


def has_vault() -> bool:
    """Whether there is a vault to open at all. Nothing is read to find out."""
    if api is None:
        return False
    try:
        return vault_path().is_file()
    except (OSError, AddonError):
        return False


def vault_text() -> str:
    """The vault file as text; an ``AddonError`` when it is not there or not readable."""
    try:
        return vault_path().read_text(encoding="utf-8")
    except OSError as error:
        raise AddonError(f"the vault file could not be read ({error})")


def backups_dir() -> Path:
    """Where copies of the vault file go - encrypted, so a backup is not a leak."""
    folder = Path(api.paths.data) / "backups"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def trim_backups() -> None:
    """Keep the newest ``backups_keep`` copies, and no more. 0 writes none, keeps none."""
    keep = number("backups_keep", DEFAULTS["backups_keep"])
    if keep <= 0:
        return
    try:
        files = sorted((item for item in backups_dir().iterdir()
                        if item.is_file() and item.name.endswith(".json")),
                       key=lambda item: item.name, reverse=True)
    except OSError:
        return
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def backup_vault(reason: str = "") -> str:
    """A timestamped copy of the vault file, or ``""`` when there was nothing to copy."""
    if not has_vault():
        return ""
    keep = number("backups_keep", DEFAULTS["backups_keep"])
    if keep <= 0:
        say("backups are switched off (Backups to keep is 0)", bad=True)
        return ""
    try:
        text = vault_text()
        where = vault_path()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tail = f"-{reason}" if reason else ""
        target = backups_dir() / f"{where.stem}{tail}-{stamp}{where.suffix or '.json'}"
        target.write_text(text, encoding="utf-8")
    except (OSError, AddonError) as error:
        say(f"the copy could not be written: {error}", bad=True)
        return ""
    trim_backups()
    return str(target)


def write_vault() -> bool:
    """Seal what is in memory and put it where the vault lives."""
    if api is None or not session.get("open"):
        return False
    try:
        text = encrypt_vault(session["master"], session["entries"],
                             number("kdf_iterations", DEFAULTS["kdf_iterations"]))
        vault_path().write_text(text, encoding="utf-8")
    except (OSError, AddonError) as error:
        say(f"nothing was written: {error}", bad=True)
        api.log(f"could not write the vault: {error}")
        return False
    return True


# --- what the tab holds between presses ---

#: The session: the master password for as long as the vault is open, the entries
#: it unlocked to, and whether it is open at all. ``lock`` empties the first two.
session: dict = {"master": "", "entries": [], "open": False}

#: The label each row of the list wears, to the entry behind it. The app can only
#: hand back what a row shows, so this is how a click becomes an entry.
row_order: dict = {}

search_word = ""            # what is in the filter box
current_uid = ""            # the entry the form is showing
showing_flag = False        # passwords in the clear, per the tick box
clip_text = ""              # what this addon last put on the clipboard
clip_timer = None           # the timer that takes it back off
last_touch = time.monotonic()   # when the tab was last used, for the auto-lock

TAB_STYLE = """
QWidget { background-color: #161616; }
QGroupBox { border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 12px;
            padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 6px; color: #c51a4a; }
QLineEdit, QPlainTextEdit { border: 1px solid #3c3c3c; border-radius: 4px;
                            padding: 3px 6px; }
QPushButton { border: 1px solid #3c3c3c; border-radius: 4px; padding: 4px 10px; }
QPushButton:disabled { color: #6a6a6a; border-color: #2a2a2a; }
"""

#: The password box, and the bar's guess-work meter, both want a monospace line:
#: ``Il1`` is exactly what the box is for showing somebody.
MONO_STYLE = "font-family: Consolas, monospace;"

#: The three colours the state chip at the top of the tab wears.
CHIP_STYLE = "color: {}; font-weight: 600; padding: 2px 6px;"
CHIP_OPEN = "#23d18b"
CHIP_SHUT = "#ff6b6b"
CHIP_NONE = "#ffb020"

#: What the right-click menu on a row offers, in the order it is offered, and
#: which action each line maps to. ``api.ui.choose`` answers with the words.
MENU_ACTIONS = (
    ("Copy the password", "copy_pass"),
    ("Copy the user name", "copy_user"),
    ("Copy the site", "copy_site"),
    ("Put it back in the form", "revert"),
    ("Delete this entry", "delete"),
)


# --- small tab helpers ---

def say(text: str, bad: bool = False) -> None:
    """The status line under the buttons: one sentence about where the addon stands."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "status", "text", str(text))
        api.tabs.set_widget(TAB, "status", "style",
                            "color: #ffb020; padding: 2px 4px;" if bad
                            else "color: #23d18b; padding: 2px 4px;")
    except AddonError:
        pass


def note(text: str) -> None:
    """The clipboard line, beside **Show passwords**: what was copied and when it goes."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, "clipnote", "text", str(text))
    except AddonError:
        pass


def set_value(identifier: str, prop: str, value: Any) -> None:
    """One control on the tab, written through the app."""
    if api is None:
        return
    try:
        api.tabs.set_widget(TAB, identifier, prop, value)
    except AddonError:
        pass


def read_value(identifier: str, what: str = "value", fallback: Any = "") -> Any:
    """One control on the tab, read back; ``fallback`` when it is not there."""
    if api is None:
        return fallback
    try:
        answer = api.tabs.widget_value(TAB, identifier, what)
    except AddonError:
        return fallback
    return fallback if answer is None else answer


def touch() -> None:
    """Say that the tab was used, which is what the auto-lock clock measures."""
    global last_touch
    last_touch = time.monotonic()


def is_open() -> bool:
    return bool(session.get("open"))


def showing() -> bool:
    return bool(showing_flag)


def entries() -> list:
    return list(session.get("entries") or [])


def find_entry(uid: str) -> dict | None:
    """The entry with this id, or ``None`` for the empty form."""
    for entry in session.get("entries") or []:
        if str(entry.get("uid") or "") == str(uid or ""):
            return entry
    return None


def current_entry() -> dict | None:
    return find_entry(current_uid) if current_uid else None


def _stop_timer(handle: Any) -> None:
    """Stop a scheduled job, whichever kind of timer this runtime handed back.

    An addon imported into the app gets a ``QTimer``, which answers to ``stop``;
    a compiled one gets ``addon_client``'s own handle, which answers to
    ``cancel``. A clipboard wipe has to be takable back either way, so neither
    name is assumed here.
    """
    if handle is None:
        return
    for name in ("cancel", "stop"):
        method = getattr(handle, name, None)
        if callable(method):
            try:
                method()
            except (RuntimeError, AddonError):
                pass
            return


# --- the tab, as a spec ---

def button(identifier: str, text: str, tip: str, enabled: bool = True) -> dict:
    """One button, reporting the only event a button has."""
    return {"kind": "button", "id": identifier, "text": text, "tooltip": tip,
            "enabled": enabled, "on": ["clicked"]}


def entry_group() -> dict:
    """The five fields of one entry, and the stamp of when it last changed."""
    return {"kind": "group", "text": "The entry", "layout": "form", "children": [
        {"kind": "line", "id": "name", "label": "Name",
         "placeholder": "Router admin, VPN, GitHub...",
         "tooltip": "What you call this login. The list is sorted by it"},
        {"kind": "line", "id": "user", "label": "User",
         "placeholder": "root", "tooltip": "The account the password belongs to"},
        {"kind": "line", "id": "pass", "label": "Password", "style": MONO_STYLE,
         "tooltip": "Bullets until **Show passwords** is ticked - the box can only "
                    "be typed in while it is showing, so nobody edits a password "
                    "they cannot see. **Copy password** works either way",
         "on": ["changed"]},
        {"kind": "line", "id": "site", "label": "Site / service",
         "placeholder": "192.168.1.1, or https://example.org",
         "tooltip": "Where this login is used"},
        {"kind": "multi", "id": "note", "label": "Note",
         "placeholder": "Recovery e-mail, the PIN, when it was changed...",
         "tooltip": "Anything worth remembering about this entry. It is encrypted "
                    "with the rest, so it is not the place for the password itself"},
        {"kind": "label", "id": "stamp", "label": "Changed", "text": "",
         "style": "color: #aaaaaa;"},
    ]}


def maker_group(values: dict) -> dict:
    """The generator and the meter, with the four switches that decide its alphabet."""
    return {"kind": "group", "text": "Make one up", "layout": "form", "children": [
        {"kind": "row", "label": "Length", "children": [
            {"kind": "spin", "id": "gen_len", "minimum": 6, "maximum": 64,
             "value": as_int(config_get(values, "gen_length",
                                        DEFAULTS["gen_length"]), 20),
             "tooltip": "How many characters the generator draws"},
            button("generate", "Generate",
                   "Draw a password from the switches below and put it in the "
                   "Password box. Ticking Show is what lets you see it, so this "
                   "does that too"),
            {"kind": "spacer"},
        ]},
        {"kind": "check", "id": "gen_upper", "text": "Upper case (A-Z)",
         "checked": bool(config_get(values, "gen_upper", True))},
        {"kind": "check", "id": "gen_digits", "text": "Digits (0-9)",
         "checked": bool(config_get(values, "gen_digits", True))},
        {"kind": "check", "id": "gen_symbols", "text": f"Symbols ({SYMBOLS})",
         "checked": bool(config_get(values, "gen_symbols", True))},
        {"kind": "check", "id": "gen_no_ambiguous",
         "text": "Skip the confusing ones (l I 1 O 0)",
         "checked": bool(config_get(values, "gen_no_ambiguous", True)),
         "tooltip": "For a password that has to be read out loud or typed from a "
                    "screen onto a phone"},
        {"kind": "row", "label": "Guess work", "children": [
            {"kind": "progress", "id": "strength", "maximum": 100, "value": 0,
             "expand": True,
             "tooltip": "An estimate from length and the characters used: 90 bits "
                        "of space is a full bar. A sentence of words scores badly "
                        "here and can still be the better password"},
            {"kind": "label", "id": "strengthtext", "text": "nothing typed",
             "style": "color: #aaaaaa; padding: 0 6px;"},
        ]},
    ]}


def list_pane() -> dict:
    """The left pane: a filter box, the entries, and the switch that shows them in clear."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "line", "id": "search", "expand": True,
             "placeholder": "Filter by name, user, site or note",
             "tooltip": "The list narrows as you type. Passwords are never searched",
             "on": ["changed", "returned"]},
            button("add", "+ New", "Start an empty entry. Save is what puts it in "
                                   "the vault, and the file is written again then"),
        ]},
        {"kind": "table", "id": "list", "columns": list(LIST_COLUMNS),
         "stretch": [len(LIST_COLUMNS) - 1], "col_widths": [150, 120, 120],
         "tooltip": "Click a row to put it in the form. Right-click, or use "
                    "**Actions**, for a menu over just that entry",
         "on": ["selected", "context"]},
        {"kind": "row", "children": [
            {"kind": "check", "id": "show", "text": "Show passwords",
             "tooltip": "Write the real passwords into the list and make the "
                        "Password box typeable. The vault is still only on this "
                        "machine either way",
             "checked": showing(), "on": ["toggled"]},
            {"kind": "spacer"},
            {"kind": "label", "id": "clipnote", "text": "",
             "style": "color: #aaaaaa;"},
        ]},
    ]}


#: The list's headings. The password column is there so a row can be read at a
#: glance, bullets or not; the note is not, because a note is a paragraph.
LIST_COLUMNS = ("Name", "User", "Password", "Site")


def tab_spec(values: dict) -> dict:
    """The whole tab: state and buttons on top, the list left, the entry right."""
    return {"kind": "column", "children": [
        {"kind": "row", "children": [
            {"kind": "label", "id": "lockstate", "text": "locked",
             "style": CHIP_STYLE.format(CHIP_SHUT)},
            {"kind": "spacer"},
            {"kind": "label", "id": "count", "text": "no vault yet",
             "style": "color: #aaaaaa;"},
        ]},
        {"kind": "row", "children": [
            button("unlock", "Create vault",
                   "Make the vault file, with a master password of your choosing, "
                   "or ask for the password that opens the one that is there"),
            button("lock", "Lock", "Forget the master password and every entry, "
                                   "here and in the clipboard, until it is asked "
                                   "for again", False),
            button("chpw", "Change master password",
                   "Seal the same entries with a different password", False),
            button("backup", "Back up",
                   "A copy of the vault file, encrypted exactly as it is, in "
                   "addons/_data/password_manager/backups"),
            button("restore", "Restore",
                   "Put a copy back over the vault file. The copy being covered is "
                   "kept first"),
        ]},
        {"kind": "label", "id": "status", "word_wrap": True, "expand": True,
         "text": "A vault of passwords, kept encrypted on this computer.",
         "style": "color: #23d18b; padding: 2px 4px;"},
        {"kind": "splitter", "sizes": [5, 4], "children": [
            list_pane(),
            {"kind": "scroll", "children": [
                {"kind": "column", "stretch": True, "children": [
                    entry_group(), maker_group(values),
                    {"kind": "row", "children": [
                        button("save", "Save entry",
                               "Write this entry into the vault file, adding it if "
                               "the form was started empty"),
                        button("revert", "Reload it",
                               "Put the saved values back into the form", False),
                        button("copy_pass", "Copy password",
                               "The password on the clipboard, and off it again "
                               "after the seconds the config names", False),
                        button("copy_user", "Copy user",
                               "The user name on the clipboard. It is not a secret, "
                               "so it is left there", False),
                        button("delete", "Delete", "Take this entry out of the "
                                                    "vault", False),
                    ]},
                ]},
            ]},
        ]},
    ]}


def tab_icon() -> str:
    """The shipped picture if a folder this addon is read from has one, else the
    drawn mark.

    Looked for by the standard rule - the id and then ``icon.png``, so here
    ``password_managericon.png`` - in this addon's own
    ``addon.store/assets/password_manager`` folder first and then in the
    program's ``assets``, which is why there is no file name to keep in step
    here: dropping the picture in either place is enough.
    """
    paths = None
    try:
        paths = api.paths
    except (AddonError, AttributeError, TypeError):    # no api on a bare import
        paths = None
    return find_addon_picture(paths, "password_manager") or TAB_ICON


def build_tab() -> None:
    """The tab as a spec; every control reports through ``on_widget``, so compiled and source match.

    Furniture rather than a page: it is pinned to the right of the bar and worn
    as its mark alone, like Settings and the store, so a vault is reached by its
    key and never dragged about.
    """
    values = merge_config(DEFAULTS, api.config.all())
    api.tabs.add_tab(TAB, spec=tab_spec(values), style=TAB_STYLE,
                     pinned=True, icon_only=True)
    api.tabs.set_icon(TAB, tab_icon())
    # The bar is a colour, not a second read-out: the words beside it already
    # say the percent, and Qt's own "%p%" would repeat it.
    set_value("strength", "format", "")
    meter(password_for_meter())


# --- the mark, and the file name it is expected to be drawn into ---

#: A key with its ring cut in half and a keyhole in the middle: one word about
#: what this tab is, and readable at the size a tab draws it. Held as an SVG
#: document so the same file works imported into the app and compiled into a
#: program of its own, where there is no Qt to draw with and no picture beside it.
TAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<circle cx="9" cy="8" r="5.2" fill="none" stroke="#ffb020" stroke-width="2.2"/>'
    '<circle cx="9" cy="8" r="1.7" fill="#ffb020"/>'
    '<path d="M12.6 11.8 21 20.2" fill="none" stroke="#ffb020" stroke-width="2.6" '
    'stroke-linecap="round"/>'
    '<path d="M16.4 15.6 18.6 13.4" fill="none" stroke="#ffb020" stroke-width="2.2" '
    'stroke-linecap="round"/>'
    '<path d="M3 3.6h5.4v4.2H3z" fill="#c51a4a"/>'
    '<path d="M4.4 3.6 6 1.6l1.6 2" fill="#c51a4a"/>'
    '</svg>'
)

#: The picture this addon's own assets folder is expected to hold: its id and
#: then ``icon.png``, the rule every addon follows, so a picture dropped in is
#: worn with nothing here changed.
TAB_ICON_FILE = "password_managericon.png"


# --- drawing what is known onto the tab ---

def password_for_meter() -> str:
    """What the meter should be measuring: the box, or the entry behind it."""
    if showing():
        return str(read_value("pass", "value", "") or "")
    entry = current_entry()
    return str((entry or {}).get("password") or "")


def meter(text: str | None = None) -> None:
    """The guess-work bar and the word beside it, for a typed password or the picked one."""
    body = password_for_meter() if text is None else str(text or "")
    percent, word = password_strength(body)
    set_value("strength", "value", percent)
    tint = ("#ff6b6b" if percent < 45 else
            "#ffb020" if percent < 75 else "#23d18b")
    set_value("strengthtext", "text", f"{percent}% - {word}")
    set_value("strengthtext", "style", f"color: {tint}; padding: 0 6px;")


def refresh_header() -> None:
    """The chip, the count, and which buttons and boxes may be used at all."""
    if api is None:
        return
    if not has_vault():
        chip, colour = "no vault yet", CHIP_NONE
    elif is_open():
        chip, colour = "unlocked", CHIP_OPEN
    else:
        chip, colour = "locked", CHIP_SHUT
    set_value("lockstate", "text", chip)
    set_value("lockstate", "style", CHIP_STYLE.format(colour))
    set_value("unlock", "text", "Unlock" if has_vault() else "Create vault")
    set_value("unlock", "enabled", not is_open())
    usable = is_open()
    for identifier in ("lock", "chpw", "copy_pass", "copy_user", "delete",
                       "revert", "save", "add", "list", "search", "show"):
        set_value(identifier, "enabled", usable)
    set_value("backup", "enabled", has_vault())
    set_value("restore", "enabled", True)
    for identifier in ("name", "user", "pass", "site", "note", "gen_len",
                       "generate"):
        set_value(identifier, "enabled", usable)
    set_value("pass", "enabled", usable and showing())


def refresh_count() -> None:
    """How many entries are in the vault, and how many the filter is showing."""
    total = len(entries())
    held = len(row_order)
    if not has_vault():
        text = "no vault yet"
    elif not is_open():
        text = "the vault is shut"
    elif held != total:
        text = f"{held} of {total} entry(ies) shown"
    else:
        text = f"{total} entry(ies)"
    minutes = number("auto_lock_minutes", DEFAULTS["auto_lock_minutes"])
    if is_open() and minutes > 0:
        text += f", locks after {minutes} min idle"
    set_value("count", "text", text)


def refresh_list() -> None:
    """The table, from the entries the filter lets through. Nothing while locked."""
    global row_order
    if api is None:
        return
    body: list = []
    labels: dict = {}
    if is_open():
        kept = [entry for entry in entries() if entry_matches(entry, search_word)]
        body, labels = label_map(sort_entries(kept))
    row_order = labels
    rows = [list(row_of(entry)) for entry in body]
    set_value("list", "rows", rows)
    refresh_count()


def row_of(entry: dict) -> list:
    """One row's four cells, with the password in whichever shape it may be seen."""
    if showing():
        secret = str(entry.get("password") or "")
    else:
        secret = mask_text() if entry.get("password") else "-"
    return [str(entry.get("name") or "(no name)"),
            str(entry.get("user") or "") or "-",
            secret,
            str(entry.get("site") or "") or "-"]


def show_entry(entry: dict | None) -> None:
    """Put one entry, or nothing, into the form."""
    if api is None:
        return
    body = entry or {}
    set_value("name", "value", str(body.get("name") or ""))
    set_value("user", "value", str(body.get("user") or ""))
    set_value("site", "value", str(body.get("site") or ""))
    set_value("note", "value", str(body.get("note") or ""))
    set_value("stamp", "text", str(body.get("changed") or "") or "not saved yet")
    password_cell(entry)
    refresh_header()
    meter()


def password_cell(entry: dict | None) -> None:
    """The password box: the real thing while passwords are shown, bullets otherwise."""
    if showing():
        set_value("pass", "value", str((entry or {}).get("password") or ""))
    else:
        set_value("pass", "value", mask_text() if entry else "")
    set_value("pass", "enabled", is_open() and showing())


def refresh_all() -> None:
    """Every part of the tab, after something about the vault changed."""
    show_entry(current_entry())
    refresh_list()
    meter()


def set_showing(on: bool) -> None:
    """The one switch for seeing a password anywhere on this tab, in the list or in the box."""
    global showing_flag
    showing_flag = bool(on)
    set_value("show", "checked", showing_flag)
    password_cell(current_entry())
    refresh_list()
    refresh_header()
    meter(password_for_meter())
    if showing_flag:
        say("passwords are showing on screen - a window behind this one can read them")


# --- reading the form ---

def form_of() -> dict:
    """The five fields, as an entry, without the id or the stamp."""
    return {"name": clean_text(read_value("name", "value", "")),
            "user": clean_text(read_value("user", "value", "")),
            "site": clean_text(read_value("site", "value", "")),
            "note": str(read_value("note", "value", "") or "").replace("\r", ""),
            "password": password_from_form()}


def password_from_form() -> str:
    """What the form says the password is.

    While bullets are showing the box holds bullets, which is not a password
    anybody meant, so an existing entry keeps the one it was saved with and a new
    one has nothing yet. That is what lets **Save** change a note without
    touching a password that was never shown.
    """
    if showing():
        return clean_text(read_value("pass", "value", ""))
    entry = current_entry()
    return str((entry or {}).get("password") or "")


def gen_pool(values: dict | None = None) -> str:
    """The generator's alphabet, as the four ticks on the tab have it now."""
    if values is None:
        values = {
            "gen_upper": bool(read_value("gen_upper", "checked", True)),
            "gen_digits": bool(read_value("gen_digits", "checked", True)),
            "gen_symbols": bool(read_value("gen_symbols", "checked", True)),
            "gen_no_ambiguous": bool(read_value("gen_no_ambiguous", "checked", True)),
        }
    return alphabet_of(values)


def gen_length() -> int:
    return as_int(read_value("gen_len", "value",
                             number("gen_length", DEFAULTS["gen_length"])),
                  number("gen_length", DEFAULTS["gen_length"]))


def press_generate(_checked: bool = False) -> None:
    """Draw a password, put it in the box, and show it: a made-up password the
    person cannot see is a password they cannot write down."""
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    pool = gen_pool()
    made = make_password(gen_length(), pool)
    if not showing():
        set_showing(True)
    set_value("pass", "value", made)
    meter(made)
    say("a password was drawn - Save puts it in the vault with the entry")


# --- the session: creating, opening, shutting ---

def create_vault() -> bool:
    """Make the vault file, with a master password typed twice.

    A vault is created rather than opened because there is no file to ask about;
    the two boxes are two separate asks, since a password typed into one box
    cannot be checked against itself.
    """
    first = api.ui.input("Create the vault", "Choose a master password:", secret=True)
    if first is None:
        say("nothing was created")
        return False
    if len(str(first)) < _MIN_MASTER:
        api.ui.warning("Too short",
                       f"A master password has to be at least {_MIN_MASTER} "
                       "characters. Anything shorter is worth the same guessing "
                       "as nothing at all.")
        return False
    again = api.ui.input("Create the vault", "Type it once more:", secret=True)
    if again is None:
        say("nothing was created")
        return False
    if str(again) != str(first):
        api.ui.warning("Not the same", "The two passwords do not match, so no "
                                       "vault was written.")
        return False
    global session, current_uid, search_word
    session = {"master": str(first), "entries": [], "open": True}
    current_uid = ""
    search_word = ""
    set_value("search", "value", "")
    if not write_vault():
        lock(quiet=True)
        return False
    touch()
    refresh_all()
    say("the vault was made. There is no way back into it without this password, "
        "so keep a backup you can read")
    api.log("vault created")
    api.ui.information(
        "The vault exists now",
        "Nothing about this vault can recover its password: it is stretched into "
        "the key and never stored. Keep a copy of the file you can open on another "
        "machine - **Back up** puts an encrypted copy in the addon's own folder.")
    return True


def unlock() -> bool:
    """Ask for the master password once, and hold the entries it opens for the session."""
    if not has_vault():
        return create_vault()
    master = api.ui.input("Unlock the vault", "Master password:", secret=True)
    if master is None:
        say("the vault stays shut")
        return False
    try:
        found = decrypt_vault(vault_text(), str(master))
    except AddonError as error:
        api.ui.warning("It will not open",
                       f"{error}\n\nNothing was changed, and the file on disk is "
                       "exactly as it was.")
        api.log(f"unlock refused: {error}")
        return False
    global session, current_uid, search_word
    session = {"master": str(master), "entries": found, "open": True}
    current_uid = ""
    search_word = ""
    set_value("search", "value", "")
    touch()
    refresh_all()
    say(f"unlocked for this session - {len(found)} entry(ies)")
    return True


def lock(quiet: bool = False) -> None:
    """Put the master password and every entry back to nothing, and wipe what was copied.

    The clipboard is cleaned through the same guarded call as the timer: a
    password that was copied and not yet wiped is a secret the locked vault no
    longer knows about, so it goes while the addon still remembers what it was.
    """
    global session, current_uid, clip_timer
    session = {"master": "", "entries": [], "open": False}
    current_uid = ""
    clip_timer = None
    wipe_clipboard()
    if api is None:
        return
    refresh_all()
    if not quiet:
        say("locked - the master password is not being held any more")


def press_unlock(_checked: bool = False) -> None:
    """The one button that means two things: make the vault, or open it."""
    if api is None or is_open():
        return
    unlock()


def press_lock(_checked: bool = False) -> None:
    if api is None:
        return
    lock()


def change_master() -> None:
    """Seal the same entries with a different password.

    The old one is asked for even though the vault is open, because this is the
    one action after which a wrong memory means an unreadable file: confirming
    what opens it now is the check that the person pressing the button is the
    person who knows it.
    """
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    old = api.ui.input("Change the master password",
                       "The password it opens with now:", secret=True)
    if old is None:
        say("nothing was changed")
        return
    try:
        if not master_opens(vault_text(), str(old)):
            api.ui.warning("Wrong password", "That is not the password this vault "
                                             "opens with.")
            return
    except AddonError as error:
        say(str(error), bad=True)
        return
    new = api.ui.input("Change the master password", "The new one:", secret=True)
    if new is None:
        say("nothing was changed")
        return
    if len(str(new)) < _MIN_MASTER:
        api.ui.warning("Too short", f"The new password has to be at least "
                                   f"{_MIN_MASTER} characters.")
        return
    again = api.ui.input("Change the master password", "Type the new one once more:",
                         secret=True)
    if str(again or "") != str(new):
        api.ui.warning("Not the same", "The two new passwords do not match, so "
                                       "nothing was rewritten.")
        say("nothing was changed")
        return
    session["master"] = str(new)
    if not write_vault():
        say("the vault could not be rewritten with the new password", bad=True)
        return
    say("the entries are sealed with the new password")
    api.log("master password changed")


def press_backup(_checked: bool = False) -> None:
    if api is None:
        return
    where = backup_vault("manual")
    if where:
        say(f"a copy of the sealed vault is at {where}")
        api.log(f"vault backed up to {where}")


def press_restore(_checked: bool = False) -> None:
    """Put a copy back over the vault file, keeping what it covers first."""
    if api is None:
        return
    picked = api.ui.pick_file("Restore a vault", kind="open",
                              initial=str(backups_dir()),
                              filter="Vault (*.json);;All files (*)")
    if not str(picked or "").strip():
        say("nothing was picked")
        return
    source = Path(str(picked).strip()).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        say(f"that file could not be read: {error}", bad=True)
        return
    try:
        vault_header(text)
    except AddonError as error:
        say(f"that is not a vault this addon can open: {error}", bad=True)
        return
    if has_vault() and not api.ui.question(
            "Restore", f"{vault_path()}\n\nwill be replaced by\n{source}\n\n"
                       "The file being covered is copied to the backups folder "
                       "first. Continue?", default=False):
        say("restore cancelled")
        return
    if has_vault():
        backup_vault("before-restore")
    try:
        vault_path().write_text(text, encoding="utf-8")
    except OSError as error:
        say(f"the vault file could not be written: {error}", bad=True)
        return
    lock(quiet=True)
    say("the copy is in place - it needs the password that opens it")
    api.log(f"vault restored from {source}")
    unlock()


# --- entries ---

def press_add(_checked: bool = False) -> None:
    """An empty form, and no row picked."""
    global current_uid
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    current_uid = ""
    show_entry(None)
    set_value("name", "value", "")
    say("a new entry: fill it in, and Save puts it in the vault")


def save_entry(_checked: bool = False) -> None:
    """Write the form into the vault, and the vault back to disk."""
    global current_uid
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    body = form_of()
    if not body["name"]:
        say("an entry needs a name - that is all the list can be searched by",
            bad=True)
        return
    if not body["password"]:
        say("no password: tick **Show passwords** to type one, or **Generate** one",
            bad=True)
        return
    found = current_entry()
    if found is None:
        entry = clean_entry(dict(body))
        session["entries"].append(entry)
        current_uid = str(entry["uid"])
    else:
        found.update(body)
        found["changed"] = time.strftime("%Y-%m-%d %H:%M")
        entry = found
        current_uid = str(entry["uid"])
    if not write_vault():
        return
    touch()
    show_entry(entry)
    refresh_list()
    say(f"saved {entry['name']!s} to {vault_path().name}")


def press_revert(_checked: bool = False) -> None:
    if api is None:
        return
    show_entry(current_entry())
    say("the form matches the vault again")


def press_delete(_checked: bool = False) -> None:
    """Take one entry out, after asking, and rewrite the file."""
    global current_uid
    if api is None or not is_open():
        return
    entry = current_entry()
    if entry is None:
        say("there is no entry picked to delete", bad=True)
        return
    if not api.ui.question("Delete", f"Take {entry.get('name')!r} out of the "
                                    "vault?\n\nThe vault file is rewritten without "
                                    "it. A backup made earlier still holds it.",
                           default=False):
        say("nothing was deleted")
        return
    session["entries"] = [one for one in session["entries"]
                         if str(one.get("uid")) != str(entry.get("uid"))]
    current_uid = ""
    if not write_vault():
        session["entries"].append(entry)
        write_vault()
        return
    show_entry(None)
    refresh_list()
    say(f"{entry.get('name')} is out of the vault")
    api.log(f"entry deleted: {entry.get('name')}")


def pick(label: str) -> None:
    """The row that was clicked, put in the form."""
    global current_uid
    uid = str(row_order.get(str(label)) or "")
    if not uid:
        return
    current_uid = uid
    show_entry(find_entry(uid))


def row_menu(label: str) -> None:
    """A right-click on a row: the menu over that one entry."""
    if api is None or not is_open():
        return
    if not str(label or ""):
        say("nothing was clicked", bad=True)
        return
    pick(str(label))
    entry = current_entry()
    if entry is None:
        return
    words = [text for text, _action in MENU_ACTIONS]
    choice = api.ui.choose(words)
    action = dict((text, where) for text, where in MENU_ACTIONS).get(str(choice or ""))
    if action == "copy_pass":
        copy_password()
    elif action == "copy_user":
        copy_user()
    elif action == "copy_site":
        copy_field("site", "site")
    elif action == "revert":
        press_revert()
    elif action == "delete":
        press_delete()


# --- the clipboard ---

def copy_field(key: str, label: str, wipe: bool = False) -> None:
    """One field of the picked entry, onto the clipboard.

    Only a password is a secret worth taking back off, so ``wipe`` is off unless
    the caller says otherwise; a user name or an address that goes and sits in
    somebody's search box is not a leak, and wiping it would be a nuisance.
    """
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    entry = current_entry()
    if entry is None:
        say("pick an entry first", bad=True)
        return
    send(str(entry.get(key) or ""), label, wipe)


def copy_password(_checked: bool = False) -> None:
    """The picked entry's password, and a timer to take it back."""
    if api is None or not is_open():
        say("open the vault first", bad=True)
        return
    if current_entry() is None:
        say("pick an entry first", bad=True)
        return
    if showing():
        typed = clean_text(read_value("pass", "value", ""))
        if typed and current_entry() is not None and \
                typed != str(current_entry().get("password") or ""):
            # The box holds something that has not been saved: copy what is in
            # front of the person rather than what is in the file, and say which.
            say("copied what is in the box, not what is saved")
            send(typed, "password", wipe=True)
            return
    send(str((current_entry() or {}).get("password") or ""), "password", wipe=True)


def copy_user(_checked: bool = False) -> None:
    copy_field("user", "user name")


def send(text: str, label: str, wipe: bool = False) -> None:
    """Put ``text`` on the clipboard, and set the timer for a secret."""
    if api is None:
        return
    if not str(text or ""):
        say(f"there is no {label} to copy", bad=True)
        return
    if not api.ui.copy(str(text)):
        say("the clipboard could not be reached", bad=True)
        api.log(f"nothing was copied: the clipboard refused a {label}")
        return
    seconds = number("clipboard_clear_seconds",
                     DEFAULTS["clipboard_clear_seconds"]) if wipe else 0
    start_clip_timer(str(text), seconds)
    if wipe and seconds > 0:
        said = f"it comes off again in {seconds}s"
    elif wipe:
        said = "it stays on the clipboard (the config says never clear it)"
    else:
        said = "it is not a secret, so it stays there"
    note(f"copied a {label} - {said}")
    if bool(cfg("notify_on_copy", True)):
        api.ui.notify(f"{label} copied - {said}", 6000)
    else:
        say(f"copied the {label}")
        note(f"copied the {label} - {said}")


def start_clip_timer(text: str, seconds: int) -> None:
    """One shot to take ``text`` back off the clipboard, replacing any earlier one."""
    global clip_timer, clip_text
    _stop_timer(clip_timer)
    clip_timer = None
    if seconds <= 0:
        clip_text = ""
        return
    clip_text = str(text or "")
    clip_timer = api.schedule.after(float(seconds), wipe_clipboard)


def wipe_clipboard() -> None:
    """Take the copied secret off the clipboard, if it is still the thing on it.

    The guard matters more than the wipe: a timer set forty seconds ago cannot
    know that the person has since copied a paragraph they need, so the app is
    asked to clear the clipboard only while it holds exactly these characters.
    A wipe that was not needed is reported as such, rather than said as though
    something had been cleaned.
    """
    global clip_timer, clip_text
    said = clip_text
    clip_text = ""
    clip_timer = None
    if api is None or not said:
        return
    try:
        gone = bool(api.ui.clipboard_clear(only_if=said))
    except AddonError:
        gone = False
    if gone:
        note("the copied password was wiped from the clipboard")
        api.log("clipboard wiped after a copied password")
    else:
        note("something else was copied, so the clipboard was left alone")


# --- the clock that locks the vault ---

def lock_due(now: float | None = None) -> bool:
    """Whether the idle switch says the vault should be shut by now."""
    minutes = number("auto_lock_minutes", DEFAULTS["auto_lock_minutes"])
    if not is_open() or minutes <= 0:
        return False
    waited = (time.monotonic() if now is None else float(now)) - last_touch
    return waited >= minutes * 60


def tick() -> None:
    """The lock check the app keeps asking for, and the one line it leaves behind."""
    if api is None:
        return
    if not lock_due():
        return
    minutes = number("auto_lock_minutes", DEFAULTS["auto_lock_minutes"])
    lock(quiet=True)
    say(f"the vault locked itself after {minutes} minute(s) without a press")


# --- the filters and the tick box ---

def typed_search(text: str) -> None:
    """The filter box, as it is typed in."""
    global search_word
    search_word = str(text or "")
    refresh_list()


def toggle_show(checked: bool) -> None:
    set_showing(bool(checked))


# --- hooks the app calls ---

def focus_vault() -> None:
    """The Tools menu entry: go to the tab, and open the vault if it is shut."""
    if api is None:
        return
    api.tabs.activate(TAB)
    if not is_open() and not unlock():
        return


def init(plugin_api: Any) -> None:
    """Build the tab, shut, and start the clock that keeps it shut."""
    global api, session, showing_flag, last_touch, current_uid, search_word
    global clip_text, clip_timer
    api = plugin_api
    # A start is a shut vault. Nothing is carried over from a run before this
    # one - least of all a master password, the entries it unlocked to, or a
    # half-copied secret the timer for wiping it has gone with the last api.
    _stop_timer(clip_timer)
    clip_timer = None
    session = {"master": "", "entries": [], "open": False}
    current_uid = ""
    search_word = ""
    clip_text = ""
    values = api.config.declare(
        FIELDS, DEFAULTS,
        title="Password manager - addons/_data/password_manager/config.json")
    showing_flag = bool(config_get(values, "show_by_default", False))
    last_touch = time.monotonic()
    build_tab()
    refresh_all()
    if not has_vault():
        say("no vault yet: **Create vault** makes one, with a password only you "
            "hold")
    api.ui.menu_item("Show the password vault", focus_vault, menu="Tools")


def start() -> None:
    """The idle clock, on the app's own timer so it stops with the addon."""
    if api is None:
        return
    api.schedule.every(float(LOCK_TICK_SECONDS), tick)


def shutdown() -> None:
    """Drop the master password and the entries, whatever the app does next."""
    global api, session, clip_timer
    session = {"master": "", "entries": [], "open": False}
    _stop_timer(clip_timer)
    clip_timer = None
    api = None


def on_widget(tab: str, identifier: str, event: str, payload: dict) -> None:
    """A tab control reported something: the only route a compiled click can take."""
    if str(tab) != TAB or api is None:
        return
    name = str(identifier)
    kind = str(event)
    body = payload or {}
    if kind == "clicked":
        touch()
        handler = HANDLERS.get(name)
        if handler is not None:
            handler()
        return
    if kind == "toggled" and name == "show":
        touch()
        toggle_show(bool(body.get("checked")))
        return
    if kind == "changed" and name == "search":
        typed_search(str(body.get("text") or ""))
        return
    if kind == "returned" and name == "search":
        typed_search(str(body.get("text") or read_value("search", "value", "")))
        return
    if kind == "changed" and name == "pass":
        # Only while the box is showing can this mean anything: writing bullets
        # into the box also moves ``textChanged``, and a metre standing for
        # bullets would read as though somebody had typed them.
        if not showing():
            return
        meter(str(body.get("text") or ""))
        return
    if kind == "selected" and name == "list":
        touch()
        pick(str(body.get("text") or ""))
        return
    if kind == "context" and name == "list":
        touch()
        row_menu(str(body.get("text") or ""))


#: What each button on the tab does, by its spec id. Listed after the functions
#: it names, because a compiled addon is this file read top to bottom.
HANDLERS.update({
    "unlock": press_unlock,
    "lock": press_lock,
    "chpw": change_master,
    "backup": press_backup,
    "restore": press_restore,
    "add": press_add,
    "save": save_entry,
    "revert": press_revert,
    "delete": press_delete,
    "copy_pass": copy_password,
    "copy_user": copy_user,
    "generate": press_generate,
})


def on_config(payload: dict) -> None:
    """The user changed the config in Settings: make the tab match the file."""
    if api is None or str(payload.get("addon") or "") != api.id:
        return
    values = merge_config(DEFAULTS, api.config.all())
    for key, field in FIELDS_BY_KEY.items():
        if field.get("kind") == "bool" and widget_id(key) in (
                "gen_upper", "gen_digits", "gen_symbols", "gen_no_ambiguous"):
            set_value(widget_id(key), "checked",
                      bool(config_get(values, key, config_get(DEFAULTS, key))))
        elif widget_id(key) == "gen_len":
            set_value("gen_len", "value",
                      as_int(config_get(values, "gen_length",
                                        DEFAULTS["gen_length"]), 20))
    set_showing(bool(config_get(values, "show_by_default", False)))
    refresh_header()
    refresh_list()
    say("config changed in Settings; the tab now matches it")


if __name__ == "__main__":                     # run as a process addon
    import addon_client

    raise SystemExit(addon_client.serve(__file__))
