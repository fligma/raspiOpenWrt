#!/usr/bin/env python3
"""What the interface is painted from: the look, the settings file it wears,
the widgets that show it and the card put up while the window is being built.

One layer, so one file: the look reads the shipped values out of
:mod:`core.const`, the settings file names the theme to wear, the widgets ask
the look for every colour, and the boot card asks it for the corners it is
round with - and none of the four was ever wanted apart from the rest. Each
half keeps its own banner and its own words where it had them.

All of it needs a screen. An addon that only wants the shape of the shipped
look reads :mod:`core.const`, which carries no Qt at all.
"""

from __future__ import annotations


###############################################################################
# was manager_theme.py
# The program's look, read from ``themes/<name>.theme``.
#
# A theme is one small JSON file: the colours the interface is painted with, the
# shape of its tabs, and - for anything the two do not cover - a few lines of
# stylesheet of its own. :mod:`core.const` holds the shape and the shipped
# values; this module only reads, checks, writes and hands out what is on disk,
# so it needs no window and no Qt and can be used just as well by an addon, a
# test script or a build.
#
# The app boots with a ``themes`` folder beside itself and a ``default.theme``
# inside it, so there is always something to edit and always something to fall
# back on. A file that cannot be read, a token nobody heard of and a colour that
# is not a colour are all answered the same way: the shipped value is kept and
# the rest of the file is used, because a half-written theme should still look
# like a program rather than break one.
#
# Which theme is in force is a profile setting (``theme``), and this module
# remembers the answer between calls. The window is what persists it and redraws
# the screen; :func:`use` only decides what the next stylesheet is built from.
#
###############################################################################


import json
import re
from pathlib import Path
from typing import Any

from core.const import (
    APP_STYLESHEET,
    DEFAULT_THEME,
    DEFAULT_THEME_NAME,
    THEMES_DIR,
    THEME_COLOR_GROUPS,
    THEME_COLORS,
    THEME_SUFFIX,
    THEME_TABS,
    is_theme_color,
    render_theme,
    theme_colors,
    theme_tabs,
)

#: The keys a ``.theme`` file is allowed to carry. Everything else is a mistake
#: somebody should hear about rather than have silently dropped.
THEME_KEYS = ("name", "author", "blurb", "colors", "tabs", "styles")

#: Characters no file name may hold on any system Berry Manager runs on.
_BAD_NAME = re.compile(r"""[<>:"/\\|?*\x00-\x1f]""")

#: What the app holds in memory about the look in force: the name asked for,
#: the theme it came to, and a count that rises whenever either changes.
_state = {"name": DEFAULT_THEME_NAME, "theme": dict(DEFAULT_THEME),
          "revision": 0}


# --- the folder and its files ---

def folder() -> Path:
    """The themes folder, made on the way so there is always somewhere to write."""
    try:
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return THEMES_DIR


def ensure_installed() -> Path:
    """Create ``themes`` and put ``default.theme`` in it; the boot-time call.

    A file already there is left exactly as it is - it may be somebody's work -
    so this is safe to run every time the program starts.
    """
    made = folder()
    target = made / f"{DEFAULT_THEME_NAME}{THEME_SUFFIX}"
    if not target.exists():
        try:
            target.write_text(dump(DEFAULT_THEME_NAME, DEFAULT_THEME),
                              encoding="utf-8")
        except OSError:
            pass
    return made


def safe_name(name: Any) -> str:
    """A theme name as it may be written to disk; empty when it could not be."""
    text = str(name or "").strip()
    if text.lower().endswith(THEME_SUFFIX.lower()):
        text = text[: -len(THEME_SUFFIX)]
    text = _BAD_NAME.sub("_", text).strip(" .")
    return text if text and text not in (".", "..") else ""


def file_for(name: Any) -> Path:
    """The file one theme owns; the default theme's when the name is unusable."""
    cleaned = safe_name(name) or DEFAULT_THEME_NAME
    return folder() / f"{cleaned}{THEME_SUFFIX}"


def names() -> list[str]:
    """Every theme on disk, the default first, the rest alphabetically."""
    found: list[str] = []
    try:
        for candidate in sorted(folder().glob(f"*{THEME_SUFFIX}"),
                                key=lambda path: path.name.lower()):
            cleaned = safe_name(candidate.stem)
            if cleaned and cleaned not in found:
                found.append(cleaned)
    except OSError:
        pass
    if DEFAULT_THEME_NAME in found:
        found.remove(DEFAULT_THEME_NAME)
    found.insert(0, DEFAULT_THEME_NAME)
    return found


def exists(name: Any) -> bool:
    """Whether a theme of that name is actually on disk."""
    return file_for(name).is_file()


def dump(name: Any, theme: dict) -> str:
    """One theme as the text of its file: two-space JSON, the name carried in it too."""
    data = dict(theme or {})
    data["name"] = safe_name(name) or str(data.get("name") or DEFAULT_THEME_NAME)
    ordered = {key: data[key] for key in THEME_KEYS if key in data}
    for key, value in data.items():
        if key not in ordered and key not in THEME_KEYS:
            ordered[key] = value
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


# --- reading and checking ---

def raw(name: Any) -> dict:
    """A theme file exactly as written; ``{}`` when it is missing or unreadable."""
    path = file_for(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve(name: Any) -> dict:
    """One theme complete: every colour and every knob, whatever the file left out."""
    data = raw(name)
    return {
        "name": safe_name(name) or DEFAULT_THEME_NAME,
        "author": str(data.get("author") or ""),
        "blurb": str(data.get("blurb") or ""),
        "colors": theme_colors(data.get("colors")),
        "tabs": theme_tabs(data.get("tabs")),
        "styles": str(data.get("styles") or ""),
    }


def validate(theme: Any) -> list[str]:
    """What is wrong with a candidate theme, one line each; empty when it is usable.

    Nothing here stops a file being written except a name that could not be a
    file: an unknown token is reported because it will be ignored, not because
    it would break the render.
    """
    problems: list[str] = []
    if not isinstance(theme, dict):
        return ["a theme has to be one JSON object"]
    name = str(theme.get("name") or "").strip()
    if not name:
        problems.append("a theme needs a name")
    elif not safe_name(name):
        problems.append(f"{name!r} cannot be used as a file name")
    elif safe_name(name) != name:
        problems.append(f"{name!r} holds characters that become '_' in the file name")
    for key in theme:
        if key not in THEME_KEYS:
            problems.append(f"{key!r} is not something a theme carries "
                            f"(known: {', '.join(THEME_KEYS)})")
    colors = theme.get("colors")
    if colors is not None:
        if not isinstance(colors, dict):
            problems.append("colors has to be an object of token: colour")
        else:
            for token, value in colors.items():
                if token not in THEME_COLORS:
                    problems.append(f"colors.{token} is not a colour the interface has")
                elif not is_theme_color(value):
                    problems.append(f"colors.{token} is not a colour: {value!r}")
    tabs = theme.get("tabs")
    if tabs is not None:
        if not isinstance(tabs, dict):
            problems.append("tabs has to be an object of knob: value")
        else:
            for knob, value in tabs.items():
                if knob not in THEME_TABS:
                    problems.append(f"tabs.{knob} is not a knob the tab bar has")
                elif isinstance(THEME_TABS[knob], bool):
                    if not isinstance(value, bool):
                        problems.append(f"tabs.{knob} has to be true or false")
                else:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        problems.append(f"tabs.{knob} has to be a number of pixels")
                    else:
                        if not 0 <= number <= 200:
                            problems.append(f"tabs.{knob} is out between 0 and 200 px")
    styles = theme.get("styles")
    if styles is not None and not isinstance(styles, str):
        problems.append("styles has to be a string of stylesheet text")
    return problems


# --- writing ---

def write(name: Any, theme: dict, *, force: bool = False) -> Path:
    """Put one theme on disk; refuses a file with problems unless ``force``."""
    if not isinstance(theme, dict):
        raise ValueError("a theme has to be one JSON object")
    cleaned = safe_name(name) or DEFAULT_THEME_NAME
    if not safe_name(cleaned):
        raise ValueError(f"{name!r} cannot be used as a theme name")
    problems = validate({**theme, "name": cleaned})
    if problems and not force:
        raise ValueError("; ".join(problems))
    path = file_for(cleaned)
    path.write_text(dump(cleaned, theme), encoding="utf-8")
    if cleaned == current_name():
        use(cleaned)             # a new file for the look in force takes effect
    return path


def remove(name: Any) -> bool:
    """Delete one theme file; the shipped default stays whatever the user does."""
    cleaned = safe_name(name)
    if not cleaned or cleaned == DEFAULT_THEME_NAME:
        return False
    path = file_for(cleaned)
    if not path.is_file():
        return False
    path.unlink()
    if cleaned == current_name():
        use(DEFAULT_THEME_NAME)
    return True


def copy(name: Any, target: Any) -> str:
    """Duplicate a theme under a new name; returns the name, or raises."""
    cleaned = safe_name(name) or DEFAULT_THEME_NAME
    wanted = safe_name(target)
    if not wanted:
        raise ValueError(f"{target!r} cannot be used as a theme name")
    if exists(wanted):
        raise ValueError(f"a theme called {wanted} is already there")
    path = write(wanted, resolve(cleaned))
    return safe_name(path.stem)


# --- the look in force ---

def current_name() -> str:
    """The theme the interface was built from."""
    return _state["name"]


def use(name: Any) -> str:
    """Make one theme the look in force; returns the name that ended up used.

    A theme with no file is not an error: the default is used and asked for
    again next time, so a deleted file downgrades the look instead of stopping
    the program.
    """
    cleaned = safe_name(name) or DEFAULT_THEME_NAME
    if not exists(cleaned) and cleaned != DEFAULT_THEME_NAME:
        cleaned = DEFAULT_THEME_NAME
    _state["name"] = cleaned
    _state["theme"] = resolve(cleaned)
    _state["revision"] += 1
    return cleaned


def theme(name: Any = None) -> dict:
    """One theme, complete; the one in force when no name is given."""
    if name is None or str(name) == "":
        return dict(_state["theme"])
    return resolve(name)


def revision() -> int:
    """A count that moves whenever the look in force changes; cached pictures watch it."""
    return _state["revision"]


def colors(name: Any = None) -> dict:
    """Every colour token and the hex it currently means."""
    return dict(theme(name)["colors"])


def color(token: Any, name: Any = None, default: str = "") -> str:
    """One colour token as a hex; ``default`` when the interface has no such token."""
    table = theme(name)["colors"]
    return str(table.get(str(token), default or THEME_COLORS.get(str(token), "")))


def tabs(name: Any = None) -> dict:
    """Every tab knob and the size it currently means, in px at 100% zoom."""
    return dict(theme(name)["tabs"])


def tab_metric(key: Any, name: Any = None) -> Any:
    """One tab knob by itself."""
    return theme(name)["tabs"].get(str(key), THEME_TABS.get(str(key)))


def chrome_px(name: Any = None) -> int:
    """What a tab spends on itself besides its text, at 100% zoom.

    The sheet's own padding plus its two borders and the small air before the
    picture: measured here rather than written down, so a theme that makes its
    tabs roomier is measured as roomier when the bar squeezes the captions.
    """
    return 6 + 2 * int(tab_metric("pad_x_px", name) or 0)


def icon_px(name: Any = None) -> int:
    """The smallest a tab's picture is drawn, at 100% zoom."""
    return int(tab_metric("icon_px", name) or THEME_TABS["icon_px"])


def shows_captions(name: Any = None) -> bool:
    """Whether a tab may show its name at all; off leaves the bar icon-only."""
    return bool(tab_metric("captions", name))


def shows_dots(name: Any = None) -> bool:
    """Whether the SSH/SFTP dots are worn beside a tab's name."""
    return bool(tab_metric("dots", name))


def stylesheet(name: Any = None) -> str:
    """The application sheet for one theme, at 100% zoom - the window scales it."""
    if (name is None or str(name) == "" or safe_name(name) == _state["name"]):
        return render_theme(_state["theme"])
    return render_theme(resolve(name))


def sheet_of(theme_data: Any) -> str:
    """The sheet a theme that is still being edited would produce."""
    return render_theme(theme_data if isinstance(theme_data, dict) else {})


#: Which tab knob answers to which words, for whoever is editing one. The knobs
#: themselves are listed by ``core.const.THEME_TABS``; this only says what
#: each one is for, so an editor can explain them without being told twice.
TAB_HINTS = {
    "icon_px": "the smallest a tab's picture is drawn",
    "radius_px": "how round a tab's corners are",
    "pad_y_px": "air above and below a tab's text",
    "pad_x_px": "air at a tab's sides",
    "gap_px": "space left between two tabs",
    "underline_px": "the line a chosen tab is marked with",
    "min_width_px": "the least room a tab may be squeezed into",
    "nest_pad_y_px": "the same air, for a tab nested in a group",
    "nest_pad_x_px": "the same side air, for a nested tab",
    "nest_gap_px": "space between two nested tabs",
    "nest_min_width_px": "the least room a nested tab may be squeezed into",
    "captions": "show a tab's name beside its picture, or the picture alone",
    "dots": "wear the SSH/SFTP marks beside a tab's name",
}


def knobs() -> list[tuple[str, Any, str]]:
    """Every tab knob, the size it means in the shipped look, and what it does."""
    return [(key, value, TAB_HINTS.get(key, "")) for key, value in THEME_TABS.items()]


# --- what the Theme Manager offers a person starting from nothing ---

#: The looks a new theme may be begun from, each complete enough to paint the
#: whole interface and short enough to read down in one go.
_TEMPLATES: tuple[tuple[str, str, dict], ...] = (
    ("default", "The shipped look: near-black surfaces, the berry accent.",
     {"name": "default", "blurb": "The shipped look: near-black surfaces, "
                                  "the berry accent.",
      "colors": dict(THEME_COLORS), "tabs": dict(THEME_TABS)}),
    ("light", "Light surfaces with the same berry accent; a bright room's look.",
     {"name": "light", "blurb": "Light surfaces with the same berry accent.",
      "tabs": dict(THEME_TABS),
      "colors": {
          "base": "#f2f2f2", "surface": "#fbfbfb", "sunken": "#e6e6e6",
          "soft": "#ececec", "menu": "#f7f7f7", "header": "#ececec",
          "raise": "#e2e2e2", "hover": "#d8d8d8", "row_hover": "#e0e0e0",
          "tab": "#eaeaea", "tab_hover": "#dedede",
          "disabled_fill": "#e4e4e4", "toggle_off": "#c9c9c9",
          "border": "#bdbdbd", "raise_edge": "#a3a3a3", "handle": "#9e9e9e",
          "faint": "#8b8b8b", "disabled_edge": "#cccccc",
          "text": "#202020", "bright": "#000000", "muted": "#5a5a5a",
          "menu_text": "#2a2a2a", "note": "#6a6a6a",
          "disabled_text": "#9a9a9a",
          "accent": "#c51a4a", "accent_deep": "#a0153c",
          "accent_edge": "#8f1236", "selection": "#f4c5d3",
          "ok": "#1a9c6b", "warn": "#b07a10", "danger_fill": "#f2dede",
          "danger_edge": "#d99999", "danger_text": "#5a1010",
          "close_ink": "#6a6a6a", "close_hot": "#c0392b",
          "icon_alt": "#5a7d9a", "icon_tint": "#4a4a4a",
          "toggle_handle": "#ffffff", "toggle_handle_off": "#f0f0f0"}}),
    ("deep sea", "Blue-black surfaces with a cyan accent.",
     {"name": "deep sea", "blurb": "Blue-black surfaces, cyan accent.",
      "tabs": dict(THEME_TABS),
      "colors": {
          "base": "#0b1220", "surface": "#121a2b", "sunken": "#0d1524",
          "soft": "#1a2437", "menu": "#0e1522", "header": "#162032",
          "raise": "#1e2a3f", "hover": "#27364f", "row_hover": "#1c2739",
          "tab": "#131d2e", "tab_hover": "#1b283c",
          "disabled_fill": "#141c2a", "toggle_off": "#1a2334",
          "border": "#2b3a52", "raise_edge": "#3a4d68", "handle": "#46596f",
          "faint": "#55637a", "disabled_edge": "#26334a",
          "text": "#d6e2f0", "bright": "#f2f7ff", "muted": "#8ea2bb",
          "menu_text": "#c8d6e8", "note": "#7c8ea6",
          "disabled_text": "#5d6d84",
          "accent": "#2f9bd6", "accent_deep": "#1f7bb0",
          "accent_edge": "#166a9a", "selection": "#1d4c6e",
          "ok": "#2ad39a", "warn": "#f0b429", "danger_fill": "#4d1f22",
          "danger_edge": "#7a3236", "danger_text": "#ffd9d9",
          "close_ink": "#93a6bd", "close_hot": "#ff7a7a",
          "icon_alt": "#7fb3d5", "icon_tint": "#d6e2f0",
          "toggle_handle": "#f2f7ff", "toggle_handle_off": "#8ea2bb"}}),
    ("ember", "Warm brown surfaces with a burnt-orange accent.",
     {"name": "ember", "blurb": "Warm brown surfaces, burnt-orange accent.",
      "tabs": dict(THEME_TABS),
      "colors": {
          "base": "#16110d", "surface": "#221a14", "sunken": "#1b1410",
          "soft": "#2e241b", "menu": "#1e1712", "header": "#2a2018",
          "raise": "#382a1f", "hover": "#463526", "row_hover": "#30251b",
          "tab": "#241b14", "tab_hover": "#33271d",
          "disabled_fill": "#241d16", "toggle_off": "#2b2118",
          "border": "#4a382a", "raise_edge": "#5e4834", "handle": "#6b5340",
          "faint": "#7a6049", "disabled_edge": "#3a2c20",
          "text": "#f0e2d0", "bright": "#fff8ee", "muted": "#b39a7e",
          "menu_text": "#e6d6bf", "note": "#a08a70",
          "disabled_text": "#7d6a56",
          "accent": "#e8761e", "accent_deep": "#bf5b12",
          "accent_edge": "#9c4a0e", "selection": "#6e3410",
          "ok": "#4bbd7a", "warn": "#f2c14e", "danger_fill": "#5e1f14",
          "danger_edge": "#7d2f1f", "danger_text": "#ffdccf",
          "close_ink": "#b39a7e", "close_hot": "#ff6b4a",
          "icon_alt": "#d8a15e", "icon_tint": "#f0e2d0",
          "toggle_handle": "#fff8ee", "toggle_handle_off": "#7d6a56"}}),
)

#: The tab bar with the captions and dots taken away: the roomiest layout a
#: theme can ask for, offered as a starting point beside the colour ones.
TEMPLATES: tuple[tuple[str, str], ...] = tuple(
    (name, blurb) for name, blurb, _data in _TEMPLATES)


def templates() -> list[dict]:
    """The looks a new theme can start from, each with the JSON it starts as."""
    out: list[dict] = []
    for name, blurb, data in _TEMPLATES:
        out.append({"name": name, "blurb": blurb,
                    "theme": json.loads(json.dumps(data))})
    return out


def template_text(name: Any) -> str:
    """One template as the text of a theme file, ready to hand to an editor."""
    wanted = str(name or "").strip().lower()
    for one, _blurb, data in _TEMPLATES:
        if one == wanted:
            return dump(data.get("name"), data)
    return dump(DEFAULT_THEME_NAME, DEFAULT_THEME)


#: The two colours a person most often wants first, so a table can show them
#: even when the whole palette is on screen.
KEY_TOKENS = ("base", "surface", "accent", "text")

#: Which tokens answer to which word a reader would type, for the search box in
#: the Theme Manager's colour list.
TOKEN_HINTS = {
    "base": "window background", "surface": "panels inputs lists",
    "sunken": "alternate rows menubar toolbar scroll",
    "soft": "progress tooltips", "menu": "menu background",
    "header": "column headers", "raise": "buttons selected tab",
    "hover": "button hover", "row_hover": "row under the pointer",
    "tab": "tab", "tab_hover": "tab hover",
    "disabled_fill": "greyed button", "toggle_off": "switch off",
    "border": "outline edges lines", "raise_edge": "button edge",
    "handle": "scrollbar handle", "faint": "check box edge",
    "disabled_edge": "greyed edge", "text": "writing",
    "bright": "selected pressed", "muted": "quiet words",
    "menu_text": "menu bar words", "note": "small grey notes",
    "disabled_text": "greyed words", "accent": "berry the highlight",
    "accent_deep": "accent pressed", "accent_edge": "switch edge",
    "selection": "selected rows", "ok": "green connected",
    "warn": "amber ready", "danger_fill": "red button",
    "danger_edge": "red button edge", "danger_text": "red button words",
    "close_ink": "tab close", "close_hot": "tab close hover",
    "icon_alt": "second icon colour", "icon_tint": "cog light",
    "toggle_handle": "switch knob", "toggle_handle_off": "switch knob off",
}


def token_hint(token: Any) -> str:
    """What a token is for, in the few words the editor has room for."""
    return TOKEN_HINTS.get(str(token), "")


def groups() -> list[tuple[str, tuple[str, ...]]]:
    """The colour tokens in the blocks the editor shows them in."""
    return [(title, tokens) for title, tokens in THEME_COLOR_GROUPS.items()]


def default_sheet() -> str:
    """The sheet as shipped, for a theme that wants to be compared to it."""
    return APP_STYLESHEET


# ---------------------------------------------------------------------------
# One module, four files: the halves below reach each other through this,
# which is what each of them used to be imported as.
import sys

ui = sys.modules[__name__]


###############################################################################
# was manager_config.py
# Reading, migrating and writing the profile files plus the saved history.
#
# Everything the manager writes to its own root lives in the ``config`` folder
# (see ``core.const.CONFIG_DIR``): the profile files, the command history,
# the activity log and the shared addon build record. The folder is created on
# the way up, and a loose file left beside the program from an older layout is
# moved into it, so an existing installation keeps its settings.
#
# Layout v3: every profile lives in its own ``{profile}.json`` holding the
# complete set of settings (connection details, tabs, backups, terminal...),
# and ``config.json`` keeps only the index - the profile order, each profile's
# file and which one is active. The app always works from the profile named by
# the index. Older files - the flat layout, or v2 where every profile lived in
# one config.json - are folded into this shape on first load and written back
# once, so upgrading an installation keeps every user value.
#
###############################################################################


from urllib.parse import urlparse

from core.const import (
    ADDON_ID_TRANSLATIONS,
    APP_CONFIG_EXAMPLE_FILE,
    APP_CONFIG_FILE,
    BASE_DIR,
    CONFIG_DIR,
    DEFAULT_APP_CONFIG,
    DEFAULT_PROFILE,
    DEFAULT_SOURCE,
    HISTORY_FILE,
    LOG_FILE,
    MAX_COMMAND_HISTORY,
    UI_ZOOM_DEFAULT,
    UI_ZOOM_MAX,
    UI_ZOOM_MIN,
)

# Needs no window and nothing from elsewhere: a theme is read from its
# file the way a profile is read from its own.

CONFIG_VERSION = 3

# Layout keys, never a profile's settings.
_STRUCTURAL_KEYS = ("config_version", "profiles", "active_profile")

# Chars a profile name can't put in a filename, plus the
# Windows device names that still collide - both go to _.
_FILE_BAD = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{n}" for n in range(1, 10)),
                   *(f"LPT{n}" for n in range(1, 10))}

# Store at load: full profiles, active name, and files.
# GUI edits a copy, writes back via save_app_config_static().
_STORE: dict = {"active": "", "profiles": [], "files": {}}


# --- small helpers ---

def _coerce_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Turn anything read from JSON into an int inside [minimum, maximum]."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


# --- values an older layout wrote under a name this version retired ---

#: The device folder the dashboard used to live in, and the name it lives under
#: now. A profile still holding the old path is taken to have never been changed
#: by hand, so it follows the shipped default; anything else is the user's own
#: arrangement and is left exactly as it is.
LEGACY_REMOTE_ROOTS = {"/www_rasconf": "/www_berry"}


def _translate_remote_root(value: Any) -> str:
    """The remote folder to work in: the stored path, with a retired default
    moved to the name the same install now uses."""
    text = str(value if value is not None else DEFAULT_PROFILE["remote_root"])
    return LEGACY_REMOTE_ROOTS.get(text, text)


# --- value normalisation (the UI can trust it) ---

def _normalize_values(config: dict) -> dict:
    """Type-check and clamp every settings key of one complete config."""
    # Pre-tab-list config keeps old addresses: rebuild web_tabs
    # from legacy web_port/web_path/luci_url.
    if not isinstance(config.get("web_tabs"), list):
        config = _migrate_web_tabs(config)
    config = _normalize_web_tabs(config)
    config = _normalize_addons(config)

    # A source path from another machine is useless; fall back to
    # the repo, then home, so the local tree is never empty.
    if not config.get("source") or not Path(str(config["source"])).is_dir():
        if DEFAULT_SOURCE.is_dir():
            config["source"] = str(DEFAULT_SOURCE)
        else:
            config["source"] = str(Path.home())

    # Clamp numbers, coerce free-text to str.
    config["ssh_timeout"] = _coerce_int(config.get("ssh_timeout"), 12, 3, 300)
    config["terminal_font_size"] = _coerce_int(config.get("terminal_font_size"), 10, 6, 24)
    # Zoom is a sane percentage; the rungs are the window's
    # business (see core.const.step_zoom).
    config["ui_zoom"] = _coerce_int(config.get("ui_zoom"), UI_ZOOM_DEFAULT, UI_ZOOM_MIN, UI_ZOOM_MAX)
    config["max_log_lines"] = _coerce_int(config.get("max_log_lines"), 500, 50, 10000)
    config["terminal_history_limit"] = _coerce_int(config.get("terminal_history_limit"), 500, 20, 5000)
    config["web_port"] = _coerce_int(config.get("web_port"), 8989, 1, 65535)
    config["reboot_wait_seconds"] = _coerce_int(config.get("reboot_wait_seconds"), 30, 0, 600)
    config["reboot_retry_interval"] = _coerce_int(config.get("reboot_retry_interval"), 10, 1, 300)
    config["port"] = _coerce_int(config.get("port"), DEFAULT_PROFILE["port"], 1, 65535)
    config["default_tab"] = str(config.get("default_tab", "Berry"))
    # A theme is a file name, and a file that is not there is not a mistake to
    # throw on somebody: the shipped default is used instead, so a profile
    # copied onto another machine - or a theme deleted from its own - still
    # opens. ``ui`` makes the themes folder on the way.
    config["theme"] = (ui.safe_name(config.get("theme"))
                       or DEFAULT_APP_CONFIG["theme"])
    config["host"] = str(config.get("host", DEFAULT_PROFILE["host"]))
    config["username"] = str(config.get("username", DEFAULT_PROFILE["username"]))
    config["key_file"] = str(config.get("key_file", ""))
    config["remote_root"] = _translate_remote_root(config.get("remote_root"))
    config["source"] = str(config.get("source", DEFAULT_PROFILE["source"]))
    config["luci_url"] = str(config.get("luci_url", "http://192.168.1.1/"))
    config["web_path"] = str(config.get("web_path", "/cgi-bin/index.py"))

    # Tab order is a list of titles; else dropped so the app
    # falls back to its built-in arrangement.
    order = config.get("tab_order")
    if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
        config["tab_order"] = []

    # Quick commands are {label, command} pairs; else dropped.
    quick = config.get("quick_commands")
    if not isinstance(quick, list):
        quick = []
    sanitized_quick = []
    for entry in quick:
        if isinstance(entry, dict) and entry.get("command"):
            sanitized_quick.append({"label": str(entry.get("label", entry["command"])), "command": str(entry["command"])})
    config["quick_commands"] = sanitized_quick

    # Two per-protocol switches became one for the main connection,
    # which carries both transports: either one on means the main
    # connection comes up at startup. The merged key takes over and
    # the old two are dropped. The old keys decide, not the shipped
    # default: a merged profile always carries a fresh ``auto_connect``.
    if "auto_connect_sftp" in config or "auto_connect_ssh" in config:
        config["auto_connect"] = bool(config.get("auto_connect")) \
            or bool(config.get("auto_connect_sftp")) \
            or bool(config.get("auto_connect_ssh"))
    for key in ("auto_connect_sftp", "auto_connect_ssh"):
        config.pop(key, None)

    for key in (
        "show_hidden_files",
        "master_connection",
        "auto_connect",
        "use_alt_icon",
        "system_notifications",
        "splash_enabled",
        "tooltips_enabled",
        "log_visible",
        "sftp_enabled",
        "ssh_enabled",
        "ignore_cert_errors",
        "reboot_auto_watch",
        "remember_tab_order",
        "trust_unknown_host",
        "remember_password",
    ):
        config[key] = bool(config.get(key, DEFAULT_APP_CONFIG[key]))

    config["logging_enabled"] = bool(config.get("logging_enabled", True))

    # The deploy era wrote these; nothing reads them now, so old
    # profile files are cleaned of them on load.
    for key in ("deploy_ignore", "post_deploy_commands",
                "backup_directory", "local_backup_directory", "keep_last_n_backups",
                "remote_backup_enabled", "local_backup_enabled",
                "mirror_delete_remote", "mirror_delete_local",
                "backup_before_deploy", "confirm_destructive"):
        config.pop(key, None)

    # Structural keys never belong inside a profile record.
    for key in _STRUCTURAL_KEYS:
        config.pop(key, None)
    return config


# --- profile store: migration and active access ---

def _complete_profile(raw: dict, base: dict) -> dict:
    """Build one full profile: base values overridden by the record, then normalised; never defaults over user data."""
    prof = _deep_copy(base)
    prof.update({k: v for k, v in raw.items() if k not in _STRUCTURAL_KEYS})
    name = str(raw.get("name") or "").strip()
    prof = _normalize_values(prof)
    prof["name"] = name
    return prof


def _unique_name(name: str, taken: set[str]) -> str:
    base = name or "Profile"
    candidate = base
    counter = 1
    while candidate in taken:
        counter += 1
        candidate = f"{base} {counter}"
    return candidate


def _profile_filename(name: str, taken: set[str]) -> str:
    """The file one profile owns (``{profile}.json``), through Windows rules; a clash gets a counter."""
    cleaned = "".join("_" if ch in _FILE_BAD else ch for ch in str(name).strip())
    cleaned = cleaned.rstrip(" .")
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    stem = cleaned or "Profile"
    candidate = stem
    counter = 1
    # `taken` holds names with extension (callers track disk),
    # so the clash check must compare the same shape.
    while (candidate + ".json").lower() in taken:
        counter += 1
        candidate = f"{stem} {counter}"
    return candidate + ".json"


def _build_store(config: dict) -> None:
    """Fold any supported layout into the store (full profiles); v2 and flat here, v3 by ``_build_store_v3``."""
    raw_profiles = config.get("profiles")
    if not isinstance(raw_profiles, list):
        raw_profiles = []

    if config.get("config_version") == 2:
        base = _normalize_values(_deep_copy(DEFAULT_APP_CONFIG))
        raw_list = raw_profiles
    else:
        # Legacy flat layout: top-level keys are the file's settings
        # (mirrored from active). They become the shared start for
        # every profile so no stored value is replaced by a default.
        # The retired per-protocol auto-connect switches are judged
        # on the raw file, so ``_normalize_values`` merges them into
        # one ``auto_connect`` before a default can cover them up.
        base = _deep_copy(DEFAULT_APP_CONFIG)
        base.update({k: v for k, v in config.items() if k not in _STRUCTURAL_KEYS})
        base = _normalize_values(base)
        raw_list = raw_profiles

    profiles: list[dict] = []
    taken: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        prof = _complete_profile(raw, base)
        prof["name"] = _unique_name(prof["name"], taken)
        taken.add(prof["name"])
        profiles.append(prof)

    if not profiles:
        # A flat file with no profile list, or an unreadable one:
        # wrap the shared settings into one default profile, keeping
        # the legacy connection values over the shipped defaults.
        first = _deep_copy(base)
        first["name"] = _unique_name(str(config.get("active_profile") or DEFAULT_PROFILE["name"]), taken)
        profiles = [first]

    active = config.get("active_profile")
    if active not in taken:
        active = profiles[0]["name"]
    _STORE["profiles"] = profiles
    _STORE["active"] = active
    # Files are worked out on first write; this branch has none.
    _STORE["files"] = {}


def _build_store_v3(index: dict) -> bool:
    """Read the v3 layout (index + one file per profile). False if not a usable index; a missing profile file is rebuilt from defaults."""
    raw_entries = index.get("profiles")
    if not isinstance(raw_entries, list) or not raw_entries:
        return False
    base = _normalize_values(_deep_copy(DEFAULT_APP_CONFIG))
    profiles: list[dict] = []
    files: dict[str, str] = {}
    taken_names: set[str] = set()
    used_files: set[str] = {"config.json"}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in taken_names:
            continue
        fname = str(entry.get("file") or "").strip()
        # Only a plain file name is trusted; a path in the index, or
        # a clash with a profile already read, is re-derived.
        if not fname or Path(fname).name != fname or fname.lower() in used_files:
            fname = _profile_filename(name, used_files)
        used_files.add(fname.lower())
        try:
            loaded = json.loads((CONFIG_DIR / fname).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        prof = _complete_profile(loaded if isinstance(loaded, dict) else {}, base)
        prof["name"] = name
        taken_names.add(name)
        files[name] = fname
        profiles.append(prof)
    if not profiles:
        return False
    active = index.get("active_profile")
    if active not in taken_names:
        active = profiles[0]["name"]
    _STORE["profiles"] = profiles
    _STORE["active"] = active
    _STORE["files"] = files
    return True


def _working_copy() -> dict:
    """A detached copy of the active profile's settings (without its name)."""
    active = next(
        (p for p in _STORE["profiles"] if p["name"] == _STORE["active"]),
        _STORE["profiles"][0],
    )
    return {k: v for k, v in _deep_copy(active).items() if k != "name"}


def profile_names() -> list[str]:
    return [p["name"] for p in _STORE["profiles"]]


def active_profile_name() -> str:
    return _STORE["active"]


def set_active_profile(name: str) -> dict:
    """Point the store at another profile and return the new working copy."""
    if any(p["name"] == name for p in _STORE["profiles"]):
        _STORE["active"] = name
    return _working_copy()


def create_profile(name: str, working: dict) -> dict:
    """Clone these settings into a new profile, activate it, and return the fresh working copy."""
    prof = _deep_copy(working)
    prof = _normalize_values(prof)
    taken = {p["name"] for p in _STORE["profiles"]}
    prof["name"] = _unique_name(str(name).strip(), taken)
    idx = next(
        (i for i, p in enumerate(_STORE["profiles"]) if p["name"] == _STORE["active"]),
        len(_STORE["profiles"]),
    )
    _STORE["profiles"].insert(idx + 1, prof)
    _STORE["active"] = prof["name"]
    persist_store()
    return _working_copy()


def rename_profile(old: str, new: str) -> bool:
    new = str(new).strip()
    if not new or new == old or any(p["name"] == new for p in _STORE["profiles"]):
        return False
    for prof in _STORE["profiles"]:
        if prof["name"] == old:
            prof["name"] = new
            break
    else:
        return False
    if _STORE["active"] == old:
        _STORE["active"] = new
    persist_store()
    return True


def delete_profile(name: str) -> dict | None:
    """Remove one profile; first remaining becomes active if needed. Returns the new copy or None."""
    if len(_STORE["profiles"]) <= 1 or not any(p["name"] == name for p in _STORE["profiles"]):
        return None
    _STORE["profiles"] = [p for p in _STORE["profiles"] if p["name"] != name]
    if _STORE["active"] == name:
        _STORE["active"] = _STORE["profiles"][0]["name"]
    persist_store()
    return _working_copy()


# --- web tabs: url, migration, normalisation ---

def web_tab_url(tab: dict, default_host: str) -> str:
    """URL for one tab. Blank ip follows the active host; the port is dropped when it matches the scheme default."""
    scheme = str(tab.get("scheme") or "http").lower()
    host = str(tab.get("ip") or "").strip() or (default_host or "127.0.0.1")
    port = _coerce_int(tab.get("port"), 80, 1, 65535)
    path = str(tab.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}{path}"
    return f"{scheme}://{host}:{port}{path}"


def _normalize_web_tab(raw: dict) -> dict | None:
    """Validate a single tab entry, dropping it if it has no usable name."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    scheme = str(raw.get("scheme") or "http").strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    return {
        "name": name,
        "ip": str(raw.get("ip") or "").strip(),
        "port": _coerce_int(raw.get("port"), 80, 1, 65535),
        "path": str(raw.get("path") or "/").strip() or "/",
        "scheme": scheme,
    }


def _normalize_web_tabs(config: dict) -> dict:
    tabs = config.get("web_tabs")
    if not isinstance(tabs, list):
        tabs = []
    cleaned = []
    for raw in tabs:
        tab = _normalize_web_tab(raw)
        if tab is not None:
            cleaned.append(tab)
    if not cleaned:
        cleaned = json.loads(json.dumps(DEFAULT_APP_CONFIG["web_tabs"]))
    config["web_tabs"] = cleaned
    return config


def _migrate_web_tabs(config: dict) -> dict:
    """Seed web_tabs from legacy web_port/web_path/luci_url, used only for a pre-tab-list config so addresses are kept."""
    luci = urlparse(str(config.get("luci_url", "http://192.168.1.1/")))
    luci_host = luci.hostname or ""
    luci_port = luci.port or (443 if luci.scheme == "https" else 80)
    luci_path = luci.path or "/"
    config["web_tabs"] = [
        {
            "name": "Web Interface",
            "ip": "",
            "port": _coerce_int(config.get("web_port"), 8989, 1, 65535),
            "path": str(config.get("web_path", "/cgi-bin/index.py")),
            "scheme": "http",
        },
        {
            "name": "LuCI",
            "ip": luci_host,
            "port": luci_port,
            "path": luci_path,
            "scheme": luci.scheme or "http",
        },
    ]
    return config


# --- addons: which run, and their data ---

def _translate_addon_ids(cleaned: dict) -> dict:
    """Fold a retired addon's record onto the addon that replaced it.

    An addon that ships inside the program and later becomes something the store
    publishes leaves every profile holding a record under the old name, with the
    switches the user set and the values the addon saved. Rather than let that
    quietly fall back to off, the record is moved: where only the retired name
    exists it simply becomes the successor's, and where both exist the successor
    wins on every switch and its saved keys are kept, with the retired record
    filling in whatever the newer one never had.
    """
    for gone, came in ADDON_ID_TRANSLATIONS.items():
        old = cleaned.pop(gone, None)
        if old is None:
            continue
        fresh = cleaned.get(came)
        if not isinstance(fresh, dict):
            cleaned[came] = old
            continue
        for field, value in old.items():
            if field == "settings":
                merged = dict(value if isinstance(value, dict) else {})
                merged.update(fresh.get("settings") or {})
                fresh["settings"] = merged
            elif field not in fresh:
                fresh[field] = value
    return cleaned


def _normalize_addons(config: dict) -> dict:
    """Keep addons a plain id -> {enabled, own_connection, ssh_enabled,
    sftp_enabled, settings} map; a retired addon's record is folded onto the
    addon that took its place, each off and on the main
    connection by default, with both of its transports allowed by default."""
    raw = config.get("addons")
    if not isinstance(raw, dict):
        raw = {}
    cleaned = {}
    for key, value in raw.items():
        addon_id = str(key).strip()
        if not addon_id:
            continue
        record = value if isinstance(value, dict) else {}
        settings = record.get("settings")
        cleaned[addon_id] = {
            "enabled": bool(record.get("enabled", False)),
            "own_connection": bool(record.get("own_connection", False)),
            # The addon's own gates over the two transports; the app-wide
            # switch stays above them, so both have to be on.
            "ssh_enabled": bool(record.get("ssh_enabled", True)),
            "sftp_enabled": bool(record.get("sftp_enabled", True)),
            "settings": settings if isinstance(settings, dict) else {},
        }
    config["addons"] = _translate_addon_ids(cleaned)
    return config


# --- load, save, transport ---

def _prepare_config_dir() -> None:
    """Make the config folder and move loose files in (only into empty slots), picking up the old log name too."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    for old, new in ((BASE_DIR / "config.json", APP_CONFIG_FILE),
                     (BASE_DIR / "command_history.json", HISTORY_FILE),
                     (BASE_DIR / "rasconf_manager.log", LOG_FILE),
                     (CONFIG_DIR / "rasconf_manager.log", LOG_FILE)):
        if old.is_file() and not new.exists():
            try:
                old.replace(new)
            except OSError:
                pass


def load_app_config() -> dict:
    """Load config, build the store, return the active profile as a working dict; older layouts fold in and are written back once."""
    _prepare_config_dir()
    config: dict = {}
    # Real config beats the example; if neither parses, defaults.
    for config_path in (APP_CONFIG_FILE, APP_CONFIG_EXAMPLE_FILE):
        if not config_path.is_file():
            continue
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            config = loaded
            break

    if config.get("config_version") == CONFIG_VERSION and _build_store_v3(config):
        pass                      # the files are already in the new layout
    else:
        _build_store(config)
        # First run or older layout: write back once as the index
        # plus one file per profile.
        persist_store()

    return _working_copy()


def import_app_config(loaded: dict) -> dict:
    """Replace the store from an imported document (any layout); nothing is written until save."""
    loaded = loaded if isinstance(loaded, dict) else {}
    if loaded.get("config_version") == CONFIG_VERSION and _build_store_v3(loaded):
        return _working_copy()
    _build_store(loaded)
    return _working_copy()


def export_document() -> dict:
    """The whole store as one portable v2 document; a single exported file restores everything on import."""
    return {
        "config_version": 2,
        "active_profile": _STORE["active"],
        "profiles": _deep_copy(_STORE["profiles"]),
    }


def persist_store() -> None:
    """Write the store: index in config.json, one file per profile; failures ignored so a read-only folder can't stop the app."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    entries = []
    used_files: set[str] = {"config.json"}
    for prof in _STORE["profiles"]:
        name = prof["name"]
        fname = _STORE["files"].get(name) or _profile_filename(name, used_files)
        _STORE["files"][name] = fname
        used_files.add(fname.lower())
        try:
            (CONFIG_DIR / fname).write_text(json.dumps(prof, indent=2) + "\n",
                                            encoding="utf-8")
        except OSError:
            pass
        entries.append({"name": name, "file": fname})
    # A renamed or deleted profile leaves its file; sweep it.
    # Only files this app wrote are tracked, so a hand-placed json
    # is never touched.
    for gone in [n for n in _STORE["files"]
                 if n not in {p["name"] for p in _STORE["profiles"]}]:
        stale = _STORE["files"].pop(gone)
        try:
            (CONFIG_DIR / stale).unlink(missing_ok=True)
        except OSError:
            pass
    index = {"config_version": CONFIG_VERSION,
             "active_profile": _STORE["active"],
             "profiles": entries}
    try:
        APP_CONFIG_FILE.write_text(json.dumps(index, indent=2) + "\n",
                                   encoding="utf-8")
    except OSError:
        pass


def save_app_config_static(config: dict) -> None:
    """Merge the working settings back into the active profile, then write."""
    merged = _normalize_values(_deep_copy(config))
    for i, prof in enumerate(_STORE["profiles"]):
        if prof["name"] == _STORE["active"]:
            merged["name"] = prof["name"]
            _STORE["profiles"][i] = merged
            break
    else:
        merged["name"] = _STORE["active"] or DEFAULT_PROFILE["name"]
        _STORE["active"] = merged["name"]
        _STORE["profiles"].append(merged)
    persist_store()


# --- command history (separate file, shared) ---

def load_command_history() -> list[str]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [str(item) for item in data if isinstance(item, str)]
    return []


def save_command_history(history: list[str]) -> None:
    """Persist the most recent entries, newest last."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history[-MAX_COMMAND_HISTORY:], indent=2), encoding="utf-8")
    except OSError:
        pass


###############################################################################
# was manager_widgets.py
# Reusable UI pieces: the themed toggles, the collapsible group, the file tree and the popup dialogs.
###############################################################################


import math
from collections.abc import Callable
import weakref
from string import Template

from PyQt6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QProgressBar,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionTab,
    QTabBar,
    QTabWidget,
    QToolTip,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from core.const import find_asset, scaled_sheet, zoom_size


# --- the bits of the look a widget paints for itself ---

#: Widgets this module handed a sheet written in theme tokens, so a theme
#: change can pour new colours into them too. Held weakly: a widget that goes
#: away takes its sheet with it, and nobody has to unregister it.
_themed: list[weakref.ref] = []


def theme_text(text: str) -> str:
    """``$token`` in a hand-written sheet becomes the colour or size the theme means."""
    values = {str(key): str(value) for key, value in
              {**ui.colors(), **ui.tabs()}.items()}
    return Template(str(text or "")).safe_substitute(**values)


def theme_style(widget: QWidget, sheet: str) -> QWidget:
    """Give one widget a small sheet of its own, written in the theme's tokens.

    The application stylesheet covers the standard parts of a widget; this is
    for the handful of rules a widget needs beyond them - a caption in the
    accent, a heading drawn flat - which would otherwise fix a colour that a
    theme exists to change.
    """
    if getattr(widget, "_theme_sheet", None) is None:
        _themed.append(weakref.ref(widget))
    widget._theme_sheet = str(sheet or "")
    return paint_theme_style(widget)


def paint_theme_style(widget: QWidget) -> QWidget:
    """Re-apply one widget's own sheet, at the zoom and from the colours in force."""
    sheet = str(getattr(widget, "_theme_sheet", "") or "")
    if sheet:
        widget.setStyleSheet(scaled_sheet(theme_text(sheet)))
    return widget


def refresh_theme_styles() -> int:
    """Re-paint every widget that carries a sheet of its own; says how many did."""
    alive: list[weakref.ref] = []
    for ref in _themed:
        widget = ref()
        if widget is None:
            continue
        alive.append(ref)
        try:
            paint_theme_style(widget)
        except RuntimeError:               # the C++ side went with the theme
            continue
    _themed[:] = alive
    return len(alive)


# --- tab captions and the pictures a crowded bar falls back on ---

#: What a tab spends on itself besides its text - the sheet's side padding,
#: its borders and the small gap before the icon - counted at 100% zoom, and
#: the number used if a theme cannot be read. Narrower than ``_TAB_MIN_TEXT``
#: a caption could not be read anyway: below it the tab drops its text
#: entirely and wears its icon alone.
_TAB_CHROME = 34
_TAB_MIN_TEXT = 24

#: The pictures this module paints itself, made once and kept: the bar
#: refits on every resize, and repainting the same glyphs each time is waste.
#: File-backed marks live here too, so an unreadable asset is tried once.
#: A theme change bumps the revision below, and every one is painted again.
_DRAWN_ICONS: dict[str, QIcon] = {}
_ICONS_REVISION = -1

#: Every tab picture is painted at least this wide (at 100% zoom), whatever the
#: bar's own icon size measures, when the theme asks for nothing: the tab keeps
#: the size the icon size gives it - only the drawn mark grows, so a bigger
#: picture never means a bigger tab.
_TAB_ICON_DRAW = 24


def tab_chrome() -> int:
    """What a tab spends on itself besides its text, from the theme in force."""
    return ui.chrome_px() or _TAB_CHROME


def tab_icon_side(bar: QTabBar) -> int:
    """The edge a tab's picture is drawn at: the bar's icon size, but never
    smaller than the mark the theme asks for. Zoomed with the rest of the bar."""
    return max(int(bar.iconSize().width()),
               zoom_size(ui.icon_px() or _TAB_ICON_DRAW))


def _drawn_icon(key: str, paint: Callable[[QPainter], None], filename: str = "") -> QIcon:
    """One tab picture, made once and remembered; a shipped asset wins, the paint glyph is the fallback so a bare checkout still draws something."""
    global _ICONS_REVISION
    if _ICONS_REVISION != ui.revision():
        # A different theme is in force: whatever was painted belongs to it.
        _DRAWN_ICONS.clear()
        _ICONS_REVISION = ui.revision()
    icon = _DRAWN_ICONS.get(key)
    if icon is None:
        path = find_asset(filename) if filename else None
        if path is not None:
            icon = QIcon(str(path))
        else:
            canvas = QPixmap(32, 32)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint(p)
            p.end()
            icon = QIcon(canvas)
        _DRAWN_ICONS[key] = icon
    return icon


def paint_default_tab(p: QPainter) -> None:
    """Four rounded tiles in the bar's grey with one accent corner.

    Drawn in the 32-unit space a tab picture is painted into, and shared with
    ``tools/make_store_icons.py``, which writes the same mark out as
    ``assets/defaulttabicon.png`` - so the file a person can edit and the drawing
    that stands in when it is gone cannot drift apart.
    """
    p.setPen(Qt.PenStyle.NoPen)
    for n, (x, y) in enumerate(((5.0, 5.0), (18.5, 5.0),
                                (5.0, 18.5), (18.5, 18.5))):
        p.setBrush(QColor(ui.color("accent")) if n == 0
                   else QColor(ui.color("icon_alt")))
        p.drawRoundedRect(QRectF(x, y, 8.5, 8.5), 2.2, 2.2)


def default_tab_icon() -> QIcon:
    """The mark for any tab that arrives without one: the shipped
    defaulttabicon.png, else the same four tiles drawn here."""
    return _drawn_icon("default_tab", paint_default_tab, "defaulttabicon.png")


def paint_terminal_tab(p: QPainter) -> None:
    """A shell frame with a raspberry prompt, in the 32-unit picture space."""
    frame = QPen(QColor(ui.color("icon_alt")))
    frame.setWidthF(2.4)
    p.setPen(frame)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.0, 6.0, 24.0, 20.0), 3.0, 3.0)
    prompt = QPen(QColor(ui.color("accent")))
    prompt.setWidthF(2.2)
    prompt.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(prompt)
    p.drawLine(QPointF(8.5, 12.5), QPointF(12.5, 16.0))
    p.drawLine(QPointF(12.5, 16.0), QPointF(8.5, 19.5))
    p.drawLine(QPointF(14.5, 19.5), QPointF(20.0, 19.5))


def terminal_tab_icon() -> QIcon:
    """The picture the SSH terminal tab wears: the shipped terminalicon.png,
    else the same shell frame drawn here."""
    return _drawn_icon("terminal_tab", paint_terminal_tab, "terminalicon.png")


def paint_settings_tab(p: QPainter) -> None:
    """A rim with eight short teeth, in the 32-unit picture space.

    At 18px anything longer reads as a sun, not a cog, which is why the stubs are
    short and the rim thick.
    """
    pen = QPen(QColor(ui.color("icon_tint")))
    pen.setWidthF(3.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawEllipse(QRectF(9.0, 9.0, 14.0, 14.0))
    for step in range(8):
        angle = math.radians(step * 45)
        dx, dy = math.cos(angle), math.sin(angle)
        p.drawLine(QPointF(16 + 8.2 * dx, 16 + 8.2 * dy),
                   QPointF(16 + 11.2 * dx, 16 + 11.2 * dy))


def settings_tab_icon() -> QIcon:
    """The cog the pinned Settings tab always wears: the shipped settingsicon.png,
    else the same rim and eight teeth drawn here. Kept at every width because
    Settings is never squeezed away."""
    return _drawn_icon("settings_tab", paint_settings_tab, "settingsicon.png")


def tab_caption(bar: QTabBar, index: int) -> str:
    """The full name a tab carries: the caption stored in its data (kept whole even when the bar elides the text), or its plain text."""
    try:
        stored = bar.tabData(index)
    except (IndexError, RuntimeError):
        stored = None
    if stored is not None and str(stored) != "":
        return str(stored)
    return bar.tabText(index)


def label_tab(bar: QTabBar, index: int, label: str, icon_only: bool = False,
              tooltip: str = "") -> None:
    """Name one tab, in the two places a name lives.

    A tab's full name always goes into its data, because that is what order
    lookups, the start-tab picker and the tooltip read; the caption in the tab
    itself is what the bar draws, and an icon-only tab simply leaves it blank.
    Only a pinned tab keeps a blank caption for good: the squeeze pass re-derives
    a movable tab's caption from its data whenever the bar re-measures.
    """
    if not 0 <= index < bar.count():
        return
    text = str(label or "")
    bar.setTabData(index, text)
    bar.setTabText(index, "" if icon_only else text)
    bar.setTabToolTip(index, str(tooltip or text))


#: Air between a tab's icon and its caption, and the give that keeps an
#: elided caption off the capsule's edge - both before zoom.
_TAB_LABEL_GAP = 6
_TAB_LABEL_SLACK = 4


def _draw_centered_tab_label(opt: QStyleOptionTab, painter: QPainter, bar: QTabBar) -> None:
    """Draw one tab's icon and caption as one centred block, holding out room for the side buttons; the styled bar would pin the icon left and read off-balance."""
    rect = opt.rect
    left = bar.tabButton(opt.tabIndex, QTabBar.ButtonPosition.LeftSide)
    right = bar.tabButton(opt.tabIndex, QTabBar.ButtonPosition.RightSide)
    pad = zoom_size(2)
    if left is not None and left.isVisible():
        rect = rect.adjusted(left.width() + pad, 0, 0, 0)
    if right is not None and right.isVisible():
        rect = rect.adjusted(0, 0, -(right.width() + pad), 0)
    has_icon = not opt.icon.isNull()
    # The mark draws bigger than the bar measures its icons (see
    # _TAB_ICON_DRAW): a clearer picture without a taller or wider tab.
    draw_side = tab_icon_side(bar) if has_icon else 0
    icon_w = draw_side
    gap = zoom_size(_TAB_LABEL_GAP) if has_icon and opt.text else 0
    # (QStyleOptionTab's font copy is invisible to PyQt; the bar's own font
    # is what initStyleOption put in there.)
    font = bar.font()
    metrics = QFontMetrics(font)
    avail = max(0, rect.width() - icon_w - gap - zoom_size(_TAB_LABEL_SLACK))
    text = metrics.elidedText(opt.text, bar.elideMode(), avail)
    text_w = min(avail, metrics.horizontalAdvance(text))
    total = icon_w + gap + text_w
    x = rect.left() + max(0, (rect.width() - total) // 2)
    middle = rect.center().y()
    painter.save()
    painter.setFont(font)
    if has_icon:
        pix = opt.icon.pixmap(QSize(draw_side, draw_side))
        painter.drawPixmap(x, middle - pix.height() // 2, pix)
        x += icon_w + gap
    if text:
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        if not enabled:
            color = QColor(ui.color("faint"))
        elif opt.state & QStyle.StateFlag.State_Selected:
            color = QColor(ui.color("text"))
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            color = QColor(ui.color("bright"))
        else:
            color = QColor(ui.color("muted"))
        painter.setPen(QPen(color))
        painter.drawText(x, rect.top(), text_w + zoom_size(_TAB_LABEL_SLACK),
                         rect.height(),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         text)
    painter.restore()


class RightPinnedTabBar(QTabBar):
    """A tab bar that keeps a run of tabs pinned to the far right edge.

    An empty, disabled *gap* tab is widened to soak up all the spare room, so
    the tabs that sit just after it - Settings and the system tabs beside it -
    are pushed against the right edge and read as detached from the reorderable
    tabs on the left. ``set_gap_index`` tells the bar which tab is the spacer and
    how many tabs follow it as a pinned block; their widths are recomputed from
    the bar's own width every time a tab moves or the window is resized, so the
    pinned run always ends up flush right and never takes part in the squeezing
    or the saved order.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gap_index = -1
        # How many tabs sit right of the gap and stay put: Settings alone until
        # the window says otherwise.
        self.pinned_count = 1
        self._refitting = False

    def set_gap_index(self, index: int, pinned: int = 1) -> None:
        old = self.gap_index
        self.gap_index = index
        self.pinned_count = max(1, int(pinned))
        # Whatever the old index points at now, it is not the gap any more -
        # and only the gap may sit disabled on this bar. Hand the tab that
        # inherited the position (a reorder can leave one sitting exactly
        # where the blink last struck) its clicks back.
        if 0 <= old < self.count() and old != index:
            self.setTabEnabled(old, True)
        self.refit()

    def pinned_indices(self) -> list[int]:
        """The tabs held at the right edge, left to right; empty until the block is pinned.

        Only the last ``pinned_count`` tabs count, so a tab the owner has not
        moved into the block yet is treated as movable rather than silently
        pinned.
        """
        if not (0 <= self.gap_index < self.count()):
            return []
        return list(range(self.count() - self.pinned_count, self.count()))

    def is_pinned(self, index: int) -> bool:
        """Whether this tab belongs to the pinned run (or is its gap)."""
        return index == self.gap_index or index in self.pinned_indices()

    def _movable_indices(self) -> list[int]:
        """Every tab the user may drag, which is everything but the gap and the pinned run."""
        pinned = set(self.pinned_indices())
        return [i for i in range(self.count())
                if i != self.gap_index and i not in pinned]

    def tabSizeHint(self, index: int) -> QSize:
        """Equalise the movable tabs to the widest, stretch the gap to soak spare room, and leave the pinned run at its own width."""
        natural = QTabBar.tabSizeHint(self, index)
        if not (0 <= self.gap_index < self.count()):
            return natural              # nothing pinned yet: leave tabs natural
        count = self.count()
        if index == self.gap_index:
            used = sum(self.tabSizeHint(i).width() for i in range(count)
                       if i != index)
            return QSize(max(0, self.width() - used), natural.height())
        if index in self.pinned_indices():
            return natural
        movable = self._movable_indices()
        pinned = self.pinned_indices()
        if movable:
            widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
            # Never let the movable tabs eat the room reserved for the pinned
            # run: the squeeze aims captions at an equal share, and this caps
            # the drawn width at that share even if the aim comes out a little
            # wide. The label paints centred into whatever rect results.
            reserved = sum(QTabBar.tabSizeHint(self, i).width() for i in pinned)
            share = max(1, (self.width() - 2 - reserved) // len(movable))
            return QSize(min(widest, share), natural.height())
        return natural

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.refit()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Paint every tab as the sheet's capsule plus a centred label; the bar takes over painting so the icon-and-caption block centres as one piece."""
        painter = QPainter(self)
        style = self.style()
        opt = QStyleOptionTab()
        for i in range(self.count()):
            if not self.tabRect(i).isValid():
                continue
            self.initStyleOption(opt, i)
            style.drawControl(QStyle.ControlElement.CE_TabBarTabShape,
                              opt, painter, self)
            _draw_centered_tab_label(opt, painter, self)
        painter.end()

    def refit(self) -> None:
        """Re-measure the bar and shorten the captions that no longer fit, dropping a tab to its icon when even an ellipsis leaves nothing readable."""
        if self._refitting:
            return
        self._refitting = True
        try:
            self._refit()
        finally:
            self._refitting = False
        self._relayout()

    def _refit(self) -> None:
        if not (0 <= self.gap_index < self.count()):
            return                      # nothing pinned yet: measure later
        if self.gap_index != self.count() - self.pinned_count - 1:
            # The pinned block is out of date - a tab went in or came out and
            # the owner has not re-pinned yet. Squeezing on the stale index
            # could treat a real pinned tab as a movable one, and the pinned
            # run must never be touched: leave the texts alone.
            return
        movable = self._movable_indices()
        if not movable:
            return
        # Only the gap ever sits disabled here; if a stale blink greyed a
        # real tab out earlier (see _relayout), this is where it heals.
        for i in range(self.count()):
            if i != self.gap_index and not self.isTabEnabled(i):
                self.setTabEnabled(i, True)
        # The pinned run wears no caption but always wears its mark, so it is
        # never left as an empty capsule; the movable tabs keep a picture too,
        # even when their name comes off later.
        for i in self.pinned_indices():
            if self.tabIcon(i).isNull():
                self.setTabIcon(i, default_tab_icon())
        for i in movable:
            if self.tabIcon(i).isNull():
                self.setTabIcon(i, default_tab_icon())
            # Measure from the full captions, whatever an earlier pass made
            # of them; the real names live in the tab's data, untouched.
            caption = tab_caption(self, i)
            if self.tabText(i) != caption:
                self.setTabText(i, caption)
        reserve = sum(QTabBar.tabSizeHint(self, i).width()
                      for i in self.pinned_indices())
        room = max(0, self.width() - 2 - reserve)
        if self.width() < zoom_size(50):
            return   # the bar has no real width yet (window still hidden
                     # or being built): restore only, never squeeze
        widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
        if widest * len(movable) <= room:
            return                      # everything still fits in full
        per = max(1, room // len(movable))
        metrics = QFontMetrics(self.font())
        # The chrome constant aims the caption at a share of the bar; the
        # sheet's real padding is never exactly that, so each pass measures
        # what the elided captions actually cost and shaves the next aim by
        # the overage. Two or three rounds settle; the loop cap is a brake.
        # The drawn mark can be wider than the bar's measured icon, and the
        # caption has to clear the picture as painted, not as measured.
        text_room = per - tab_icon_side(self) - zoom_size(tab_chrome())
        # A theme may say a tab never wears a name at all; that is decided
        # before any measuring, and the loop below takes the captions off.
        allow_captions = ui.shows_captions()
        for _ in range(4):
            icon_only = not allow_captions or text_room < zoom_size(_TAB_MIN_TEXT)
            for i in movable:
                caption = tab_caption(self, i)
                if icon_only:
                    self.setTabText(i, "")
                else:
                    text = metrics.elidedText(caption, Qt.TextElideMode.ElideRight,
                                              max(8, text_room))
                    self.setTabText(i, str(text))
                # Only a tab whose name was cut down explains itself on hover,
                # so one that still shows everything keeps whatever tooltip it
                # already had (the SSH tab's connection title among them).
                self.setTabToolTip(i, caption)
            if icon_only:
                break
            widest = max(QTabBar.tabSizeHint(self, i).width() for i in movable)
            if widest <= per:
                break
            text_room -= widest - per

    def _relayout(self) -> None:
        """Force the bar to re-run tabSizeHint by blinking the gap tab's enabled state - only when the index provably is the gap."""
        if 0 <= self.gap_index < self.count() \
                and self.gap_index == self.count() - self.pinned_count - 1:
            self.setTabEnabled(self.gap_index, True)
            self.setTabEnabled(self.gap_index, False)


class RightPinnedTabWidget(QTabWidget):
    """The main tab widget, announcing every add and remove.

    The pinned Settings tab depends on a gap whose index is stored in the bar;
    adding or removing a tab to the left of that gap shifts every later index,
    so the owner has to re-pin. This Qt build exposes no ``countChanged`` signal
    on the tab bar (only ``tabRemoved``), so the widget fires its own
    :data:`tabCountChanged` from the ``addTab``/``insertTab``/``removeTab``/
    ``clear`` calls the app actually makes. Qt never adds a tab to itself, so
    wrapping those Python entry points catches every change in the count."""

    tabCountChanged = pyqtSignal(int)

    def _announce(self) -> None:
        self.tabCountChanged.emit(self.count())

    def _capture(self, index: int, label: object) -> None:
        """Store the tab's full name in its data the moment it appears, so a squeezed label never reaches name lookups."""
        if 0 <= index < self.count() and str(label or "") != "":
            self.tabBar().setTabData(index, str(label))

    def addTab(self, widget: QWidget, label: str = "") -> int:
        index = super().addTab(widget, label)
        self._capture(index, label)
        self._announce()
        return index

    def insertTab(self, index: int, widget: QWidget, label: str = "") -> int:
        placed = super().insertTab(index, widget, label)
        self._capture(placed, label)
        self._announce()
        return placed

    def setTabText(self, index: int, text: str) -> None:
        super().setTabText(index, text)
        # A rename is a new caption, not a shortened one: refresh the data
        # and let the bar re-measure what every tab can now afford.
        if 0 <= index < self.count():
            self.tabBar().setTabData(index, str(text))
        self.tabBar().refit()

    def removeTab(self, index: int) -> None:
        super().removeTab(index)
        self._announce()

    def clear(self) -> None:
        super().clear()
        self._announce()


#: The three states a transport indicator can be in, each with the style object
#: name that colours its dot and the short word said beside it. ``on`` means the
#: transport is usable right now, ``ready`` that every switch is on while
#: nothing stands connected, ``off`` that a switch refuses it.
TRANSPORT_DOT: dict[str, tuple[str, str]] = {
    "on": ("statusDotOnline", "connected"),
    "ready": ("statusDotReady", "ready"),
    "off": ("statusDotOffline", "off"),
}


def transport_dot(dot: QLabel, mode: str) -> None:
    """Put one dot into its state; the colour comes from the app stylesheet, so
    the widget is repolished instead of painted."""
    name = TRANSPORT_DOT.get(mode, TRANSPORT_DOT["off"])[0]
    if dot.objectName() != name:
        dot.setObjectName(name)
        dot.style().unpolish(dot)
        dot.style().polish(dot)


class TabTransportDots(QWidget):
    """Two small status dots for a tab's label: 's' for SSH, 'f' for SFTP.

    Placed beside a tab's text in the tab bar so the live connection state is
    visible from whichever tab is showing. Each dot is coloured by the app's
    shared status-dot style (green usable, amber waiting, red refused) and
    carries a one-letter mark; a transport the tab's addon never declared has no
    dot at all, and :meth:`set_state` drives the rest from the same answer the
    addon's Settings row gives.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        # The dots are rebuilt whenever the zoom changes, so reading the zoom in
        # force here is enough to keep them in step with the tab's own text.
        layout.setContentsMargins(zoom_size(3), 0, zoom_size(3), 0)
        layout.setSpacing(zoom_size(2))
        self._dots: dict[str, tuple[QLabel, QLabel]] = {}
        for transport, letter in (("ssh", "s"), ("sftp", "f")):
            dot = QLabel("\u25CF")
            dot.setObjectName("statusDotOffline")
            dot.setToolTip(f"{transport.upper()}: not connected")
            mark = QLabel(letter)
            theme_style(mark, "color: $muted; font-size: 8pt;")
            layout.addWidget(dot)
            layout.addWidget(mark)
            self._dots[transport] = (dot, mark)

    def set_state(self, states: dict[str, str]) -> None:
        """Colour each dot from one owner's transport states (``on``, ``ready``
        or ``off``); a transport missing from the dict is not used at all, so
        its mark and dot leave the tab's label. A theme that switches the dots
        off takes them all away, whatever the states say."""
        worn = ui.shows_dots()
        for transport, (dot, mark) in self._dots.items():
            mode = states.get(transport) if worn else None
            dot.setVisible(mode is not None)
            mark.setVisible(mode is not None)
            if mode is None:
                continue
            transport_dot(dot, mode)
            word = TRANSPORT_DOT.get(mode, TRANSPORT_DOT["off"])[1]
            dot.setToolTip(f"{transport.upper()}: {word}")


class TabCloseButton(QToolButton):
    """The X worn by a tab that opened itself: an addon's page, or a page's own.

    The app's tabs and the profile's configurable tabs are never given one -
    closing those by hand would only confuse - so this widget appears on the
    tabs that have nowhere to be saved and are expected to go away.

    It is painted instead of styled because the app sheet hands every
    ``QToolButton`` ``padding: 6px 12px`` along with its border and background,
    and a button's own sheet only replaces the properties it spells out. Inside
    a sixteen-pixel square that padding leaves no content rect at all, so a
    *typed* mark is clipped to nothing: the button is there and clickable and
    draws no pixel. Painting in ``paintEvent`` ignores the style's content rect
    entirely, and needs no font to carry a glyph either.
    """

    NAME = "tabCloseButton"  # what tells the transport-dot pass to spare it
    SIDE = 18          # the box's edge at 100% zoom
    INK = "close_ink"  # the token the mark is drawn in
    HOT = "close_hot"  # and the one it takes up under the pointer

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The name is what tells the transport-dot pass to leave this button in
        # place: it strips every other widget sitting in a tab's right slot, and
        # asks for this one by the name below rather than by a copy of it.
        self.setObjectName(self.NAME)
        self.setAccessibleName("Close tab")
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Close this tab")

    def sizeHint(self) -> QSize:
        """The box at the zoom the window is drawn at; the tab measures this to hold its caption clear of the mark."""
        side = zoom_size(self.SIDE)
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event: QEvent) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = min(self.width(), self.height())
        # The strokes cross over a square a little under half the box, and are
        # drawn as thick in proportion: a hairline X of that size vanishes into
        # the capsule's border, while a mark past this reads as a smudge.
        mark = box * 0.44
        left = (self.width() - mark) / 2.0
        top = (self.height() - mark) / 2.0
        pen = QPen(QColor(ui.color(
            self.HOT if self.underMouse() or self.isDown() else self.INK)))
        pen.setWidthF(max(1.5, box * 0.11))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(left, top), QPointF(left + mark, top + mark))
        painter.drawLine(QPointF(left + mark, top), QPointF(left, top + mark))


class ToggleSwitch(QCheckBox):
    """A pill-shaped on/off switch that replaces checkboxes in Settings,
    painted in the raspberry accent colours of the app theme."""

    TRACK_W = 40
    TRACK_H = 20

    def _track(self) -> tuple[int, int]:
        """The pill's size at the current zoom; painted not styled, so the hand-written sizes are asked of core.const."""
        return zoom_size(self.TRACK_W), zoom_size(self.TRACK_H)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._handle = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"handle_pos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._handle)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        """Set the state and move the painted handle too, so a blocked-signals rebuild still shows the switch on."""
        if self.signalsBlocked():
            self._anim.stop()
            self._handle = 1.0 if bool(checked) else 0.0
            self.update()
        super().setChecked(bool(checked))

    def _get_handle(self) -> float:
        return self._handle

    def _set_handle(self, value: float) -> None:
        self._handle = float(value)
        self.update()

    handle_pos = pyqtProperty(float, fget=_get_handle, fset=_set_handle)

    def minimumSizeHint(self) -> QSize:
        fm = self.fontMetrics()
        track_w, track_h = self._track()
        width = track_w
        if self.text():
            width += zoom_size(8) + fm.horizontalAdvance(self.text())
        return QSize(width, max(track_h, fm.height()) + zoom_size(4))

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track_w, track_h = self._track()
        track_top = (self.height() - track_h) / 2
        if on:
            track = QColor(ui.color("accent"))
            border = QColor(ui.color("accent_edge"))
        else:
            track = QColor(ui.color("toggle_off"))
            border = QColor(ui.color("raise_edge"))
        if not enabled:
            track.setAlpha(120)
            border.setAlpha(120)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(track))
        # The handle is a fixed share of the track - 65% of the radius, which is
        # what the old hand-written 3.5 px inset came to - so it keeps its shape
        # at every zoom instead of growing a border of its own.
        radius = track_h / 2
        painter.drawRoundedRect(
            QRectF(0.5, track_top + 0.5, track_w - 1, track_h - 1), radius, radius
        )
        handle_r = radius * 0.65
        travel = track_w - track_h
        center_x = radius + self._handle * travel
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ui.color(
            "toggle_handle" if enabled else "toggle_handle_off")))
        painter.drawEllipse(
            QRectF(center_x - handle_r, track_top + radius - handle_r, handle_r * 2, handle_r * 2)
        )
        if self.text():
            color = self.palette().color(self.palette().ColorRole.WindowText)
            if not enabled:
                color.setAlpha(140)
            painter.setPen(QPen(color))
            gap = zoom_size(8)
            text_rect = QRectF(track_w + gap, 0, self.width() - track_w - gap, self.height())
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )


class _TipBubble(QLabel):
    """One frameless panel that fades in near the cursor and holds a tool-tip.

    Qt's own tool-tip pops on at a spot it never leaves, and only if the pointer
    rests still long enough; this is a plain top-level label whose opacity is
    animated, so a tip arrives softly and a timer can then walk it along.

    The card paints itself - the theme's panel colour under an accent edge, the
    same pair the progress notice wears - because a translucent frameless window
    leans on nothing else for its ground. The words come in flat: runs of
    whitespace are one space, the wrap never passes four lines, and what does
    not fit is dropped whole-word with an ellipsis, so a paragraph-length tip
    cannot follow the cursor around as a wall of text.
    """

    MAX_LINES = 4         # the most the bubble will ever say
    MAX_WIDTH = 360       # at this width (100% zoom)...
    MIN_WIDTH = 120       # ... and never narrower than a readable chip
    SIDE_PAD = 10         # the words' margin inside the panel
    TOP_PAD = 7
    RADIUS = 6

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # The card walks the cursor, so it would otherwise end up under it: as
        # transparent to input, the pointer keeps reporting the widget beneath,
        # a click lands where the user aimed it, and the card never steals a tip.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(130)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show_tip(self, text: str) -> None:
        words = " ".join(str(text or "").split())
        fm = self.fontMetrics()
        room = zoom_size(self.MAX_WIDTH) - 2 * zoom_size(self.SIDE_PAD)
        bound = self._bound(fm, words, room)
        if self._lines(fm, bound.height()) > self.MAX_LINES:
            words = self._trim(fm, words, room)
            bound = self._bound(fm, words, room)
        lines = min(self.MAX_LINES,
                    max(1, self._lines(fm, bound.height())))
        self.setText(words)
        self.setFixedSize(
            max(zoom_size(self.MIN_WIDTH),
                min(zoom_size(self.MAX_WIDTH),
                    bound.width() + 2 * zoom_size(self.SIDE_PAD))),
            lines * fm.lineSpacing() + 2 * zoom_size(self.TOP_PAD))
        self._fade_to(1.0)
        self.show()

    @staticmethod
    def _bound(fm: QFontMetrics, words: str, room: int) -> QRect:
        """The box the words need when wrapped inside ``room``."""
        return fm.boundingRect(
            QRect(0, 0, room, 100 * fm.lineSpacing()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap), words)

    @staticmethod
    def _lines(fm: QFontMetrics, height: int) -> int:
        """How many wrapped lines a measured box holds; the measurement lands
        a pixel or two off an exact multiple, which rounds back here."""
        step = max(1, fm.lineSpacing())
        return max(1, int(round(height / step)))

    @classmethod
    def _trim(cls, fm: QFontMetrics, words: str, room: int) -> str:
        """Drop whole words from the end until the rest fits the line budget,
        and mark the cut with an ellipsis."""
        said = words.split()
        while said:
            said.pop()
            trial = " ".join(said) + " \u2026"
            if cls._lines(fm, cls._bound(fm, trial, room).height()) \
                    <= cls.MAX_LINES:
                return trial
        return "\u2026"                    # one word longer than four lines

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(ui.color("accent")), 1))
        painter.setBrush(QColor(ui.color("surface")))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                                zoom_size(self.RADIUS),
                                zoom_size(self.RADIUS))
        painter.setPen(QColor(ui.color("text")))
        pad_x, pad_y = zoom_size(self.SIDE_PAD), zoom_size(self.TOP_PAD)
        painter.drawText(self.rect().adjusted(pad_x, pad_y, -pad_x, -pad_y),
                         int(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignTop
                             | Qt.TextFlag.TextWordWrap), self.text())
        painter.end()

    def hide_tip(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self.setWindowOpacity(0.0)
        self.hide()

    def _fade_to(self, end: float, then_hide: bool = False) -> None:
        self._fade.stop()
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(end)
        self._fade.start()


class _SmartTip(QObject):
    """The one tool-tip keeper for the whole window.

    Installed as an application event filter, it answers ``ToolTip`` events
    wherever they come from: any widget carrying text gets a bubble that fades
    in and walks along under the cursor, and the native tool-tip never appears.
    A widget with no text of its own is left to Qt, so menus and other built-in
    tips keep working.

    Exactly one tip is on screen at a time, which is what the two paths make
    awkward: Qt's own tip is raised by whatever this filter hands back, and a
    child with no tool-tip of its own inherits its parent's - so hovering a
    button inside a group box could raise the native tip while this keeper's
    bubble was still up. Showing the bubble therefore takes the native one down
    first, and a tip this keeper cannot serve dismisses the bubble instead of
    overlapping it. Switched off, nothing is raised at all and no native tip is
    let through either.
    """

    GAP = 16          # the bubble's corner sits this far off the pointer

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._bubble = _TipBubble()
        self._target = None
        self._region = None
        self._on = True
        self._timer = QTimer(self._bubble)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    # --- on and off ---

    def enabled(self) -> bool:
        """Whether tool-tips are being shown at all."""
        return self._on

    def set_enabled(self, on: bool) -> None:
        """Show tool-tips, or hide every one of them - this bubble and Qt's."""
        self._on = bool(on)
        if not self._on:
            self.dismiss()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._bubble:
            return False
        etype = event.type()
        if etype == QEvent.Type.ToolTip:
            return self._on_tooltip(watched, event)
        if etype in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel,
                     QEvent.Type.WindowDeactivate):
            self.dismiss()
        return False

    def _on_tooltip(self, watched: QObject, event: QEvent) -> bool:
        if not self._on:
            # Switched off means off: the native tip is stopped here too, or it
            # would carry on appearing for everything this keeper used to draw.
            self.dismiss()
            return True
        text = self._text_for(watched, event)
        if not text:
            # Qt may raise its own tip for this widget. Take the bubble down
            # first, so the two are never on screen together.
            if self.showing():
                self.dismiss()
            return False
        self.show_at(text, QCursor.pos(), watched)
        return True

    @staticmethod
    def _text_for(widget: QObject, event: QEvent) -> str:
        # A tab bar keeps one tip per tab, not on the bar itself.
        if isinstance(widget, QTabBar) and hasattr(event, "pos"):
            index = widget.tabAt(event.pos())
            return str(widget.tabToolTip(index) or "") if index >= 0 else ""
        getter = getattr(widget, "toolTip", None)
        text = str(getter() or "") if callable(getter) else ""
        if text:
            return text
        # What the event itself carries is the only other words worth showing:
        # a tip set per item, or one Qt worked out for this widget. Riding the
        # bubble keeps it a single tip; leaving it would let it arrive as the
        # plain native one, beside any bubble already on screen.
        return str(getattr(event, "text", lambda: "")() or "")

    def show_at(self, text: str, global_pos, target, region=None) -> None:
        """Fade the bubble in on ``text``, near ``global_pos``, tied to ``target``.

        ``region`` is an optional ``(widget, global_pos) -> bool`` that says
        which part of the target still counts as hovering it - a group box shows
        its tip over the title words only.
        """
        # Only one tip at a time, and it is this one: Qt's own may be sitting
        # under the cursor already, raised by a widget this filter passed on.
        QToolTip.hideText()
        self._target = target
        self._region = region
        self._bubble.show_tip(text)
        self._place(global_pos)
        self._timer.start()

    def dismiss(self) -> None:
        """Stop following and fade the bubble away."""
        self._timer.stop()
        self._target = None
        self._region = None
        self._bubble.hide_tip()

    def showing(self) -> bool:
        """Whether a tip is on screen right now."""
        return self._bubble.isVisible()

    def current_text(self) -> str:
        """The words the bubble is holding, empty when it shows nothing."""
        return str(self._bubble.text() or "")

    def _tick(self) -> None:
        try:
            target = self._target
            if target is None:
                self.dismiss()
                return
            pos = QCursor.pos()
            under = self._app.widgetAt(pos)
            if under is not None and under is not target \
                    and self._descendant(under, self._bubble):
                # The card, or its shadow, is what the pointer is over. It is
                # transparent to input, so this is the window manager's doing
                # and not the user's: hold the tip up and keep following.
                pass
            elif under is None or not self._descendant(under, target):
                self.dismiss()
                return
            if self._region is not None and not self._region(target, pos):
                self.dismiss()
                return
            self._place(pos)
        except RuntimeError:                # the widget went away mid-hover
            self.dismiss()

    def _place(self, pos) -> None:
        """Put the card beside the pointer, off whatever it can avoid covering.

        A tip that sits on the control being pointed at is worse than no tip: it
        hides the thing the pointer is on and the words beside it. So the four
        corners the card could take are looked over in the usual order and the
        first that fits the screen and leaves the target widget untouched wins;
        failing that the first that merely fits is used. The order is fixed
        rather than the smallest overlap, because the card is re-placed every
        few dozen milliseconds and a choice that moves between corners as the
        pointer travels reads as a jump rather than as care.
        """
        bubble = self._bubble
        size = bubble.size()
        screen = self._app.screenAt(pos) or self._app.primaryScreen()
        area = screen.availableGeometry()
        gap = zoom_size(self.GAP)
        clear = self._target_rect()
        right = pos.x() + gap
        left = pos.x() - size.width() - gap
        below = pos.y() + gap
        above = pos.y() - size.height() - gap
        spots = ((right, below), (right, above), (left, below), (left, above))
        cards = [QRect(x, y, size.width(), size.height()) for x, y in spots]
        for card in cards:
            if area.contains(card) and not self._covers(card, clear):
                bubble.move(card.topLeft())
                return
        for card in cards:
            if area.contains(card):
                bubble.move(card.topLeft())
                return
        # Nothing fits - the card is by a screen edge. Keep it on screen at the
        # usual place rather than push it off the display.
        bubble.move(min(max(area.left(), right),
                        max(area.left(), area.right() - size.width())),
                    min(max(area.top(), below),
                        max(area.top(), area.bottom() - size.height())))

    @staticmethod
    def _covers(card: QRect, clear: QRect | None) -> bool:
        """Whether the card would sit on the widget being pointed at.

        ``QRect`` has no ``area()`` to ask, and a corner that misses the widget
        hands back a null rectangle, so the sides of the overlap are read with
        the sign taken off: nothing overlapping is zero. A widget too big for
        the card to get round still counts as covered - there is no corner that
        does not touch it - and the caller falls back to the plain ordering.
        """
        if clear is None:
            return False
        overlap = card.intersected(clear)
        return max(0, overlap.width()) * max(0, overlap.height()) > 0

    def _target_rect(self) -> QRect | None:
        """Where the widget being pointed at sits on the desktop, or nothing."""
        target = self._target
        if target is None:
            return None
        try:
            top = target.mapToGlobal(QPoint(0, 0))
            return QRect(top.x(), top.y(), target.width(), target.height())
        except RuntimeError:                # the widget went away mid-hover
            return None

    @staticmethod
    def _descendant(widget, ancestor) -> bool:
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False


#: The one keeper, made when :func:`install_smart_tooltips` runs.
_SMART_TIP: "_SmartTip | None" = None


def install_smart_tooltips(app: QApplication) -> "_SmartTip":
    """Give the whole window the cursor-following, fading tool-tip; call it once.

    The native ``QToolTip`` is replaced for every widget that carries text - and
    for a group box's title, which :class:`_TitleTip` routes through here too.
    """
    global _SMART_TIP
    _SMART_TIP = _SmartTip(app)
    app.installEventFilter(_SMART_TIP)
    return _SMART_TIP


def smart_tip() -> "_SmartTip | None":
    """The tool-tip keeper, if one has been installed."""
    return _SMART_TIP


def set_tooltips_enabled(on: bool) -> bool:
    """Show tool-tips, or hide them; says whether there was a keeper to ask.

    Off stops the bubble and the native ``QToolTip`` both, so the switch in
    Settings means the same thing over every widget in the window.
    """
    engine = _SMART_TIP
    if engine is None:
        return False
    engine.set_enabled(bool(on))
    return True


def tooltips_enabled() -> bool:
    """Whether tool-tips are being shown; True when no keeper is installed."""
    engine = _SMART_TIP
    return True if engine is None else engine.enabled()


class ProgressNotice(QFrame):
    """A small card that floats over the window's corner and keeps a live job's
    percentage on screen while it runs.

    A tray toast says one thing and leaves; a job like a store install wants the
    opposite - a number that moves while the work does, from a window the person
    may have pushed behind anything else. So this is a frameless top-level card:
    who is speaking, how far the job has got as a bar with its percent, and one
    line of what it is doing now. The app keeps one per speaker and re-shows it
    on every step; nothing here waits on the addon that owns it.

    A notice that reaches its end holds for a few seconds and goes away on its
    own, so the finish line is read rather than chased; a new job takes the same
    card back before it closes. It never takes focus: ``WA_ShowWithoutActivating``
    keeps the pointer where the user left it.
    """

    HOLD_MS = 4000      # a finished notice stays on screen this long
    GAP = 18            # the card's margin from the window's corner

    def __init__(self, win: QWidget) -> None:
        super().__init__(win, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        column = QVBoxLayout(self)
        column.setContentsMargins(zoom_size(12), zoom_size(10),
                                  zoom_size(12), zoom_size(10))
        self._title = QLabel(self)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setFormat("%p%")
        self._bar.setTextVisible(True)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        theme_style(self._title, "QLabel { color: $bright; font-weight: 700; }")
        theme_style(self._text, "QLabel { color: $muted; }")
        theme_style(self, "QFrame { background-color: $surface; border: 1px"
                          " solid $accent; border-radius: 8px; }"
                          " QProgressBar { border: 1px solid $border;"
                          " border-radius: 5px; background-color: $soft;"
                          " text-color: $text; height: 16px; }"
                          " QProgressBar::chunk { border-radius: 4px;"
                          " background-color: $accent; }")
        self._closer: QTimer | None = None
        self.setMaximumWidth(zoom_size(340))
        self.setMinimumWidth(zoom_size(220))

    def show_notice(self, title: str, text: str, value: int,
                    maximum: int) -> None:
        """Put the card up with this figure in it; a full bar closes it soon."""
        limit = max(1, int(maximum))
        shown = max(0, min(limit, int(value)))
        self._bar.setMaximum(limit)
        self._bar.setValue(shown)
        self._title.setText(str(title or ""))
        self._text.setText(str(text or ""))
        self._text.setVisible(bool(str(text or "").strip()))
        self._cancel_close()
        self.adjustSize()
        self._place()
        self.show()
        # A window manager is free to ignore where a window was put before it
        # was ever mapped, which leaves the card sitting in the middle of the
        # screen; placing again once it is visible pins it to the corner the
        # first place only aimed at.
        self._place()
        if shown >= limit:
            self._hold_then_close()

    def close_notice(self) -> None:
        """Take the card away now, timer and all."""
        self._cancel_close()
        self.hide()

    def _cancel_close(self) -> None:
        if self._closer is not None:
            self._closer.stop()
            self._closer = None

    def _hold_then_close(self) -> None:
        self._cancel_close()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self.close_notice)
        timer.start(self.HOLD_MS)
        self._closer = timer

    def _place(self) -> None:
        """Sit the card inside the parent window's bottom-right corner."""
        win = self.parentWidget()
        if win is None:
            return
        gap = zoom_size(self.GAP)
        spot = win.mapToGlobal(QPoint(win.width() - self.width() - gap,
                                      win.height() - self.height() - gap))
        self.move(max(0, spot.x()), max(0, spot.y()))


class _TitleTip(QObject):
    """Answers tool-tip events for a group box, but only over its title.

    A QGroupBox is handed the tool-tip event for every pixel it covers, and any
    child without a tool-tip of its own inherits the parent's, so a note set on
    the panel followed the mouse across all the options inside it. The box is
    left with no tool-tip and this filter shows the text only while the pointer
    rests on the words of the title, which sit on the top border. When the
    cursor-following bubble is installed it drives that; otherwise the plain
    ``QToolTip`` is used, so a bare widget test still shows the note.
    """

    LEFT = 12     # the "left: 12px" of QGroupBox::title in the app stylesheet
    PAD = 6       # and its "padding: 0 6px"
    BAND = 24     # the title is drawn across the border, so a thin strip

    def __init__(self, text: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.text = text

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip or not isinstance(watched, QGroupBox):
            return False
        # A tool-tip event is a QHelpEvent, not a mouse event: it carries no
        # globalPosition(), only pos(), which is already relative to the box the
        # event was sent to. So the local point is taken as it stands and the
        # global one the tool-tip wants is the box's own mapping of it.
        where = event.pos()
        engine = _SMART_TIP
        if engine is not None and not engine.enabled():
            # Tool-tips are switched off in Appearance: a title note goes with
            # them, rather than arriving as the native tip behind their back.
            return True
        if self._over_title(watched, where):
            if engine is not None:
                engine.show_at(self.text, QCursor.pos(), watched,
                               region=self._region_over)
            else:
                QToolTip.showText(watched.mapToGlobal(where), self.text, watched)
        elif engine is not None:
            engine.dismiss()
        else:
            QToolTip.hideText()
        return True

    @classmethod
    def _region_over(cls, box, global_pos) -> bool:
        """Still over the title? the bubble asks this as the cursor walks."""
        return cls._over_title(box, box.mapFromGlobal(global_pos))

    @classmethod
    def _over_title(cls, box: QGroupBox, point: QPoint) -> bool:
        # The same title geometry the app stylesheet spells out, at the zoom the
        # window is currently drawn at.
        left, pad, band = zoom_size(cls.LEFT), zoom_size(cls.PAD), zoom_size(cls.BAND)
        if not 0 <= point.y() <= band:
            return False
        right = left + box.fontMetrics().horizontalAdvance(box.title()) + pad
        return left - pad <= point.x() <= right


def tip_on_title(box: QGroupBox, text: str) -> QGroupBox:
    """Attach text to a group box, shown only over its title (box.setToolTip covers the whole panel)."""
    box.setToolTip("")
    box.installEventFilter(_TitleTip(text, box))
    return box


class ToggleGroup(QGroupBox):
    """A settings category headed by a ToggleSwitch instead of the checkable
    group-box checkbox. Keeps the old isChecked/setChecked/toggled interface
    so the Settings code does not have to care which kind it got."""

    def __init__(self, title: str, checked: bool, body: str = "box", tip: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 12, 10)
        outer.setSpacing(6)
        header = QHBoxLayout()
        caption = QLabel(title)
        theme_style(caption, "color: $accent; font-weight: 600;")
        header.addWidget(caption)
        header.addStretch(1)
        self.switch = ToggleSwitch(parent=self)
        self.switch.setChecked(bool(checked))
        header.addWidget(self.switch)
        if tip:
            # The heading and its switch, never the group itself: a tool-tip on
            # the panel would pop up wherever the mouse rested, including over
            # every option the body holds. See _TitleTip for the same rule on
            # panels that have no switch.
            caption.setToolTip(tip)
            self.switch.setToolTip(tip)
        outer.addLayout(header)
        body_widget = QWidget()
        self.body_widget = body_widget
        if body == "form":
            self.body_layout = QFormLayout(self.body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
            outer.setContentsMargins(22, 8, 12, 10)
        else:
            self.body_layout = QVBoxLayout(self.body_widget)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(8)
        body_widget.setVisible(self.switch.isChecked())
        self.switch.toggled.connect(body_widget.setVisible)
        outer.addWidget(body_widget)
        # Old checkable-groupbox interface used across the Settings tab.
        self.toggled = self.switch.toggled
        self.isChecked = self.switch.isChecked
        self.setChecked = self.switch.setChecked


class CollapseGroup(QGroupBox):
    """A settings category whose contents fold away behind its heading.

    :class:`ToggleGroup` switches what it holds on and off; this one only decides
    whether you can see it, and starts folded so a group nobody looks at daily
    takes up no room. The heading is a flat tool button carrying the arrow Qt
    uses in a tree: pointing right while closed, down while open.
    """

    def __init__(self, title: str, collapsed: bool = True, body: str = "box",
                 tip: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 12, 10)
        outer.setSpacing(6)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(not collapsed)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setAutoRaise(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        theme_style(
            self.header,
            # Every state flat: the app sheet paints a QToolButton as a raised
            # box and its :checked state in the accent colour, which would turn
            # an open category into a button. A sheet on the widget itself
            # outranks the app's, so the heading reads as a caption with an
            # arrow, in the same colour as any other category title.
            "QToolButton, QToolButton:hover, QToolButton:pressed,"
            " QToolButton:checked, QToolButton:selected {"
            " background-color: transparent; border: none; padding: 0; }"
            "QToolButton { color: $accent; font-weight: 600; }"
        )
        if tip:
            # Only the heading is asked about: the body holds the information the
            # note describes, and a tool-tip on the panel would follow the mouse
            # across all of it (see _TitleTip for the same rule on plain groups).
            self.header.setToolTip(tip)
        self.header.toggled.connect(self._set_open)
        self._set_arrow(self.header.isChecked())
        outer.addWidget(self.header)
        self.body_widget = QWidget()
        if body == "form":
            self.body_layout = QFormLayout(self.body_widget)
            self.body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            self.body_layout.setHorizontalSpacing(12)
            self.body_layout.setVerticalSpacing(8)
        else:
            self.body_layout = QVBoxLayout(self.body_widget)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(8)
        self.body_widget.setVisible(self.header.isChecked())
        outer.addWidget(self.body_widget)
        self.isOpen = self.header.isChecked
        self.setOpen = self.header.setChecked

    def _set_arrow(self, open_: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if open_
                                 else Qt.ArrowType.RightArrow)

    def _set_open(self, open_: bool) -> None:
        self._set_arrow(open_)
        self.body_widget.setVisible(open_)


def make_file_tree(title: str) -> QTreeWidget:
    """A file list with the columns, selection mode and look both file browsers use; title labels the first column."""
    tree = QTreeWidget()
    tree.setHeaderLabels([title, "Size", "Modified"])
    tree.setRootIsDecorated(True)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
    tree.setColumnWidth(1, 90)
    tree.setColumnWidth(2, 140)
    tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)
    return tree


class StringListDialog(QDialog):
    """Editor for a list of strings (used for ignore patterns and post-deploy commands).

    Values are only read back through values() when the dialog is accepted, and
    blank lines are dropped there rather than blocked on entry.
    """

    def __init__(self, title: str, label: str, items: list[str], parent: QWidget | None = None, monospace: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.list_widget = QListWidget()
        for item in items:
            self.list_widget.addItem(item)
        if monospace:
            font = QFont("Consolas", 10)
            font.setStyleHint(QFont.StyleHint.Monospace)
            self.list_widget.setFont(font)
        layout.addWidget(self.list_widget)
        # Add / edit / remove act on the current or selected rows.
        row = QHBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self.add_item)
        edit_button = QPushButton("Edit selected")
        edit_button.clicked.connect(self.edit_selected)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        row.addWidget(add_button)
        row.addWidget(edit_button)
        row.addWidget(remove_button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_item(self) -> None:
        text, ok = QInputDialog.getText(self, "Add entry", "Value:")
        if ok and text.strip():
            self.list_widget.addItem(text.strip())

    def edit_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "Edit entry", "Value:", text=item.text())
        if ok:
            item.setText(text.strip())

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list[str]:
        """The list as typed, minus anything that is only whitespace."""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]


class TabEditDialog(QDialog):
    """Editor for one configurable web tab (display name, ip, port, path)."""

    def __init__(self, parent: QWidget | None = None, tab: dict | None = None, title: str = "Web tab") -> None:
        super().__init__(parent)
        tab = tab or {}
        self.setWindowTitle(title)
        layout = QFormLayout(self)
        self.name_input = QLineEdit(str(tab.get("name", "")))
        self.name_input.setPlaceholderText("e.g. LuCI")
        layout.addRow("Display name", self.name_input)
        self.ip_input = QLineEdit(str(tab.get("ip", "")))
        self.ip_input.setPlaceholderText("IP or hostname (leave blank to use profile host)")
        self.ip_input.setToolTip(
            "Address this tab opens. Empty means follow the connected profile's host, so the "
            "tab updates whenever you switch profiles."
        )
        layout.addRow("IP / host", self.ip_input)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        try:
            self.port_input.setValue(int(tab.get("port", 80)))
        except (TypeError, ValueError):
            self.port_input.setValue(80)
        layout.addRow("Port", self.port_input)
        self.path_input = QLineEdit(str(tab.get("path", "/")))
        self.path_input.setPlaceholderText("/")
        layout.addRow("Path", self.path_input)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItems(["http", "https"])
        self.scheme_combo.setCurrentText(str(tab.get("scheme", "http")).lower() or "http")
        layout.addRow("Protocol", self.scheme_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict:
        path = self.path_input.text().strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        return {
            "name": self.name_input.text().strip(),
            "ip": self.ip_input.text().strip(),
            "port": self.port_input.value(),
            "path": path,
            "scheme": self.scheme_combo.currentText(),
        }


# --- the colour wheel: what api.ui.pick_color and a "color" field open ---

#: The hue/saturation picture at full value, built once per size and kept -
#: repainting 40 000 pixels on every drag would be pure waste.
_WHEEL_DISCS: dict[int, QPixmap] = {}


class _ColorWheel(QWidget):
    """An HSV disc: the angle is the hue, the distance from the centre the
    saturation; brightness comes from the slider beside it.

    The value is a black veil laid over the full-strength disc with
    ``SourceAtop`` - the same maths HSV does behind it - and that blend
    leaves the corners outside the circle transparent."""

    def __init__(self, parent: QWidget | None = None, side: int = 200) -> None:
        super().__init__(parent)
        self._side = zoom_size(side)
        self.setFixedSize(self._side, self._side)
        self._hue = 0.0        # 0..1
        self._sat = 0.0        # 0..1
        self._value = 1.0      # 0..1
        self.on_change = None  # callback, handed the new QColor

    def set_hsv(self, hue: float, sat: float, value: float) -> None:
        self._hue, self._sat, self._value = hue, sat, value
        self.update()

    def hsv(self) -> tuple:
        return self._hue, self._sat, self._value

    def set_colour(self, colour: QColor) -> None:
        """Point the wheel at a colour; a grey one keeps the current hue."""
        hue, sat, value, _a = colour.getHsvF()
        if hue < 0.0:
            hue = self._hue
        self.set_hsv(hue, max(0.0, sat), max(0.0, value))

    def colour(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._sat, self._value)

    @staticmethod
    def _disc(side: int) -> QPixmap:
        cached = _WHEEL_DISCS.get(side)
        if cached is not None:
            return cached
        img = QImage(side, side, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        radius = side / 2.0
        for y in range(side):
            dy = radius - y - 0.5              # centre-based, up positive
            for x in range(side):
                dx = x + 0.5 - radius
                dist = math.hypot(dx, dy)
                if dist > radius:
                    continue
                hue = math.degrees(math.atan2(dy, dx)) % 360.0
                sat = min(1.0, dist / radius)
                img.setPixelColor(x, y, QColor.fromHsvF(hue / 360.0, sat, 1.0))
        disc = QPixmap.fromImage(img)
        _WHEEL_DISCS[side] = disc
        return disc

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self._side / 2.0
        painter.drawPixmap(0, 0, self._disc(self._side))
        if self._value < 1.0:
            painter.setCompositionMode(QPainter.CompositionMode.SourceAtop)
            painter.fillRect(self.rect(), QColor(
                0, 0, 0, int(round(255 * (1.0 - self._value)))))
            painter.setCompositionMode(QPainter.CompositionMode.SourceOver)
        angle = self._hue * 2.0 * math.pi
        mx = radius + radius * self._sat * math.cos(angle)
        my = radius - radius * self._sat * math.sin(angle)
        ring = max(6.0, self._side * 0.045)
        pen = QPen(QColor("#ffffff") if self._value < 0.6 else QColor("#202020"))
        pen.setWidthF(max(1.5, self._side / 100.0))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(mx, my), ring, ring)

    def _pick(self, pos: QPointF) -> None:
        radius = self._side / 2.0
        dx = pos.x() + 0.5 - radius
        dy = radius - pos.y() - 0.5
        self._hue = (math.degrees(math.atan2(dy, dx)) % 360.0) / 360.0
        self._sat = min(1.0, math.hypot(dx, dy) / radius)
        self.update()
        if callable(self.on_change):
            self.on_change(self.colour())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position())


class ColorWheelDialog(QDialog):
    """A colour wheel with the hex line beside it - and the hex fills itself.

    Turning the wheel, dragging the marker or moving the brightness slider
    writes ``#rrggbb`` into the line; typing a hex moves the wheel to it.
    ``get_colour`` is the one-call form: the hex back, or ``""`` when the
    dialog was cancelled."""

    def __init__(self, initial: str = "", parent: QWidget | None = None,
                 title: str = "Choose a colour") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        start = QColor(str(initial)) if str(initial or "").strip() else QColor()
        if not start.isValid():
            start = QColor("#ffb020")

        layout = QHBoxLayout(self)
        self.wheel = _ColorWheel(self)
        self.wheel.on_change = self._from_wheel
        layout.addWidget(self.wheel)

        side = QVBoxLayout()
        row = QHBoxLayout()
        self.value_slider = QSlider(Qt.Orientation.Vertical)
        self.value_slider.setRange(0, 255)
        self.value_slider.setToolTip("Brightness")
        self.value_slider.valueChanged.connect(self._from_slider)
        row.addWidget(self.value_slider)
        info = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setMinimumHeight(zoom_size(36))
        info.addWidget(self.preview)
        info.addWidget(QLabel("Hex:"))
        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("#rrggbb")
        self.hex_input.textChanged.connect(self._from_hex)
        info.addWidget(self.hex_input)
        info.addStretch(1)
        row.addLayout(info, 1)
        side.addLayout(row, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        side.addWidget(buttons)
        layout.addLayout(side, 1)

        self._syncing = False
        hue, sat, value, _a = start.getHsvF()
        self.wheel.set_hsv(max(0.0, hue), max(0.0, sat), max(0.0, value))
        self._syncing = True
        self.value_slider.setValue(int(round(value * 255)))
        self.hex_input.setText(start.name())
        self._syncing = False
        self._show(start)

    # --- the three controls keeping each other current ---

    def _show(self, colour: QColor) -> None:
        self.preview.setStyleSheet(scaled_sheet(
            f"QLabel {{ background: {colour.name()};"
            " border: 1px solid #555555; border-radius: 6px; }"))

    def _push(self, colour: QColor) -> None:
        """One control moved: bring the hex line and the preview along."""
        self._syncing = True
        self.hex_input.setText(colour.name())
        self._syncing = False
        self._show(colour)

    def _from_wheel(self, colour: QColor) -> None:
        self._syncing = True
        self.value_slider.setValue(int(round(colour.valueF() * 255)))
        self._syncing = False
        self._push(colour)

    def _from_slider(self, value: int) -> None:
        hue, sat, _old = self.wheel.hsv()
        colour = QColor.fromHsvF(hue, sat, max(0.0, value / 255.0))
        self.wheel.set_hsv(hue, sat, colour.valueF())
        if not self._syncing:
            self._push(colour)

    def _from_hex(self, text: str) -> None:
        if self._syncing:
            return
        colour = QColor(str(text).strip())
        if not colour.isValid():
            return                      # half-typed hex: wait, don't fight
        self.wheel.set_colour(colour)
        self._syncing = True
        self.value_slider.setValue(int(round(colour.valueF() * 255)))
        self._syncing = False
        self._show(colour)

    # --- the answer ---

    def colour(self) -> str:
        return self.wheel.colour().name()

    @staticmethod
    def get_colour(initial: str = "", parent: QWidget | None = None,
                   title: str = "Choose a colour") -> str:
        box = ColorWheelDialog(initial, parent, title)
        return box.colour() if box.exec() else ""


###############################################################################
# was manager_splash.py
# The boot splash: one small framed card while the window is being built.
#
# Everything expensive in starting up happens before the main window exists -
# the settings are read, the theme resolved, the addons indexed, the tabs
# built - so for a moment there is nothing on screen at all to say the program
# is working. This module draws one card in the middle of the screen: the app's
# logo, its name and version, a line naming what the boot is loading right now,
# and a bar filling with a percentage. It is painted from the theme in force -
# the same colours and the same corner radius the window wears - with a border
# around the edge, and it carries no title bar, because it carries no window
# furniture at all.
#
# The splash is a module-level singleton. :func:`show` puts it on screen from
# the entry point, ``BerryManager.__init__`` calls :func:`progress` between its
# own steps without needing to know a splash exists, and :func:`finish` takes
# the card away once the window is up. With no splash - switched off in
# Settings, or never shown - :func:`progress` does nothing at all, so booting
# without one is a normal path rather than an error.
#
# One thing the file has to work around: all of the loading happens before the
# event loop starts, so nothing repaints on its own. Every :func:`progress`
# hands Qt a ``processEvents`` turn, and that is what actually moves the bar.
#
###############################################################################


from core.const import APP_VERSION, ICON_FILES, find_asset, zoom_size

#: The card's own size at 100% zoom; the interface zoom multiplies it, like
#: every other hand-written number in the program.
CARD_WIDTH = 420
LOGO_SIDE = 110

#: The part of the bar the addon phase is spread over: one step per addon as
#: it is read in (``Loading``) and per addon as it is told to begin
#: (``Starting``). The window's own boot walks up to the first band and away
#: from the second; see ``AddonManager.reload`` and ``start_done``.
ADDON_BAND = (0.75, 0.88)
ADDON_START_BAND = (0.88, 0.93)


class _SplashCard(QWidget):
    """The frameless card itself: logo, name, the step being loaded, a bar."""

    def __init__(self, config: dict) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        # No title bar, no frame: the only edge this window has is the border
        # the stylesheet draws. Translucent so that border can be rounded.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("splashCard")
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(zoom_size(28), zoom_size(22),
                                zoom_size(28), zoom_size(18))
        body.setSpacing(zoom_size(8))

        self.logo = QLabel(card)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._place_logo(config)

        title = QLabel(f"Berry Manager V{APP_VERSION}", card)
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.step = QLabel("", card)
        self.step.setObjectName("splashStep")
        self.step.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.bar = QProgressBar(card)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")

        body.addWidget(self.logo)
        body.addWidget(title)
        body.addWidget(self.step)
        body.addWidget(self.bar)

        self.setFixedWidth(zoom_size(CARD_WIDTH))
        self.setStyleSheet(self._card_sheet())
        self.adjustSize()
        self._center_on_screen()

    def _place_logo(self, config: dict) -> None:
        """The same logo the window wears, alternate choice and all."""
        primary, alternate = ICON_FILES
        wanted = alternate if bool(config.get("use_alt_icon")) else primary
        path = find_asset(wanted) or find_asset(primary)
        if path is None:              # a build without pictures still opens
            self.logo.hide()
            return
        side = zoom_size(LOGO_SIDE)
        self.logo.setPixmap(QPixmap(str(path)).scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _card_sheet(self) -> str:
        """The theme's colours and corner, written out for this card alone:
        the window's own stylesheet is not applied until later in the boot."""
        radius = zoom_size(int(ui.tab_metric("radius_px") or 6))
        inner = max(1, zoom_size(3))
        return f"""
        QWidget {{ background-color: transparent; }}
        QWidget#splashCard {{
            background-color: {ui.color("surface")};
            border: {zoom_size(2)}px solid {ui.color("accent")};
            border-radius: {radius}px;
        }}
        QLabel {{ background-color: transparent;
                  color: {ui.color("text")}; }}
        QLabel#splashTitle {{ color: {ui.color("bright")};
                              font-weight: 600; font-size: {zoom_size(15)}px; }}
        QLabel#splashStep {{ color: {ui.color("muted")};
                             font-size: {zoom_size(11)}px; }}
        QProgressBar {{
            background-color: {ui.color("soft")};
            border: 1px solid {ui.color("border")};
            border-radius: {inner}px;
            height: {zoom_size(18)}px;
            text-align: center;
            color: {ui.color("text")};
            font-size: {zoom_size(10)}px;
        }}
        QProgressBar::chunk {{
            background-color: {ui.color("accent")};
            border-radius: {inner}px;
            margin: 1px;
        }}
        """

    def _center_on_screen(self) -> None:
        """Dead centre of the monitor the boot is happening on."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center() - self.rect().center())

    def report(self, fraction: float, label: str = "") -> None:
        """Move the bar to ``fraction`` (0..1) and name what is loading."""
        try:
            part = float(fraction)
        except (TypeError, ValueError):
            return
        self.bar.setValue(max(0, min(100, round(part * 100))))
        if label:
            self.step.setText(str(label))

    def show_card(self) -> None:
        self.show()
        self.raise_()


#: The one card the program ever shows; ``None`` means no splash is up, and
#: every call below answers that case by doing nothing.
_card: _SplashCard | None = None


def show(config: dict | None = None) -> None:
    """Put the splash on screen; obeys the Settings switch and does nothing twice.

    The card paints itself before returning, so the very first thing a double
    click produces is the splash - nothing heavy may start until it is up.
    """
    global _card
    if _card is not None:
        return
    cfg = config if isinstance(config, dict) else {}
    if not bool(cfg.get("splash_enabled", True)):
        return
    _card = _SplashCard(cfg)
    _card.show_card()
    progress(0.02, "Waking up")


def progress(fraction: float, label: str = "") -> None:
    """Report a boot step; harmless with no splash, and repaints right away.

    The event loop is not running while the window is built, so this is the
    only way the card can actually move: every report hands Qt a turn.
    """
    if _card is None:
        return
    _card.report(fraction, label)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def addon_step(number: int, total: int, name: str, *, verb: str = "Loading",
               band: tuple[float, float] = ADDON_BAND) -> None:
    """Name one addon as the boot starts it, spread across the bar's band.

    Called from the addon manager's own loops; with no splash up - switched
    off, or a rescan long after boot - this says nothing anywhere and the
    load runs exactly as it always did.
    """
    if _card is None or total <= 0:
        return
    low, high = band
    part = low + (high - low) * ((min(max(int(number), 1), total) - 1) / total)
    progress(part, f"{verb} addon {number} of {total}: {name}")


def finish() -> None:
    """Take the card away; the window is up and does the talking now."""
    global _card
    if _card is not None:
        _card.close()
        _card = None


def active() -> bool:
    """Whether a splash is up; for tests and for anyone who must not wait on one."""
    return _card is not None
