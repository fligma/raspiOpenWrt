#!/usr/bin/env python3
"""Every build-time tool Berry Manager needs, in the one file.

Nine small programs used to sit in ``utils/update util/tools``: four that read
the source and stop a build over something that would only be noticed by whoever
happens to open the page (``check_names``, ``check_api_docs``,
``check_guide_links``, ``check_connection_saying``), two that write the catalogs
the addon store and the theme store read off GitHub (``gen_addon_store_index``,
``gen_theme_store_index``), and three that draw picture files out of the drawings
held in the sources (``make_addon_icons``, ``make_store_icons``,
``make_web_icons``). They are one job seen nine times - prove the tree, or
regenerate what is derived from it - and none of them is used while the program
runs, so they live here at the top of the repository rather than inside the app.

Name the tool to run it, from any folder::

    python buildtools.py check-names [file or folder ...]
    python buildtools.py check-api-docs [file ...]
    python buildtools.py check-guide-links
    python buildtools.py check-connection-saying
    python buildtools.py gen-addon-store-index [--store DIR] [--check]
    python buildtools.py gen-theme-store-index [--store DIR] [--check]
    python buildtools.py make-addon-icons [--side 128] [--force] [--check] [id ...]
    python buildtools.py make-store-icons [--side 128] [--force] [--check]
    python buildtools.py make-web-icons [--side 512] [--force] [--check]

    python buildtools.py --list            # what is here, and what it is for

Every path is worked out from this file's own place, so nothing depends on the
folder it is run from, and ``.github/workflows/build.yml`` runs each of them
before it packages anything. Qt is only imported by the commands that draw or
that read the app's own modules, and pyflakes only by ``check-names``, so a
machine missing one of them can still run the rest.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import struct
import sys
from pathlib import Path, PurePosixPath
from typing import Any

# --- where everything lies, read off this file's own place ---

#: The repository, which is where this file is.
REPO = Path(__file__).resolve().parent

#: ``utils/update util`` - the program itself, its addons and its assets.
APP = REPO / "utils" / "update util"

#: The two catalogs' folders, and the picture folder the program ships in.
ADDON_STORE = APP / "addon.store"
THEME_STORE = APP / "themes.store"
ASSETS = APP / "assets"

#: The dashboard's folder. It lives in the repository rather than beside the app
#: because the tree an install uploads is that one, and the pictures the page
#: links to have to be in the tree that goes over.
WEB_ASSETS = REPO / "src" / "www_rasconf" / "assets"

#: Folders that hold nothing the program runs from.
SKIP_DIRS = {"__pycache__", ".venv", ".git", "node_modules", "_build",
             "_data", "dist", "build"}

#: What a tab picture is always called: the addon's id, then this. The same rule
#: ``addon_common`` uses when it looks in a program's assets folder, kept here as
#: well because these tools import nothing of the app when they draw.
ICON_TAIL = "icon.png"

#: A catalog id is a name Python, PyInstaller and a download can all use.
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def _utf8_stdout() -> None:
    """Write UTF-8 whatever the console speaks: these print the marks, ● ○ ✓."""
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")


def _use_app() -> None:
    """Put the program's folder on the path, for the commands that read it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")    # no window needed
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def clean_id(value: str, fallback: str) -> str:
    """A file stem turned into the id a catalog and a build both use."""
    return _UNSAFE_ID.sub("_", str(value).strip()) or fallback


def flat(value: Any) -> str:
    """A sentence on the one line a catalog row and a label both need."""
    return " ".join(str(value or "").split())


def words(value: Any) -> list[str]:
    """One manifest value written as a list, however it was written in the file.

    ``requires`` is published as a list and an addon may have written it as a
    comma separated line, so both are read the same way here and in the app.
    """
    if isinstance(value, str):
        value = [part for part in re.split(r"[,; ]+", value) if part]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        word = flat(item)
        if word and word not in out:
            out.append(word)
    return out


def mark_status(entry: dict, prior: dict | None) -> str:
    """new when the old catalog did not have it, updated when the version moved."""
    if prior is None:
        return "new"
    if str(prior.get("version") or "") != str(entry.get("version") or ""):
        return "updated"
    return ""


def render_index(index: dict) -> str:
    """A catalog as tidy, stable JSON with a trailing newline.

    Stable on purpose: with nothing changed the same file comes out byte for
    byte, so a build that regenerates a catalog only has something to commit
    when the catalog really moved.
    """
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def load_index(target: Path) -> dict | None:
    """The index already on disk, for the new/updated marks; nothing if unreadable."""
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ============================================================ the four readers
#
# Each of these four stops a build over something that crashes nothing: a name
# nothing binds, a call with no docstring, a link to a page nobody wrote, a row
# that means the wrong thing. All four are visible in the source without running
# the program, which is exactly why they are looked for here rather than in a
# window somebody has to open.

#: What pyflakes says when a file cannot possibly run as it stands. Matched on
#: the printed text because the wording of these messages is the one part of
#: pyflakes that has stayed stable across its versions.
FATAL_MARKS = ("undefined name", "does not exist", "syntax error",
               "expected an indented block", "unexpected indent",
               "unterminated", "duplicate argument", "starred")

#: The three templates the Addon Dev tab hands an author, filled with a probe
#: addon; each is read for names afterwards.
TEMPLATE_PROBES = (
    ("_ADDON_SOURCE", {"id": "probe", "name": "Probe", "title": "Probe",
                        "tab": "Probe", "menu": "Probe", "version": "1.0",
                        "author": "author", "description": "a probe addon"}),
    ("_TEST_SOURCE", {"id": "probe", "tab": "Probe"}),
    ("_LAUNCHER", {"id": "probe", "module": "probe"}),
)


def _name_for_report(path: Path) -> str:
    """The file as a person running the tool from the app's folder would write it."""
    try:
        return str(path.relative_to(APP))
    except ValueError:                        # named from somewhere else entirely
        return str(path)


def names_sources(arguments: list[str]) -> list[Path]:
    """The files to read: those named, or every module of the program, and this one.

    This file is read beside the app's own modules rather than instead of them:
    it holds the code that builds and ships the program, and a name missing from
    it is a broken build rather than a broken window - worth the same looking.
    """
    if not arguments:
        found = sorted(path for path in APP.rglob("*.py")
                       if not set(path.parts) & SKIP_DIRS)
        if Path(__file__) not in found:
            found.append(Path(__file__))
        return found
    found: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if not path.is_absolute():
            path = APP / path
        if path.is_dir():
            found += sorted(item for item in path.rglob("*.py")
                            if not set(item.parts) & SKIP_DIRS)
        else:
            found.append(path)
    return found


def fatal_lines(text: str, label: str) -> list[str]:
    """What pyflakes says about ``text`` that means it cannot run as it stands."""
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    printed, errors = io.StringIO(), io.StringIO()
    check(text + "\n", label, Reporter(printed, errors))
    return [line for line in printed.getvalue().splitlines()
            + errors.getvalue().splitlines()
            if any(mark in line for mark in FATAL_MARKS)]


def names_templates() -> list[str]:
    """Names missing from the files the Addon Dev tab writes, once filled in.

    Quiet when the app's own modules cannot be imported here at all: that is a
    checkout with something wrong in it, which the pass over the real files has
    already said about.
    """
    _use_app()
    try:
        import core.addon as addon_module
    except Exception as error:                       # noqa: BLE001 - optional
        print(f"  note: templates were not read (the app did not import: {error})")
        return []
    bad: list[str] = []
    for name, fields in TEMPLATE_PROBES:
        template = getattr(addon_module, name, None)
        if template is None:
            bad.append(f"core/addon.py has no {name} to fill in")
            continue
        try:
            source = template.substitute(**fields)
        except (KeyError, ValueError) as error:
            bad.append(f"{name}: cannot be filled in ({error})")
            continue
        bad += [f"{name} renders: {line}" for line in
                fatal_lines(source, f"<{name}>")]
    return bad


def cmd_check_names(argv: list[str]) -> int:
    """Stop a build on names that do not exist.

    Every module under the app's folder is read with pyflakes, and only the
    answers that mean a crash are kept: a name nothing binds, an import of
    something that is not there, a file that does not parse. The rest of what
    pyflakes says - an import nobody uses, a value assigned and left - is left to
    a developer's own linter, because failing a build on those would only teach
    people to ignore the build.

    Why this is a build step and not a code review habit: Berry Manager is a Qt
    program, and a Python exception raised where Qt called in does not print and
    carry on - it takes the window down with it. A released build did exactly
    that on opening the About dialog, over one line written for a trailing
    ``.format()`` that never reached it.
    """
    _utf8_stdout()
    try:
        from pyflakes.api import check                 # noqa: F401 - presence only
    except ImportError:
        print("pyflakes is not installed here; run: pip install pyflakes")
        return 2

    files = names_sources(argv)
    bad: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            bad.append(f"{path}: cannot be read: {error}")
            continue
        for line in fatal_lines(text, str(path)):
            # The app's own folder is left off the reported name, so the
            # line reads the way a traceback does from the working folder.
            try:
                line = line.replace(str(path), _name_for_report(path))
            except ValueError:
                pass
            bad.append(line)

    bad += names_templates()

    print(f"checked {len(files)} module(s) and "
          f"{len(TEMPLATE_PROBES)} template(s) for names that do not exist")
    for line in bad:
        print(f"  {line}")
    if bad:
        print(f"\n{len(bad)} name(s) would raise the moment they are reached; "
              "nothing was built")
        return 1
    return 0


#: The one module that hands out the API, and the classes it hands out.
PROTOCOL = "addon_api.py"

#: A class whose public calls an addon reads, by its name's ending. The
#: connection object is named without the suffix and spelled out beside it.
API_SUFFIX = "API"
EXTRA_CLASSES = ("Connection",)


def _api_classes(tree: ast.Module, classes: dict[str, ast.ClassDef]) -> list[str]:
    """The namespace classes ``AddonAPI.__init__`` binds, in the order it binds them.

    The Docs pane reads this off the running module's bytecode; reading the same
    assignments out of the syntax tree gives the same list without needing Qt,
    and a class that is never bound is not an API an addon can reach, so one
    that is defined and left unused is not asked to explain itself.
    """
    builder = classes.get(f"Addon{API_SUFFIX}")
    if builder is None:
        return []
    bound: list[str] = []
    for node in ast.walk(builder):
        if not isinstance(node, ast.Assign):
            continue
        maker = node.value
        func = maker.func if isinstance(maker, ast.Call) else maker
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name and (name.endswith(API_SUFFIX) or name in EXTRA_CLASSES):
            if name not in bound:
                bound.append(name)
    return bound


def _property_getter(node: ast.Assign) -> ast.AST | None:
    """The function a ``name = property(func)`` line points at, in its class."""
    if not isinstance(node.value, ast.Call) \
            or getattr(node.value.func, "id", "") != "property":
        return None
    if not node.value.args or not isinstance(node.value.args[0], ast.Name):
        return node.value.args[0] if node.value.args else None
    wanted = node.value.args[0].id
    parent = getattr(node, "_class", None)
    for item in getattr(parent, "body", []):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and item.name == wanted:
            return item
    return None


def _public_calls(cls: ast.ClassDef) -> list[tuple[str, ast.AST]]:
    """The methods and properties an addon can call, as name and definition pairs.

    A property is checked on its getter, which is where the words for it live;
    a ``property(...)`` line with nothing readable behind it counts as silent.
    """
    found: list[tuple[str, ast.AST]] = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                found.append((node.name, node))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    node._class = cls                # type: ignore[attr-defined]
                    found.append((target.id, _property_getter(node) or node))
    return found


def api_missing(path: Path) -> list[str]:
    """Every undocumented public call in one module, as ``file:line: name``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}: cannot be read: {error}"]
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [f"{_name_for_report(path)}: does not parse: {error}"]

    classes = {node.name: node for node in tree.body
               if isinstance(node, ast.ClassDef)}
    wanted = _api_classes(tree, classes)
    # ``api`` itself has calls of its own, and the connection object is not
    # bound as a namespace - an addon is handed one by ``api.device.open()``.
    for name in (f"Addon{API_SUFFIX}", *EXTRA_CLASSES):
        if name in classes and name not in wanted:
            wanted.append(name)

    bad: list[str] = []
    for name in wanted:
        cls = classes.get(name)
        if cls is None:
            bad.append(f"{_name_for_report(path)}: class {name} is gone")
            continue
        for method, node in _public_calls(cls):
            if not (ast.get_docstring(node) or "").strip():
                location = f"{_name_for_report(path)}:{getattr(node, 'lineno', cls.lineno)}"
                bad.append(f"{location}: {name}.{method} says nothing about itself")
    if path.name == PROTOCOL and not (ast.get_docstring(tree) or "").strip():
        bad.append(f"{_name_for_report(path)}:1: the module has no docstring")
    return bad


def cmd_check_api_docs(argv: list[str]) -> int:
    """Stop a build on an addon-facing call that says nothing about itself.

    The Addon Dev tab's **Docs** pane builds its reference off ``addon_api`` as
    it runs: every public method and property of every namespace an addon can
    reach is listed with the first line of its docstring as the summary and the
    rest as the detail. A call with no docstring is not made to look good with
    invented words - the pane prints that it says nothing - which is honest but
    useless to the person reading it, and only ever noticed by whoever happens to
    open that page.

    Only the calls an addon is handed are held to this, and the set of them is
    taken from the same place the pane takes it. A private helper starting with an
    underscore is free to be undocumented, because nobody outside the module is
    told about it. ``addon_api`` is the only module read by default, because it is
    the only one that says what the calls do: an addon running in its own process
    gets a ``addon_client`` proxy that forwards these same names over the pipe.
    """
    _utf8_stdout()
    if argv:
        files = [Path(item) if Path(item).is_absolute() else APP / Path(item)
                 for item in argv]
    else:
        files = [APP / PROTOCOL]
    bad: list[str] = []
    for path in files:
        if path.is_dir():
            bad += [found for item in sorted(path.rglob("*.py"))
                    if item.name == PROTOCOL for found in api_missing(item)]
            continue
        bad += api_missing(path)
    print(f"checked {len(files)} module(s) for undocumented API calls")
    for line in bad:
        print(f"  {line}")
    if bad:
        print(f"\n{len(bad)} call(s) would show up in the Docs pane as saying "
              "nothing; nothing was built")
        return 1
    return 0


#: One command's failures, gathered so every check can report all of them and
#: not only the first; each command empties it on the way in.
FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    """One thing the tree has to satisfy; all of them are reported, not the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """A line of measurement, said for whoever reads the log afterwards."""
    print(f"       {text}")


def report_failures(closing: str) -> int:
    """Print what failed, or the one line that says the whole check passed."""
    if FAILURES:
        print()
        for text in FAILURES:
            print(f"FAILED: {text}")
        return 1
    print(f"\n{closing}")
    return 0


def cmd_check_guide_links(argv: list[str]) -> int:
    """Stop a build on a guide that links to a page nobody wrote.

    The Addon Dev tab's **Docs** pane is built off the running app: its guides
    come from a fixed table of markdown, its reference is read off ``addon_api``,
    and the two are stitched together by links an addon author clicks -
    ``guide:config`` to jump to another page, ``api:tabs.add_tab`` to jump to a
    call. A link to a page that was renamed, or never added, does not raise: the
    pane shows the words and the click does nothing, which is only ever noticed
    by whoever tries it.

    The templates the **New addon...** button writes are read too. They are text
    inside a ``string.Template``, so a mistake in one is a syntax error in a file
    the author only compiles after the app has handed it to them; here the
    template is filled in and parsed, which costs nothing and catches it first.
    """
    _utf8_stdout()
    del FAILURES[:]
    _use_app()
    import core.addon

    if not core.addon.api_reference():
        print("addon_api could not be imported, so the reference is empty "
              "and every api: link would be reported missing")
        return 1

    dev = core.addon
    print("\n1. the guides themselves")
    keys: list[str] = []
    titles = set()
    for key, title, text in dev.GUIDES:
        keys.append(key)
        page = dev._html(text)
        check(bool(page.strip()), f"{key} renders to something to read")
        check(key not in keys[:-1], f"{key} is a key no other guide has")
        check(title not in titles, f"{title!r} is a title no other guide has")
        titles.add(title)
    print(f"     {len(keys)} guide(s): {', '.join(keys)}")

    print("\n2. where the links inside them go")
    pages, calls = dev.api_counts()
    print(f"     the reference has {calls} name(s) across {pages} page(s)")
    for key, _title, text in dev.GUIDES:
        for match in dev._LINK_RE.finditer(text):
            target = match.group(2)
            if target.startswith("guide:"):
                check(target[6:] in keys,
                      f"{key} links to {target}, which is a guide")
            elif target.startswith("api:"):
                found = dev.api_entry(target[4:])
                check(found is not None,
                      f"{key} links to {target}, which the reference has")

    print("\n3. the page each namespace opens on")
    for addr, guide in sorted(dev.GUIDE_FOR_NAMESPACE.items()):
        check(guide in keys, f"api.{addr} sends the reader to {guide!r}, "
                             "which is a guide")

    print("\n4. the templates an author is handed")
    _template_parses(dev, dev._ADDON_SOURCE.substitute(
        id="probe", name="Probe", title="Probe", tab="Probe",
        menu="Probe", version="1.0", author="author",
        description="a probe addon"), "the addon template")
    _template_parses(dev, dev._TEST_SOURCE.substitute(id="probe", tab="Probe"),
                     "the test template")
    _template_parses(dev, dev._LAUNCHER.substitute(id="probe", module="probe"),
                     "the launcher template")
    return report_failures("the guides, their links and the templates all line up")


def _template_parses(dev: Any, source: str, what: str) -> None:
    """One filled-in template, read by ``ast``; ``dev`` is kept for the log line."""
    del dev
    try:
        ast.parse(source)
        check(True, f"{what} parses as written")
    except SyntaxError as error:
        check(False, f"{what} does not parse: line {error.lineno}: {error.msg}")


#: What the store's published addons say, and the picture the catalog should
#: name for each: every one of them now wears its own, under the ``<id>icon.png``
#: rule, in ``addon.store/assets/<id>/`` beside its source.
SHIPPED_CATALOG = {
    "cert_manager": (["ssh"], "app", "cert_managericon.png"),
    "openwrt_logs": (["ssh"], "both", "openwrt_logsicon.png"),
    "openwrt_package_manager": (["ssh", "sftp"], "app",
                                "openwrt_package_managericon.png"),
    # Every shell this addon opens is its own socket, on its own thread, so it
    # is the one published addon that may not ride the app's session at all.
    "multi_ssh": (["ssh"], "own", "multi_sshicon.png"),
    # The dashboard installer, which used to ship inside the program. It needs
    # both transports - SSH for uci and the check, SFTP for the files - and says
    # "both" so one session can be held open across a long transfer instead of
    # opened once per file.
    "berry_web": (["ssh", "sftp"], "both", "berry_webicon.png"),
    # The vault. It reads and writes one file in its own data folder and never
    # opens a transport, so it is the second shipped addon that must not ride the
    # app's session - and the reason the connection panel says "none" at all.
    "password_manager": ([], "none", "password_managericon.png"),
}


class _Quiet:
    """The manager's ``log`` as a check needs it: heard, and remembered nowhere."""

    def log(self, *args: Any, **kwargs: Any) -> None:
        pass


def cmd_check_connection_saying(argv: list[str]) -> int:
    """Stop a build on a tab that would say the wrong thing about its addon.

    An addon says which connection it rides - on the Settings page for a shipped
    addon, on the store's Addon manager for one installed later - and that saying
    comes out of two small decisions: how the word in a manifest's ``connection``
    is read, and which of the four sayings wins when an addon's transports, its
    being shipped with the program, and its declaration disagree. Both are pure,
    and both are quiet - a wrong answer does not crash, it just tells the user
    that an addon waits for a connection it never opens.

    The catalog the store reads is checked against the same rules, so a published
    entry and the page it will build cannot drift apart, and every addon this
    repository publishes is read as it really declares.
    """
    _utf8_stdout()
    del FAILURES[:]
    _use_app()
    import core.addon

    manager = core.addon
    print("\n1. which saying a tab shows")
    cases = (
        (([], False, "own"), "none", "no transports reaches no device"),
        (([], True, "both"), "none", "no transport beats being shipped"),
        ((["ssh"], True, "own"), "app", "a shipped addon rides the app"),
        ((["ssh"], True, "both"), "app", "a shipped addon gets no switch"),
        ((["ssh"], False, "own"), "own", "own stays own"),
        ((["ssh", "sftp"], False, "both"), "both", "both stays both"),
        ((["ssh"], False, "app"), "app", "app stays app"),
        ((["ssh"], False, ""), "app", "nothing said reads as the app's"),
        ((["ssh"], False, None), "app", "no answer reads as the app's"),
        ((["ssh"], False, "nonsense"), "app", "a word nobody knows falls back"),
        ((["ssh"], False, " BOTH "), "both", "the word is trimmed and lowered"),
    )
    for arguments, want, why in cases:
        got = manager.connection_choice(*arguments)
        check(got == want, f"{why}: {list(arguments)} -> {got!r}")

    print("\n2. what a manifest's word becomes")
    addon = manager.Addon("probe", "check", "", "file")
    for text, want in (("", "none"), ("none", "none"), ("NONE", "none"),
                       ("app", "app"), ("main", "app"), ("own", "own"),
                       ("private", "own"), ("both", "both"), ("BOTH", "both"),
                       ("weird", "keep"), (None, "keep")):
        addon.connection = "keep"
        addon.requires = ()
        manager.AddonManager.apply_manifest(_Quiet(), addon, {"connection": text})
        # A word nobody knows leaves the answer that was there, which for an
        # addon meeting the page for the first time is the app's own connection:
        # a bad declaration does not move a switch on its own.
        check(addon.connection == want,
              f"connection {text!r} read as {addon.connection!r}")

    print("\n3. requires only ever keeps the two real transports")
    addon = manager.Addon("probe", "check", "", "file")
    manager.AddonManager.apply_manifest(
        _Quiet(), addon, {"requires": ["SFTP", "ssh", "serial"]})
    check(addon.requires == ("sftp", "ssh"),
          f"requires came out as {addon.requires!r}")
    manager.AddonManager.apply_manifest(_Quiet(), addon, {"requires": "ssh"})
    check(addon.requires == ("ssh",),
          f"a plain string did not become one transport: {addon.requires!r}")
    manager.AddonManager.apply_manifest(_Quiet(), addon, {"requires": []})
    check(addon.requires == (),
          f"an empty list gained a transport: {addon.requires!r}")

    print("\n4. the published catalog, and the page it would build")
    catalog_path = ADDON_STORE / "index.json"
    if not catalog_path.is_file():
        check(False, f"there is no catalog at {catalog_path}")
        return report_failures("the tabs say what the manifests and the catalog mean")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = {str(row.get("id")): row for row in catalog.get("addons") or []}
    check(set(rows) == set(SHIPPED_CATALOG),
          f"the catalog lists {sorted(rows)}, expected {sorted(SHIPPED_CATALOG)}")
    for name, (requires, connection, icon) in SHIPPED_CATALOG.items():
        row = rows.get(name)
        if row is None:
            check(False, f"{name} is not in the catalog")
            continue
        check(list(row.get("requires") or []) == requires
              and row.get("connection") == connection
              and str(row.get("icon") or "") == icon,
              f"{name} is published as {row.get('requires')} / "
              f"{row.get('connection')} / {str(row.get('icon') or '')!r}")

        # The same words, read the way the app reads them onto the page.
        addon = manager.Addon(name, "check", "", "file")
        addon.connection = "app"
        manager.AddonManager.apply_manifest(_Quiet(), addon, row)
        shown = manager.connection_choice(addon.requires, False, addon.connection)
        check(shown == connection,
              f"{name}'s page would say {shown!r} where the catalog says "
              f"{connection!r}")

    # An addon with no picture of its own is the reason the default exists.
    for name, (_requires, _connection, icon) in SHIPPED_CATALOG.items():
        if icon:
            picture = ADDON_STORE / "assets" / name / icon
            where = "is there" if picture.is_file() else "is nowhere to be found"
            check(picture.is_file(), f"{name} names {icon}, which {where}")
        else:
            folder = ADDON_STORE / "assets" / name
            empty = not folder.is_dir() or not any(folder.iterdir())
            saying = ("has nothing, so the store shows the default one"
                      if empty else "holds a picture after all")
            check(empty, f"{name} names no picture and its folder {saying}")
    return report_failures("the tabs say what the manifests and the catalog mean")


# ================================================== the two catalog generators
#
# Both catalogs are derived from files that already sit in the tree, so neither
# is kept by hand: one drifts the moment an addon or a theme is added, renamed or
# versioned. Both write stable JSON, so a build that regenerates one has
# something to commit only when the catalog really moved.

#: What an addon's own picture is always called, and the plain name for an addon
#: that has one picture and no standard name.
GENERIC_ICON = "icon.png"

#: Icon suffixes worth passing to PyInstaller, most preferred first.
ICON_SUFFIXES = (".ico", ".icns", ".png")

#: Endings a folder picture may have at all, for a last look for something.
PICTURE_SUFFIXES = ICON_SUFFIXES + (".svg", ".jpg", ".jpeg", ".webp")


def read_manifest(path: Path) -> dict:
    """The ``ADDON = {...}`` dict of one file, read without importing it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ADDON"
                for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def list_assets(store: Path, addon_id: str) -> list[str]:
    """The asset files under ``addon.store/assets/<id>/``, as paths from that folder."""
    folder = store / "assets" / addon_id
    if not folder.is_dir():
        return []
    return sorted(path.relative_to(folder).as_posix()
                  for path in folder.rglob("*") if path.is_file())


def connection_of(manifest: dict) -> str:
    """How one addon reaches a device, as the catalog should say it.

    The manifest's own word wins. An addon that names nothing but does need a
    transport talks over the app's connection - that is what ``requires`` gates
    on - so it is reported as ``app`` rather than as needing no device, and an
    addon needing nothing is ``none``. A word nothing recognises is kept, so a
    fork's own vocabulary still shows.
    """
    said = flat(manifest.get("connection")).lower()
    if said:
        return said
    return "app" if words(manifest.get("requires")) else "none"


def pick_icon(assets: list[str], addon_id: str, declared: str = "") -> str:
    """The asset that stands for one addon, picked by the naming rule.

    The standard name goes first, so an addon that follows the rule needs no
    declaration; then the name its own manifest declares, then the build icons
    under its id, then a plain ``icon.png``, and last the one picture its folder
    holds - which is how an addon that predates the rule keeps the file it has.
    """
    named = {name.lower(): name for name in assets}

    def take(value: Any) -> str:
        return named.get(flat(value).lower(), "")

    wanted = [take(f"{addon_id}{ICON_TAIL}"), take(declared)]
    wanted += [take(f"{addon_id}{suffix}") for suffix in ICON_SUFFIXES]
    wanted.append(take(GENERIC_ICON))
    for item in wanted:
        if item:
            return item
    for suffix in PICTURE_SUFFIXES:
        for name in assets:
            if PurePosixPath(name).suffix.lower() == suffix:
                return name
    return ""


def list_test(store: Path, addon_id: str) -> str:
    """``test_<id>.py`` when the store folder ships one for this addon, else nothing.

    The store runs this test over a freshly downloaded addon before it spends
    time compiling it, so an addon that carries a test gets checked on install.
    """
    name = f"test_{addon_id}.py"
    if (store / "_tests" / name).is_file():
        return name
    return ""


def addon_index_build(store: Path, previous: dict | None = None) -> dict:
    """The whole addon catalog for one store folder, marked against the last index."""
    store = Path(store)
    before = {str(entry.get("id")): entry
              for entry in (previous or {}).get("addons", [])
              } if isinstance(previous, dict) else {}
    entries: list[dict] = []
    for path in sorted(store.glob("*.py")):
        if path.name.startswith("_"):
            continue
        manifest = read_manifest(path)
        if not manifest:
            continue
        addon_id = clean_id(path.stem, "addon")
        assets = list_assets(store, addon_id)
        entry: dict[str, Any] = {
            "id": addon_id,
            "name": str(manifest.get("name") or addon_id),
            "version": str(manifest.get("version") or ""),
            "author": str(manifest.get("author") or ""),
            "description": flat(manifest.get("description")),
            "filename": path.name,
            # What the addon says it needs, and how it reaches the device. The
            # store shows both, so a person can see before installing whether
            # this works on the machine in front of them or talks to a router.
            "requires": words(manifest.get("requires")),
            "connection": connection_of(manifest),
        }
        if assets:
            entry["assets"] = assets
        icon = pick_icon(assets, addon_id, str(manifest.get("icon") or ""))
        if icon:
            entry["icon"] = icon
        test_name = list_test(store, addon_id)
        if test_name:
            entry["test"] = test_name
        status = mark_status(entry, before.get(addon_id))
        if status:
            entry["status"] = status
        entries.append(entry)
    return {"addons": entries}


def cmd_gen_addon_store_index(argv: list[str]) -> int:
    """Generate the addon catalog, or only report whether the file on disk is current."""
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="buildtools.py gen-addon-store-index",
        description="Build addon.store/index.json from the addons in the store folder.")
    parser.add_argument("--store", type=Path, default=ADDON_STORE,
                        help="the addon.store folder to read")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; fail if the index is out of date")
    args = parser.parse_args(argv)
    return write_index(args.store, "addons", addon_index_build,
                       "gen-addon-store-index", "addon(s)", args.check)


#: The ending a theme file always has, the same one the app reads the themes
#: folder with.
THEME_GLOB = "*.theme"


def read_theme(path: Path) -> dict:
    """The JSON object one ``.theme`` file holds; ``{}`` when it holds none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def theme_index_build(store: Path, previous: dict | None = None) -> dict:
    """The whole theme catalog for one store folder, marked against the last index."""
    store = Path(store)
    before = {str(entry.get("id")): entry
              for entry in (previous or {}).get("themes", [])
              } if isinstance(previous, dict) else {}
    entries: list[dict] = []
    for path in sorted(store.glob(THEME_GLOB), key=lambda one: one.name.lower()):
        if path.name.startswith("_"):
            continue
        data = read_theme(path)
        if not data:
            continue
        theme_id = clean_id(path.stem, "theme")
        entry: dict[str, Any] = {
            "id": theme_id,
            "name": str(data.get("name") or path.stem),
            # A theme that does not say its version is the file it always was;
            # the key is not one a ``.theme`` normally carries, so raising it is
            # an author's deliberate "take this one as newer".
            "version": flat(data.get("version")) or "1",
            "author": flat(data.get("author")),
            "blurb": flat(data.get("blurb")),
            "filename": path.name,
        }
        status = mark_status(entry, before.get(theme_id))
        if status:
            entry["status"] = status
        entries.append(entry)
    return {"themes": entries}


def cmd_gen_theme_store_index(argv: list[str]) -> int:
    """Generate the theme catalog, or only report whether the file on disk is current."""
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="buildtools.py gen-theme-store-index",
        description="Build themes.store/index.json from the themes in the store folder.")
    parser.add_argument("--store", type=Path, default=THEME_STORE,
                        help="the themes.store folder to read")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; fail if the index is out of date")
    args = parser.parse_args(argv)
    return write_index(args.store, "themes", theme_index_build,
                       "gen-theme-store-index", "theme(s)", args.check)


def write_index(store: Path, key: str, builder: Any, name: str,
                count_word: str, only_check: bool) -> int:
    """The one shape both catalogs are written in: build, compare, then commit.

    ``key`` is the field the JSON holds the entries under, ``builder`` makes the
    catalog out of the folder, and ``name`` is the command a person is pointed at
    when the file on disk has fallen behind.
    """
    store = Path(store)
    target = store / "index.json"
    index = builder(store, load_index(target))
    text = render_index(index)
    try:
        # A file nothing can read is not the new one; ``load_index`` already
        # treats it as nothing, and a check says the same thing out loud.
        current = target.read_text(encoding="utf-8") if target.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    count = len(index.get(key) or [])
    if only_check:
        if text == current:
            print(f"{target.name} is up to date ({count} {count_word})")
            return 0
        print(f"{target.name} is out of date; run: python buildtools.py {name}")
        return 1
    target.write_text(text, encoding="utf-8")
    changed = [entry["id"] for entry in index.get(key) or []
               if entry.get("status") in ("new", "updated")]
    print(f"wrote {target} with {count} {count_word}"
          + (f"; marked {', '.join(changed)}" if changed else ""))
    return 0


# ==================================================== the three picture writers
#
# Every mark the program and the dashboard wear is drawn somewhere in the tree -
# an SVG in an addon's ``TAB_ICON``, a painter in ``core.ui``, the app's own logo
# - and these write those drawings out as the files that ship. Nothing is edited
# in a bitmap and nothing is resized by eye, so what is shipped and what stands
# in for it cannot disagree. Qt is imported inside these commands only: a machine
# without it can still read the source and rebuild a catalog.

#: Where each family of addons keeps its source, and where its pictures go.
#: The program's own assets folder serves the addons that ship inside it; a
#: store addon's pictures stay beside its source and travel to whoever installs it.
ICON_FAMILIES = (
    (APP / "addons", ASSETS),
    (ADDON_STORE, ADDON_STORE / "assets"),
)

#: The GUI interface, made once and kept: Qt destroys a ``QApplication`` the
#: moment Python drops its last reference, and a ``QPixmap`` built after that
#: takes the program down with it rather than answering.
_QT_APP: Any = None


def icon_name(addon_id: str) -> str:
    """The one file name an addon's own picture is looked up under."""
    return f"{addon_id}{ICON_TAIL}"


def read_tab_icon(path: Path) -> str:
    """The ``TAB_ICON`` literal of one addon file, without importing the file.

    ``ast`` is what makes this safe: the addon's own code never runs, so a module
    that reaches for a device or a config file at import time is no obstacle. A
    drawing written as several adjacent strings is folded by ``literal_eval``,
    which is how the sources hold them; anything the literal reader refuses is
    simply no drawing.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        named = any(isinstance(target, ast.Name) and target.id == "TAB_ICON"
                    for target in node.targets)
        if not named:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            return ""
        return value if isinstance(value, str) else ""
    return ""


def find_icon_addons(wanted: list[str]) -> list[tuple[str, Path, Path]]:
    """``(id, source, where its picture goes)`` for every addon that can have one.

    A folder addon is not walked here: its id is its folder and its picture is
    already under the rule, and this is about the addons the program ships and
    the store publishes. Names beginning with ``_`` are skipped, as the runtime
    skips them.
    """
    found: list[tuple[str, Path, Path]] = []
    for source_dir, asset_dir in ICON_FAMILIES:
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.glob("*.py")):
            addon_id = path.stem
            if addon_id.startswith("_") or addon_id in {"_tests"}:
                continue
            if wanted and addon_id not in wanted:
                continue
            folder = asset_dir if source_dir == (APP / "addons") \
                else asset_dir / addon_id
            found.append((addon_id, path, folder))
    return found


def _qt_bar() -> Any:
    """The GUI bar Qt wants before anything is drawn or read, kept alive here.

    Qt destroys a ``QApplication`` the moment Python drops its last reference, and
    a picture made after that takes the program down rather than answering, so the
    one bar all three picture commands share is held on the module and not in a
    local name that goes out of scope.
    """
    global _QT_APP
    if _QT_APP is None:
        from PyQt6.QtWidgets import QApplication

        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def raster(svg: str, side: int) -> Any:
    """One SVG document as a square transparent-field image; ``None`` if Qt
    cannot draw it, which is said out loud rather than written as a blank file."""
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

    _qt_bar()
    drawn = QPixmap()
    if not drawn.loadFromData(QByteArray(svg.encode("utf-8")), "SVG"):
        return None
    canvas = QImage(int(side), int(side), QImage.Format.Format_ARGB32)
    canvas.fill(QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(0, 0, int(side), int(side), drawn)
    painter.end()
    return canvas


def cmd_make_addon_icons(argv: list[str]) -> int:
    """Write every addon's own tab picture into the folder its icons live in.

    Each addon keeps its mark in two shapes: an SVG document held in ``TAB_ICON``,
    which the app draws onto the tab bar, and a picture file, which is what the
    addon store's card wears and what a built program carries inside itself. The
    file is the half a person can change, so it is the half worth having in the
    tree - and it has to answer to one name, the rule every lookup already uses:
    the addon's id and then ``icon.png``.

    Nothing is imported: the file is parsed and ``TAB_ICON`` is read out of it, so
    an addon that needs Qt, or a device, or its own config file before it can be
    imported still gets its picture. An existing file is left exactly as it is
    unless ``--force``, because artwork drawn by hand should never be replaced by
    the program's own fallback mark.
    """
    _utf8_stdout()
    _use_app()
    parser = argparse.ArgumentParser(
        prog="buildtools.py make-addon-icons",
        description="Write each addon's tab picture out of its own TAB_ICON.")
    parser.add_argument("ids", nargs="*", help="only these addons, by their id")
    parser.add_argument("--side", type=int, default=128,
                        help="the picture's width and height (default 128)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report which pictures are missing")
    args = parser.parse_args(argv)

    wanted = [str(item) for item in args.ids]
    side = max(16, int(args.side))
    missing: list[str] = []
    written: list[str] = []
    kept: list[str] = []
    nodrawing: list[str] = []

    for addon_id, source, folder in find_icon_addons(wanted):
        target = folder / icon_name(addon_id)
        if target.is_file() and not args.force:
            kept.append(str(target.relative_to(APP)))
            continue
        svg = read_tab_icon(source)
        if not svg:
            # An addon with no drawing of its own is not an error: its tab wears
            # the program's default tab picture until somebody puts a file under
            # this name, and the store shows it its placeholder card.
            nodrawing.append(addon_id)
            missing.append(addon_id)
            continue
        if args.check:
            missing.append(addon_id)
            print(f"missing  {target.relative_to(APP)} "
                  f"(a drawing is held in {source.name})")
            continue
        image = raster(svg, side)
        if image is None:
            print(f"cannot draw {addon_id}: {source.name} holds no SVG Qt reads")
            missing.append(addon_id)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        if not image.save(str(target), "PNG"):
            print(f"could not write {target}")
            missing.append(addon_id)
            continue
        written.append(str(target.relative_to(APP)))

    for path in written:
        print(f"wrote    {path}")
    # ``--check`` writes nothing, so nothing was "kept": the file simply is
    # where the rule says it should be.
    kept_word = "there    " if args.check else "kept     "
    for path in kept:
        print(f"{kept_word}{path}")
    for addon_id in nodrawing:
        print(f"no drawing in the source of {addon_id}; its picture is the one "
              f"a person puts there: {icon_name(addon_id)}")
    if args.check and missing:
        print(f"{len(missing)} addon(s) hold no picture file")
        return 1
    if not written:
        print("nothing to write: every addon that can draw one already has its file")
    else:
        print(f"wrote {len(written)} picture(s), kept {len(kept)} that were already there")
    return 0


#: The program's own four colours.
BACK = "#161616"
RIM = "#c51a4a"
OK = "#23d18b"
LINE = "#8a8a8a"

#: The square each mark is drawn into, and the stroke it is drawn with at that
#: size. Everything is scaled from these, so a bigger tile gets a proportionally
#: thicker line and stays legible when the tab asks it for 18px.
STORE_SIDE = 512
STROKE = 22

#: Where the tile starts inside the canvas, in the same 512 units as the marks.
INSET = 26


def _canvas(size: int) -> tuple[Any, float, Any]:
    """A transparent square, with the scale one draw pass should work in."""
    from PyQt6.QtGui import QColor, QImage, QPainter

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return image, size / STORE_SIDE, painter


def _tile(painter: Any, scale: float, size: int) -> None:
    """The rounded near-black tile every mark sits on, with a crimson rim."""
    from PyQt6.QtGui import QColor, QPen, QRectF

    inset = INSET * scale
    side = size - 2 * inset
    painter.setBrush(QColor(BACK))
    painter.setPen(QPen(QColor(RIM), 10 * scale))
    painter.drawRoundedRect(QRectF(inset, inset, side, side),
                            18 * scale, 18 * scale)


def _pen(painter: Any, color: str, scale: float, weight: float = 1.0) -> None:
    """One round-capped stroke of the tile's own weight, scaled with it."""
    from PyQt6.QtGui import QColor, QPen
    from PyQt6.QtCore import Qt

    painter.setPen(QPen(QColor(color), STROKE * weight * scale,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _at(point: tuple[float, float], scale: float) -> Any:
    """One 512-unit coordinate, moved onto the tile being drawn."""
    from PyQt6.QtCore import QPointF

    inset = INSET * scale
    return QPointF(inset + point[0] * scale, inset + point[1] * scale)


def _line(painter: Any, a: tuple, b: tuple, scale: float) -> None:
    painter.drawLine(_at(a, scale), _at(b, scale))


def _shape(painter: Any, points: list, scale: float) -> None:
    """An open polyline through 512-unit coordinates, as one stroke."""
    from PyQt6.QtGui import QPolygonF

    painter.drawPolyline(QPolygonF([_at(point, scale) for point in points]))


def _dot(painter: Any, center: tuple, scale: float, radius: float) -> None:
    spot = _at(center, scale)
    painter.drawEllipse(spot, radius * scale, radius * scale)


def draw_default(size: int = STORE_SIDE) -> Any:
    """A plugin tile: a square with one corner broken off, and its dot.

    The missing corner is where the piece would join another, and the green dot is
    the one thing every addon with a picture of its own shows differently.
    """
    image, scale, painter = _canvas(size)
    _tile(painter, scale, size)
    _pen(painter, LINE, scale, 0.9)
    _shape(painter, [(150, 150), (150, 360), (360, 360), (360, 250),
                     (300, 250), (300, 150), (150, 150)], scale)
    _pen(painter, RIM, scale, 0.7)
    for x in (200, 260):                     # the two pins below the piece
        _line(painter, (x, 360), (x, 410), scale)
    _pen(painter, OK, scale, 0.6)
    _dot(painter, (330, 190), scale, 22)
    painter.end()
    return image


def draw_packages(size: int = STORE_SIDE) -> Any:
    """An open crate with a list of what is in it, ticked off line by line.

    The three lines with ticks are what a package manager reads and writes; the
    crate is where they came from; the flaps standing open are that it is a store.

    No file is written from it any more: OpenWrt Package Manager put its own mark
    under the ``<id>icon.png`` rule, and ``make-addon-icons`` draws that one out
    of the addon's own ``TAB_ICON``. It is kept because the tile it sits on is
    the shape a hand-drawn card wants, and a fork that names a picture in its
    manifest can still be drawn from here.
    """
    image, scale, painter = _canvas(size)
    _tile(painter, scale, size)
    _pen(painter, LINE, scale, 0.85)
    _shape(painter, [(140, 220), (140, 380), (370, 380), (370, 220)], scale)
    _shape(painter, [(140, 220), (100, 150), (200, 150), (255, 220)], scale)
    _shape(painter, [(370, 220), (410, 150), (310, 150), (255, 220)], scale)
    _pen(painter, RIM, scale, 0.7)
    for y in (265, 310, 350):                # what is in the crate, as lines
        _line(painter, (215, y), (335, y), scale)
    _pen(painter, OK, scale, 0.6)
    for y in (265, 310, 350):                # each one answered for with a tick
        _shape(painter, [(170, y - 12), (190, y + 10), (205, y - 18)], scale)
    painter.end()
    return image


def draw_interface_mark(paint: Any, size: int = 128) -> Any:
    """One of the bar's own paintings, written out as a file of ``size`` a side.

    The painter is the very function ``core.ui`` falls back to when no asset is
    beside it, called in the 32-unit space it was written for and scaled up from
    there - so the shipped picture and the stand-in are one drawing seen at two
    sizes rather than two drawings that have to be kept alike. The colours come
    from the theme in force, which in a fresh checkout is the built-in one.
    """
    _use_app()
    from PyQt6.QtGui import QColor, QImage, QPainter

    canvas = QImage(int(size), int(size), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 32.0, size / 32.0)
    paint(painter)
    painter.end()
    return canvas


def _interface_targets(size: int) -> list:
    """The three marks the window paints itself, paired with where each goes.

    Read here rather than at import time: a machine with no Qt installed still
    has no business failing a tool it never runs, and ``--check`` needs the paths
    long before anything is drawn.
    """
    _use_app()
    import core.ui

    return [
        (ASSETS / "defaulttabicon.png",
         lambda: draw_interface_mark(core.ui.paint_default_tab, size)),
        (ASSETS / "terminalicon.png",
         lambda: draw_interface_mark(core.ui.paint_terminal_tab, size)),
        (ASSETS / "settingsicon.png",
         lambda: draw_interface_mark(core.ui.paint_settings_tab, size)),
    ]


def cmd_make_store_icons(argv: list[str]) -> int:
    """Write the pictures the program itself wears into its assets folder.

    Three of these marks are drawn by the interface at run time when no file is
    beside it - the tile any unnamed tab wears, the shell the terminal tab wears,
    the cog the pinned Settings tab wears - and this writes those same drawings
    out as files, so what ships and what stands in for it cannot disagree: the
    painters come out of :mod:`core.ui` itself. The fourth is the store's own
    default - the tile an addon with no picture of its own is shown with - drawn
    here in the program's colours because no run-time code has any need to draw
    it. An addon's own mark is not this command's business: ``make-addon-icons``
    writes those.
    """
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="buildtools.py make-store-icons",
        description="Write the program's own pictures out of the painters in core.ui.")
    parser.add_argument("--side", type=int, default=128,
                        help="the picture's width and height (default 128)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report which pictures are missing")
    args = parser.parse_args(argv)
    size = max(16, int(args.side))

    targets = [(ASSETS / "storedefaultaddon.png",
                lambda: draw_default(size))] + _interface_targets(size)
    if args.check:
        missing = [str(path.relative_to(APP)) for path, _draw in targets
                   if not path.is_file()]
        for name in missing:
            print(f"missing  {name}")
        if missing:
            print(f"{len(missing)} of the program's own pictures are not in assets")
            return 1
        print(f"the program's own {len(targets)} picture(s) are all in assets")
        return 0

    # The one GUI bar, held on the module for as long as the drawing lasts: Qt
    # destroys a ``QApplication`` the moment Python drops its last reference, and
    # a picture made after that is lost rather than written.
    _use_app()
    _qt_bar()
    made = 0
    kept = 0
    for path, drawer in targets:
        if path.is_file() and not args.force:
            kept += 1
            print(f"kept     {path.relative_to(APP)}")
            continue
        image = drawer()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            print(f"FAILED: {path} could not be written")
            return 1
        made += 1
        print(f"wrote    {path.relative_to(APP)} at {image.width()}x{image.height()}")
    print(f"{made} written, {kept} already there")
    return 0


#: The one picture the dashboard's whole set is cut from: the program's own logo.
APP_ICON = ASSETS / "icon.png"

#: ``(file name, side)`` for each PNG the page asks for, smallest first.
WEB_PNG_TARGETS = (
    ("favicon-16x16.png", 16),
    ("favicon-32x32.png", 32),
    ("apple-touch-icon.png", 180),
    ("android-chrome-192x192.png", 192),
    ("android-chrome-512x512.png", 512),
)

#: The frames carried inside ``favicon.ico``. 16 and 32 are what browsers ask for
#: today; 48 is what a Windows desktop tile draws.
ICO_SIZES = (16, 32, 48)

ICO_NAME = "favicon.ico"

#: The name of the PNG that ``--side`` is allowed to redraw at another size. It
#: keeps its name whatever it is drawn at: a phone stores the size it fetched, and
#: the manifest points at the file, not at its measurements.
LARGEST_PNG = "android-chrome-512x512.png"

#: How far apart two rasters may be, per byte on average, and still be called
#: one picture. A smooth downscale lands a unit or two off between one Qt build
#: and another on the edge of a stroke; a genuinely different icon is worth tens.
PIXEL_SLACK = 8.0


def web_targets(side: int) -> list[tuple[str, int]]:
    """The PNG set to write, with the biggest file drawn at ``side``."""
    return [(name, max(16, int(side)) if name == LARGEST_PNG else size)
            for name, size in WEB_PNG_TARGETS]


def app_icon() -> Any:
    """The program's own picture as Qt reads it, or ``None``."""
    from PyQt6.QtGui import QImage

    if not APP_ICON.is_file():
        return None
    image = QImage(str(APP_ICON))
    return None if image.isNull() else image


def cut(base: Any, side: int) -> Any:
    """One square ARGB raster, the whole source scaled into it.

    A non-square source is trimmed from the middle first, so a mark that grows
    taller than wide is still centred rather than squashed.
    ``SmoothTransformation`` is the difference between a readable 16-pixel mark
    and a block of pixels: the source is 512 a side, so a tab icon is downscaled
    by thirty-two and every part of the drawing has to reach the result.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    square = base
    wide, tall = base.width(), base.height()
    if wide != tall:
        short = min(wide, tall)
        square = base.copy(int((wide - short) / 2), int((tall - short) / 2),
                           short, short)
    image = square.scaled(int(side), int(side),
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def png_of(image: Any) -> bytes:
    """One ``QImage`` as PNG bytes, without touching the disk."""
    from PyQt6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt refused to write a PNG")
    return bytes(buffer.data())


def image_pixels(image: Any) -> bytes:
    """The picture's own bytes, as premultiplied ARGB, whatever format it held.

    Rows go in whole, padding and all: two images of one width have one row
    length, so the padding cannot make the same drawing read as a different one.
    Premultiplied rather than straight, because a transparent pixel is then zero
    on every channel no matter what colour was stored under it - so the empty
    corners of a mark cannot drift for the wrong reason when two platforms read
    the same file.
    """
    flat_image = image.convertToFormat(image.Format.Format_ARGB32_Premultiplied)
    total = flat_image.bytesPerLine() * flat_image.height()
    return bytes(flat_image.constBits().asstring(total))


def same_picture(first: Any, second: Any) -> bool:
    """Whether two images are the same picture, give or take a platform's rounding.

    The check is a mean distance per byte over the whole premultiplied raster,
    not an equality of bytes: this tool's own output is held to, but a runner's
    Qt is allowed to round a scaled edge differently than the machine that drew
    the file did. Two faithful downscales of one source are nowhere near
    ``PIXEL_SLACK`` apart; the program's icon and any other picture are far above
    it.
    """
    a = image_pixels(first)
    b = image_pixels(second)
    if len(a) != len(b):
        return False
    if not a:
        return True
    total = sum(x - y if x >= y else y - x for x, y in zip(a, b))
    return total / len(a) <= PIXEL_SLACK


def ico_of(pictures: list) -> bytes:
    """PNG-compressed ``.ico``: the directory header, the entries, then frames.

    ``pictures`` is ``(side, png bytes)`` per frame, biggest last. A width or a
    height of 256 is written as 0, which is how the format says "larger than a
    byte can hold"; the rest is lengths and offsets, and offsets are counted from
    the front of the file. Built here rather than saved by Qt because Qt's ICO
    writer wants a ``QIcon`` and picks its own frames - and this has to run in CI
    on a machine that holds PyQt6 and nothing else.
    """
    head = struct.pack("<HHH", 0, 1, len(pictures))
    entries = b""
    body = b""
    offset = len(head) + 16 * len(pictures)
    for side, png in pictures:
        byte = 0 if int(side) >= 256 else int(side)
        entries += struct.pack("<BBBBHHII", byte, byte, 0, 0, 1, 32,
                               len(png), offset)
        offset += len(png)
        body += png
    return head + entries + body


def png_bytes(base: Any, size: int) -> bytes:
    """The bytes one PNG target should hold."""
    return png_of(cut(base, size))


def ico_bytes(base: Any) -> bytes:
    """The bytes ``favicon.ico`` should hold: every frame, in order."""
    return ico_of([(side, png_bytes(base, side)) for side in ICO_SIZES])


def why_png_differs(path: Path, base: Any, size: int) -> str:
    """``""`` when the file shows this picture, else the reason it does not."""
    from PyQt6.QtGui import QImage

    if not path.is_file():
        return "is not there"
    if path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        return "is not a PNG"
    have = QImage(str(path))
    if have.isNull():
        return "is not a picture a browser can read"
    if have.width() != size or have.height() != size:
        return f"is {have.width()}x{have.height()}, not {size}x{size}"
    if not same_picture(cut(base, size), have):
        return "is not the program's icon"
    return ""


def why_ico_differs(path: Path, base: Any) -> str:
    """The same question of an ICO, asked of every frame inside it.

    The file is taken apart rather than handed to Qt because an image reader
    returns the first frame and nothing else, and the point of the check is that
    each size the browser may ask for is really in there.
    """
    if not path.is_file():
        return "is not there"
    from PyQt6.QtGui import QImage

    data = path.read_bytes()
    if len(data) < 6:
        return "is too short to be an ICO"
    _reserved, kind, count = struct.unpack("<HHH", data[:6])
    if kind != 1:
        return "is not an icon file but a bitmap group"
    if count != len(ICO_SIZES):
        return f"holds {count} frame(s), not the {len(ICO_SIZES)} this writes"
    for index, side in enumerate(ICO_SIZES):
        entry = data[6 + 16 * index: 22 + 16 * index]
        if len(entry) < 16:
            return "has a frame whose entry is cut off"
        wide, high, _colors, _reserved, _planes, _bits, length, start = \
            struct.unpack("<BBBBHHII", entry)
        if (wide or 256) != side or (high or 256) != side:
            return f"frame {index + 1} is {(wide or 256)}x{(high or 256)}"
        frame = data[start:start + length]
        image = QImage()
        if not image.loadFromData(frame, "PNG"):
            return f"frame {index + 1} holds no PNG Qt can read"
        if not same_picture(cut(base, side), image):
            return f"frame {index + 1} is not the program's icon"
    return ""


def picture_line(path: Path, why: str) -> str:
    """One line of output about one file, named the way a person looks for it."""
    word = "same     " if not why else "drift    "
    text = str(path.relative_to(WEB_ASSETS.parent.parent))
    return f"{word}{text}" + (f": {why}" if why else "")


def cmd_make_web_icons(argv: list[str]) -> int:
    """Write the dashboard's pictures out of the picture the program itself wears.

    The web interface used to carry artwork of its own, drawn for a page and left
    there: a browser tab showing something other than the app that installs it is
    a small lie told twice a day. So every one of the page's pictures now comes
    out of ``assets/icon.png`` - the file the window, the built program and the
    taskbar all use - cut to the sizes a browser and a phone actually ask for.
    The names are not free: they are the ones ``cgi-bin/pages.py`` links to and
    the ones a phone writes down when a page is added to a home screen.

    ``--check`` writes nothing and exits 1 when a file is missing, is the wrong
    size, or shows a different picture than the app's icon would produce - which
    is how a workflow can hold the page to the program without redrawing anything.
    It compares what a browser sees (the decoded pixels, premultiplied, and for an
    ICO each frame inside it) rather than the bytes one Qt version happens to
    write, and it allows ``PIXEL_SLACK`` per byte, because one platform's smooth
    downscale rounds the edge of a stroke a unit or two off another's.
    """
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="buildtools.py make-web-icons",
        description="Cut the dashboard's pictures out of the program's own icon.")
    parser.add_argument("--side", type=int, default=512,
                        help=f"the size {LARGEST_PNG} is drawn at (default 512)")
    parser.add_argument("--force", action="store_true",
                        help="rewrite a file that is already there")
    parser.add_argument("--check", action="store_true",
                        help="write nothing, only report what has drifted")
    args = parser.parse_args(argv)

    _use_app()
    if not APP_ICON.is_file():
        print(f"there is no program icon at {APP_ICON}")
        return 2
    _qt_bar()
    base = app_icon()
    if base is None:
        print(f"{APP_ICON} cannot be read as a picture")
        return 2

    drift = 0
    written = 0
    kept = 0

    for name, size in web_targets(args.side):
        path = WEB_ASSETS / name
        if args.check:
            why = why_png_differs(path, base, size)
            print(picture_line(path, why))
            drift += 1 if why else 0
            continue
        if path.is_file() and not args.force:
            kept += 1
            print(picture_line(path, ""))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes(base, size))
        written += 1
        print(f"wrote    {path.relative_to(WEB_ASSETS.parent.parent)} "
              f"at {size}x{size}")

    ico = WEB_ASSETS / ICO_NAME
    if args.check:
        why = why_ico_differs(ico, base)
        print(picture_line(ico, why))
        drift += 1 if why else 0
    elif ico.is_file() and not args.force:
        kept += 1
        print(picture_line(ico, ""))
    else:
        ico.parent.mkdir(parents=True, exist_ok=True)
        ico.write_bytes(ico_bytes(base))
        written += 1
        print(f"wrote    {ico.relative_to(WEB_ASSETS.parent.parent)} "
              f"({len(ICO_SIZES)} frames)")

    if args.check:
        if drift:
            print(f"{drift} of the dashboard's picture(s) do not match "
                  f"{APP_ICON.relative_to(APP)}")
            return 1
        print("the dashboard wears the program's icon in every picture it links to")
        return 0
    if written:
        print(f"wrote {written} picture(s), kept {kept} that were already there")
    else:
        print("nothing written: the dashboard already has its pictures "
              "(--force to draw them again)")
    return 0


# ============================================================== the command bar

#: ``name -> (what it does, the line the help shows)`` in the order a build runs
#: them: read the source, rebuild what is derived from it, then draw the pictures.
COMMANDS: dict[str, tuple[Any, str]] = {
    "check-names": (
        cmd_check_names,
        "every module read for a name nothing binds (needs pyflakes)"),
    "check-api-docs": (
        cmd_check_api_docs,
        "every addon-facing call that has no docstring"),
    "check-guide-links": (
        cmd_check_guide_links,
        "every link in the Addon Dev guides, and the templates it writes"),
    "check-connection-saying": (
        cmd_check_connection_saying,
        "what a tab and the published catalog say about a connection"),
    "gen-addon-store-index": (
        cmd_gen_addon_store_index,
        "write addon.store/index.json from the addons in the folder"),
    "gen-theme-store-index": (
        cmd_gen_theme_store_index,
        "write themes.store/index.json from the themes in the folder"),
    "make-addon-icons": (
        cmd_make_addon_icons,
        "draw each addon's picture out of its own TAB_ICON"),
    "make-store-icons": (
        cmd_make_store_icons,
        "draw the program's own pictures out of the painters in core.ui"),
    "make-web-icons": (
        cmd_make_web_icons,
        "cut the dashboard's favicons and tiles out of the app's icon"),
}


def usage() -> str:
    """The whole help: what to type, and what each name answers."""
    lines = [f"usage: buildtools.py <command> [args]      (in {REPO})", "",
             "the build-time tools of Berry Manager; every path is read from",
             "this file's own place, so it can be run from any folder.", "",
             "commands:"]
    width = max(len(name) for name in COMMANDS)
    for name, (_run, saying) in COMMANDS.items():
        lines.append(f"  {name.ljust(width)}  {saying}")
    lines += ["", "each command takes its own --help,",
              "and --check on the generators and the picture writers writes",
              "nothing and only says what has fallen behind."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run one command by name; the arguments after it belong to that command."""
    _utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("--list", "list"):
        for name in COMMANDS:
            print(name)
        return 0
    wanted = argv[0].replace("_", "-").lstrip("-")
    found = COMMANDS.get(wanted)
    if found is None:
        # A name typed wrong is worth the list rather than a bare exit code:
        # a workflow log that says nothing about the nine names is how a build
        # gets re-run three times over a hyphen.
        print(f"there is no command {argv[0]!r}\n")
        print(usage())
        return 2
    runner, _saying = found
    return runner(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
