#!/usr/bin/env python3
"""A test for the retirement of a shipped addon: ``manager_addons.prune_retired_addons``
and the two places in ``manager_config`` that read the same tables.

``web_installer`` used to ship inside the program and is now the store's
``berry_web``. That leaves three jobs undone on any machine that had it, and this
file checks all three without a window and without a device:

* the leftovers go. ``addons/web_installer.py``, the compiled program under any
  of its shapes, the manifest beside it and the folder it was laid out in are
  gone on the next start, because an id the program no longer knows is dead
  weight that Settings would go on listing;
* the answers stay. ``addons/_data/web_installer`` is *moved* to
  ``addons/_data/berry_web`` when nothing is there yet, so a dashboard that
  worked keeps its repository, its token and its PIN file; once the successor
  has a folder of its own the old one is only confusion and goes;
* the profile reads across. A record under the retired name is folded onto the
  new id with its switches and its saved settings, and a ``remote_root`` still
  holding the retired default follows it - a path somebody set by hand is left
  exactly where they put it.

Everything is done in a temporary folder handed to the sweep as arguments, so a
run makes and breaks as many installs as it likes beside the program's real
``addons`` folder, and cleans up after itself.

Run it from the Test panel, or on the command line::

    python addons/_tests/test_retire.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

#: This run's themes, so nothing written on the way to a config touches the
#: program's own folder: the themes directory is taken from the environment once,
#: at import.
THEMES = Path(tempfile.mkdtemp(prefix="rasconf-retire-themes-"))
os.environ["RASCONF_THEMES"] = str(THEMES)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Berry Manager lives two levels up from here.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import manager_addons                                        # noqa: E402
import manager_config                                        # noqa: E402
from manager_const import (ADDON_ID_TRANSLATIONS,             # noqa: E402
                           DEFAULT_APP_CONFIG, DEFAULT_PROFILE,
                           RETIRED_SHIPPED_ADDON_IDS, SHIPPED_ADDON_IDS)

#: The retired name this version introduced, and the addon that took its place.
RETIRED = "web_installer"
HEIR = "berry_web"

FAILURES: list[str] = []


def check(condition: object, text: str) -> None:
    """One thing the upgrade has to do; all of them are reported, not just the first."""
    if not condition:
        FAILURES.append(text)
    print(("  ok   " if condition else "  FAIL ") + text)


def look(text: str) -> None:
    """Say what a run produced, without judging it."""
    print(f"       {text}")


# --- an install on disk, made to order ---

def build(files: dict) -> Path:
    """A temporary addons folder holding exactly ``files``, by relative path."""
    home = Path(tempfile.mkdtemp(prefix="rasconf-retire-test-"))
    for name, text in files.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text), encoding="utf-8")
    return home


def sweep(home: Path) -> tuple[list[str], list[str]]:
    """The startup sweep over that install, and what it said as it went."""
    said: list[str] = []
    lines = manager_addons.prune_retired_addons(
        said.append, addons=home / "addons", data=home / "addons" / "_data")
    return list(lines), said


def kept(home: Path, name: str) -> str:
    """What one file of the sandbox holds now; ``<gone>`` when it is not there."""
    path = home / name
    if not path.exists():
        return "<gone>"
    return "<folder>" if path.is_dir() else path.read_text(encoding="utf-8")


def retired_shapes() -> list[str]:
    """Every file name an install may hold this addon's id under."""
    suffix = manager_addons.ADDON_EXECUTABLE_SUFFIX
    return [f"{RETIRED}.py", f"{RETIRED}{suffix}", f"{RETIRED}.bin",
            f"{RETIRED}.json"]


# --- one: the tables the upgrade reads ---

def test_the_tables() -> None:
    print("\n1. what this version says is retired")
    check(RETIRED in RETIRED_SHIPPED_ADDON_IDS,
          f"{RETIRED} is not listed as retired, so nothing sweeps it")
    check(RETIRED not in SHIPPED_ADDON_IDS,
          f"{RETIRED} is still listed as shipped, so the build would pack it")
    check(ADDON_ID_TRANSLATIONS.get(RETIRED) == HEIR,
          f"{RETIRED}'s settings have no successor to move to: "
          f"{ADDON_ID_TRANSLATIONS.get(RETIRED)!r}")
    check(HEIR not in SHIPPED_ADDON_IDS and HEIR not in RETIRED_SHIPPED_ADDON_IDS,
          f"{HEIR} is a store addon and may not be claimed by either list")
    for gone, came in ADDON_ID_TRANSLATIONS.items():
        check(gone in RETIRED_SHIPPED_ADDON_IDS,
              f"{gone} names a successor but is not retired, so its folder is "
              f"never moved to {came}")
        check(came != gone, f"{gone} is translated onto itself")
    check(not (set(SHIPPED_ADDON_IDS) & set(RETIRED_SHIPPED_ADDON_IDS)),
          "an addon is both shipped and retired")

    # The files themselves: the old one gone from the tree, the new one published.
    check(not (ROOT / "addons" / f"{RETIRED}.py").exists(),
          "the retired addon is still in addons/, so it would load again")
    check(not (ROOT / "addons" / "_tests" / f"test_{RETIRED}.py").exists(),
          f"test_{RETIRED}.py is left behind testing an addon that is gone")
    check(not (ROOT / "assets" / f"{RETIRED}icon.png").exists(),
          f"assets/{RETIRED}icon.png is left behind for an addon that is gone")
    check(not (ROOT / "addons" / "_data" / RETIRED).exists(),
          f"addons/_data/{RETIRED} is still in the checkout; the sweep should "
          "have cleared it here too")
    check((ROOT / "addon.store" / f"{HEIR}.py").is_file(),
          f"addon.store/{HEIR}.py is not published, so the translation names "
          "an addon that does not exist")
    check((ROOT / "addon.store" / "_tests" / f"test_{HEIR}.py").is_file(),
          f"the store addon has no test of its own at test_{HEIR}.py")

    # The app has to actually run the sweep, before anything looks for an addon
    # to load, and say what it did in the window's own log.
    boot = (ROOT / "berry_manager.py").read_text(encoding="utf-8")
    check("prune_retired_addons()" in boot,
          "nothing calls the sweep at startup, so leftovers wait for a manual run")
    check(boot.index("prune_retired_addons()") < boot.index("BerryManager()"),
          "the sweep runs after the window has already gone looking for addons")
    check("window.log(line)" in boot
          and boot.index("window.log(line)") > boot.index("prune_retired_addons()"),
          "what the sweep did is never said to the log a person would read")


# --- two: the sweep, on an install that had the old addon ---

def test_the_move() -> None:
    print("\n2. the settings folder follows the addon")
    saved = json.dumps({"repo": "raspiOpenWrt", "token": ""})
    home = build({
        f"addons/{RETIRED}.py": "# the shipped addon, from an older install\n",
        f"addons/{RETIRED}.json": '{"name": "Rasconf web installer"}',
        f"addons/_data/{RETIRED}/config.json": saved,
        f"addons/_data/{RETIRED}/fields.json": "[]",
        # Somebody else's addon, and the store addon next door: none of these
        # may be touched by a sweep that was asked about one id.
        "addons/berry.py": "# shipped\n",
        f"addons/{RETIRED}_notes.py": "# a different addon whose name starts the same\n",
        "addons/_data/cert_manager/config.json": '{"port": 8989}',
        "addons/_data/addon_store/config.json": '{"source": "x"}',
    })
    try:
        said, logged = sweep(home)
        check(kept(home, f"addons/_data/{HEIR}/config.json") == saved,
              f"the saved config did not arrive under {HEIR}: "
              f"{kept(home, f'addons/_data/{HEIR}/config.json')!r}")
        check(kept(home, f"addons/_data/{HEIR}/fields.json") == "[]",
              "a folder was moved by copying one file out of it, not the whole of it")
        check(kept(home, f"addons/_data/{RETIRED}") == "<gone>",
              "the retired settings folder is still there beside its successor")
        check(kept(home, f"addons/{RETIRED}.py") == "<gone>",
              "the retired addon's source was left in addons/, where it would load")
        check(kept(home, f"addons/{RETIRED}.json") == "<gone>",
              "the manifest beside it was left, so Settings would list an addon "
              "that is not there")
        check(kept(home, "addons/berry.py") == "# shipped\n",
              "an addon that ships was moved or deleted by the sweep")
        check(kept(home, f"addons/{RETIRED}_notes.py") != "<gone>",
              f"an addon whose name merely starts with {RETIRED} went with it")
        check(kept(home, "addons/_data/cert_manager/config.json") == '{"port": 8989}',
              "another addon's settings were swept with the retired one")
        check(any("moved" in line and HEIR in line for line in said),
              f"the move was never said out loud: {said}")
        check(logged == said, "the lines returned are not the lines logged")
        look(" / ".join(said))

        print("\n2b. and again, with nothing left to do")
        again, _twice = sweep(home)
        check(again == [], f"a second start found work again: {again}")
        check(kept(home, f"addons/_data/{HEIR}/config.json") == saved,
              "a second sweep undid the carry-over")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_successor_wins() -> None:
    print("\n3. a successor that has already been used")
    home = build({
        f"addons/_data/{RETIRED}/config.json": "the old answer",
        f"addons/_data/{HEIR}/config.json": "the answer the new addon wrote",
    })
    try:
        said, _logged = sweep(home)
        check(kept(home, f"addons/_data/{HEIR}/config.json")
              == "the answer the new addon wrote",
              "the new addon's own settings were overwritten by an old folder")
        check(kept(home, f"addons/_data/{RETIRED}") == "<gone>",
              "the old folder is still there to be confused with the new one")
        check(any("removed" in line for line in said),
              f"the deletion was never said: {said}")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_every_shape() -> None:
    print("\n4. every shape a retired program comes in")
    shapes = retired_shapes()
    files = {f"addons/{name}": f"# {name}\n" for name in shapes}
    files[f"addons/{RETIRED}/addon.json"] = "an addon laid out as a folder"
    files[f"addons/_data/{RETIRED}/config.json"] = "{}"
    home = build(files)
    try:
        named = [path.name for path in
                 manager_addons.retired_addon_files(home / "addons", RETIRED)]
        missing = [name for name in shapes + [RETIRED] if name not in named]
        check(not missing,
              f"the sweep does not look for {missing}; an install writes them "
              f"under {RETIRED}")
        said, _logged = sweep(home)
        for name in shapes:
            check(kept(home, f"addons/{name}") == "<gone>",
                  f"{name} was left in addons/")
        check(kept(home, f"addons/{RETIRED}") == "<gone>",
              "an addon foldered under the retired id is still there")
        check(kept(home, f"addons/_data/{HEIR}/config.json") == "{}",
              "the settings of a foldered addon did not reach the successor")
        check(len([line for line in said if "could not" in line]) == 0,
              f"the sweep could not do something it tried: {said}")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_clean_install() -> None:
    print("\n5. nothing to sweep says nothing")
    home = build({"addons/berry.py": "# shipped\n",
                  "addons/_data/berry/config.json": "{}"})
    try:
        said, logged = sweep(home)
        check(said == [], f"a clean install was reported as work done: {said}")
        check(logged == [], "the log was read out with nothing in it")
        check(kept(home, "addons/berry.py") == "# shipped\n",
              "a sweep with nothing to remove still removed something")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_addon_with_no_heir() -> None:
    print("\n6. a retired name nobody replaced")
    home = build({"addons/_data/old_thing/config.json": "gone by now",
                  "addons/old_thing.py": "# nothing took this over\n"})
    retired = manager_addons.RETIRED_SHIPPED_ADDON_IDS
    translated = manager_addons.ADDON_ID_TRANSLATIONS
    try:
        manager_addons.RETIRED_SHIPPED_ADDON_IDS = ("old_thing",)
        manager_addons.ADDON_ID_TRANSLATIONS = {}
        said, _logged = sweep(home)
        check(kept(home, "addons/old_thing.py") == "<gone>",
              "a retired addon with no successor kept its source")
        check(kept(home, "addons/_data/old_thing") == "<gone>",
              "its settings stayed, with no addon left to read them")
        check(any("removed" in line for line in said),
              f"the sweep was silent about deleting them: {said}")
    finally:
        manager_addons.RETIRED_SHIPPED_ADDON_IDS = retired
        manager_addons.ADDON_ID_TRANSLATIONS = translated
        shutil.rmtree(home, ignore_errors=True)


# --- three: the profile's own record ---

def test_the_record() -> None:
    print("\n7. the switches and saved values in a profile")
    only_old = {"addons": {RETIRED: {"enabled": True, "own_connection": False,
                                     "ssh_enabled": True, "sftp_enabled": False,
                                     "settings": {"web_port": 8989}}}}
    done = manager_config._normalize_addons(json.loads(json.dumps(only_old)))
    record = done["addons"].get(HEIR) or {}
    check(RETIRED not in done["addons"], "the retired id is still in the profile")
    check(bool(record.get("enabled")),
          f"an addon that was switched on came across switched off: {record}")
    check(record.get("sftp_enabled") is False,
          "a transport switch was not carried across")
    check(record.get("settings") == {"web_port": 8989},
          f"what the addon saved was left behind: {record.get('settings')}")

    both = {"addons": {
        RETIRED: {"enabled": False, "own_connection": True, "ssh_enabled": False,
                  "sftp_enabled": False,
                  "settings": {"web_port": 8989, "repo": "old name"}},
        HEIR: {"enabled": True, "own_connection": False, "ssh_enabled": True,
               "sftp_enabled": True, "settings": {"repo": "new name"}},
    }}
    merged = manager_config._normalize_addons(json.loads(json.dumps(both)))["addons"]
    check(RETIRED not in merged, "both ids are kept when one is the other's old name")
    check(merged[HEIR]["enabled"] and merged[HEIR]["ssh_enabled"],
          "the retired record overruled what the new addon's record says")
    check(merged[HEIR]["settings"].get("repo") == "new name",
          "an old value covered a newer one")
    check(merged[HEIR]["settings"].get("web_port") == 8989,
          f"a value only the old record had was dropped: {merged[HEIR]['settings']}")

    plain = manager_config._normalize_addons({"addons": {
        RETIRED: True, "other_addon": {"enabled": True}}})["addons"]
    check(isinstance(plain.get(HEIR), dict) and not plain[HEIR]["enabled"],
          f"a record that is not a record at all did not come across clean: {plain}")
    check(plain.get("other_addon", {}).get("enabled") is True,
          "an unrelated addon's record was changed by the fold")
    check(RETIRED not in plain, "a record of the wrong shape was left under the old id")


def test_the_folder() -> None:
    print("\n8. the device folder the profile works in")
    check(DEFAULT_PROFILE["remote_root"] == "/www_berry",
          f"the shipped default is {DEFAULT_PROFILE['remote_root']!r}, not the "
          "folder the dashboard is installed into now")
    check(DEFAULT_APP_CONFIG["remote_root"] == DEFAULT_PROFILE["remote_root"],
          "the app default and the profile default name two different folders")
    move = manager_config._translate_remote_root
    check(move("/www_rasconf") == "/www_berry",
          f"the retired default did not follow the addon: {move('/www_rasconf')!r}")
    check(move("/www_rasconf/dash") == "/www_rasconf/dash",
          "a folder inside the old one was rewritten by a rule about the default")
    check(move("/srv/site") == "/srv/site",
          "a path somebody set by hand was moved under the new default")
    check(manager_config.LEGACY_REMOTE_ROOTS.get("/www_rasconf")
          == DEFAULT_PROFILE["remote_root"],
          "the rule and the shipped default disagree about where the dashboard lives")
    check(move(None) == DEFAULT_PROFILE["remote_root"],
          f"an unset remote_root came out as {move(None)!r}")
    check(move("") == "",
          f"an empty path was given a default of its own: {move('')!r}")


def test_the_example() -> None:
    print("\n9. the example config a new install starts from")
    text = (ROOT / "config.example.json").read_text(encoding="utf-8")
    example = json.loads(text)
    check(RETIRED not in text, f"config.example.json still names {RETIRED}")
    profiles = example.get("profiles") or [example]
    profile = profiles[0] if isinstance(profiles[0], dict) else {}
    check(HEIR in (profile.get("addons") or {}),
          f"config.example.json has no {HEIR} entry for the addon that replaced "
          f"it: {sorted(profile.get('addons') or {})}")
    entry = (profile.get("addons") or {}).get(HEIR) or {}
    check(set(entry) >= {"enabled", "settings"},
          f"the example's {HEIR} record is not the shape a profile reads: {entry}")
    check(profile.get("remote_root") == DEFAULT_PROFILE["remote_root"],
          f"the example points at {profile.get('remote_root')!r}, a folder other "
          "than the shipped default")


def main() -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        # The Test pane wants it live, and a Windows console often has no room
        # for the marks these lines use.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8",
                               errors="replace")
    print("the retirement of a shipped addon: what an upgrade has to finish")
    look(f"addons folder of the program itself: {manager_addons.ADDONS_DIR}")
    try:
        test_the_tables()
        test_the_move()
        test_the_successor_wins()
        test_every_shape()
        test_a_clean_install()
        test_an_addon_with_no_heir()
        test_the_record()
        test_the_folder()
        test_the_example()
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
