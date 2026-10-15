#!/usr/bin/env python3
"""Stop a build on an addon-facing call that says nothing about itself.

The Addon Dev tab's **Docs** pane builds its reference off ``addon_api`` as it
runs: every public method and property of every namespace an addon can reach is
listed with the first line of its docstring as the summary and the rest as the
detail. A call with no docstring is not made to look good with invented words -
the pane prints that it says nothing - which is honest but useless to the person
reading it, and only ever noticed by whoever happens to open that page. This
looks for them instead, before anything is built::

    pip install -r requirements.txt      # nothing extra: this reads text only
    python tools/check_api_docs.py       # the whole protocol
    python tools/check_api_docs.py addon_api.py

Only the calls an addon is handed are held to this, and the set of them is taken
from the same place the pane takes it: the namespaces ``AddonAPI.__init__`` binds
(``api.tabs``, ``api.ui`` and the rest), ``api`` itself, and the connection object
``api.device.open()`` returns. A private helper starting with an underscore is
free to be undocumented, because nobody outside the module is told about it.

``addon_api`` is the only module read, because it is the only one that says what
the calls do: an addon running in its own process gets a ``addon_client`` proxy
that forwards these same names over the pipe, so the words written here are what
it reads too, and its own classes are plumbing rather than an interface.

Why this is a build step rather than a review habit: the reference is only ever
regenerated when someone opens the pane, so a new call added without a docstring
would sit unexplained in every program built from here on, and the gap is plain
to see without running anything.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: The one module that hands out the API, and the classes it hands out.
PROTOCOL = "addon_api.py"

#: A class whose public calls an addon reads, by its name's ending. The
#: connection object is named without the suffix and spelled out beside it.
API_SUFFIX = "API"
EXTRA_CLASSES = ("Connection",)

ROOT = Path(__file__).resolve().parent.parent


def _name(path: Path) -> str:
    """The file as a person running the tool from its own folder would write it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:                        # named from somewhere else entirely
        return str(path)


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """Top-level classes by name."""
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _api_classes(tree: ast.Module, classes: dict[str, ast.ClassDef]) -> list[str]:
    """The namespace classes ``AddonAPI.__init__`` binds, in the order it binds them.

    The pane reads this off the running module's bytecode; reading the same
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


def _getter(node: ast.Assign) -> ast.AST | None:
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
                    found.append((target.id, _getter(node) or node))
    return found


def _says_anything(node: ast.AST) -> bool:
    """Whether a definition carries a docstring with some words in it."""
    return bool((ast.get_docstring(node) or "").strip())


def missing(path: Path) -> list[str]:
    """Every undocumented public call in one module, as ``file:line: name``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}: cannot be read: {error}"]
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [f"{_name(path)}: does not parse: {error}"]

    classes = _classes(tree)
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
            bad.append(f"{_name(path)}: class {name} is gone")
            continue
        for method, node in _public_calls(cls):
            if not _says_anything(node):
                location = f"{_name(path)}:{getattr(node, 'lineno', cls.lineno)}"
                bad.append(f"{location}: {name}.{method} says nothing about itself")
    if path.name == PROTOCOL and not (ast.get_docstring(tree) or "").strip():
        bad.append(f"{_name(path)}:1: the module has no docstring")
    return bad


def sources(arguments: list[str]) -> list[Path]:
    """The files to read: those named, or the module that hands out the API."""
    if arguments:
        found: list[Path] = []
        for argument in arguments:
            path = Path(argument)
            found.append(path if path.is_absolute() else ROOT / path)
        return found
    return [ROOT / PROTOCOL]


def main(arguments: list[str]) -> int:
    files = sources(arguments)
    bad: list[str] = []
    for path in files:
        if path.is_dir():
            bad += [found for item in sorted(path.rglob("*.py"))
                    if item.name == PROTOCOL for found in missing(item)]
            continue
        bad += missing(path)
    print(f"checked {len(files)} module(s) for undocumented API calls")
    for line in bad:
        print(f"  {line}")
    if bad:
        print(f"\n{len(bad)} call(s) would show up in the Docs pane as saying "
              "nothing; nothing was built")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
