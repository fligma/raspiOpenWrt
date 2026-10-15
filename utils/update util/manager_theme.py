#!/usr/bin/env python3
"""The program's look, read from ``themes/<name>.theme``.

A theme is one small JSON file: the colours the interface is painted with, the
shape of its tabs, and - for anything the two do not cover - a few lines of
stylesheet of its own. :mod:`manager_const` holds the shape and the shipped
values; this module only reads, checks, writes and hands out what is on disk,
so it needs no window and no Qt and can be used just as well by an addon, a
test script or a build.

The app boots with a ``themes`` folder beside itself and a ``default.theme``
inside it, so there is always something to edit and always something to fall
back on. A file that cannot be read, a token nobody heard of and a colour that
is not a colour are all answered the same way: the shipped value is kept and
the rest of the file is used, because a half-written theme should still look
like a program rather than break one.

Which theme is in force is a profile setting (``theme``), and this module
remembers the answer between calls. The window is what persists it and redraws
the screen; :func:`use` only decides what the next stylesheet is built from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from manager_const import (
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
#: themselves are listed by ``manager_const.THEME_TABS``; this only says what
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
