#!/usr/bin/env python3
"""A test for addons/theme_manager.py.

The addon is a whole editor for a thing the app draws itself from, so three
things have to be true and none of them can be read off the source alone:

* the tab's spec really turns into the controls the addon later sets, and every
  property it sets is one that widget understands - the same question
  ``test_web_installer.py`` asks, asked here of a notebook inside a notebook,
  trees with children, and a colour line;
* the themes folder is there when the program starts, with one theme in it, and
  that theme is still the shipped sheet written out;
* the editor's own route is true end to end - make a theme from a template,
  change a colour and a knob, refuse the ones that are not colours, save,
  apply, duplicate, rename, delete - and what lands in ``themes`` is what the
  window would be drawn from.

``RASCONF_THEMES`` is pointed at a folder of its own before anything is
imported, so the run makes and breaks as many looks as it likes beside the
program's real ``themes`` folder, and cleans up after itself.

Run it from the Test panel, or on the command line::

    python addons/_tests/test_theme_manager.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

#: The folder this run's themes live in, named before ``manager_const`` is read:
#: the themes directory is taken from the environment once, at import.
THEMES = Path(tempfile.mkdtemp(prefix="rasconf-theme-test-"))
os.environ["RASCONF_THEMES"] = str(THEMES)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # widgets, no window

#: The folder Berry Manager and addon_client live in, two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import addon_client                                          # noqa: E402
import manager_theme                                         # noqa: E402

SOURCE = ROOT / "addons" / "theme_manager.py"
FAILURES: list[str] = []

#: The one colour the shipped sheet is anchored on, checked against the file
#: written at boot rather than against a number written down twice.
from manager_const import APP_STYLESHEET, THEME_COLORS       # noqa: E402


def check(condition: object, text: str) -> None:
    """One thing the addon has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


def ids_of(spec: Any, found: list[str] | None = None) -> list[str]:
    """Every ``id`` in a spec, however deep it is nested."""
    out = found if found is not None else []
    if isinstance(spec, dict):
        if spec.get("id"):
            out.append(str(spec["id"]))
        for value in spec.values():
            ids_of(value, out)
    elif isinstance(spec, (list, tuple)):
        for value in spec:
            ids_of(value, out)
    return out


def kinds_of(spec: Any, found: dict | None = None) -> dict:
    """Every ``id`` in a spec, with the kind of control it is built as."""
    out = found if found is not None else {}
    if isinstance(spec, dict):
        if spec.get("id"):
            out[str(spec["id"])] = str(spec.get("kind") or "")
        for value in spec.values():
            kinds_of(value, out)
    elif isinstance(spec, (list, tuple)):
        for value in spec:
            kinds_of(value, out)
    return out


# --- the api the addon is handed ---

class Api(addon_client.StubAPI):
    """A stub that also plays the tab: what the addon sets, it can read back."""

    def __init__(self, addon_id: str = "theme_manager") -> None:
        super().__init__(addon_id=addon_id)
        #: One control's own state, as the addon's ``get`` reads it.
        self.form: dict[str, Any] = {}
        #: The lines on the lists and trees, kept apart from the picked one.
        self.shown: dict[str, list] = {}
        #: Which rows of each list the user has their pointer on.
        self.picks: dict[str, list[str]] = {}
        #: What the boxes the addon asks through answer.
        self.typed = ""
        self.chosen = ""
        self.picked_colour = ""
        self.confirms = True

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        if method == "tabs.set_widget":
            # The tab answers back: a control holds what was last set on it, so
            # the addon's own ``get`` reads its own ``put`` the way it would in
            # the app, and a test drives the form instead of faking it.
            identifier = str(self.argument(method, "identifier", arguments, named, ""))
            prop = str(self.argument(method, "prop", arguments, named, "value"))
            value = self.argument(method, "value", arguments, named)
            if prop in ("items", "rows"):
                self.shown[identifier] = list(value or [])
            elif prop == "checked":
                self.form[identifier] = bool(value)
            elif prop == "selected":
                self.picks[identifier] = [str(one) for one in (value or [])]
            elif prop in ("value", "text", "append", "current"):
                self.form[identifier] = value
            return None
        if method == "tabs.widget_value":
            identifier = str(self.argument(method, "identifier", arguments, named, ""))
            what = str(self.argument(method, "what", arguments, named, "value"))
            if what == "selected":
                return list(self.picks.get(identifier) or [])
            if what == "checked":
                return bool(self.form.get(identifier, False))
            if identifier not in self.form:
                return None
            return self.form[identifier]
        if method == "ui.input":
            return self.typed
        if method == "ui.choose":
            return self.chosen
        if method == "ui.pick_color":
            return self.picked_colour
        if method == "ui.question":
            return self.confirms
        return super().default(method, arguments, named)

    def sets(self, identifier: str, prop: str | None = None) -> list:
        """What the addon sent to one control on its tab, in order."""
        out = []
        for where, arguments, _named in self.calls:
            if where == "tabs.set_widget" and arguments[1:2] == (identifier,):
                if prop is None or arguments[2:3] == (prop,):
                    out.append(arguments[3] if len(arguments) > 3 else None)
        return out

    def sent(self, identifier: str, prop: str) -> Any:
        """The last thing set on one control. (`StubAPI.last` answers a different
        question - the last *call* of one method - and `asked` is built on it.)"""
        values = self.sets(identifier, prop)
        return values[-1] if values else None

    def said(self) -> str:
        """The status line, as the last thing the addon put on it."""
        return str(self.sent("status", "text") or "")

    def went_bad(self) -> bool:
        """Whether the last thing said was said in the warning colour."""
        return self.sent("status", "style") == BAD_STYLE

    def rows_of(self, identifier: str) -> list:
        return list(self.shown.get(identifier) or [])


#: The style the addon paints a refusal in; read off the addon once it is loaded.
BAD_STYLE = ""

#: The name the editor's combo and the list hold when nothing is picked.
DEFAULT = "default"


# --- one: the tab, built by Qt from the spec ---

def test_the_tab(mgr: Any, app: Any) -> None:
    """Every control the addon later sets, and every property it sets on it."""
    print("\n1. the tab, built by Qt from the spec")
    import addon_api

    widget, registry = addon_api.build_spec(mgr.tab_spec())
    wanted = ids_of(mgr.tab_spec())
    check(not set(wanted) - set(registry),
          f"the spec promises controls that are not built: "
          f"{sorted(set(wanted) - set(registry)) or 'none'}")
    check(len(set(wanted)) == len(wanted),
          f"two controls share an id, so one of them is unreachable: "
          f"{sorted(one for one in set(wanted) if wanted.count(one) > 1)}")

    # The three sub-tabs are the addon's own furniture, and the app owns the bar
    # outside them: the captions have to be exactly what SUB_PAGES promises.
    book = registry[mgr.SUB]
    captions = [book.tabText(index) for index in range(book.count())]
    check(captions == list(mgr.SUB_PAGES),
          f"the notebook's pages are {captions}, not {list(mgr.SUB_PAGES)}")
    check(book.count() == 3, f"the editor has {book.count()} sub-tabs, not 3")

    table = registry["themes"]
    check(table.columnCount() == 4,
          f"the theme list has {table.columnCount()} columns, not 4")
    check(table.rootIsDecorated() is False,
          "the theme list draws branches: it is a table, not a tree")
    for identifier in ("tokens", "knobs", "docs"):
        check(registry[identifier].columnCount() == (2 if identifier != "docs" else 1),
              f"{identifier} has the wrong number of columns")

    check(registry["tinfo"].isReadOnly() and registry["docpage"].isReadOnly(),
          "the read-only text panes can be typed into")
    check(not registry["source"].isReadOnly(),
          "the JSON box cannot be typed into, so 'Use the JSON' has no use")
    check(registry["knobnum"].maximum() >= 200 and registry["knobnum"].minimum() == 0,
          f"the pixel box runs {registry['knobnum'].minimum()}..{registry['knobnum'].maximum()}")
    check(registry["hex"].inherits("QLineEdit"),
          "the hex line is not a line, so 'value' will not reach it")

    # Anything the user works with says what it is for, in a tooltip or, for a
    # text box, in the grey line that is in it until something is typed.
    HANDLED = ("button", "combo", "line", "spin", "check", "color", "tree", "table")
    kinds = kinds_of(mgr.tab_spec())
    quiet = [identifier for identifier, kind in kinds.items()
             if kind in HANDLED and not str(registry[identifier].toolTip())]
    check(not quiet, f"these controls have nothing to explain them: {quiet or 'none'}")
    blind = [identifier for identifier, kind in kinds.items()
             if kind == "multi" and not str(registry[identifier].toolTip())
             and not str(getattr(registry[identifier], "placeholderText", lambda: "")())]
    check(not blind, f"these text panes start empty and say nothing: {blind or 'none'}")

    captions_on = {identifier: str(registry[identifier].text())
                   for identifier, _text, _tip in
                   mgr.THEME_BUTTONS + mgr.EDIT_BUTTONS}
    check(all(captions_on[identifier] == text
              for identifier, text, _tip in mgr.THEME_BUTTONS + mgr.EDIT_BUTTONS),
          f"a button does not wear the caption its handler is named for: {captions_on}")

    def applies(identifier: str, prop: str, value: Any) -> str:
        try:
            addon_api.apply_widget_property(registry[identifier], prop, value)
        except Exception as error:          # noqa: BLE001 - the point of the check
            return f"{type(error).__name__}: {error}"
        return ""

    sample_rows = [{"cells": [f"group ({1})", ""], "color": "#c51a4a",
                    "expanded": True,
                    "children": [{"cells": ["accent", "#c51a4a"]}]}]
    # Every ``(control, property, value)`` the addon sends, taken from the calls
    # it makes below: a property a widget does not understand is swallowed by
    # ``put``, and the tab simply never changes.
    properties = [("status", "text", "hello"), ("status", "style", mgr._STATUS_BAD),
                  ("where", "text", "folder: themes"),
                  ("subtabs", "current", 1),
                  ("themes", "rows", [{"cells": [DEFAULT, "*", "author", "a look"]}]),
                  ("tokens", "rows", sample_rows),
                  ("knobs", "rows", sample_rows),
                  ("docs", "rows", sample_rows),
                  ("tpl", "items", ["default", "deep sea"]),
                  ("tplnote", "value", "dark blues"),
                  ("tplnote", "text", "dark blues"),
                  ("tinfo", "value", "{\n  \"name\": \"x\"\n}"),
                  ("draft", "items", [DEFAULT]), ("draft", "value", DEFAULT),
                  ("draftas", "value", "a new name"),
                  ("newname", "value", ""),
                  ("applylive", "checked", False),
                  ("tokennote", "value", "accent . the colour of a link"),
                  ("hex", "value", "#2b8aa8"),
                  ("knobnum", "value", 40), ("knobnum", "visible", False),
                  ("knobbool", "checked", True), ("knobbool", "visible", True),
                  ("source", "value", '{"name": "x"}'),
                  ("draftstate", "text", "x . 41 colours, 13 tab knobs"),
                  ("dochead", "value", "api.theme.colors"),
                  ("docpage", "value", "one page of it")]
    for identifier, prop, value in properties:
        why = applies(identifier, prop, value)
        check(not why, f"{identifier} takes '{prop}'" + (f": {why}" if why else ""))

    # The addon reads three things back by name and acts on them: the picked row
    # of a list, the value of a line, and the state of a switch.
    addon_api.apply_widget_property(registry["themes"], "rows",
                                    [{"cells": [DEFAULT, "*", "author", "a look"]},
                                     {"cells": ["second", "", "author", "another"]}])
    addon_api.apply_widget_property(registry["tokens"], "rows", sample_rows)
    picked = addon_api.read_widget_value(registry["themes"], "selected")
    check(isinstance(picked, list),
          f"a table's 'selected' reads back as {type(picked).__name__}, not a list")
    leaf = registry["tokens"].topLevelItem(0).child(0)
    check(leaf is not None, "the colour rows did not come out as a tree of branches")
    leaf.setSelected(True)
    got = addon_api.read_widget_value(registry["tokens"], "selected")
    check(got == ["accent"], f"a tree picks up the first column of its rows: {got}")
    addon_api.apply_widget_property(registry["hex"], "value", "#2b8aa8")
    check(addon_api.read_widget_value(registry["hex"], "value") == "#2b8aa8",
          "the hex line does not read back what was put in it")
    addon_api.apply_widget_property(registry["applylive"], "checked", False)
    check(addon_api.read_widget_value(registry["applylive"], "checked") is False,
          "the redraw switch does not read back")
    widget.deleteLater()


# --- two: what the program leaves on disk when it starts ---

def test_boot() -> None:
    """The themes folder and its default, made by the app rather than by here."""
    print("\n2. the themes folder at boot")
    made = Path(manager_theme.ensure_installed()) / "default.theme"
    check(made.parent == THEMES, f"the folder made was {made.parent}, not {THEMES}")
    check(made.is_file(), f"no theme was written at {made}")
    check(made.name == "default.theme",
          f"the boot call wrote {made.name}, not default.theme")
    check(manager_theme.current_name() == DEFAULT,
          f"the look in force is {manager_theme.current_name()!r}")
    check(manager_theme.names()[:1] == [DEFAULT],
          f"the list does not begin with the shipped look: {manager_theme.names()}")
    written = json.loads(made.read_text(encoding="utf-8"))
    check(written.get("name") == DEFAULT,
          f"the file calls itself {written.get('name')!r}")
    check(set(written.get("colors") or {}) <= set(THEME_COLORS),
          "the file names a colour token the interface does not have")
    check(manager_theme.validate(written) == [],
          f"the shipped theme does not validate: {manager_theme.validate(written)}")
    # The look the window is painted from and the look in the folder are one
    # thing: the sheet the app carries is the sheet the file renders to.
    check(manager_theme.stylesheet(DEFAULT) == APP_STYLESHEET,
          "themes/default.theme does not render the sheet the app ships")
    accent = manager_theme.color("accent")
    check(accent == THEME_COLORS["accent"],
          f"the accent read from disk is {accent}, not {THEME_COLORS['accent']}")
    check(json.loads(made.read_text(encoding="utf-8"))["tabs"]["captions"] is True,
          "a fresh install hides its tab captions")
    text = made.read_text(encoding="utf-8")
    again = Path(manager_theme.ensure_installed()) / "default.theme"
    check(again == made and made.read_text(encoding="utf-8") == text,
          "the boot check wrote a second file, or overwrote somebody's work")


# --- three: init, and the tab it builds ---

def test_init(mgr: Any) -> Api:
    """What the app is told when the addon starts."""
    print("\n3. init")
    api = Api()
    mgr.init(api)
    look(mgr.TAB)
    check(api.called("tabs.add_tab"), "no tab was added")
    check(api.asked("tabs.add_tab", "name") == mgr.TAB,
          f"the tab was added as {api.asked('tabs.add_tab', 'name')!r}")
    check(bool(api.asked("tabs.add_tab", "pinned")),
          "the editor's tab is not pinned, so it can be closed by accident")
    check(isinstance(api.asked("tabs.add_tab", "spec"), dict),
          "the tab came with no spec, so it is an empty widget")
    style = str(api.asked("tabs.add_tab", "style") or "")
    check("background-color" in style
          and manager_theme.color("accent").lstrip("#").lower() in style.lower(),
          f"the page sheet does not carry the look in force: {style[:70]!r}")
    check(api.called("tabs.set_icon"), "the tab wears no mark")
    check(api.declared.get("defaults") == mgr.DEFAULTS,
          "the config the addon declares is not the one in its file")
    check(str(api.asked("config.declare", "title") or "").startswith("Theme Manager"),
          f"the Settings form is titled {api.asked('config.declare', 'title')!r}")
    watched = [arguments[0] for where, arguments, _named in api.calls
               if where == "events.on"]
    check("theme_changed" in watched,
          f"the addon watches {watched}, so another part of the app changing the "
          f"look will not be noticed here")
    check(api.called("ui.menu_item"), "nothing in the menus opens the editor")
    check(api.rows_of("themes"), "the list was never filled in")
    check(not api.went_bad(), f"init said it on the warning colour: {api.said()}")
    names = [one["cells"][0] for one in api.rows_of("themes")]
    check(names == manager_theme.names(),
          f"the list reads {names}, the folder holds {manager_theme.names()}")
    live = [one["cells"][1] for one in api.rows_of("themes")]
    check(live[0] == "*" and "*" not in live[1:],
          f"the star is not on the one theme in force: {live}")
    check(api.sent("draft", "items") == manager_theme.names(),
          "the editor's combo does not offer every theme on disk")
    check(str(api.sent("where", "text") or "").startswith("folder:"),
          f"the tab does not say where the files are: {api.sent('where', 'text')!r}")
    check(mgr.draft is not None and mgr.draft_name == DEFAULT,
          f"nothing is in the editor's hand after init: {mgr.draft_name!r}")
    check(len(mgr.draft.get("colors") or {}) == len(THEME_COLORS),
          "the draft holds only part of the palette, so a saved file would lose tokens")
    check(api.rows_of("docs"), "the Docs tree is empty")
    return api


#: The looks this run makes, edits and destroys. None of them is the program's.
SECOND = "test look"
THIRD = "second look"
RENAMED = "second renamed"
AGAIN = "renamed again"
FOURTH = "applied look"


# --- four: the list, and what a row means ---

def test_picking(mgr: Any, api: Api) -> None:
    """The Themes page: every file on disk, and the one under the pointer."""
    print("\n4. the list, and what a row means")
    api.picks["themes"] = [DEFAULT]
    check(mgr.picked_theme() == DEFAULT, f"the picked row reads as {mgr.picked_theme()!r}")
    check(mgr.context_name == "", "a right-click's row outlived the click it belonged to")
    mgr.on_widget(mgr.TAB, "themes", "selected", {"text": DEFAULT})
    shown = str(api.form.get("tinfo") or "")
    try:
        whole = json.loads(shown)
    except ValueError:
        whole = {}
    check(whole.get("name") == DEFAULT,
          f"the pane under the list is not the picked theme's own file: {shown[:60]!r}")
    check(DEFAULT in api.said(), f"picking a row said {api.said()!r}")
    # Nothing picked at all is not nothing said: the command works on the look in
    # force rather than on an empty selection.
    api.picks["themes"] = []
    check(mgr.picked_theme() == manager_theme.current_name(),
          "with no row picked a command would have nothing to work on")
    # The template combo explains itself, because a name alone says little.
    names = [str(one.get("name")) for one in manager_theme.templates()]
    api.form["tpl"] = names[-1]
    mgr.on_widget(mgr.TAB, "tpl", "changed", {"text": names[-1]})
    check(str(api.form.get("tplnote") or "") and names[-1] in api.said(),
          f"the template line said nothing about {names[-1]!r}: {api.said()!r}")
    check(len(names) >= 2, f"only {len(names)} templates to start from: {names}")
    api.picks["themes"] = [DEFAULT]


# --- five: making one from a template ---

def test_create(mgr: Any, api: Api) -> None:
    """'Create' writes a file, opens it, and puts the editor in front."""
    print("\n5. a theme from a template")
    starting = next(str(one.get("name")) for one in manager_theme.templates()
                    if str(one.get("name")) != DEFAULT)
    api.form["tpl"] = starting
    api.form["newname"] = SECOND
    mgr.create_from_template()
    made = THEMES / f"{SECOND}.theme"
    check(made.is_file(), f"no {made.name} was written")
    check(mgr.draft_name == SECOND, f"the editor holds {mgr.draft_name!r}")
    check(api.sent("subtabs", "current") == 1,
          "the tab did not move to the Edit page after making one")
    check(SECOND in api.said(), f"making one said {api.said()!r}")
    check(api.form.get("newname") == "",
          "the name line still holds it, so a second press would only refuse")
    hand = manager_theme.raw(SECOND)
    check(hand.get("name") == SECOND, f"the file calls itself {hand.get('name')!r}")
    check(starting in str(hand.get("blurb") or "") or
          hand.get("colors") != THEME_COLORS,
          "the new file is not the template it was made from")
    saved = api.times("theme.save")
    api.form["newname"] = ""
    api.typed = ""
    mgr.create_from_template()
    check("needs a name" in api.said(), f"an empty name went through: {api.said()!r}")
    api.typed = SECOND
    mgr.create_from_template()
    check(api.times("theme.save") == saved, "a name already taken was written over")
    check("already there" in api.said(), f"the refusal said {api.said()!r}")
    # A name that cannot be a file is made into one that can, on the way in.
    api.typed = "sys/admin"
    mgr.create_from_template()
    check((THEMES / "sys_admin.theme").is_file(),
          "a name with a slash in it did not come out as a plain file")
    check(not (THEMES / "sys").exists(), "a slash became a folder")
    check(mgr.draft_name == "sys_admin",
          f"the editor is named for {mgr.draft_name!r}, not for the file made")
    manager_theme.remove("sys_admin")


# --- six: colours ---

def test_colours(mgr: Any, api: Api) -> None:
    """One token at a time, refused in the app's own words when it is wrong."""
    print("\n6. a colour at a time")
    mgr.on_widget(mgr.TAB, "draft", "changed", {"text": SECOND})
    check(mgr.draft_name == SECOND, "the editor's combo did not open the theme")
    mgr.pick_from("token", {"text": "accent"})
    check(mgr.picked_token == "accent", f"the token line points at {mgr.picked_token!r}")
    check(api.form.get("hex") == mgr.token_value("accent"),
          f"the hex line was not filled in from the draft: {api.form.get('hex')!r}")
    check("accent" in str(api.form.get("tokennote") or ""),
          "nothing says what the picked token is for")
    mgr.pick_from("token", {"text": "not_a_token"})
    check(mgr.picked_token == "accent", "a row that is no token moved the editor")
    # A refusal, in the app's words, and nothing written.
    api.form["hex"] = "#zzzzzz"
    mgr.on_widget(mgr.TAB, "setcolor", "clicked", {})
    check(api.went_bad() and "not a colour" in api.said(),
          f"a colour that is not one was answered with {api.said()!r}")
    check(mgr.token_value("accent") != "#zzzzzz", "the draft took it anyway")
    check("#zzzzzz" not in (THEMES / f"{SECOND}.theme").read_text(encoding="utf-8"),
          "a refused colour reached the file")
    mgr.picked_token = "nonsense"
    api.form["hex"] = "#123456"
    mgr.on_widget(mgr.TAB, "setcolor", "clicked", {})
    check("is not a colour the interface has" in api.said(),
          f"an unknown token was answered with {api.said()!r}")
    check("nonsense" not in (mgr.draft.get("colors") or {}),
          "a token nobody has went into the draft")
    # The wheel fills the line, and the line goes on the token.
    api.picked_colour = "#010203"
    mgr.on_widget(mgr.TAB, "wheel", "clicked", {})
    check(api.form.get("hex") == "#010203",
          f"the wheel's colour never reached the line: {api.form.get('hex')!r}")
    mgr.pick_from("token", {"text": "text"})
    api.form["hex"] = "#fefefe"
    mgr.on_widget(mgr.TAB, "setcolor", "clicked", {})
    check(mgr.token_value("text") == "#fefefe",
          f"the draft holds {mgr.token_value('text')!r} for text")
    check("text is #fefefe" in api.said(), f"setting it said {api.said()!r}")
    check(api.sent("source", "value") == mgr.draft_json(),
          "the JSON box was not redrawn with the change in it")
    mgr.picked_token = ""
    mgr.on_widget(mgr.TAB, "setcolor", "clicked", {})
    check("pick a colour" in api.said(), f"with nothing picked it said {api.said()!r}")
    mgr.picked_token = "accent"


# --- seven: the tab bar ---

def test_knobs(mgr: Any, api: Api) -> None:
    """Pixels in one branch, switches in the other, each with its own control."""
    print("\n7. the tab bar's knobs")
    branches = {str(one["cells"][0]): one for one in api.rows_of("knobs")}
    sizes = [one for one in branches.values() if str(one["cells"][0]).startswith("Sizes")]
    switches = [one for one in branches.values()
                if str(one["cells"][0]).startswith("Switches")]
    check(len(sizes) == 1 and len(switches) == 1,
          f"the knobs are not in two branches: {sorted(branches)}")
    counts = manager_theme.tabs()
    drawn = len(sizes[0].get("children") or []) + len(switches[0].get("children") or [])
    check(drawn == len(counts), f"the trees hold {drawn} of the {len(counts)} knobs")
    mgr.on_widget(mgr.TAB, "knobs", "selected", {"text": "icon_px"})
    check(mgr.picked_knob == "icon_px", f"the knob line points at {mgr.picked_knob!r}")
    check(api.form.get("knobnum") == int(counts["icon_px"]),
          f"the pixel box shows {api.form.get('knobnum')!r}")
    check(api.sent("knobnum", "visible") is True and
          api.sent("knobbool", "visible") is False,
          "a knob of pixels is answering to the switch box")
    api.form["knobnum"] = 40
    mgr.on_widget(mgr.TAB, "setknob", "clicked", {})
    check((mgr.draft.get("tabs") or {}).get("icon_px") == 40,
          "the pixels did not go into the draft")
    # Out of the range the app draws in, it is refused rather than rounded.
    api.form["knobnum"] = 4000
    mgr.on_widget(mgr.TAB, "setknob", "clicked", {})
    check("out between 0 and 200" in api.said(),
          f"a 4000 px icon was answered with {api.said()!r}")
    check((mgr.draft.get("tabs") or {}).get("icon_px") == 40, "the draft took it anyway")
    api.form["knobnum"] = 30
    mgr.set_knob()
    mgr.on_widget(mgr.TAB, "knobs", "selected", {"text": "captions"})
    check(api.sent("knobbool", "visible") is True and
          api.sent("knobnum", "visible") is False,
          "a switch is answering to the pixel box")
    api.form["knobbool"] = False
    mgr.on_widget(mgr.TAB, "setknob", "clicked", {})
    check((mgr.draft.get("tabs") or {}).get("captions") is False,
          "the switch did not go into the draft")
    # A word where a switch belongs is refused by the app, not by here.
    mgr.draft.setdefault("tabs", {})["captions"] = "yes"
    api.form["knobbool"] = True
    mgr.set_knob()
    check(mgr.draft["tabs"]["captions"] is True,
          "a knob that is a switch is holding a word")
    mgr.on_widget(mgr.TAB, "knobs", "selected", {"text": "no_such_knob"})
    check(mgr.picked_knob == "captions", "a row that is no knob moved the editor")


# --- eight: save, apply, discard ---

def test_save(mgr: Any, api: Api) -> None:
    """Nothing reaches the disk until Save, or the window until Apply."""
    print("\n8. save, apply, discard")
    api.form["applylive"] = False
    check(mgr.save_draft(False), f"Save said no: {api.said()!r}")
    on_disk = manager_theme.raw(SECOND)
    check((on_disk.get("colors") or {}).get("text") == "#fefefe",
          "the colour never reached the file")
    check((on_disk.get("tabs") or {}).get("icon_px") == 30,
          "the knob never reached the file")
    check(manager_theme.current_name() == DEFAULT,
          "saving redrew the window with 'redraw on save' off")
    check("saved" in api.said() and "drawn from" not in api.said(),
          f"saving said {api.said()!r}")
    # Save and apply: the file lands and the window follows it.
    api.form["draftas"] = FOURTH
    check(mgr.save_draft(True), f"Save and apply said no: {api.said()!r}")
    check((THEMES / f"{FOURTH}.theme").is_file(), "no file for the applied look")
    check(mgr.draft_name == FOURTH, f"the editor is named for {mgr.draft_name!r}")
    check(manager_theme.current_name() == FOURTH,
          f"the window is still drawn from {manager_theme.current_name()!r}")
    check(api.form.get("draftas") == "", "the 'new name' line is still filled in")
    # Saving the theme in force, with the switch on, applies it by itself.
    api.form["applylive"] = True
    used = api.times("theme.use")
    mgr.draft.setdefault("colors", {})["note"] = "#101010"
    check(mgr.save_draft(False), "saving the look in force said no")
    check(api.times("theme.use") == used + 1,
          "the look in force was saved without the window being redrawn")
    check(mgr.token_value("note") == "#101010", "the draft lost the change")
    # A file the app would refuse is refused here too, before it is written.
    was = (THEMES / f"{FOURTH}.theme").read_text(encoding="utf-8")
    mgr.draft["colors"]["accent"] = "#zz"
    check(not mgr.save_draft(False), "a bad colour was written without a word")
    check("not a colour" in api.said(), f"the refusal said {api.said()!r}")
    check((THEMES / f"{FOURTH}.theme").read_text(encoding="utf-8") == was,
          "the refused write went through anyway")
    # Discard: the draft goes back to what the file says.
    mgr.picked_token = "accent"
    mgr.on_widget(mgr.TAB, "discard", "clicked", {})
    check(mgr.token_value("accent") != "#zz", "the re-read kept the bad colour")
    check(mgr.same_draft(), "after a re-read the draft is still said to be changed")
    # A draft with no file behind it starts again from the look in force.
    mgr.draft_name = "no such look"
    mgr.discard_draft()
    check("started again" in api.said(), f"it said {api.said()!r}")
    check(mgr.draft_name == manager_theme.current_name(),
          f"it opened {mgr.draft_name!r} instead of the look in force")
    # 'Blank from default' is a draft nothing has written yet.
    mgr.on_widget(mgr.TAB, "newdraft", "clicked", {})
    check(mgr.draft is not None and not manager_theme.exists(mgr.draft_name),
          f"'Blank from default' holds {mgr.draft_name!r}, which is on disk")
    check(len((mgr.draft or {}).get("colors") or {}) == len(THEME_COLORS),
          "a blank draft does not start from the whole shipped palette")
    check("not on disk" in api.said(), f"it said {api.said()!r}")


# --- nine: the JSON direct ---

def test_source(mgr: Any, api: Api) -> None:
    """The box is the file: typed into, checked, and read back."""
    print("\n9. the JSON direct")
    mgr.open_draft(SECOND)
    held = json.dumps(mgr.draft, sort_keys=True)
    mgr.on_widget(mgr.TAB, "getsource", "clicked", {})
    text = str(api.form.get("source") or "")
    check(json.loads(text) == mgr.draft, "the box does not hold the draft")
    check(text.endswith("\n") and "\n  " in text,
          "the JSON is not the shape its file is written in")
    api.form["source"] = json.dumps({"name": SECOND, "colors": {"accent": "#ff0000"},
                                     "tabs": {"gap_px": 9}})
    mgr.on_widget(mgr.TAB, "putsource", "clicked", {})
    check(mgr.draft["tabs"]["gap_px"] == 9 and mgr.draft["colors"]["accent"] == "#ff0000",
          f"the draft did not take what the box said: {str(mgr.draft)[:60]}")
    check(api.said() and not api.went_bad(), f"typing a good file said {api.said()!r}")
    held = json.dumps(mgr.draft, sort_keys=True)
    refusals = (("{ not json", "not JSON"),
                ("[1, 2]", "one JSON object"),
                ("", "box is empty"),
                (json.dumps({"name": SECOND, "colors": {"nonsense": "#ff0000"}}),
                 "is not a colour the interface has"),
                (json.dumps({"name": SECOND, "tabs": {"gap_px": "wide"}}),
                 "number of pixels"),
                (json.dumps({"name": "sys/admin"}), "file name"),
                (json.dumps({"name": SECOND, "colours": {}}),
                 "not something a theme carries"))
    for text, says in refusals:
        api.form["source"] = text
        mgr.on_widget(mgr.TAB, "putsource", "clicked", {})
        check(says in api.said(), f"{text[:24]!r} was answered with {api.said()!r}")
        check(json.dumps(mgr.draft, sort_keys=True) == held,
              f"{text[:24]!r} changed the draft it was refused over")
    # What is typed here is what the window would be painted from.
    api.form["source"] = json.dumps({"name": SECOND, "colors": {"accent": "#0f0f0f"}})
    mgr.use_source()
    sheet = mgr.guarded(lambda: api.theme.sheet_of(mgr.draft), "render the draft")
    check(isinstance(sheet, str) and "#0f0f0f" in sheet,
          f"the draft does not render its own accent: {str(sheet)[:80]!r}")


# --- ten: copies, names, and deleting one ---

def test_files(mgr: Any, api: Api) -> None:
    """The commands on the list, including the one that must never work."""
    print("\n10. copies, names, and deleting one")
    api.picks["themes"] = [SECOND]
    api.form["newname"] = THIRD
    mgr.on_widget(mgr.TAB, "dup", "clicked", {})
    check((THEMES / f"{THIRD}.theme").is_file(), f"no {THIRD}.theme was made")
    check(manager_theme.raw(THIRD).get("colors") ==
          manager_theme.raw(SECOND).get("colors"),
          "the copy is not the theme it was copied")
    check("copied" in api.said(), f"duplicating said {api.said()!r}")
    api.form["newname"] = THIRD
    mgr.duplicate_picked()
    check("already there" in api.said(), "a name in use was given to a second file")
    # Rename: the new file lands before the old one goes.
    api.form["newname"] = RENAMED
    api.picks["themes"] = [THIRD]
    mgr.on_widget(mgr.TAB, "rename", "clicked", {})
    check((THEMES / f"{RENAMED}.theme").is_file(), "the new name has no file")
    check(not (THEMES / f"{THIRD}.theme").exists(), "the old file was left behind")
    check(RENAMED in api.said(), f"renaming said {api.said()!r}")
    copies = api.times("theme.copy")
    api.form["newname"] = RENAMED
    api.picks["themes"] = [RENAMED]
    mgr.rename_picked()
    check(api.times("theme.copy") == copies, "a rename to the same name went through")
    # The editor follows a rename of the theme it is holding.
    mgr.open_draft(RENAMED)
    api.form["newname"] = AGAIN
    api.picks["themes"] = [RENAMED]
    mgr.rename_picked()
    check(mgr.draft_name == AGAIN and mgr.draft.get("name") == AGAIN,
          f"the editor is still named for {mgr.draft_name!r}")
    # A right-click works on the row it was shown over, once.
    api.chosen = "Duplicate"
    api.form["newname"] = ""
    api.typed = "menu copy"
    api.picks["themes"] = [RENAMED]
    mgr.on_widget(mgr.TAB, "themes", "context", {"text": SECOND})
    check((THEMES / "menu copy.theme").is_file(),
          "the menu's command worked on the picked row, not the clicked one")
    check(mgr.context_name == "", "the right-click's row is still remembered")
    api.chosen = ""
    api.typed = "nothing at all"
    mgr.on_widget(mgr.TAB, "themes", "context", {"text": SECOND})
    check(not (THEMES / "nothing at all.theme").exists(),
          "closing the menu without choosing ran a command anyway")
    check(mgr.context_name == "", "a refused menu left its row for the next click")
    # Apply through the list, and the star moves with it.
    api.picks["themes"] = ["menu copy"]
    mgr.on_widget(mgr.TAB, "use", "clicked", {})
    check(manager_theme.current_name() == "menu copy",
          f"the window is drawn from {manager_theme.current_name()!r}")
    starred = [one["cells"][0] for one in api.rows_of("themes") if one["cells"][1] == "*"]
    check(starred == ["menu copy"], f"the list stars {starred}")
    # A theme with no file is not an error: the default is used, and said.
    api.picks["themes"] = ["a look with no file"]
    mgr.apply_picked()
    check(manager_theme.current_name() == DEFAULT,
          "asking for a theme with no file left the window somewhere else")
    check("no file" in api.said(), f"it said {api.said()!r}")
    # Delete: asked first, and the shipped look never goes.
    api.confirms = False
    api.picks["themes"] = ["menu copy"]
    mgr.on_widget(mgr.TAB, "del", "clicked", {})
    check((THEMES / "menu copy.theme").is_file(), "the ask was skipped and the file went")
    check("nothing deleted" in api.said(), f"refusing said {api.said()!r}")
    api.confirms = True
    api.picks["themes"] = [DEFAULT]
    mgr.on_widget(mgr.TAB, "del", "clicked", {})
    check((THEMES / "default.theme").is_file(), "the shipped default was deleted")
    check("shipped default and stays" in api.said(), f"it said {api.said()!r}")
    api.picks["themes"] = ["menu copy"]
    mgr.delete_picked()
    check(not (THEMES / "menu copy.theme").exists(), "the file stayed")
    check("deleted" in api.said(), f"it said {api.said()!r}")
    # Deleting the theme in the editor's hand empties the hand.
    mgr.open_draft(AGAIN)
    api.picks["themes"] = [AGAIN]
    api.confirms = True
    mgr.delete_picked()
    check(not (THEMES / f"{AGAIN}.theme").exists(), f"{AGAIN}.theme was left behind")
    check(mgr.draft is None and mgr.draft_name == "",
          f"the editor is still holding {mgr.draft_name!r}, which is gone")
    check("nothing in the editor" in str(api.form.get("draftstate") or ""),
          "the editor still says it holds something")
    # A name with no file behind it is not a theme, and is not opened as one.
    check(not mgr.open_draft(AGAIN), "the editor opened a name that is not on disk")
    check("no themes" in api.said() and ".theme to open" in api.said(),
          f"opening a gone theme said {api.said()!r}")
    check(mgr.draft is None, "a refused open left something in the hand")
    # The menu's Delete goes to the file it was shown over.
    api.chosen = "Delete"
    api.confirms = True
    api.picks["themes"] = [SECOND]
    mgr.on_widget(mgr.TAB, "themes", "context", {"text": FOURTH})
    check(not (THEMES / f"{FOURTH}.theme").exists(),
          "the menu's Delete did not reach the file")
    check(mgr.context_name == "", "the menu's Delete left its row remembered")
    api.chosen = ""


# --- eleven: the Docs sub-tab ---

def test_docs(mgr: Any, api: Api) -> None:
    """The reference, read off the API the app actually runs."""
    print("\n11. the Docs sub-tab")
    api.picks["themes"] = [DEFAULT]
    mgr.refresh_all()
    rows = api.rows_of("docs")
    branches = {str(one["cells"][0]): one for one in rows}
    guides = f"Guides ({len(mgr.GUIDES)})"
    check(list(branches)[:1] == [guides],
          f"the tree does not open with the guides: {list(branches)[:2]}")
    check(len(branches[guides].get("children") or []) == len(mgr.GUIDES),
          "the guides branch does not hold the guides")
    check(all(len(body) > 400 for _title, body in mgr.GUIDES),
          "one of this addon's own guides is too short to be a guide")
    known = {str(one.get("name")): one for one in (api.theme.reference() or [])}
    if not known:
        look("no api.theme to read here: addon_api could not be imported")
        return
    named = {one for _title, names in mgr.DOC_HEADINGS for one in names}
    check(named <= set(known),
          f"the headings name calls that are not there: {sorted(named - set(known))}")
    check(set(known) <= named,
          f"api.theme has grown calls nothing documents: {sorted(set(known) - named)}")
    for title, names in mgr.DOC_HEADINGS:
        label = f"{title} ({len([one for one in names if one in known])})"
        check(label in branches, f"the branch {label!r} is not in the tree")
    leaves = [str(one["cells"][0]) for one in rows for one in (one.get("children") or [])]
    check(sorted(leaves[len(mgr.GUIDES):]) == sorted(known),
          f"the tree lists {len(leaves) - len(mgr.GUIDES)} calls, the API has {len(known)}")
    check(all(str(one.get("color") or "").startswith("#") for one in rows),
          "a heading in the tree is not painted in one of the theme's colours")
    for title, body in mgr.GUIDES:
        for call in set(re.findall(r"api\.theme\.([a-z_]+)", body)):
            check(call in known,
                  f"the guide {title!r} describes api.theme.{call}, which is not there")
    # The calls are documented in the app's own words, read off the live API.
    mgr.on_widget(mgr.TAB, "docs", "selected", {"text": "colors"})
    check(api.form.get("dochead") == "api.theme.colors",
          f"the page is headed {api.form.get('dochead')!r}")
    page = str(api.form.get("docpage") or "")
    check("api.theme.colors(" in page, f"the page does not give the call: {page[:60]!r}")
    check(known["colors"]["doc"].splitlines()[0].strip() in page,
          "the page is not the docstring the API carries")
    mgr.show_doc("Which themes there are")
    page = str(api.form.get("docpage") or "")
    check("api.theme.name" in page and "api.theme.folder" in page,
          f"a heading opens on nothing: {page[:60]!r}")
    mgr.show_doc(mgr.GUIDES[0][0])
    check(api.form.get("dochead") == mgr.GUIDES[0][0],
          "a guide does not open under its own title")
    check(len(str(api.form.get("docpage") or "")) > 400, "the guide page is empty")


# --- twelve: the hooks the app calls ---

def test_hooks(mgr: Any, api: Api) -> None:
    """Routing, the theme event, the config, and going away."""
    print("\n12. the hooks the app calls")
    mgr.open_draft(SECOND)
    mgr.on_widget("Some other tab", "newdraft", "clicked", {})
    check(mgr.draft_name == SECOND, "a click meant for another tab was answered here")
    check(mgr.on_widget(mgr.TAB, "no_such_button", "clicked", {}) is None,
          "an unknown control id raised")
    check(mgr.on_widget(mgr.TAB, "save", "unknown_event", {}) is None,
          "an unknown interaction raised")
    # The menu and the app's links both land on the Edit page.
    mgr.focus_editor()
    check(api.asked("tabs.activate", "name") == mgr.TAB,
          f"the menu opened {api.asked('tabs.activate', 'name')!r}")
    check(api.sent("subtabs", "current") == 1, "the menu did not move to the editor")
    # The look changing elsewhere is noticed, and said.
    reads = api.times("theme.names")
    mgr.on_event("ssh_connected", {"host": "elsewhere"})
    check(api.times("theme.names") == reads, "an unrelated event re-read everything")
    applied = api.times("theme.use")
    mgr.on_event("theme_changed", {"theme": SECOND, "wanted": SECOND})
    check(api.times("theme.names") > reads, "the look moving did not re-read the folder")
    check(SECOND in api.said(), f"the change was answered with {api.said()!r}")
    check(api.times("theme.use") == applied,
          "the event that said the look moved applied it all over again")
    # The config's own switches, applied from Settings.
    api.config_values["save_applies"] = False
    mgr.on_config({"addon": api.id})
    check(api.sent("applylive", "checked") is False,
          "the switch under the editor's name did not follow the config")
    check(api.rows_of("tokens"), "applying the config did not redraw the colour tree")
    api.config_values["save_applies"] = True
    groups = api.times("theme.groups")
    mgr.on_config({"addon": "someone else"})
    check(api.times("theme.groups") == groups,
          "another addon's settings changed this tab's form")
    mgr.on_config({"addon": api.id})
    check(api.sent("applylive", "checked") is True,
          "the config was not re-read when it was applied")
    mgr.on_config({})
    look("a payload with no addon in it did not raise")
    # Going away: nothing left pointing at an api that has gone.
    mgr.shutdown()
    check(mgr.api is None and mgr.draft is None and mgr.draft_name == "",
          "shutdown left state behind")
    actions = [mgr.refresh_all, mgr.start, mgr.show_source, mgr.focus_editor,
               mgr.blank_draft, mgr.discard_draft, mgr.apply_picked,
               mgr.delete_picked, mgr.create_from_template, mgr.open_folder,
               lambda: mgr.on_event("theme_changed", {}),
               lambda: mgr.on_config({"addon": "x"}),
               lambda: mgr.on_widget(mgr.TAB, "save", "clicked", {}),
               lambda: mgr.pick_from("doc", {"text": "name"}),
               lambda: mgr.theme_menu({"text": DEFAULT}),
               lambda: mgr.save_draft(True), lambda: mgr.show_page(2),
               lambda: mgr.show_info(DEFAULT), mgr.tab_icon, mgr.page_style,
               mgr.tab_spec]
    for action in actions:
        try:
            action()
            good = True
        except Exception as error:               # noqa: BLE001 - a bare addon must not raise
            good = False
            look(f"raised {type(error).__name__}: {error}")
        check(good, f"{getattr(action, '__name__', 'one hook')} works with no api")
    check(mgr.tab_icon().startswith("<svg"), "the fallback mark is not a mark")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live, and a Windows console often has no room
        # for the arrows and middots these pages use.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    try:
        if not SOURCE.is_file():
            print(f"there is no addon at {SOURCE}")
            return 2
        global BAD_STYLE
        mgr = addon_client.load_addon(SOURCE)
        BAD_STYLE = mgr._STATUS_BAD
        app = None
        try:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
        except Exception as error:               # noqa: BLE001 - Qt is optional
            look(f"no Qt here, so the spec itself is not built: {error}")

        print(f"theme_manager: the addon at {SOURCE.name}")
        look(f"themes in {THEMES}")
        # The tab's caption is how the app finds its way to the editor.
        gui = (ROOT / "manager_gui.py").read_text(encoding="utf-8")
        wanted = re.search(r'THEME_MANAGER_TAB\s*=\s*"([^"]+)"', gui)
        check(wanted is not None and wanted.group(1) == mgr.TAB,
              f"the app looks for a tab called {wanted and wanted.group(1)!r}, "
              f"and this addon's tab is {mgr.TAB!r}")
        if app is not None:
            test_the_tab(mgr, app)
        test_boot()
        api = test_init(mgr)
        test_picking(mgr, api)
        test_create(mgr, api)
        test_colours(mgr, api)
        test_knobs(mgr, api)
        test_save(mgr, api)
        test_source(mgr, api)
        test_files(mgr, api)
        test_docs(mgr, api)
        test_hooks(mgr, api)
    finally:
        shutil.rmtree(THEMES, ignore_errors=True)

    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
