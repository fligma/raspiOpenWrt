![SCREENSHOT.png](https://raw.githubusercontent.com/fligma/raspiOpenWrt/refs/heads/main/media/SCREENSHOT.png)
# Berry Manager

A desktop tool for managing a Raspberry Pi running OpenWrt and the Rasconf web
dashboard on it. It talks to the device over SSH/SFTP and gives you an
interactive terminal, embedded web views, and - through the bundled Berry
addon - a full file manager.

## Build the executable

```
pyinstaller --noconfirm --onefile --windowed --icon="assets/icon.png" --add-data="assets:assets" --add-data="config.example.json:." --add-data="_addon_bundle:addons" --hidden-import=PyQt6.QtWebEngineWidgets --hidden-import=PyQt6.QtWebEngineCore --hidden-import=secretstorage --hidden-import=jeepney berry_manager.py
```

On Windows use `;` between the parts of every `--add-data` pair
(`--add-data="assets;assets"`) and drop the last two options:
`secretstorage` and `jeepney` are the Linux credential-store backends that
`keyring` imports lazily, and a build fails on a hidden import it cannot find.
`--add-data="assets:assets"` keeps the folder name, which is what
`core.const.ASSETS_DIR` expects, so the icons are found in the unpacked
one-file build exactly as they are in a source checkout.
`--add-data="_addon_bundle:addons"` does the same for the addons that ship with
the program: the folder is staged by the workflow out of `addons` and lands
inside the archive under the name `core.const.BUILTIN_ADDONS_DIR` reads.
`--add-data="_addon_harness:harness"` packs a plain copy of `addon_common.py`
and `addon_client.py` - the two modules an addon imports to reach the app -
where `core.const.HARNESS_DIR` points at it. They have to lie on disk: the
Python that compiles or tests an addon is a process of its own, outside this
archive, and cannot read what is packed inside it. A program built before that
copy existed fetches the two files from the repository the addons come from, so
installing an addon does not depend on which build you happen to be running.

The QtWebEngine modules are imported inside a `try`, so the source scanner
cannot see them; that is what the two `--hidden-import` options are for. Their
helper process and resource files come from the hooks PyInstaller ships for
`PyQt6.QtWebEngine*`, so there is nothing to `--collect-all` - and those names
are extension modules rather than packages, so collecting them as packages only
produces a "not a package" warning.

The GitHub workflow in `.github/workflows/build.yml` runs exactly this command
per operating system, for the pushes that can change its result: a first job
reads the commits a push carries and says which half of the build moved. The
executables are packaged when anything of the program did - the app, the
addons that ship in `addons`, its assets, its requirements, the workflow
itself. A push that only brought an addon to `addon.store`, or changed the
tool that writes `index.json` from it, packages nothing and only rewrites the
catalog, and a push of neither - the web dashboard, the pictures, a README -
does nothing. A manual run, and a push whose starting commit cannot be read,
counts as everything. Before the packaging, five steps read the source: one
looks for names that nothing binds (`tools/check_names.py`) - in a Qt program an
exception raised inside a slot takes the window down rather than printing, so a
dead name is a crash for whoever opens that dialog, and it is looked for
statically instead - one looks for an addon-facing call that has nothing written
about it (`tools/check_api_docs.py`), because the Addon Dev tab's reference is
built out of exactly those words and would print a gap rather than make something
up - one follows every link in the guides beside that reference, and parses
the templates **New addon...** writes (`tools/check_guide_links.py`), because a
link to a page that was renamed prints its words and goes nowhere, and a mistake
in a template is a syntax error the author only meets in the file they are handed
- one works out what an addon's Settings row and the Settings Connection
list would say about each addon, and each entry the published catalog holds
(`tools/check_connection_saying.py`), because a wrong answer there crashes
nothing and simply tells the user about a connection the addon never opens - and
one runs every `test_*.py` in
`addons/_tests` against the stubbed app - a failing test fails the build, and
the tests themselves are never packaged. The one test that asks the real
repository - **Berry web dashboard**'s GitHub route, the half that cannot be checked
from the source alone - is handed this run's own token, because sixty anonymous
GitHub requests an hour are shared by everything a runner does, the catalog job
of the same push among them; when GitHub still refuses, or no token is there, that
half says so in the log and is skipped rather than failing the build, and the
local-folder route - which needs no internet - is still run. Each of those tests
measures a tab by what it promised, not by how many pixels one machine made of
it: a column is checked against the width the addon pinned in its own spec, a
coloured row against the plain row beside it, an addon's mark against the pixels
it actually drew - because the same Qt lays out with another font, another row
height and another rasteriser elsewhere, and a build should not stop over that.
So the step says what it is answering from before it runs anything: the system
and Python, the Qt and PyQt in it, and how wide one word is in a default font.
The other way a test goes green on one runner and red on the other is a record
that names only one system: the update addon's fake releases held a Windows file
alone, so a Linux runner - where the addon honestly answers that the release has
nothing for this system - failed a dozen checks that were really asking about the
fixture. Those records now carry one file per system, as a published release does,
and the run says which system it is telling the story from instead of reading it
off the machine; a check beside them asks each system to find its own file in
every record, so a one-sided fixture is named as the failure it is.
The step after that
stages the addons
out of `addons` into `_addon_bundle`, which is what gets packed inside the
executable: top-level `.py` addons, compiled addons (a loose `.exe` or `.bin`
with its `<name>.json` manifest, or a folder whose program is `addon.exe`,
`addon.bin` or `<folder>.exe`), and folder addons built from `addon.py` or
`main.py`; `__pycache__` and any `tests`/`_tests` folder are stripped from the
copy. The `addons` folder that ships beside the built program is left empty
for the user's own drop-ins. Last, a build that got this far is published: a
`release` job tags the commit `berry` + `APP_VERSION`, opens a release at that
tag and attaches one archive per system, each holding the program with its
`addons` folder - which is what the in-app **Manager update** tab downloads, and
what a person clicking through to the releases page is handed (see [Berry
Manager update](#shipped-addon-berry-manager-update)).

The store's addons are therefore checked by CI only on a push that also moves
the program: `addon.store/_tests` is run by the same packaging step. A new
addon on its own is proved when somebody installs it - the store fetches
`test_<id>.py` beside the source and runs it before spending a minute
compiling - and the catalog itself is generated from the manifest in the file,
which needs no import of the addon. A test that never got as far as the addon -
a Python with no PyQt6 in it, or a program whose copy of the modules an addon
imports could not be found - says so and compiles anyway; only what the addon
itself failed stops an install.

PyQt6-WebEngine is optional. If it is not installed, the Web Interface and
LuCI tabs show the URL and an "Open in browser" button instead of an embedded
page, and the rest of the tool works normally.

Every app module is imported statically at the top of its user, which is what
lets PyInstaller collect them. Commit a new module with the rest of
`utils/update util`, or the build fails on the missing import. The two
third-party libraries with real import weight behind them are the exception,
and they are read on use rather than on the way up: `core.net` brings in
paramiko inside the one call that opens an SSH connection, and `core.window`
brings in keyring inside the three calls that touch the credential store -
together near a second of boot time that the window no longer pays before it
appears, and paid anyway the moment a connection is asked for (auto-connect
reads paramiko on a worker thread, so it lands off the GUI's path either way).
PyInstaller still collects both, because its scanner follows plain imports
inside functions as readily as ones at the top; only imports that sit behind a
condition - the QtWebEngine pair, tried and fallen back on - need the
`--hidden-import` list.

### Assets

`assets/` holds every picture the program is shipped with, and that now includes
the addons that live inside it: `icon.png` and `icon2.png` for the logo, the
marks the window paints its tabs with (`defaulttabicon.png`,
`terminalicon.png`, `settingsicon.png` for the pinned Settings cog),
`storedefaultaddon.png` as the store's placeholder card, and one per shipped
addon - `berryicon.png`, `addon_storeicon.png`, `manager_updateicon.png`,
`theme_managericon.png`. A downloadable addon's own
picture does not live here: it is kept with its source in
`addon.store/assets/<id>` - `berry_web/berry_webicon.png`,
`openwrt_logs/openwrt_logsicon.png`,
`openwrt_package_manager/openwrt_package_managericon.png`,
`multi_ssh/multi_sshicon.png`, `cert_manager/cert_managericon.png` - and arrives
inside the program the store builds for that addon.

The naming rule for an addon's picture is one line, and it is the same in both
folders: the addon's id and then `icon.png`, so `berry` is worn by
`berryicon.png` and `openwrt_logs` by `openwrt_logsicon.png`, with nothing in
either file naming it. `addon_common.find_addon_picture` is the lookup: it tries
that one name in the folder the addon's own file sits in - `assets/<id>/` under
it, which is where a store addon's picture is - and then in the program's
`assets`, so the same file name reaches an addon being written, one shipped
inside the program and one installed from the store. Nothing is tried under any
older name: the aliases this lookup used to keep (`updateicon.png`,
`storeicon.png`, `themeicon.png`, `webinstallericon.png`, `openwrtpkgmgr.png`)
were retired once every mark had its file, and one name per addon is one less
thing to remember - which is what the picture of the package manager learned in
1.3, when its card and its tab mark both changed with the file's name and no code
in between. A picture is never required: an addon without one is drawn the SVG in
its own `TAB_ICON` on the bar and shown `storedefaultaddon.png` as a card, and a
tab with neither wears `defaulttabicon.png`. The files themselves are written by
two tools rather than edited in a bitmap, so either can be remade bigger or
nudged in one run: `tools/make_addon_icons.py` draws each addon's out of its
`TAB_ICON`, and `tools/make_store_icons.py` the program's own out of the painters
in `core.ui`. Both take `--force` to rewrite a file that is already there
and `--check` to name what is missing, and the workflow runs both checks, so a
picture that was never made stops the build instead of quietly never arriving.

Nothing hard-codes the folder: `core.const.find_asset(name)` looks in `assets`
beside the resources, then the old top-level location, then beside the
executable, so an existing `icon.png` next to the `.exe` still overrides the
built-in one. Add a new image to `assets/`, ask for it with `find_asset`, and the
GitHub workflow picks it up without any change (it copies the whole folder into
the bundle). An addon reaches the same folder through `api.paths.assets`.

## Modules

Ten files hold the app: one is the entry point, three are the contract an addon
imports and keep their names because a compiled addon reads them by name, and
the six that make the window itself live in the `core/` package, one per layer.

| Module | Holds |
| --- | --- |
| `berry_manager.py` | Entry point: Qt application, window icon, startup |
| `addon_api.py` | The `api` object every addon receives and the declarative widget builder; the plain helpers are re-exported from `addon_common` |
| `addon_common.py` | The Qt-free half of the contract: errors, JSON results, the config and build-mark files. Imported by an addon, by a test and by the app; reads only `core.const` of the app's own modules |
| `addon_client.py` | The addon side of the pipe - the `api` a process addon uses, `serve()` for its own process, and the recording stub a test runs against |
| `core/const.py` | Paths, defaults, the colour tokens and tab knobs a theme is made of, and the app stylesheet built from them. Carries no Qt at all, which is what lets `addon_common` read it |
| `core/ui.py` | The look of the window, read from the `themes/<name>.theme` files - reading, checking, writing and rendering them, and the templates a new one is begun from; the reading, normalising and writing of `config.json`; the reusable pieces - `RightPinnedTabBar` and `RightPinnedTabWidget` (the main bar, its pinned run and its own measuring), `ToggleSwitch`, `ToggleGroup`, `CollapseGroup`, `TabTransportDots`, `TabCloseButton`, the shared file tree and the popup dialogs; and the startup splash card |
| `core/net.py` | `SharedConnection` - the app's one SSH session with its SFTP channel and the shell channels opened on it - plus path safety and the background worker threads, and `TerminalScreen`, the xterm-style emulator the SSH tab shows (escape-sequence parser, cell grid, key encoding) |
| `core/views.py` | The three device tabs: `BrowserMixin` (embedded web tabs and the HTTPS certificate switch), `MainConnectionMixin` (the app's one main connection - the Connect/Disconnect pair, the `main_connected` flag, both status dots and the connected/disconnected events, Berry owning the file views) and `TerminalMixin` (wiring of the terminal, quick commands, reboot watch) |
| `core/addon.py` | The addon runtime - discovery, loading, enable/disable, events, the Settings pages with each addon's config panel, the executable-addon bridge and its callback tokens - and the Addon Dev tab, the templates, the build, the install and the metadata reader |
| `core/window.py` | `BerryManager` - menu, tab shell, connection panel, Settings tab, profiles and credentials, dialog-driven menu actions, log, shutdown |

The three device tabs are mixins (`BrowserMixin`, `MainConnectionMixin`,
`TerminalMixin`) that `BerryManager` inherits alongside `QMainWindow`, so the
code of each tab stays in its own half of `core/views.py` while the window keeps
the shared state and never has to be imported by it - the tabs name it only for
the type checker. What the window itself owns - the menu, the Settings tab, the
profiles and the actions that open a dialog - is in `core/window.py`.

## Configuration file

The manager keeps everything it writes in one `config` folder beside the script
(`core.const.CONFIG_DIR`): `config.json` - the profile index - one
`{profile}.json` holding each profile's complete settings,
`command_history.json`, `berry_manager.log` and the shared addon build record
`config/_build.json`. The folder is created on the way up, and a loose file
left in the program root by an older version is moved into it on the first
run. The folder is Git-ignored; `config.example.json` is the tracked template,
written out with a complete profile including the default tab order, so
copying it gives a working starting point rather than a list of empty keys.
It is also what the first run reads: with nothing in `config` yet, the program
falls back to the example, so the values it holds are the ones a new install
starts from - which is why it carries a whole profile and not a skeleton.
An older config file - a single flat set of settings, or one with every
profile written inside it - is folded into the per-profile layout and
rewritten once the first time the new version runs. Export and import stay
one self-contained file that carries every profile.

Passwords and private-key passphrases are never written to `config.json`.
When "Remember password securely" is on, the password is stored in the
operating system credential store (Windows Credential Manager on Windows),
keyed by `user@host:port`.

### Profiles

Connection details are grouped into named profiles (host, port, username,
key, remote directory, source, host-key and remember-password flags), and
every profile carries the complete set of settings the app uses - each
profile is its own `{profile}.json` in the config folder. The selector lives
in the File menu: **New profile...**, **Load profile** - one clickable entry
per profile, the active one ticked - **Rename current profile...** and
**Delete current profile...**. Loading another profile saves what is open
first, then rebuilds every page from the clicked profile's file; the password
field is cleared and a stored credential is picked up again.

### Settings tab

Settings is a tab bar of its own, one page per subject: **Connection**,
**Startup & defaults** and **Custom tabs**, then a page for every addon that came
inside the program and any page an addon built for itself, and last a single
**Installed addons** page for everything added afterwards. Each page scrolls on
its own, so a section only ever shows the
options that belong to it, and **Apply settings** sits under the bar and applies
the whole of it whichever page is showing. The **SSH terminal** options live on
the Connection page and can be ticked off: unchecking it hides its options,
removes the terminal from the window, and refuses SSH to the
addons and the device API until it is back on; the state is saved
(`ssh_enabled`). The **File transfer (SFTP)** block on that same page has its own
**Enable the SFTP connection** switch (`sftp_enabled`): off, the remote directory
line goes and addons are refused SFTP work. Neither switch opens or closes
anything by itself: the app keeps **one main connection** - a single SSH session
that carries SFTP with it - and these two are gates over the parts of it. Each is
only the app-wide half of the gate: every addon also carries an **SSH** and an
**SFTP** switch under its name on its own Settings page, and a transport reaches
the addon only when both are on. The **Custom tabs** page is always shown. The
file tab is no longer built into the app: the bundled **Berry** addon browses
files over this connection, and its on/off switch is on **Berry**'s own Settings
page beside the other shipped addons. The connection panel is titled
**Connection settings**; when SFTP is switched off
(`sftp_enabled`) it drops the **Remote dir** line, because that
directory is the root Berry opens on. Nested toggles (logging) still
hide their own indented options while off.

The panel itself is the whole of the app's connecting. **Connect** opens the one
main connection - an SSH session that carries SFTP, and the terminal's shell as
a channel on it - **Disconnect** closes it, and **Connect at startup** says
whether the next launch opens it by itself. It is one switch for both
transports: SFTP has no startup switch of its own, and the launch connects for
SSH, for SFTP, or for both, and stays away only when the two transports are
switched off. The switch in the panel's header
(`master_connection`) is the master gate: off, the panel hides its controls,
what was open is closed and nothing may connect at all - neither the app's
session nor an addon's own.

The pages of the Settings bar, and what each holds:

- **Connection** - the connection panel with its **SSH terminal** and
  **File transfer (SFTP)** blocks, and **Connection status** (folded away until
  you open it)
  - Read-only, and nothing here is saved. The program's own **SSH** and **SFTP**
    indicators - the two dots that used to sit in the window header - are here
    instead, each with the word **connected** or **closed** beside it. They are
    two lines for one thing: both follow the app's single main connection,
    because that one session carries both transports. Under them is one line per
    running addon, naming each transport its manifest declared with the dot's
    own word beside it - **connected**, **ready** or **off** - and then saying
    whose connection it rides, in one of four sayings: **on the main connection**
    for an addon that only ever shares the app's session, **on its own connection**
    for one whose manifest says `"own"`, **on the main connection now** or **on
    its own connection now** for one that declared `"both"` and may be switched
    between the two (the line says which it is on, and its tool tip says the row
    is where that is chosen), and **no device connection** for one that declared
    no transport at all. The words are the very same answer as the control beside
    that addon's switch on its row - the same `connection_choice` decision serves
    both - so the list and the rows can never disagree, and an addon that can
    reach a device only through a session it opens itself is never shown waiting
    on a **Connect** it will ignore. The list is redrawn whenever a connection or
    an addon changes, so it is never a snapshot of when the page was opened. Both
    the dots and the whole block disappear when the two transports are switched
    off.
- **Startup & defaults** (its own page)
  - Default tab shown at startup (only the currently visible tabs are listed; it
    falls back to the first visible tab when the stored one is gone)
  - **Remember tab order** - keep the tabs in the order you set them for the next
    launch (`remember_tab_order`, `tab_order`), with the **Tab order** list under
    it saying what that order is: one row per movable tab, left to right as the bar
    shows them. Drag a row, or select one and press **Up**/**Down**, and the tabs
    move on the bar the same moment and the order is written down then - there is
    nothing left to remember to save, and the tab being looked at stays the tab
    being looked at. **Reset to built-in order** puts them back the way the program
    builds them and forgets the saved arrangement. The list goes grey while
    remembering is off, since with it off every launch starts from the built-in
    order whatever was dragged. A tab that arrives later - an addon switched on, a
    web tab added - is not in the saved order and joins at the end of the row. The
    gap and the pinned run at the right edge are deliberately not listed, so
    furniture cannot be dragged into the middle of it by accident.
  - **Show the Addon Dev tab** - the addon index with its details, test and
    compile panels, and the Tools > Addon Dev entry that goes with it
    (`addon_dev_enabled`). Off, the tab and the menu entry are gone; the
    addons themselves are still switched on and off on their own Settings pages.
  - **Pop up notifications** - let the app raise a notice in the system's
    notification area as well as writing it to the status line: what an addon
    says through `api.ui.popup`, titled for the addon that spoke, and this
    program's own news such as an update being out. While it is on the app keeps
    an icon in the area, which is what the notices come from - right-click it to
    quit, left-click to bring the window back (`system_notifications`, on as
    shipped; off, every message stays inside the window)
  - **Show the startup splash** - the card that comes up the moment the
    program is launched, before anything else is on screen: the logo, the
    line naming what the boot is loading right now - the menu, the panels,
    each addon by name as it is read in and told to begin - and a bar with a
    percentage, painted from the theme in force with a border around the edge
    and no title bar of its own (`splash_enabled`, on as
    shipped; off, the screen stays empty until the window itself appears)
  - **Enable logging to file** - when off, nothing is written to
    `berry_manager.log`. While on it exposes the maximum on-screen log lines
    and the log file path. The on-screen pane under the tabs is switched
    separately, in View > **Show log console**.
- **File transfer (SFTP)** (a block inside the panel, on the Connection page)
  - **Enable the SFTP connection** - the gate: off, the addons and the device
    API are refused SFTP work and the connection panel drops its remote
    directory line (`sftp_enabled`). It opens and closes nothing by itself: the
    transports travel on the app's one main connection
- **SSH terminal** (the panel's other block, on the same page; its **Show the
  SSH terminal tab** switch is also the app-wide SSH gate)
  - Terminal font size (the terminal's own base size; View > Zoom in/out scales
    it together with everything else)
  - Reboot timing: **Wait before first retry** and **Retry every**, plus the
    option to be asked after a reboot is sent through the button or a quick
    command
  - Edit quick commands
- **Custom tabs** (its own page, always enabled)
  - **Tabs** - add, edit, or remove the embedded browser tabs. Each tab has a
    display name, an IP/hostname (leave blank to follow the active profile
    host), a port, a path, and http/https.
  - **Accept untrusted HTTPS certificates** - let the embedded browser load
    pages whose certificate it cannot verify (`ignore_cert_errors`). QtWebEngine
    reads this when it starts, so the manager offers to restart when you change
    it.
- A page of the Settings bar for every addon that built one for itself with
  `api.settings_pages`, sitting with the program's own categories ahead of
  **Installed addons** and taken away again by the app when the addon is switched
  off or removed (see
  [A Settings page an addon owns](#a-settings-page-an-addon-owns)).
- **Installed addons**, last, and a page under its own name for every shipped
  addon
  - The addons that ship inside the app - Berry, the Berry
    addon store, the Berry Manager update and the Theme Manager - each get a
    Settings page titled
    with their own name, carrying
    their enable switch, their SSH and SFTP switches, their config panel and
    their connection indicators (see below). **Berry** is switched from its page,
    which shows or hides its file tab. The pages are made and unmade with the
    rows: a **Rescan** that finds a
    shipped addon gives it a page, one that no longer finds it takes the page
    away, and a name changed in the manifest renames the tab.
  - Everything that came in after the build shares the **Installed addons** page:
    a hand-written `.py`, a program built by the Addon Dev tab, an addon the
    store installed. At its top sits the addons folder path with **Open folder**,
    **Open addon data** and **Rescan** beside it, and below that one row per
    addon. A store install is not marked down for where it came from: it gets the
    same switch, the same *own connection* option (where
    its manifest declares `"connection": "both"`), its own SSH and SFTP switches
    and the same config panel as any other, because the store writes a manifest
    beside the program it built and seeds its settings folder before it is ever
    started.

### The tab bar

The window is titled `Berry Manager V` plus `core.const.APP_VERSION`: the
version is said in the title bar as well as in Help > About, so a screenshot and a
bug report name the copy they came from. That one value is also what
`api.session.version()` answers with and what the release job tags, so a build
never disagrees with itself about which version it is. The box also links that
number to the release built from it - `core.const.PROJECT_RELEASE_URL` with
`UPDATE_TAG_PREFIX` in front of the version, which is the tag the workflow
pushes, so 1.6.4 opens `.../releases/tag/berry1.6.4` - making the About box a
door to the build it names rather than a caption about it.

The bar holds two things at once: the row of tabs the user arranges, and a short
run kept against the right edge. An empty, disabled *gap* tab soaks up whatever
width is left over, which is what keeps the run after it - **Settings**, and every
tab that asked to be pinned beside it - detached from the movable row instead of
drifting at its end. Those tabs are window furniture: never dragged into the
order, never written to `tab_order`, keeping their place while the tabs beside them
come and go. Settings wears no caption, only the gear drawn for it
(`core.ui.settings_tab_icon`), and an addon's tab does the same when it
asks to be icon-only - the store's, the manager updater's and the certificate
manager's all do (see
[Tabs an addon owns](#tabs-an-addon-owns)). A tab that shows no name has not lost
it: the full name lives in the tab's own data, which is what its tooltip says,
what the start-tab picker lists and what the saved order is written in.

Widths are worked out rather than left to Qt. Every movable tab is measured at the
width of the widest, so the row reads as a set of equal capsules; when their total
no longer fits, the captions are elided in step, and a bar too narrow for an
ellipsis leaves a tab its picture and no text at all. The pinned run is measured at
its own width and never squeezed, and the movable tabs are capped so they cannot
eat the room reserved for it. None of this is a one-off at startup: the row is
re-measured whenever a tab is added, removed, renamed or dragged, whenever a
transport dot arrives or goes, whenever the window is resized and at every step of
View > Zoom - which is why a tab that arrives after an addon was switched on costs
the rest of them no more width than it should, and why no caption is left sized
for a bar that stopped having that width several drags ago.

### Themes: the look of the window

The window is drawn from one file: `themes/<name>.theme`, a small JSON object, and
`core/ui.py` is the whole of reading, checking, writing and rendering those
files. It holds no Qt at all, which is why the app and an addon running as its own
process see the same look. The folder is made the first time the program runs, with
`default.theme` written into it - the shipped look spelled out, so there is always
a file to copy from and something to fall back on. `THEMES_DIR` is that folder, and
the `RASCONF_THEMES` environment variable moves it, which is how a build keeps its
themes with its user instead of beside a read-only install.

A theme says six things. `name`, `author` and `blurb` are for people, and are the
words the list shows. `colors`, `tabs` and `styles` are what the window is built
from, and anything a file leaves out is taken from the shipped look, so two colours
in a file is a complete theme. `colors` names the tokens of
`core.const.THEME_COLORS` - surfaces, lines, words, accents, state - grouped for
editors by `THEME_COLOR_GROUPS`; `tabs` holds the thirteen values of `THEME_TABS`,
which are the shape of the tab bar: `icon_px`, `radius_px`, the paddings, gaps and
underline, the widths a tab may not go under, and the two switches `captions` and
`dots`; `styles` is a few lines of Qt stylesheet for whatever those do not reach.

The bar itself belongs to the app, so a tab an addon adds is drawn with the same
icon size, corner radius and caption policy as the tabs beside it, whatever the
addon's own page looks like. The page belongs to the addon: the bundled ones read
the current look through `api.theme` and paint themselves from it, rather than
writing the same hex numbers down twice (see
[Tabs an addon owns](#tabs-an-addon-owns)).

Which look is worn is the `theme` key, kept with the profile, so two profiles can
wear two looks. View > **Theme** lists every file on disk with the one in use
ticked, and adds the folder and the editor below them; it fills itself as the menu
opens rather than at startup, so a theme written in the same run is pickable.
Settings > **Appearance** is the same choice as a picker, a blurb line and two
buttons. Both go through `apply_theme`, which rebuilds the sheet, re-applies the
zoom, saves the config and sends `theme_changed` - nothing needs a restart. A name
with no file behind it draws the default, and the status line says so instead of
quietly showing something else.

A file is checked on the way in. `validate` answers one line per problem - a colour
that is not a colour, a token the interface does not have, a knob outside 0-200 px,
a switch holding a word - and `save` refuses an invalid theme unless the write is
forced. `default` cannot be deleted, and a name that would not make a legal file
name has its bad characters turned into `_`. Making and editing themes is the job of
the bundled **Theme Manager** addon; an addon that only wants to follow the look
needs nothing beyond `api.theme` and `theme_changed`.

### HTTPS in the embedded tabs

The embedded browser is Chromium (QtWebEngine), and it only loads an https page
whose certificate chain ends in a trust anchor that the **operating system**
trusts. On Windows that means the certificate belongs to
`certmgr.msc` -> *Trusted Root Certification Authorities* -> *Certificates* for
your user (or *Local Machine* for all users). A trust decision made inside
another browser does not carry over to QtWebEngine, and a certificate imported
without the *CA* basic constraint is reported as `ERR_CERT_AUTHORITY_INVALID`.

Two ways to make a self-signed dashboard load:

1. **Proper fix, no setting needed**: issue the certificate with
   `basicConstraints=critical,CA:TRUE`, `keyUsage=digitalSignature,keyCertSign`
   (plus `serverAuth` in the extended key usage) and a `subjectAltName` that
   matches the address you browse to, then import it into the Windows
   *Trusted Root Certification Authorities* store. Both the embedded tabs and
   your browser then verify it.
2. **Quick switch**: turn on **Accept untrusted HTTPS certificates** in
   Settings -> Custom tabs, or press *Accept and restart* on the notice bar that
   appears under the address bar of a failed tab. Certificate errors are then
   ignored in every embedded tab, so the connection is encrypted but not
   authenticated - keep it off for anything that handles real credentials.

A web tab that fails to load shows a notice bar with the reason, an
**Open in system browser** button (your browser still verifies the
certificate), and, for a blocked https page, the one-click **Accept and
restart**. With the bypass already active in the session, a failed https
page says nothing in the tab - only in the log - because there is no
certificate choice left to make; the page itself is what it is. The switch
is stored per profile in `ignore_cert_errors`.

Only tabs that opened themselves wear a close button: an addon's page
(the update addon's releases page, for instance) and a tab a page opened -
embedded pages may open links that ask for their own tab, which used to be
swallowed in silence. Pressing the **✕** takes the tab away, and whoever
opened it simply forgets it. The profile's own tabs and the app's tabs
carry no button, because they are removed through Settings > Custom tabs,
not by hand. A page-opened tab starts out called *New page*, takes the
site's own title as its name once the page has one, and follows its own
address rather than the active profile's host.

Auto-connect is one switch for the one main connection: **Connect at startup**,
in the connection panel and saved as `auto_connect`, opens the SSH session -
with SFTP on it - when the app starts, using the profile's host, user and
remembered password. Nothing is opened when both transports are switched off:
the app says so in the log rather than starting a session nobody may use. The
terminal rides that same session, so a shell attaches by itself as the
connection comes up, unless the SSH tab is switched off. An
older profile that carries the two per-protocol options is folded into this one
the first time the new version reads it: either of them on means the main
connection opens at startup, and the two retired keys are dropped.

The View menu has an **Alternate icon** toggle that switches between
`assets/icon.png` and `assets/icon2.png`; the choice is saved to
`use_alt_icon` and applies to the toolbar picture, the window and taskbar
icon, and the logo in Help > About, all from the same `current_icon_path()`
answer, so the About box shows whichever icon is selected.

The same menu has **Show log console** (**Ctrl+Shift+L**), which folds the log
pane at the bottom of the window away and brings it back. Only the pane is
hidden: `log()` keeps appending its lines and, while file logging is on,
nothing is lost from `berry_manager.log`, so switching the pane back on shows
what happened while it was out of sight. The tabs above stretch into the freed
space. The choice is stored per profile in `log_visible` and is applied when the
window opens.

Also in the same menu: **Zoom in** (**Ctrl++**, and **Ctrl+=** on keyboards
where plus needs Shift), **Zoom out** (**Ctrl+-**, **Ctrl+_**) and **Reset
zoom** (**Ctrl+0**). These set the density of the whole interface rather than
one font size: every step moves `ui_zoom` along a fixed ladder (60% to 200%),
and each point size and pixel value in the theme - paddings, the tabs and their
icons, the checkbox and switch sizes, the log pane height, the header logo, the
addon panels - is multiplied by it, along with the terminal's font.
Settings > **Connection** > **Terminal font size** stays the terminal's own
base, so the two combine: a
12 pt terminal at 125% draws at 15 pt. The percentage is saved per profile and
is applied when the window opens; nothing needs a restart.

### Full option list

| Key | Meaning |
| --- | --- |
| `profiles` | List of saved connection profiles |
| `active_profile` | Name of the profile currently loaded |
| `config_version` | Which layout the file is in: 3 is the index plus one file per profile, which the program writes; 2 is every profile inside one file, which is what **Export** produces and what an older install holds. Either is read, and an older one is rewritten once |
| `ssh_timeout` | Seconds for SSH connect/auth (3-300) |
| `logging_enabled` | Master switch for writing `berry_manager.log` |
| `show_hidden_files` | Include dot-files in Berry's device listing |
| `master_connection` | The master switch: off, nothing connects at all - neither the app's main connection nor an addon's own |
| `auto_connect` | Open the main connection (SSH and SFTP together) when the app starts; the one startup switch, and nothing opens while both transports are off |
| `sftp_enabled` | App-wide SFTP gate: its options show, and addons and the device API may use SFTP, only while it is on - and only while the addon's own SFTP switch is on too |
| `ssh_enabled` | App-wide SSH gate and SSH tab visibility: off, the terminal tab is gone and addons are refused SSH work, whatever their own switch says |
| `addon_dev_enabled` | Show the Addon Dev tab and its Tools menu entry |
| `system_notifications` | Allow notices in the system's notification area - an addon's `api.ui.popup` and this program's own news, such as an update being available. Off, those words reach the status line and the log only |
| `splash_enabled` | Show the startup splash while the window is built - the frameless, themed card with the logo, the boot step being loaded and a percentage (see `core.ui`). Off, the program opens with nothing on screen until the window appears |
| `reboot_wait_seconds` | Downtime allowed for a reboot before the first reconnect attempt (0-600) |
| `reboot_retry_interval` | Seconds between reconnect attempts until the device answers (1-300) |
| `reboot_auto_watch` | Offer to watch for the device when a reboot command is sent |
| `default_tab` | Tab selected when the window opens (defaults to Berry; falls back to the first visible tab) |
| `remember_tab_order` | Keep the tabs in the order you set them, next time the app starts; off, every launch begins from the built-in order |
| `tab_order` | That order, exactly as the Settings **Tab order** list shows it: the full name kept in each tab's data rather than the caption the bar has room for, and without the gap and the pinned run, which are never moved. `[]` means the built-in arrangement (SSH, the web tabs, then the addons) |
| `terminal_font_size` | SSH terminal font point size (before the interface zoom) |
| `ui_zoom` | Interface density in percent, set by View > Zoom in/out (60-200, 100 as shipped) |
| `theme` | Which `themes/<name>.theme` the window is drawn from, named without the suffix; set by View > Theme and Settings > Appearance, and a name with no file behind it falls back to `default` |
| `max_log_lines` | Lines kept in the on-screen log (the log file is not truncated) |
| `log_visible` | Show the log pane at the bottom of the window (View > Show log console) |
| `terminal_history_limit` | Quick-command runs remembered in `command_history.json` |
| `web_tabs` | List of embedded browser tabs (name, ip, port, path, scheme) |
| `addons` | Per-addon record for this profile: `enabled`, `own_connection`, the addon's own `ssh_enabled` / `sftp_enabled` transport switches and the addon's `settings` |
| `ignore_cert_errors` | Load https pages whose certificate the embedded browser cannot verify |
| `use_alt_icon` | Show `icon2.png` instead of `icon.png` in the toolbar |
| `quick_commands` | Buttons shown above the terminal |
| `name` | The profile's own title: what its file is named after, and what the File menu's profile list shows |
| `host` | The address this profile connects to |
| `port` | SSH port for that host (1-65535) |
| `username` | SSH user it connects as |
| `key_file` | Private key to authenticate with, instead of a password |
| `remote_root` | Folder on the device Berry treats as its root - the web directory it opens on |
| `source` | The local checkout the Addon Dev tab builds from. A path that is not a folder on this machine is replaced on load, so a profile copied across computers still opens |
| `trust_unknown_host` | Accept a host key never seen before instead of refusing the connection |
| `remember_password` | Keep this profile's password between runs, in the operating system's credential store - never in the config file |
| `web_port` | The dashboard port; offered to an addon as its starting value, and one of the three keys the first **Custom tabs** list is built from |
| `web_path` | The dashboard's path on that port, and the other half of that same seeding |
| `luci_url` | LuCI's full address, which the first `web_tabs` list takes its LuCI tab from |

Editing `quick_commands` is done through the Tools menu or the button on the
Settings tab; you no longer need to open the JSON by hand. The deploy keys the
older versions wrote (`deploy_ignore`, `post_deploy_commands`, the backup,
mirror and `confirm_destructive` options) are no longer read - Berry browses and
edits files directly instead of deploying a source tree - and are stripped from
the profile files when an old config is loaded.

## Managing files

File browsing lives in the bundled **Berry** addon rather than in the app: the
**Berry** tab has a Device page (the current profile's own SSH/SFTP connection)
and a Local page (this machine), and lets you list, open, edit, rename, copy,
delete, chmod, upload and download anywhere on the device - see
[Shipped addon: Berry file manager](#shipped-addon-berry-file-manager).

**Connect** in the connection panel opens the main connection - the SSH session
Berry works over, with its SFTP channel on it - and **Disconnect** closes it.
One button covers both transports because one session carries them; there is no
second connection to make and no file tree built inside the app itself.

## SSH terminal

The SSH tab is a terminal, not a log view. One focused widget holds the
keystrokes: every key you press goes to the device's shell as the raw bytes a
real terminal would send, so full-screen programs work - `nano`, `vi`, `htop`,
colored prompts, progress bars that redraw in place. `^G`, `^X` and the rest of
the control keys arrive as themselves, arrow keys and `Home`/`End` follow the
mode the program asked for, and the terminal answers the "who are you / where
is your cursor?" queries such programs wait for before they draw. The device
is told the exact grid size, so resizing the window re-lays out a running
`nano` live.

Copy and paste are deliberately out of the shell's way: select with the mouse
and press **Ctrl+Shift+C** to copy, **Ctrl+Shift+V** (or Shift+Insert) to
paste - wrapped in bracketed-paste markers when the program asks for that.
Plain **Ctrl+C** stays the interrupt, and plain **Ctrl+V** stays `^V`. Normal
output is kept in a scrollback (wheel or scrollbar; any key or new output
jumps back to the bottom), while a full-screen program owns the whole display
and restores the shell's screen when it exits. A program that sets a window
title puts it in the SSH tab's tooltip; the bell shows in the status bar.

The quick-command buttons above the terminal type their command into the shell,
Enter included, when a shell is open - otherwise they run it as a one-off
command over the main connection and log the output, which needs that
connection open first. Every quick-command run is saved to
`command_history.json`; typed commands are the shell's own business now, so
its arrow-key history is what browses them.

SSH and SFTP have two connection indicators - on the Settings **Connection**
page, in **Connection status**, folded away by default - and they are two lines
for one thing: both
follow the app's single main connection, so they light up and go dark together.
**Open terminal** and **Close terminal** in the SSH tab attach and detach the
shell, which is a channel of that session - closing it leaves the connection,
and SFTP, alone. **Connect** and **Disconnect** in the connection panel open and
close the session itself, and nothing in the app connects without them: the
terminal has no password prompt of its own and says so when the session is down.
Disconnecting marks both transports offline and fires the
`sftp_disconnected` / `ssh_disconnected` events; Berry reopens its listing the
next time you connect.

## Rebooting the device

**Reboot device** in the SSH tab sends `reboot` and then waits for the router to
come back: it holds off for `reboot_wait_seconds` (the downtime the device is
allowed to take), then tries SSH every `reboot_retry_interval` seconds until the
device answers, and reopens the terminal session. Progress is printed in the
terminal with a `[reboot]` prefix, and **Cancel reboot wait** stops the cycle
early.

Running the **Reboot device** button, or a quick command that looks like a
reboot (`sudo reboot`, `busybox reboot`, `shutdown -r ...`), offers to start
the same watch - the offer is controlled by **Offer to watch for the device
after a reboot command** on the Settings **Connection** page. A `reboot` you
type yourself is not noticed: the terminal passes keystrokes straight through
without reading
the line, so use the button or a quick command when you want the wait. Both
timings live in the **SSH terminal** block on the Settings **Connection** page
and are saved to `config.json`.

## OpenWrt SFTP requirement

Dropbear provides SSH access but may not ship an SFTP subsystem. If the
manager reports that SSH connected but SFTP startup failed, install the server
package on the device and reconnect:

```sh
apk update
apk add openssh-sftp-server
```

On older opkg-based OpenWrt releases use `opkg update` then
`opkg install openssh-sftp-server`.

## Addons

An addon is a file you drop next to the program; it extends the app without
changing it. The folder is `addons` beside `berry_manager.py`, or beside the
`.exe` when the app is built. It is created the first time the app starts (or
when you press **Open folder** on the Settings
**Installed addons** page), so it exists even if you never add
anything to it.

Four layouts are recognised, and each one is a separate addon:

| In `addons` | What it is |
| --- | --- |
| `mytool.py` | A Python module imported into the app |
| `mytool/addon.py` or `mytool/main.py` | The same, with its own folder for data and a `manifest.json` |
| `mytool.exe` (or `.bin`; on Linux, any executable file) | A program the app starts and talks to over stdin/stdout, with its `mytool.json` manifest beside it |
| `mytool/addon.exe`, `mytool/addon.bin` or `mytool/mytool.exe` | The same program living in its own folder - where a folder build has to keep its `_internal` beside it |

Folders and files that begin with `.` or `_`, and the `addons/_data` folder, are
the app's own working space and are never read as addons.

A built program also brings addons of its own: every addon in this folder at the
moment of the build is packed **inside the executable** and listed as a shipped
addon. It is switched on and off like any other and keeps its own config file
beside the `.exe`, but its source belongs to the build - see
[Addons inside the built executable](#addons-inside-the-built-executable).

Settings splits addons over its own tab bar: a page per addon that came inside
the program, and one **Installed addons** page with a row for everything that
came in after the build - a hand-written `.py`, a program built by the Addon Dev
tab, and an addon the store installed. Each row carries the addon's enable
switch, its version and author, what it said about itself, a **Restart**
button, an **own connection** switch where the addon's manifest declared
`"connection": "both"` - that choice is the user's only where the author
left it open - and
a **Show tab** button when the addon opened one. A shipped addon gets the
switch, its state and its config panel, plus **Show tab** while it has one - but
no **Restart**: restarting reads the addon's file again, and that file is inside
the program - and no **own connection**, which is the user's choice to make
about an addon of theirs and not about the program's own. An addon that declared
`"connection": "own"`, or the default `"app"`, carries a plain label on its row
naming the connection it uses instead of a switch: only a `"both"` addon leaves
that choice to the user, and the label says *own connection* or *main
connection* in the same words **Connection status** uses, out of the same one
decision. An addon that declared no transport is labelled *no device
connection* instead, so a row never implies that an addon is waiting on a
**Connect** it will never make.
Under the addon's name sits its own row of switches, one per transport the
manifest declared - **SSH** and **SFTP** - each followed by the dot that speaks
for it, with the quiet line *connections this addon may use* beside them. The
name is said once: a dot never repeats the label it stands next to. They are the
addon's half of the app-wide gates in Settings > Connection: a transport is
handed to the addon only while that switch and the app-wide one are both on, and
the switches are shown whether or not the addon is running, so an addon can be
limited before it is ever started.
They decide who may use a connection, never whether one is opened: the
transports still travel on the app's one main session. The choice is stored per
profile in `addons -> <id> -> ssh_enabled` and `sftp_enabled`, both on for an
addon the profile has never spoken about.
Switching an addon off stops it and takes
away everything it added - its tabs, the Settings pages it built, its menu
entries, its timers and its listeners - without restarting the app; switching it on
starts it again. The new tab joins the row of tabs without being jumped to - being
switched on is not a request to look at it - and **Show tab**, or the addon's own
menu entry, is how you get there. Where it lands is the app's decision: a second
tab from an addon that already has one goes immediately left of the tab that
opened it, and a tab that asked to be pinned sits in the run at the right edge
instead of in the row at all (see [Tabs an addon owns](#tabs-an-addon-owns)). What
an addon keeps in its row - its config panel and
any controls it added - folds away while it is off. Switching one addon
rebuilds only that addon's row, so the list does not shift under you while you
are reading it. The choice is stored per profile in
`addons -> <id> -> enabled`, and a **Rescan** button picks up files that were
added or removed meanwhile.

Beside each transport switch sits one dot, in one of three colours, and it answers
the whole question at once: can this addon use that transport, does it, and over
which connection? **Green** means it can right now - the one main connection is
open for an addon that shares it, or an addon on a connection of its own has
already run a job over it. **Amber** means every switch is on while nothing stands
connected: an addon riding the main connection is waiting for **Connect**, and one
on a connection of its own has simply not been asked yet, so *may* and *has* stay
two different words, in the dot's own tool tip. **Red** means a switch refuses it,
and the tool tip names which - the master switch in the connection panel's header,
that transport's app-wide switch on the Settings **Connection** page, the addon's
own enable switch, the transport's switch beside the dot, or a transport the
manifest never declared under `"requires"`. The same three states colour the
`s` and `f` marks on that addon's tabs, so a tab never looks live while its addon
is refused, and an addon on its own connection keeps a live mark even while the
main connection is down. The answer holds whether or not the addon is running: one
that is switched off is refused every connection and says so in red. Berry
normally wears green on both as soon as the main connection is up, because its
Device page lists files over SFTP and runs chmod and find over SSH. The same
information, spelled out in words rather than in colours, is listed per running
addon in **Connection status** on the Settings **Connection** page.

Beneath a row, when that addon keeps one, sits a **Config** panel: the settings
that belong to the installation rather than to a profile, edited as a form and
stored as plain JSON in `addons/_data/<id>/config.json`. See
[Each addon's own config file](#each-addons-own-config-file).

An addon that crashes is on its own: every call is wrapped, the failure is
written to the log and shown in red on its Settings row, and the rest of the
app carries on.

Run the app from a source checkout and one more tab appears, **Addon Dev**,
that works on the addon files themselves - the manifest, the log, the processes,
a test script and a compile to a program of its own. A built program can show it
too: **Show the Addon Dev tab** in Settings says whether it is there. See
[The Addon Dev tab](#the-addon-dev-tab).

### Writing one

```python
from addon_common import AddonError     # plain Python, nothing Qt behind it

ADDON = {"name": "My tool", "version": "1.0", "api": 1,
         "requires": ["ssh"], "connection": "app",
         "description": "What it does"}
# A picture called mytoolicon.png in the addon's assets folder is found on its
# own; "icon" is only needed for a file named anything else.

api = None

def init(plugin_api):
    global api
    api = plugin_api
    api.ui.menu_item("Do the thing", do_the_thing, menu="Tools")
    api.events.on("ssh_connected", lambda payload: api.device.run_async("uptime"))

def shutdown():
    pass
```

`init(api)` is required. Everything else is optional and is called when it
happens:

| Hook | When |
| --- | --- |
| `start()` | after `init`, once the window is complete |
| `shutdown()` | the addon is switched off, or the app closes |
| `on_event(name, payload)` | any event, see the list below |
| `on_<event>(payload)` | one named event, e.g. `on_tab_changed` |
| `on_message(sender, data)` | another addon called `api.peers.send` |
| `on_widget(tab, id, event, payload)` | the user touched something in one of its tabs, or on one of its Settings pages - the page's name is where a tab's would be |
| `on_config(payload)` | its `addons/_data/<id>/config.json` was saved, by Settings or by itself |

The object in `init` is the only door into the app:

| Namespace | What it gives |
| --- | --- |
| `api.id`, `api.name`, `api.log(text)`, `api.error(text)` | identity and the shared log |
| `api.session` | everything the session knows: `profile()`, `settings()`, `status()`, `tabs()`, `current_tab()`, `web_url()`, `version()`, `app_stylesheet()` (already scaled to the current zoom), `zoom()`, `app_icon()`, and the four an installer needs: `rescan_addons()` (look at `addons` again on the next turn of the event loop, so the caller is not torn down mid-request), `set_addon_enabled(id, enabled)` (write another addon's switch into the active profile, without starting or stopping anything), `addon_state(id)` (how that addon stands here - `installed`, `enabled` and `running` as one dict, read off the app's own records) and `set_addon_running(id, running)` (write the switch *and* load or stop the addon, answering with the same state dict) |
| `api.device` | SSH and SFTP over the app's one main connection, or a connection of the addon's own: `run(command)`, `run_async(command, callback)`, `shell_send(line)`, `is_connected()`, `ensure_connected()`, `open()` (raw paramiko), and `list`, `exists`, `stat`, `read_text`, `read_bytes`, `write_text`, `write_bytes`, `append_text`, `mkdir`, `remove`, `rename`, `upload`, `download`. Every call is refused unless the master switch, that transport's app-wide switch, the addon's own switch on its Settings page and that transport's switch on the same row are all on, the transport is listed under `"requires"`, and the connection it reaches is the one `"connection"` declares - which for an addon sharing the app's session also means that connection has to be open. A `terminal` leaf in one of the addon's own tabs is judged by exactly these gates too, only reached through `api.tabs.set_widget` instead of this namespace (see **Addons in other languages**) |
| `api.tabs` | `add_web_tab`, `add_tab` - each taking `position`, `pinned` and `icon_only` - `close`, `rename`, `activate`, `names`, `exists`, `url`, `navigate`, `reload`, `view`, `widget`, `set_widget`, `widget_value`, `set_html`, `inject_css`, `inject_js`, `set_style`, `set_icon`, `icon`, `use_app_icon`, `set_pinned` |
| `api.theme` | the looks the window is drawn from, one JSON file each in `themes`: `name`, `names`, `exists`, `folder`, `read`, `raw`, `colors`, `color`, `tabs`, `metric`, `icon_px`, `chrome_px`, `shows_captions`, `shows_dots`, `groups`, `token_knobs`, `stylesheet`, `sheet_of`, `validate`, `templates`, `template_text`, `reference`, and the five that move them - `use`, `save`, `copy`, `rename`, `remove` |
| `api.ui` | `information`, `warning`, `error`, `question`, `input(title, label, default, secret=False)` - one line of text, `None` when the box was cancelled, and `secret` draws dots while it is typed, `choose`, `popup(title, text)` - a notice the app raises for the addon, named for whoever raised it, `notify`, `progress_notice(title, text, value, maximum)` - a small card over the window's corner with a bar and its percentage, re-shown on every step of a long job and holding a moment before fading when it fills, `progress_notice_close()` - take that card away now, `menu_item`, `dialog`, `pick_color(title, initial)` - the colour wheel, answering `#rrggbb` or `""`, `pick_file(title, kind, filter, initial)` - a file box on this machine, `kind` being `open`, `save` or `folder` and nothing picked answering `""`, `set_setting_widget`, `window()` |
| `api.events` | `on(name, callback)` (returns the unsubscribe callable), `off`, `names` |
| `api.settings` | the addon's own saved dict, per profile: `all`, `get`, `set`, `update`, `pop`, `remove` |
| `api.settings_pages` | a page of the app's own Settings window that the addon owns - `add`, `remove`, `names`, `exists`, `title`, `set_title`, `activate`, `widget`, `set_widget`, `widget_value`, and `set_icon`, which says a settings page wears no mark and answers False |
| `api.config` | its own file in `addons/_data/<id>`: `declare`, `file`, `path()`, `exists()`, `open_folder()`, `schema()`, `defaults()`, `stored()`, `all()`, `get`, `set`, `update`, `pop`, `reset`, `text`, `save_text` |
| `api.schedule` | `after(seconds, fn)` and `every(seconds, fn)`; both stop with the addon |
| `api.paths` | `addon`, `data` (a private folder, created on demand), `config` (this addon's config file), `configs` (the addon's own data folder, where that file sits), `base`, `addons`, `assets` (the pictures that ship inside the program - or, for a compiled addon, the ones packed inside it, which the app's folder is the fallback for), `log` |
| `api.peers` | `list()`, `info(id)`, `send(id, data)` to talk to another addon |

The events are `startup`, `shutdown`, `sftp_connected`, `sftp_disconnected`,
`ssh_connected`, `ssh_disconnected`, `operation_done`, `tab_changed`,
`profile_changed`, `settings_applied`, `addon_enabled`, `addon_disabled`,
`addon_config_changed`, `transport_changed` and `theme_changed`. A
`transport_changed` carries the `transport`, plus a `scope` of `"app"` or
`"addon"` saying which switch moved and, for an addon's own, its `addon` id; an
addon that shows a connection state of its own redraws from it. A `theme_changed`
carries `theme` - the look the window is drawn from now - and `wanted`, the name
that was asked for, which differs only when that theme has no file. Anything that
painted itself with colours read earlier re-reads them there; a tab built from a
spec needs nothing, because the window repaints it under it.

Device work runs in a small shared thread pool, so a slow command never
freezes the window. `run()` waits for the result while keeping the interface
responsive; `run_async()` hands it to a callback instead, which is what you
want from `init`; and `run(command, block=False)` gives back a future -
`done()`, `result()`, `cancel()` - which is how one command goes to several
devices at the same time and stays off the GUI thread. It is the same future
whether the addon was imported into the app or built as its own program, which
is worth something to an addon the store compiles: see **Addons in other
languages**. Relative file paths are confined to the profile's remote
directory, and `outside_root=True` opts out of that. A call that rides the app's
main connection needs nothing else - no credentials, no password - only that
the connection is open. A call that opens a connection of its own uses the
profile's saved connection details, which means a password has to be in the
connection panel (or saved with **Remember password**) for it to connect at all.
A `terminal` leaf is the one thing that does not read the profile at all: it
connects on exactly the details the addon hands to `connect`, which is how an
addon can ask for a password of its own and keep it in nothing it saves.

Every `api.device` call passes the same gates, in this order. The **switches**:
the master switch in the connection panel, that transport's app-wide switch
(`ssh_enabled`, `sftp_enabled`), the addon's own switch on its Settings page and
*that transport's* switch on the same row - all four have to be on, since an
addon switched off, or switched off for one transport, has no business with that
part of a connection. The **transports**: the manifest's `"requires"` list names
each transport the API may hand the addon.
The **connection source**: `"connection"` says whose connection the calls
use - `"app"` (the default) rides only the app's main session, `"own"` only a
connection of its own (`run(device=...)`, `open()`, `bind()`), `"none"` - like
leaving `"requires"` out - reaches no device, and `"both"`
lets the user's *own connection* switch in Settings pick, which is also the
only case where the addon may switch it itself with `api.device.set_mode()`.
The **main connection**: an addon sharing the app's session may use it only
while it is open, and no addon may open it - the Connect button, the
**Connect at startup** switch and the reconnect after a reboot are the only
things that do, and `ensure_connected()` asks the app for it. An addon on a
connection of its own is judged on its own and connects whether or not the main
connection is up.
An addon that comes inside the program always rides the app's session.

### Each addon's own config file

`api.settings` is per profile and is meant for small notes (a checkbox, the last
tab used). Anything a user would want to look at and edit themselves - where the
certificate lives, which command restarts the web server - belongs in
`addons/_data/<id>/config.json`: one plain JSON file per addon, shared by every
profile on this installation, editable by hand with any editor.

The addon describes its file once, from `init`:

```python
DEFAULTS = {"cert_dir": "/etc/ssl", "cert_name": "mycert.crt", "days": 365,
            "subject": {"C": "NO", "CN": "knet3b.lan"}, "backup": True}
FIELDS = [
    {"key": "cert_dir", "label": "Folder on the device", "kind": "text",
     "hint": "Usually /etc/ssl"},
    {"key": "days", "label": "Valid for (days)", "kind": "number",
     "minimum": 1, "maximum": 3650},
    {"key": "subject.CN", "label": "Common name", "kind": "text"},
    {"key": "backup", "label": "Back up the old pair first", "kind": "bool"},
]

def init(plugin_api):
    global api
    api = plugin_api
    values = api.config.declare(FIELDS, DEFAULTS, title="where it writes")
```

| Kind | Control | Reads as |
| --- | --- | --- |
| `text` | one-line box | `"/etc/ssl"` |
| `number` | spin box, with `minimum` and `maximum` | `365` |
| `bool` | switch | `true` / `false` |
| `choice` | combo, with `choices` | `"ec"` |
| `list` | one-line box, comma separated | `["a", "b"]` |
| `multiline` | text box, one entry per line | `["line one", "line two"]` |

A dotted `key` reads and writes inside a section, so `subject.CN` is the `CN`
of the `subject` object and `san_dns` sits at the top level. `hint` becomes the
control's tool-tip and `placeholder` the greyed-out example.

What the app then shows:

- Under the addon's row in Settings, a **Config** group with one line
  per declared field - or a raw JSON editor when the addon declared nothing, so
  any addon's file can still be edited from the window. **Save config** writes
  the file, **Reload** re-reads it, **Reset to defaults** puts back what the
  addon shipped, **Open file** hands it to the system's JSON handler. A key that
  is not in the form - added by hand in an editor - survives a save.
- The panel is folded away while the addon is switched off, with the rest of
  what the row holds: a stopped addon shows its name, its state and its note.
  The tidy form itself never depends on the addon's code being importable:
  `declare` keeps a copy of the field descriptions in
  `addons/_data/<id>/fields.json`.
- Saving fires `on_config` on the addon and the `addon_config_changed` event for
  the others, so an open tab can pick up the new values without a restart. The
  addon itself reads the file whenever it needs it (`api.config.all()`), which
  is also why a hand edit works.
- **Open addon data** at the top of the addons section shows the `addons/_data`
  folder, where every addon's own `config.json` and data live side by side.

`config` - the manager's own folder holding the config, the log, the command
history and the build record - and the per-addon data folder `addons/_data` are
Git-ignored: they hold machine-local paths and, for the certificate addon,
copies of the public certificate. Neither the private key nor any password is
ever written there.

### Tabs an addon owns

`api.tabs.add_web_tab(name, url=...)` - or `ip=`, `port=`, `path=` and
`scheme=` instead of a full address, which follows the active profile's host the way
the profile's own web tabs do - opens an embedded browser page that
belongs to the addon rather than to the profile: it is not written to
`config.json`, never appears in Settings > Custom tabs, is not rebuilt when you
switch profiles, and closes again when the addon is switched off. It wears an
**✕** on its tab so the user can close it by hand whenever they like, and the
addon forgets it quietly: `api.tabs.exists(name)` answers no from then on, and
a later `close(name)` is a harmless no. Settings >
Custom tabs stays exactly as you left it.

`api.tabs.add_tab(name, widget=...)` takes any Qt widget a Python addon builds,
with `style=` for a stylesheet of its own, so an addon's tab can look like
nothing else in the app. Adding one does not steal the view - the tab appears
in the row and `activate` is how an addon shows it when it really means to.
For a page, `set_html`, `inject_css` and `inject_js`
let an addon restyle or script what the device serves.

Both calls take `position=` for where the tab goes, and mean it to be the app's
answer when the key is left out: the place immediately left of the leftmost
movable tab this addon already owns, so a report or a helper opened from an addon's
own tab sits beside its parent instead of at the far end of the bar where the user
has to go looking for it. An addon with nothing open yet, or one whose only tab is
pinned, starts at the end of the movable row, just left of the gap.

`pinned=True` asks for a different kind of tab: one held in the run at the right
edge, beside Settings, out of the row the user drags about and out of the saved
order, keeping its place while the tabs around it come and go. `icon_only=True`
draws it as its mark with no caption - the name still addresses it, and the tooltip
still says it - and only a pinned tab may keep a blank caption, because the bar puts
a movable tab's name back the moment it re-measures. Both apply to an embedded page
from `add_web_tab` as well, and both can be changed afterwards with
`api.tabs.set_pinned(tab, pinned=True, icon_only=False)`; `pinned=False` lets the
tab back into the row with its caption restored. That is how the **Addon store**,
**Manager update** and **Certificates** tabs come to sit beside Settings as three
marks rather than take places in the order the user arranged: like the page they
open, they are the window's furniture, recognised by their pictures. The
certificate manager asks for it the same way the other two do - `pinned=True,
icon_only=True` on its `add_tab` call - so nothing about it is special-cased in
the app, and renaming a tab is no reason to give it back its caption: the tooltip
still says what it is.

A tab can also carry a mark beside its label, the way a page's favicon sits
beside its title: `api.tabs.set_icon(tab, icon)`, with `api.tabs.icon(tab)` to
ask whether it has one. The icon is either an SVG document written out as text
or a picture file - an absolute path, or a name beside the addon's own folder, so
`set_icon(tab, "icon.png")` works for a folder addon - and passing nothing takes
the mark away again. Text is what an addon that might be compiled should send: it
crosses the pipe unchanged and needs no file lying next to the program, while a
picture has to be somewhere the app can actually read. Drawing an SVG needs Qt's
`qsvg` plugin, which a source checkout always has; a build that did not collect
it loses the mark, says so in the log and leaves the tab working, which is all a
decoration is worth. An embedded page is left alone: it keeps following the
favicon the page itself sends, which is set by the device and not by the addon.

Every addon wears a mark of its own now, and each holds it as an SVG string in
`TAB_ICON` for exactly that reason: `addons/berry.py` (a cluster of drupelets),
`addons/manager_update.py` (a download mark), `addons/theme_manager.py` (a tray
of colour dabs), `addons/addon_store.py` (a bag with an arrow into it),
`addon.store/cert_manager.py` (a trophy), `addon.store/openwrt_logs.py` (a log
page lined in the device colours), `addon.store/openwrt_package_manager.py` (a
package box), `addon.store/multi_ssh.py` (two shells, one behind the other,
since more than one at a time is the whole point) and
`addon.store/berry_web.py` (a berry lying on a page, inside a browser frame).

Each of them looks for a real file first, and the search is one rule with two
places to look: the file named `{id}icon.png`, tried in the folder the addon's
own source keeps its pictures in - `assets/<id>/` beside it, which is where a
store addon's file is - and then in the program's `assets`. `berry` is therefore
worn by `assets/berryicon.png` and `multi_ssh` by
`addon.store/assets/multi_ssh/multi_sshicon.png`, with no addon naming its file
anywhere in its source. `addon_common.find_addon_picture` is that lookup, and it
is what closed the gap `find_icon` left open: `api.paths.assets` always answers
with the *program's* folder, so a store addon's own picture was never found
while the addon was being written - only inside the executable the store builds,
which packs the assets flat. Nothing is tried under an older name now: the
aliases the lookup used to keep (`updateicon.png`, `storeicon.png`,
`themeicon.png`, `webinstallericon.png`, `openwrtpkgmgr.png`) are retired, and
the drawing in `TAB_ICON` is worn when no file is there at all.

A tab that never names a mark is not left bare either: it wears the program's
default tab picture (`assets/defaulttabicon.png`), the SSH tab wears
`assets/terminalicon.png` and the pinned Settings tab wears
`assets/settingsicon.png` - each of the three falling back on the same drawing
in `core.ui` when the file is absent, so the shipped picture and the
painted one can never drift apart. Every tab mark is drawn larger than the bar
measures it at, so the pictures read clearly without the tabs growing.

The addon store's cards follow the same rule, and an addon that shipped no
picture is not left with a blank box: the store wears
`assets/storedefaultaddon.png`, the program's own mark for an unnamed addon,
and a drawn tile when even that file is missing.

A tab can instead wear the program's own logo: `api.tabs.use_app_icon(tab)`
points it at whatever `api.session.app_icon()` returns - the same picture the
window and toolbar wear - and it keeps pointing there, so switching to the
alternate icon in View changes the tab's mark in the same breath. Choosing a
mark with `set_icon` ends the follow; `use_app_icon(tab, False)` drops it and
takes the mark away. Every addon that ships inside the program now wears a mark
of its own instead, and so does the store's **Berry web dashboard**, which took
over the installer that used to follow the logo; the call
stays for an addon that would rather look like the program than like itself.

### A Settings page an addon owns

Everything above puts an addon beside the app's tabs. `api.settings_pages` puts it
*inside* Settings instead: a page of the app's own Settings bar, standing with
**Connection**, **Startup & defaults** and the program's other categories -
outside the shared **Installed addons** page, which keeps the switches and the form
built from the addon's declared fields.

```python
api.settings_pages.add("planner", title="My planner", spec={
    "kind": "form",
    "children": [
        {"label": "Days ahead", "kind": "spin", "id": "days"},
    ],
})
```

- The page is built the way a tab is - a declarative `spec`, or any QWidget from a
  Python addon - and its controls report to the same `on_widget` hook, with the
  page's name where a tab's would be, so one handler serves both.
- `add` replaces whatever already holds that name, which is what lets an addon
  rebuild its page from nothing after a config change, and what makes `remove` the
  only thing a switch on that page has to do.
- The page is the app's furniture: it is taken away by itself when the addon is
  switched off or removed, so a stopped addon leaves nothing behind, and it is
  never written to the profile's config.
- `activate` opens Settings on the page, `set_title` renames it, and `widget`,
  `set_widget` and `widget_value` read and write its controls exactly as a tab's.
  `set_icon` answers False: a Settings page wears no mark of its own.
- A page is not a settings *store*. What the user may still change belongs in
  `addons/_data/<id>/config.json` and is edited on the addon's own row; a page is
  for the controls that need more room than a row gives - a tree, a table, a log,
  a button. For one or two switches, `api.ui.set_setting_widget(spec)` puts them
  under that row instead.

The template in **New addon...** builds one of these beside its tab, added or
removed by a switch of its own in the addon's config, as the shape of an addon that
has more to say than a tab holds.

### Addons in other languages

A program in the folder is started by the app and speaks one JSON object per
line on its stdout, read on its stdin. It sends a request

```json
{"id": 1, "api": "device.run", "args": {"command": "uptime"}}
```

and gets back `{"id": 1, "result": ...}` or `{"id": 1, "error": ...}`. It is
told the API version, its own data folder and the active session in a first
`{"op": "start"}` message, receives `{"op": "event", ...}` for every event and
`{"op": "peer", ...}` from other addons, and `{"op": "shutdown"}` when it is
being stopped. Anything it prints to stderr goes to the log with the addon's
name in front of it.

A second process cannot build Qt widgets, so it describes its interface and
the app builds it: pass `add_tab` a `spec` instead of a `widget`, and address
its parts by `id` with `set_widget` and `widget_value`. A widget is one object
with a `kind`, and containers hold `children`:

```json
{"kind": "column", "children": [
  {"kind": "label", "id": "status", "text": "not connected"},
  {"kind": "table", "id": "rows", "columns": ["file", "size"]},
  {"kind": "button", "id": "refresh", "text": "Refresh", "on": ["clicked"]}]}
```

Kinds: `column`, `row`, `grid`, `form`, `group`, `scroll`, `splitter`, `tabs`,
`label`, `button`, `line`, `multi`, `check`, `combo`, `list`, `tree`, `table`,
`progress`, `spin`, `slider`, `spacer`, `image`, `web`, `color` (a hex line with
the colour wheel behind its swatch) and `terminal` (its own paragraph below).
Common attributes: `id`, `text`,
`tooltip`, `style` (a Qt stylesheet, so the addon can theme its own tab),
`enabled`, `visible`, `font_size`, `items`, `rows`, `value`, `checked`,
`placeholder`, `readonly`, `minimum`, `maximum`, `columns`, `word_wrap`,
`expand` (claim the width its row or column leaves over - what lets one status
line stretch across a header instead of being squeezed by a spacer), and
`format` for a progress bar - the words painted inside it, where Qt expands
`%p`, `%v` and `%m` into the percentage and the numbers. An `image` leaf holds a
picture of its own: `size` is the most it may measure in either direction (drawn
at the interface zoom), and its `value` - or `set_widget(tab, id, "image", ...)`
- is an image file's path or an SVG document as text, the same two shapes a tab's
`set_icon` takes; when there is no picture to read the leaf goes blank rather
than show the user a path. A `list`, `tree` or `table` entry can wear a mark of
its own too: a list item as `{"text": ..., "icon": ...}`, a row as a dict with
`"icon"` beside `"cells"`, and that picture is drawn in front of the row's first
cell. A `table` can also wear a check box in front of one column: the spec names
the column with `"checkbox"` and lets several rows be picked at once with
`"multi"`, a row carries `"check": True/False` to start ticked or not, and a box
the user moves reports `toggled` with the row's text and what it is ticked to.
`on`
lists the interactions to report back: `clicked`, `toggled`, `changed`,
`returned`, `selected` and, for a `list`, `tree` or `table`, `context` - a
right-click, carrying the text of the row under the cursor, so an addon can
answer with a menu from `api.ui.choose` without a second round trip. A `tabs`
container is not fixed at the moment it is built either: `set_widget(tab, id,
"add_page", {"label": ..., "spec": ...})` puts one more sub-page on it - of any
kind, including a `terminal` - with `"position"` for somewhere other than the
end and `"current": False` to leave the view where it is, `"remove_page"` takes
one away by index or by caption, and `widget_value(tab, id, "pages")` reads
every caption back, which is how an addon opens a page per connection and shuts
it again. Removing by caption is the safer of the two answers, because a page
taken out of the middle moves the numbers of the ones after it and no caption
does. `api.ui.dialog(spec)` and
`api.ui.set_setting_widget(spec)` use the same format, which is how a non-Qt
addon gets its own controls in Settings.

`on` is not decoration: a control that does not list an interaction stays quiet,
and the addon hears about it in `on_widget(tab, id, event, payload)`. Holding on
to the widget and connecting its signal (`api.tabs.widget(...).clicked.connect`)
looks equivalent and is not - only the app's own process has the widget, so that
route works while the addon is imported and goes silent the moment the same file
is compiled. The same applies to `setEnabled`: use
`api.tabs.set_widget(tab, id, "enabled", False)` rather than the widget. Address
the tab by id in both directions and one file serves the app and its own program.

A `terminal` leaf is the app's own terminal screen - the widget that draws the
device's escape sequences and sends the keys typed into it - run on a connection
of its own rather than the app's main session, so one tab can hold several and
each is a separate machine. `set_widget(tab, id, "connect", {...})` brings one
up and takes `host`, `port`, `username` and either `password` or `key_file` (a
plain `"user@host:port"` string says the same thing); `"disconnect"` drops it,
`"send"` types raw bytes, `"command"` types a line with the Enter added, and
`"write"` prints a note from the addon onto the screen without sending it
anywhere. `widget_value` answers `"text"` for what is showing, `"grid"` for its
shape and `"state"` for what it is attached to - and `"state"` leaves the
password out, so an addon can show where a shell is without echoing what opened
it. In `on` it answers `ready` (the link came up), `closed` (it went down,
asked for or lost) and `failed` (carrying the reason), plus `bell` and `title`
when it asks for them. The three properties that reach the device - `connect`,
`send`, `command` - go through the same gates as any `api.device` call: the
manifest needs `"requires": ["ssh"]` and a `"connection"` of `"own"` (or `"both"`
with the addon's own connection switch on), and the master switch and the app's
SSH switch apply as always. Everything else on the leaf stays available,
because reading what is on a screen and writing a line on it are the addon's
business with its own widget. `addon.store/multi_ssh.py` is the worked example:
it adds a sub-page per session, puts one `terminal` in each, and asks for the
password with `api.ui.input(..., secret=True)` every time rather than keeping one
in a file.

An executable may also carry a `<id>.json` (or `addon.json`, `manifest.json`)
next to it with `name`, `version`, `author`, `description` and, if it is not a
standalone program, the `command` list used to start it - for example
`{"command": ["python3", "tool.py"]}`.

A function cannot be sent as JSON, and a second process could not call one back
anyway, so a request that takes a callback names which argument it is and keeps
the function at its own end:

```json
{"id": 4, "api": "ui.menu_item", "args": {"text": "Refresh", "menu": "Tools"},
 "callback": "callback", "token": 2}
```

The app replaces that argument with a closure that pushes
`{"op": "invoke", "token": 2, "args": [...]}` whenever the menu entry is
pressed, and the addon calls the function it stored under token 2. This is why
`device.run_async(command, on_done)` and `ui.menu_item(text, callback)` work in
a process addon exactly as they do in a Python one. The two things a process
addon genuinely cannot have are `device.open()` (a paramiko session belongs to
one interpreter) and `ui.window()` (a Qt widget belongs to the app).

A job the app is still running is another thing the pipe cannot carry, so
`device.run(command, block=False)` answers with a handle instead of a future:

```json
{"id": 7, "result": {"addon_future": 3}}
```

The addon then asks about the job with three calls the bridge answers out of its
own registry, never passing them to the api - `device.future_done`,
`device.future_result` and `device.future_cancel`, each taking that number.
`addon_client.py` recognises the handle and gives the addon a `RemoteFuture`
wearing the future's face, so `block=False` means the same thing in either
runtime and a compiled addon can fetch from several devices at once the way an
imported one does. The answer stays filed under its number after it was read,
so a handle keeps reporting itself done and keeps giving the same result - or
the same failure, in words - instead of turning into a hole; the oldest of
those remembered answers go after there are 64, and all of them go when the
process is stopped, which cancels whatever nobody came back for.

`addon_client.py` is the other half of this protocol, so an author does not
write it: `addon_client.serve(__file__)` gives a Python file the loop, the
`api` object, the manifest handshake and a clean exit code, and any language
without it can follow the same few lines of JSON. The app pushes `start` as soon
as the process is up, which is normally while the addon is still being imported,
so `serve` holds that line until `init` has run and fires the addon's `start`
once, from whichever of the two arrives last. Put

```python
if __name__ == "__main__":
    import addon_client
    raise SystemExit(addon_client.serve(__file__))
```

at the bottom of an addon and the same file runs both ways - imported into the
app, and started as its own program by the app or from a shell. The app adds its
own folder to the child's `PYTHONPATH`, so `import addon_client` and
`from addon_common import AddonError` resolve either way.

The smallest addon worth having is one `.py` in `addons`: a manifest, an `init`
that adds a Tools menu entry, and a line in the log when a connection comes up -
which is the shape **New addon...** writes. Switching an addon off in Settings
leaves its file untouched on disk.

### The Addon Dev tab

As long as an addon is a `.py` file rather than a program built from it there is
something to manage around that file, so one more tab, **Addon Dev**
(Tools > Addon Dev, Ctrl+Shift+A), sits in front of Settings - in a source
checkout and in a built program alike. **Show the Addon Dev tab** in
Settings > Startup & defaults adds and removes it together with its menu entry;
switching it off leaves the addons themselves untouched, only the page that
works on their files goes away.

What the built program cannot do, it says rather than tries: the addons that
came inside the `.exe` are listed and their source is shown as it is, with every
field read-only, and **Compile**/**Test** explain that there is nothing to build
with until a Python interpreter is found on the path. The modules an addon
imports are no longer such a reason: a build made by the current workflow
carries a plain copy of them, and one that does not is pointed at the addon
store, which fetches them.

The table on top is the runtime's own list - id, name, version, whether the
addon is imported or running as a process (with its pid), whether it loaded or
failed, and the file it came from. Beside it: **Restart**, **Show tab**, **Open
source**, **Rescan**, **New addon...**, **Open folder** and the same **Enabled**
switch Settings has. Everything below it works on the selected row, and the
refresh timer only updates what changes by itself, so a half-typed manifest is
never overruled while you are writing it.

| Pane | What it is for |
| --- | --- |
| **Details** | The manifest - name, version, author, description, notes, api - edited as a form, and below it the raw JSON of `addons/_data/<id>/config.json` with Save, Reload and Open. |
| **Compile** | The source/compiled choice for this id, the PyInstaller options, **Compile addon**, **Cancel**, **Clean staging** and the build output. |
| **Test** | `addons/_tests/test_<id>.py`: **Create test script** writes it from the template, **Run test** runs it as its own process and shows its output and result. |
| **Docs** | The API reference and the guides, with a *find* box over them: it searches names, summaries, signatures and guide text at once, and every page is opened by the list beside it. **Build guide** on the Compile pane and **Testing guide** on the Test pane jump into the page that explains what each of them does. |
| **Log** | The lines this addon wrote with `api.log`, taken from `berry_manager.log`, with a text filter, an *errors only* filter and *follow*. |
| **Processes** | The pid, state, exit code, uptime, request count and command line of every addon running as a program; and what the app holds for the imported ones - their tabs, timers, menu entries and event names, plus the threads serving them. |

A table, six panes and the notes under them do not fit into a short window, so
the whole tab sits in a scroll area: each part keeps the height it needs - the
index as many lines as there are addons, an output view at least a dozen - and a
window that cannot show all of it gains a scroll bar instead of clipping text.

Details are written back where the app actually reads them: into the
`ADDON = {` literal of the addon's own file when the file has one, and otherwise
into a `<id>.json` manifest beside it. The form reads the values from those files
rather than from memory, so an addon that is switched off still shows the name
its author gave it instead of its identifier - and saving cannot write the
identifier over it. Keys the form does not own (`command`, `id`) are kept as
they were.

New addon asks for the header only: the identifier names the file, the tab and
the config file, and the name, tab title and menu entry fill themselves in as
you type (an identifier already in use is refused). **New addon...** writes
`addons/<id>.py` and, when it was asked for, `addons/_tests/test_<id>.py`, and can
switch the addon on straight away. What it writes is the shape an addon is usually
wanted to be rather than a skeleton: a header docstring naming the four calls worth
a new author first, the `ADDON` dict with its `"requires"` and `"connection"`
explained, `FIELDS`/`DEFAULTS` for the config, a tab built from a declarative spec -
a status line, a line to type into, a button that reports back through `on_widget`
and a list of what it has said, kept to however many lines the config allows - and a
page of the app's own Settings window that a switch on that page adds or drops,
whose button answers with the version, the profile's own counter and the config path
read back off the app. `start`, `shutdown`, `focus`, `on_config`, `on_connected`,
`on_event` and `on_message` are all in it, so the file says where each thing
belongs, and a comment under the `add_tab` call names the other shapes of the same
call - `pinned=True, icon_only=True`, `add_web_tab`, and a second tab the app puts to
this one's left - instead of leaving them to be found. It compiles as written and
does something visible the moment the addon is switched on, which is what makes it a
starting point rather than a sample.

The test script drives the addon through `addon_client.stub_api`, the recording
stand-in for `api`: nothing is opened on screen, nothing reaches a device, and
the checks are about what the addon *asked for* (`api.called("tabs.add_tab")`,
`api.asked("device.run", "command")`, the lines in `api.logs`). The template asks
the four questions worth asking of any addon and checks the answers - does `init`
build what the addon promises and give every control an `id`, does a value the user
set in the config reach the code that uses it, does each button do the thing it sits
there for, and does the addon go quiet once `shutdown` has taken the API back - and
`api.answer(...)` is how a test makes the app reply with something specific, so a
failed device command or a refused dialog is exercised with no device anywhere near
it. The Settings page and another addon's `on_message` are checked in the same
breath as the tab. It prints every call, counts the checks that passed and fails
with the list of the ones that did not, so it is also runnable on its own, or in
CI, without the app. The GitHub workflow takes that at its word: before every build
it runs every `test_*.py` in `addons/_tests`, and a failing test stops the build
instead of shipping inside an executable.

Compile starts PyInstaller for the one addon against a generated launcher
(`addon_client.serve(addon)`), staging everything inside `addons/_build/<id>` so
nothing of yours is touched, and puts the result where the runtime looks for it:
`addons/<id>.exe` on Windows, an extension-less executable with the exec bit on
Linux, or the whole folder of a `--onedir` build at `addons/<id>/` with its
program named `addon.bin`. `build_command` and `staged_name` take a `root=` for
an addon that builds things of its own, which is how the addon store stages its
work inside `addons/_data/addon_store/build` instead of in the dev tab's folder,
and clears it as soon as the program is installed. Every build is also given
`--paths` for the folder holding `addon_common.py` and `addon_client.py`, since
the launcher it compiles imports both - `build_command` takes a `paths=` for a
caller that keeps its own copy, which is how the addon store builds with the two
files it fetched when the program it runs inside has none on disk. The
command no longer passes `--clean`: that flag throws away PyInstaller's own
analysis cache, so every build would start cold and re-read every module the
last build already worked out - and nothing stale can ride in, because each
build stages inside the one folder named for it - the dev tab's
`addons/_build/<id>`, the store's same-shaped folder under its `root=` - and
the caller empties that folder before it starts. PyInstaller
is **not** needed to run the app - only to build an addon, and it has to be
installed in the interpreter the source runs from
(`python -m pip install pyinstaller`); the Compile pane says so, with the exact
command, instead of failing.

`addons/_build` and the programs a build leaves in `addons` are Git-ignored, the
same way the config and data folders are: they belong to one machine. The test
scripts in `addons/_tests` are not, because the tab only starts one off and what
you write into it afterwards is source.

A built program keeps the id of the file it came from, and only one of the two
can be *the* addon called `mytool`, so the choice is recorded per addon in
`config/_build.json` (`prefer`: `source` or `compiled`, with the date,
path and options of the last build). The radios at the top of the Compile pane
set it, the losing copy stays visible in the row's *alternatives*, and a rescan
starts the one you chose. Because a compiled addon imports only
`addon_common`, a build needs neither PyQt6 nor paramiko in it.

The Docs pane exists so that an author never has to leave the window to find out
what a call does, and never has to trust a document that was written once. The
reference is read off the running app: `addon_api` is walked at run time and every
namespace, every public call, its signature and the first line of its docstring
are taken from the module the program is actually using - so a signature in the
pane *is* the signature the call has, in this build, including a frozen one, where
there is no source to read and the walk goes over the compiled bytecode instead. A
call whose docstring says nothing is listed as saying nothing rather than having
words invented for it, which is why every public method of the API is expected to
carry a summary - and why a build is stopped when one does not
(`tools/check_api_docs.py`, reading the same namespaces as the pane). Beside the
reference sit the guides - the short prose version of this chapter's addon half -
and a `[text](api:tabs.add_tab)` or
`[text](guide:tabs)` link in any of them opens the page it names inside the pane,
and a build is stopped when one of those links names something the pane has not
(`tools/check_guide_links.py`), while a link to this README opens the local copy
when there is one and the published page when there is not. The whole thing is
generated from
`core/addon.py` and `addon_api.py`, so the day the API changes is the day the
documentation changes with it.

### The Berry addon store

`addons/addon_store.py` is one more addon inside the program. Its **Addon
store** tab is laid out like an app store rather than a spreadsheet: the
catalogs of one or more GitHub repositories - `index.json` in each store folder,
so the list of what can be had lives on GitHub and is never baked into a build -
are read and put side by side, and the tab shows the addons as cards on the left
with the whole story of whichever one is picked on the right. A card wears the
addon's picture, its name, its version and its author and a *new*, *update
available* or *installed* mark; the pane beside it writes out the description,
where the row came from - the source's `owner/repo`, the branch, tag or commit
it was read at, and the commit the catalog itself was made from - what its
manifest declares (the `ssh`/`sftp` transports it asks for, whose connection it
rides, which api version it speaks), whether it is installed here, and which
version was recorded beside that install. **Refresh** re-reads the catalogs and
marks the rows *new* and *updated*, and works out which installed addons the
store has since moved past, **Install** takes the chosen ones, **Update**
rebuilds the chosen ones that are behind over the top of what is there, and
**Delete** takes one back. The buttons work on the whole selection: ctrl-click
picks several rows and one job then works through them in order.
The tab wears its own mark - `assets/addon_storeicon.png`, the one name the rule
allows, then the SVG held in `TAB_ICON` when no file is there - sent through
`api.tabs.set_icon`, so a compiled copy keeps it too,
and it asks for `pinned=True, icon_only=True`: the store is somewhere a user goes
from the window's edge rather than a page they laid out, so it holds beside
Settings as its mark and never costs the rest of the row any width - and its
pinned tab says which addon it belongs to in its tooltip, which is what an
icon-only tab has left to speak with.

An addon's picture follows one rule, the same for a card and for the tab an
addon wears: the catalog's `icon` key when it names one, and `{id}icon.png`
otherwise, looked for in `assets/<id>/` of the source that published the row and
cached while the store is open, so a listing costs no more requests after the
first. Where nothing is there, the card wears `assets/storedefaultaddon.png`
from the program's own assets, and where even that is missing a drawn tile - so
a store never shows a blank box, and an addon that shipped no picture is still
found by eye.

An update is only offered when the catalog's version is ahead of the one
recorded beside the install in its manifest - `addons/<id>.json` next to a
loose program, `addons/<id>/<id>.json` inside a folder install; dotted
versions are compared as numbers so `1.10` beats `1.9`, and an install with no
recorded version is taken for behind whatever the catalog names. A row in that
state is marked *update available* in amber. Choosing **Update** asks first -
once for the whole batch when several are picked - and then runs the same steps
as an install: the addon is stopped and switched off before anything is
downloaded, so the running program is not holding the file the new one replaces,
and what its switch said beforehand is remembered; the program in `addons/` is
replaced as the new one is copied in, and an install the store made as a
one-file gives up its loose `.exe` to make room for the folder. The addon's
`addons/_data/<id>/` settings are never touched, and its switch in Settings >
Addons is put back exactly where it was once the new program lands - an addon
that was on comes back on with the rescan that ends the job, and one that was
off stays off. A fresh install has no prior switch to honour and still lands
switched off.

An installed addon's row wears a check box in the store's own list: ticking it
switches that addon on and unticking it stops it, the same record and the same
load-or-stop that Settings > Addons performs, so an addon can be woken up from
the store without leaving the tab. The box is drawn from the app's live answer
(`api.session.addon_state`), not from a file the store keeps, and moving one
while a job is working is put right by the job's own last repaint.

An install is one job - of one addon or of ten - and the tab says what it is
doing the whole way through:

1. the addon's `.py`, its assets under `assets/<id>` of that source's store
   folder and, when the source publishes one, its `test_<id>.py` are downloaded
   into `addons/_data/addon_store/staging`. A picture that will not arrive is
   said and left out rather than refused: assets are decoration, every addon can
   draw its own tab mark, and only the `.py` is needed to build anything;
2. the test is run against the downloaded file, with the modules it imports put
   on the search path. What the addon itself failed stops the install; a run
   that never got past importing Qt, or the app's own modules, is this machine's
   doing and is only said out loud;
3. PyInstaller builds a folder program for it - the `.exe` and its
   `_internal` support files - with the `addon_client.serve` launcher behind it
   and its assets packed inside. A folder rather than a one-file on purpose:
   a one-file carries its whole runtime in a single archive and unpacks all
   ~200 MB of it to a temp folder on *every* start - measured at twenty seconds
   on Windows, longer with Defender and several addons launching at once, which
   is what made an enabled addon look dead - and the second bootloader it
   spawns can outlive being killed. A folder boots straight off disk, compiles
   faster, and is one process to manage. The program is told to wear the
   addon's own picture, which on Windows means an
   `.ico`: PyInstaller converts a PNG only when Pillow happens to be installed,
   and it is in no requirement of this project, so the PNG is rewrapped here -
   its own bytes inside an icon directory when it is small enough to count, or
   drawn down to 256 px by Qt when it is not - and passed over with no icon at
   all rather than stopping the build. When
   PyInstaller is not installed the store puts it in with `pip` rather than
   asking you to;
4. the program lands in `addons/<id>/` (`<id>.exe` on Windows, `addon.bin` on
   Linux) and the downloads and the build are thrown away - an install writes
   the addon into the active profile as **switched off**, so nothing of it runs
   until you turn it on, and an update puts its switch back where it stood
   before the job stopped it;
5. Settings is rescanned and its row appears on the **Installed addons**
   page with a switch, its own SSH and SFTP switches, a config panel and -
   where its manifest declared `"connection": "both"` - an *own connection*
   option, like any other addon's, waiting to be turned on. The rescan reopens
   whichever tab was showing when it started, so working in the store does not
   throw the user somewhere else when it finishes.

The whole of a job runs without holding the tab still, and it does not lean on
the status line alone: every step also raises a small live card over the
window's corner - `api.ui.progress_notice` - whose bar carries the percentage
for the *whole* batch, a finished addon counting as one full share, so ten
installs creep by tenths of a tenth and the number never runs back. The card
fills to hundred on the job's last line, holds a few seconds and fades; a job
that ends bad closes it straight away and the status line says how far the
batch got before it stopped.

Step 4 also writes down what the addon's own source declared, which is what
makes that row a full one before the program has ever run: a `<id>.json`
manifest inside the program's folder - `name`, `version`, `author`,
`description`, `api`, the
`ssh`/`sftp` transports `api.device` is allowed to hand it, the `connection`
declaration saying whose connection it may use, and an `origin` mark
saying the store put it there - and its `fields.json` form, with a `config.json`
seeded from the addon's defaults, under `addons/_data/<id>`. Both are read
without importing or running any of the addon, since the source is parsed with
`ast`; both are what **Delete** takes back together with the program. What
Delete does with the addon's own `addons/_data/<id>` folder is a switch rather
than a surprise: `delete_config` in the store's own config, shown on its
Settings row as **Delete takes an addon's settings too**, starts **on** - an
addon that is gone should not leave a folder nobody owns - and a delete that is
about to take one says which folder, and names the two switches that would leave
it alone, before it does anything. Afterwards the status line says which of the
two happened, naming the folder it left behind when it left one. A reinstall
after a delete that kept the folder therefore comes home to the settings it had.

That manifest is also why an install is never filed among the bundled addons:
`core.const.SHIPPED_ADDON_IDS` names the only five that came inside the
program, and anything else is treated as the user's own.

Where the addons come from is a list rather than one repository, and **Sources...**
on the tab is what edits it. The store starts with this program's own repository -
`fligma/raspiOpenWrt`, `main`, the `addon.store` folder inside it - and any number
more can be added to it, each one an owner, a repository, a branch, tag or commit
and the store folder inside it. A source on the list can have any of those four
parts changed, can be moved to the front, and can be removed: **Remove** asks
first, says that nothing already installed is touched, and offers the program's own
repository back if it was the one dropped. Everything is written to the config as it
is accepted, and the catalog is re-read once, when the menu is done - a repository
added is a catalog fetched, and there is no sense in doing that for every keystroke
on the way. The list is kept in `addons/_data/addon_store/config.json` as a
`sources` array of records, which the one-value-per-line Settings form cannot hold:
that is why it gets its own button rather than a row of boxes. A config that still
names one repository the old way - the four flat keys, no `sources` - is read as the
single source it means, rather than losing it.

The catalogs are read in that order and put together, so the first source wins an id
that two of them publish and **Move first** is how one repository's versions take
over from another's, while the rest of the second source's catalog still shows what
its own repository has. A source that cannot be reached is reported on the status
line and skipped rather than taking the others down with it. Which source a row came
out of is kept with the entry, and is what the addon's file, its assets, its test and
the modules it imports are fetched from, so an addon arrives with exactly what sat
beside the catalog row naming it. The header chip at the top of the tab says where
the list points: one source named in full, several named by the first and counted,
and the token - shared by every source, for a private repository or when anonymous
requests are refused - stays editable in that same config file. The same header
holds the answer about settings for this session only: a **delete settings**
check box beside the chip, drawn from `delete_config` and put back under it
whenever the config is saved from Settings, so a page edit always wins and a
change made here is gone when the tab is rebuilt.

A catalog entry needs nothing but the `.py` and its manifest - a tab, a button
and a test script beside it is the smallest thing that can be listed there, and
**New addon...** in the Addon Dev tab writes exactly that shape.

### Store addon: Certificate manager

`addon.store/cert_manager.py` is a complete addon rather than a snippet, and it
is the one that needs the config file above. It is not inside the program: it is
installed from the addon store, which compiles it and leaves its settings form
filled in before it is ever started. It adds a **Certificates** tab and a
Tools menu entry, and works on the certificate pair the device's web interface
serves - `/etc/ssl/mycert.crt` and `/etc/ssl/mycert.key` by default, both names
and the folder coming from `addons/_data/cert_manager/config.json`. Its tab is
asked for as `pinned=True, icon_only=True`, the way the store's and the manager
updater's are, so the trophy sits beside Settings rather than take width from
the row the user arranged - one api call, no special case in the app, and the
name still in the tooltip.

| Button | What it does |
| --- | --- |
| **View current** | One SSH script: `ls -l` on both files, `-subject -issuer -dates -serial`, the SHA-256 fingerprint, `-checkend 0` to say whether it has expired, a check that the profile's own address is covered, then `-noout -text` in the viewer. The status line shows how many days are left. |
| **Reissue certificate** | Checks the subject locally first, asks, then in one script: back up the current pair with a timestamp, write an openssl `.cnf` in a heredoc, run `openssl req -x509 -nodes` for the configured subject, alternative names, key type and lifetime, install the new `.crt` and `.key` with modes 644 and 600, run the follow-up commands, and read the result back. |
| **Save to config** | Keeps what is in the form in `addons/_data/cert_manager/config.json` |
| **Reload from config** | Puts the saved values back into the form |
| **Save a copy locally** | Downloads the certificate PEM into `addons/_data/cert_manager/`, for importing into the Windows trust store (see [HTTPS in the embedded tabs](#https-in-the-embedded-tabs)) |

The form holds the folder and both file names, the `openssl` command, the six
subject fields, the DNS and IP alternative names (optionally plus the address
from the connection panel), the key type with its curve or size, the lifetime,
whether to reuse the existing key, the `CA` flag, the key usages, the commands
to run afterwards and where backups go. Everything is per installation, so
another device or another naming scheme is a config edit, not a code change -
and because every action is one non-interactive shell script run over SSH, an
OpenWrt device with busybox `sh` and `openssl` needs nothing installed.

Nothing on the device is touched without the profile you are connected to, and
the private key stays there: the addon never downloads it. The example
configuration matches a self-signed dashboard certificate - `CA:TRUE`,
`digitalSignature, nonRepudiation, keyEncipherment`, `serverAuth`, a DNS name
and the LAN address in `subjectAltName`, a 256-bit EC key and a year of
validity - which is the shape the embedded tabs expect.

Its tab is a spec and its five buttons arrive as `on_widget`, so nothing here
changes when the file is compiled: **View current** issues the same SSH script
either way, and the callback that fills the viewer in comes back through the
pipe. Its mark follows the rule like every other addon's: the picture at
`addon.store/assets/cert_manager/cert_managericon.png` is worn when the file is
there, and otherwise the trophy is an SVG document held as text in `TAB_ICON`
and sent with `api.tabs.set_icon`, so a compiled copy still gets its mark - the
text crosses the pipe unchanged, with no file beside it to lose and no Qt in it
to draw with.

Two parts of the subject are not negotiable, because openssl says so: the
`Country` holds exactly two letters (`GB`, `NO`, `DE`) or nothing at all, and
there has to be a name - a blank `Common name` takes the address from the
connection panel. **Reissue** checks both before it goes near SSH, since the
device only notices after it has spent seconds making a key, and answers with
`Error making certificate request` and an asn1 complaint about a string being
too long. Complaints the check cannot see are still read out of the device's
answer - a missing `openssl`, a key usage openssl does not know, a folder that
is not there - and put in the status line instead of only in the output.

The words an early version kept in the subject *as its defaults* - `Country`,
`State`, `City`, `Orginization`, `Org Unit`, `Hostname.lan` - were hints that got
saved as values, which is how a config file nobody had filled in came to ask
openssl for a seven-letter country. Such a value now reads as the blank it meant
to be, the form starts empty with an example in each box, and saving it back
fixes the file. Anything else someone typed is left exactly as it is.

`addon.store/_tests/test_cert_manager.py` covers the text that reaches openssl,
the
check that refuses a doomed request, the script for each kind of key, both
failure routes, the tab as Qt builds it and the icon it sends, and **Run test**
in the Addon Dev tab runs it.

### Store addon: Berry web dashboard

`addon.store/berry_web.py` adds a **Berry web** tab and a Tools menu entry, and
is what puts the dashboard the device serves - the tree under `src/www_rasconf`
in this project's repository - onto a Pi. It is installed from the store rather
than shipped, and it is the successor of the addon that used to sit in
`addons/web_installer.py`: the config keys are named the same, and an install
that had that one carries its settings across by itself (see
[What an upgrade clears](#what-an-upgrade-clears)).

It looks before it acts. One SSH command asks the connected host whether the
served folder is there and how many files it holds, what `uci` says
`uhttpd.berry.home` is, whether `python3` is on the path, and what comes back
when the host is asked for its own page on the configured port. A page that
answers as the dashboard lands the tab on **Status**; a missing folder, a port
that says nothing or a page that belongs to something else lands it on the
**Installer**, where the work is.

| Button | What it does |
| --- | --- |
| **Re-check** | That one command again, and the tab rearranged around the answer |
| **Check page** | `curl`, or busybox `wget`, against `127.0.0.1` and then the address in the connection panel, and says whether the dashboard answered |
| **Open dashboard** | The page in an embedded tab, following the profile's host |
| **Fetch files** | Asks GitHub what is in that folder at that branch or tag, downloads it into `addons/_data/berry_web/staging/`, asks the device what it already has, and fills the plan table. Writes nothing. |
| **Install** | The same, and then, after one confirmation that lists what will happen: back the folder up on the device, write the files, tidy away the leftovers if the config asks, write `css/theme.css` from the theme the app is wearing, set the uhttpd section up with `uci`, `chmod 755` the CGI entry, run the restart command, and ask the device for its own page |
| **Read** / **Refresh** | `uci show uhttpd.<section>` and `/etc/config/uhttpd` into the **Raw file** page, read-only: the file is shown here, never written from here |
| **Apply** | The options on the **Options** page, with `uci set` and one `uci commit`, then the read-back and the restart |
| **Move folder to this name** | Renames the folder the server is using to the name in the form, points `uhttpd.<section>.home` at it, commits and restarts - `/www_rasconf` to `/www_berry` in one action |
| **Restart web server** | The configured restart command and nothing else |
| **Save to config** | Keeps what is in the forms in `addons/_data/berry_web/config.json` |
| **Reload from config** | Puts the saved values back into the forms |
| **Stop** | Gives up on the job in hand; what has already gone across stays on the device |

The tab gives the whole width to what is happening: a header line carries where
the files come from, which folder they land in and a chip saying whether this
addon may reach the device over SFTP; a step flow across the top -
`○ Files → ○ Compare → ○ Backup → ○ Write → ○ Serve → ○ Restart → ○ Check` -
lights the step running now, ticks the ones the route walked and dims the ones
this job never takes; and three badges count the plan up (*N to write*,
*N already there*, *N extra on device*) above a notebook of three pages -
**Status**, **Installer** (its own notebook of the plan table, the log and the
install form) and **uhttpd** (the options form and the raw file). The plan table
is one row per file with its size, what the device has of it and what this job
means to do - `new`, `already there`, `replaced`, `written again` - the row
tinted by that action, so an update is read before it is run, and a look-around
costs one API call and a download.

- A file whose size matches what is there is left alone (**Leave a file alone
  when its size matches**), which is what makes a second install take a second
  or two; untick it to write every file again whatever the device has.
- `cgi-bin/hash.file` - the PIN the dashboard asks for - is on the keep list. It
  is never uploaded and never tidied away, because it is not in the repository
  and belongs to the device.
- The file list comes from GitHub's Trees API and the bytes from
  `raw.githubusercontent.com`, over `http.client` with one connection reused per
  host, so nothing that is not already bundled is needed. Anonymous use of that
  API is capped at sixty requests an hour, so a token can be set for a private
  repository, and when the list is refused the addon falls back to the
  dashboard's own layout and says out loud that a file added since would be
  missed there. A branch or tag that is not there is caught on the first file
  and stopped, rather than asked for twenty-one more times.
- Everything is config: the repository, the ref, the folder inside it, what is
  never sent, what is never deleted, the served folder's own name
  (`web_home`, where the retired addon had only the profile's root plus a
  subfolder), the uhttpd section, the port and path to check afterwards, the
  backup folder, the entry file and the restart commands. Setting **Files come
  from** to **Local folder** installs from a folder on this machine - the
  profile's own source by default - and then needs no internet at all.
- The words that reach the device are built from config, so each one is checked
  on the way: a served folder is refused unless it is a plain absolute path, a
  section name unless it is a bare word, and every value goes into `uci` inside
  its own quotes. A move asks by name first and its script refuses outright when
  the new name is already taken, because a `mv` onto an existing folder would
  bury the dashboard inside it rather than replace it.
- The job runs one file per `api.schedule.after` tick, so the bar and the table
  move the whole way through. Each stage of the route - finding the files,
  downloading, asking the device, backing up, writing, tidying away, painting
  the page, setting the server up, restarting, checking - owns a weighted slice
  of the bar and writes its word inside it, so the bar says what is happening and
  keeps moving while the device thinks, and ends full only when the route really
  is over. The one-command buttons use the bar the same way.
- The page's colours come along with it: `css/theme.css` is written from
  `api.theme.colors()` - the app's tokens onto the custom properties the
  dashboard's own stylesheet declares - and uploaded with the rest, so the page
  in the browser is the same colours as the window that installed it. The
  committed copy under `src/www_rasconf/css/` carries the shipped default theme,
  so a folder copied across by hand still looks right.
- The tab is a spec and its thirteen buttons report through `on_widget`. The
  manifest says `connection: "both"`, so a person may put it on a connection of
  its own, which is also what lets a long transfer hold one session open instead
  of opening one per file; on the app's session the same writing code sends each
  file with `device.upload` instead, and paths above the profile's root -
  `/www_berry` usually is one - go with `outside_root=True`. Its mark follows the
  rule like every other addon's: `addon.store/assets/berry_web/berry_webicon.png`
  when the file is there, and otherwise the browser-window-and-drupelets drawing
  held as text in `TAB_ICON`.

`addon.store/_tests/test_berry_web.py` drives the whole route without a device -
the tab built by Qt (its three pages, the nested uhttpd notebook, the tables and
the step flow), a canned answer from a host read back five different ways, which
page each answer lands the tab on, the `uci` script and every refusal in front of
it, the move, the settings carried across from the retired addon, the stylesheet
made from a fixed set of colours, and the bar watched stage by stage. Both routes
the files come by are checked too: this checkout walked as the dashboard, and a
repository list cut down to what is installed, with the built-in list standing in
when GitHub refuses - the one half that asks the real repository running only when
this machine names a token for it.
**Run test** in the Addon Dev tab runs it.

### Shipped addon: Berry file manager

`addons/berry.py` adds one tab - **Berry** - whose file view holds two pages,
**Device** (this computer's current profile SSH/SFTP connection) and **Local**
(this computer), plus a Tools menu entry. The device page turns the profile's
own SSH/SFTP connection into a real file manager; the local page does the same
for your machine, and the two hand files to each other from within the one tab.
With the old built-in SFTP tab gone, Berry is the app's file manager; and its
device page is not confined to the remote folder: every path is resolved with
`outside_root=True`, so you can walk the whole file system - `/etc`, `/tmp`,
`/www`, `/overlay` - from one table.

Each page lists one row per entry - on the device **Name, Size, Type, Modified,
Mode and full path**, locally the same without the Mode column - and behaves like
a file manager rather than a dump:

| Control | What it does |
| --- | --- |
| Address bar + **Go** | Type any folder path and press Enter to list it |
| **Up** / **Root** (device) / **Home** | The folder above, `/`, or the folder the pane opens by default |
| **Places** | A bookmark list to jump straight to; the folders in it come from the config |
| **Refresh** | List the current folder again |
| **Filter** | Hide rows whose name lacks the text - on this machine, with nothing fetched |
| **Sort** | By name, size, modified time or type, ascending or descending; **folders always stay on top** |
| **show hidden** | Include names that begin with a dot |
| Double-click | Walk into a folder, or open a file in the notepad editor tab |

Actions live in a slim bar **above** the listing and, for most of them, in the
**right-click menu** on a row: open, edit, rename / move, delete (a folder goes
with its contents, after a confirmation), copy, paste, info, show path, new
folder and new file - plus chmod, download, disk and find on the device, and
upload to the device on the local page. The **Actions ▾** button opens the same
menu for the selected row, so nothing depends on a mouse gesture. What the menu
offers follows the row: a folder does not get Edit or Download, a local file does
not get Chmod, and an empty area only gets the folder-level items.

Editing a file opens a **notepad-like tab of its own** (`Berry Editor`), not a
popup: double-click a file (or pick Edit) and the whole tab shows the text in a
big area shaded for its language. The text lives **only in memory** - the file is
touched by nothing until **Save**, which writes it back (over SFTP on the device,
to disk locally), re-lists so the size and time update, and closes the tab;
**Close** discards (asking first if there are unsaved edits) and **Reload**
re-reads. A **language** box re-shades the open text on the fly, and it starts on
`auto`, which follows the file's extension. Syntax colors are built in for
**json, python, html, css, javascript and ini** and are drawn by the app, so a
compiled copy of this addon - which has no Qt of its own - still gets them from a
plain `"highlight": "python"` in its spec. The `info`, `df`, `stat` and `find`
results stay read-only popups, so the file view is not cluttered by them.

Copy and paste cross the two pages. Copy on one and paste on the other
transfers the bytes - a local file pasted onto the device is uploaded, a device
file pasted into the local page is downloaded - while pasting on the page it
came from copies it in place (`cp -a` on the device, a real file copy here). The
local **Upload to device** button sends its selected file straight into the
folder the device page is showing.

- The whole listing is fetched once and then filtered, sorted and hidden on this
  machine, so typing in the Filter box and toggling show-hidden are instant and
  cost no round trip; **Refresh** is the only thing that asks the device again.
- Local paths for the device page's **Upload** and **Download** are typed rather
  than picked from a dialog, because a dialog is a Qt widget and a compiled copy
  of this addon has none - the same file runs imported and as its own program.
  The local page needs no such typing: it reads the real folders beside the app.
  Downloads default to the addon's own data folder, or to `download_dir`.
- `find`, `chmod`, `df` and `info` are built with `shlex.quote`, so a name with a
  space in it survives the trip to the shell; a mode that is not three or four
  octal digits is refused before anything runs.
- Everything worth changing lives in `addons/_data/berry/config.json`, shown as a form
  on this addon's own Settings page, under its name: the starting
  folder on each side,
  the bookmarked places, the show-hidden and sort defaults, whether deletes ask
  first, the size at which the editor warns, where downloads land, and the two
  `find` limits.

The tab's file view and the editor are both specs, and every control - the
buttons, the address bar, filter, sort, show-hidden, Places, the listings on both
pages (their selection, double-click and right-click) and the editor's body,
language box and buttons - reports back through `on_widget`, so nothing here
changes when the file is compiled into a program of its own: the blocking `device`
calls still run in the app's thread pool while a private event loop keeps the
window painting, with every button greyed for the length of one operation. Because
both pages share one tab, their control ids are prefixed (`d` for the device page,
`l` for the local page) and the side is read back from the id, so a single
`on_widget` routes every interaction to the right page. The right-click reaches the
addon as a `context` event carrying the row under the cursor, and the menu is
shown with `api.ui.choose`; the editor's colors are set through the same route, as
a `highlight` property. Their marks (drupelets, and a page and pencil for the
editor) are SVG documents held as text, so a compiled copy still wears them -
except that the Berry tab looks for `assets/berryicon.png` first and wears that
picture whenever the program ships one (`tab_icon()` answers either the path or
the drawing, and `api.tabs.set_icon` reads both).
`addons/_tests/test_berry.py` drives both pages against a stubbed device and
dialogs and a real temporary folder - the sort keeping folders on top, the filter
and show-hidden, the posix and native path arithmetic behind every action, the
recursion and `outside_root` on a delete, the quoting in `chmod` and `find`, the
menu contents per row kind, the copy/paste that uploads one way and downloads the
other, the editor that keeps edits in memory until Save and discards on Close, and
the tab and editor as Qt builds them (the two-page frame, the headers, the
right-click policy, and the live syntax highlighter on the editor) - and **Run
test** in the Addon Dev tab runs it.

### Shipped addon: Theme Manager

`addons/theme_manager.py` is the program's own editor for the looks it is drawn
from, and the reason `.theme` files are worth dropping into the folder. It adds one
pinned tab, **Theme Manager**, with three sub-tabs of its own - the app owns the
outer bar and its shape, this addon owns the page.

**Themes** is the folder as a table: every name on disk with its author and its
blurb, starred when the window is drawn from it, and six buttons under it - Apply,
Edit it, Duplicate, Rename, Delete, and the folder itself. Delete asks first (a
config switch), and `default` refuses either way. A right-click on a row opens the
same commands, and works on the row that was clicked rather than the row the table
was still pointing at.

**Edit** works on one *draft*: the theme held complete in memory, with the name of
the file it would be written to. Colours are picked from a tree grouped by what they
are used for and changed by typing a hex value or by the app's colour wheel; the tab
knobs are picked from a tree of pixels and switches, each answered by the box that
fits it; and the JSON of the draft can be typed into directly, then read back with
**Use the JSON**. Every change is checked by `api.theme.validate` before the draft
keeps it, in the app's own words - `colors.accent is not a colour: '#zz'`,
`tabs.icon_px is out between 0 and 200 px` - and the box mirrors the draft, so what
is on show is what would be written. Nothing reaches the disk until **Save**, and
nothing reaches the window until **Apply**; a switch under the editor's name makes
saving the theme already in force redraw as well. **Re-read from disk** throws the
draft away, and a name that has no file behind it cannot be opened at all.

**New from template** takes the name typed above it and one of the four shipped
starting looks - the dark `default`, the light one, `deep sea` and `ember` - writes
the file, and opens it in the editor to be changed. A new draft is never allowed to
be named over a file that exists.

**Docs** is laid out like the Addon Dev panel's: a tree of every `api.theme` call,
under headings that count their own lines, beside the page each one opens. The
calls, their signatures and their descriptions are read off the running API with
`api.theme.reference()`, so the page cannot disagree with the program it describes,
and only the six guides beside it are written down in the addon.

The addon never touches the disk or the window itself - everything goes through
`api.theme`, `api.tabs` and `api.ui`, so the same file works imported into the app
and run as its own process, and `addons/_tests/test_theme_manager.py` can drive the
whole route - make, colour, knob, refuse, save, apply, duplicate, rename, delete -
against a folder of its own.

### Shipped addon: Berry Manager update

`addons/manager_update.py` adds a **Manager update** tab and a Tools menu
entry, and keeps the program itself up to date. It reads this repository's
tags off GitHub's API, takes the ones whose names are the prefix `berry` plus
a version - `berry1.6.0` is version 1.6.0, anything else the tags name is
ignored - and compares the newest against the version this copy carries. That
version has exactly one source now, `core.const.APP_VERSION`: the About
dialog shows it, `api.session.version()` answers with it, and the update
check compares against it, so one line changes per release. A repository with
no `berry` tag yet - the state before the first release is ever published -
is said plainly as *nothing to compare with* and is not an error.

**How a release gets published.** The workflow publishes it. A push that
rebuilt the program ends in a `release` job that reads `APP_VERSION`, makes the
tag `berry` + that version when it is not there, publishes it as a **release**,
and attaches `berry_manager-windows.zip` and `berry_manager-linux.zip` - each
the program with the drop-in `addons` folder beside it, the same shape the
artifact has. An uploaded artifact comes back without the executable bit, so
the job sets that mode on the Linux program before packing it: a release someone
installs by hand starts with `./berry_manager`, and the in-app update does not
depend on either, since its swap sets the mode itself. Only what reached `main`
is published - a manual run started from another branch builds its programs and
puts up no release. Shipping a version is therefore one edit: change `APP_VERSION`
and push. The two names are what tells the builds apart, which is exactly the
rule the addon reads (a name saying `windows` or `.exe`, or one saying `linux`),
and a re-run at the same version replaces that release's files instead of
putting up a second pair to choose from.

That automation exists because a GitHub tag points at source only and carries
no files, so the bytes have to ride on a release under the tag; and the
workflow's artifacts can only be downloaded by a signed-in browser - an
artifact link handed to the addon answers with a sign-in page, which the
download recognises and refuses by name, explaining the fix. Publishing by hand
still works on the same terms: tag `berry` plus the version in `APP_VERSION`,
*Create a new release* rather than just the tag, attach one file per system
named for its system - a zip
holding `berry_manager.exe` / `berry_manager` is unpacked happily whatever
folder it sits in, and on Linux the swap puts the executable bit back - and a
hand-made tag that never got a release is picked up by the next build. Nothing
has to be typed into the description, but it is a fallback: when a release
carries no asset for this system, the addon looks for a plain `https` link in
the description that serves the bytes with no sign-in, and prefers one whose
address names this system. And when a tag is ahead but offers no download at
all, the tab says so and spells these options out rather than letting the user
find a broken button.

**Download and install** then works in one careful line, paced a megabyte a
tick through `api.schedule.after` so the window keeps breathing while the
program comes down: redirects followed with the download's one-time signature
kept, the bytes checked they really are a program for this system (`MZ` or
ELF, or unpacked from a zip), staged in `addons/_data/manager_update/`, and
only then swapped into the running file's place. On Windows the running
program cannot be deleted, only renamed, so it is parked as
`berry_manager.old`, the new file takes its name, and the leftover is deleted
- or, if Windows still holds it, by the fresh copy the moment it starts
(`clear_replaced_program()` in `berry_manager.py` runs before anything else).
On Linux the new file simply replaces the old in one step. The swap moves
that one file and nothing else: the `config` folder, the `addons` folder and
every addon's settings under `addons/_data` are exactly where they were,
which is what makes an update of the program safe for a machine that has
installed addons of its own. Then the addon offers to restart through the
app's own relaunch, and a refusal leaves this copy running until the user
starts the new one themselves. A copy running from a source checkout is
turned away with the reason - there is no single file of it to replace.

The tab is a spec, and it is two panes over the job's log: the published
versions newest first on the left, each row saying where it stands against this
copy - *an update*, *this copy*, *a way back*, or *older than the floor*, in a
colour of its own - and on the right the story of whichever one is picked: the
release's own title, who published it and when, the seven letters of the commit
it was made from, the words it was published with (the commit's message where
the release wrote nothing), and the files it moved against the version before
it, each line tinted by whether it was added, changed or removed. Two requests
read the whole list whatever its length; a commit and a comparison belong to one
version and are asked for only when its row is picked, then kept for the rest of
the run, so clicking back and forth costs nothing. The header keeps the status
line and an `owner/repo @ prefix*` chip saying where the tags come from, and
below the panes **Check now**, **Install the picked version** and **Open
releases page** (the repository's releases in an embedded tab) report through
`on_widget`. It wears `assets/manager_updateicon.png` - the one name every
shipped addon's picture is looked for under - falling back on the drawn download
mark held in `TAB_ICON` when the file is missing, and it is pinned and
icon-only like the store's, holding beside Settings instead of taking a place in
the row of tabs.

**Going back is offered as readily as going up**: the same one careful swap,
one file for another, the config and the addons untouched - down to
`min_version`, which starts at `1.6.0`. A program older than that predates how
this addon stages a download and moves it into place, so a move that far back
is refused before anything is fetched, and the refusal names the line so it can
be moved. Going back asks a plainer question than going up does, and once the
file has landed the list is re-placed against the version that is running now,
without reading GitHub a second time.

**When it checks is config**: `check_startup` reads the tags once, a moment
after the window is up, and `check_again` runs that read again on `check_minutes`
for a program left open for days. A version found by a check that ran on its own
goes up through the app's own notifications - `api.ui.popup`, once
per version while this copy runs, saying which version is out and what this copy
is - so it does not have to be looked for on a tab nobody opened; `notify_found`
turn that off and the tab's status line is the only place it is said. The
repository, tag prefix, token, the floor and the check settings live in
`addons/_data/manager_update/config.json`, so pointing the update at a fork is a
config edit, and a change to them re-points, re-times and re-checks without a
restart. Its own published test, `addons/_tests/test_manager_update.py`, drives
every state of the check, the version list and its pane, the notices, the
schedule, the asset and link picking, the byte sniffing, the swap and the whole
job machine without a network, and the build runs it like every other addon's.

### Store addon: OpenWrt Log Viewer

`addon.store/openwrt_logs.py` adds one tab - **OpenWrt Logs** - with two pages:
**Single device** reads the log of the profile's own host, and **Multi
device** queries several hosts side by side, each device's rows wearing the
colour that device was given (the translucent wash is painted by the app, and
the Colour field in the form is a hex box whose swatch opens the same colour
wheel as `api.ui.pick_color`). Every line arrives over SSH with one command
per fetch: the log command, the row cap (50 to 50 000) and the timeout are
config, and auto-refresh pulls the log again on a timer. Search works two
ways - highlight the matches, or filter the list down to them - with case
sensitivity and regular expressions as switches. Like the other addons the tab
is a spec and every control reports through `on_widget`, so the program the
store compiles runs the same code;
`addon.store/_tests/test_openwrt_logs.py` drives it through the stub, including
the picture the tab asks to wear. The multi page is the one place in the
store's addons that asks for several devices **at once**, with
`run(command, block=False)`, and then polls each job - the only api call whose
answer is a job still running rather than a value, so the build has to be
compiled against a runtime that carries such a job over the pipe. Version 2.8 is
the first of those: a 2.7 program in an app that cannot carry the job reads no
logs in the multi view, and the store's **Install** will not overwrite an addon
that is already there, so it takes **Delete** and then **Install** to move over.

### Store addon: OpenWrt Package Manager

`addon.store/openwrt_package_manager.py` adds a **Package Manager** tab of two
pages: **Installed Packages** and **Install Software**. One config choice -
`package_manager`, `apk` or `opkg` - decides every command the addon runs, so a
modern OpenWrt release and a legacy opkg one are the same tab, and the same is
true of `apk update`/`opkg update`, `apk search`/`opkg list`, `apk add`/`opkg
install` and `apk del`/`opkg remove`. Both pages are tables of three columns,
and the third one is the reason 1.3 exists: **a description of each package,
read off the device**. Neither backend puts one in the list of what is running -
apk keeps the words in its repository index and opkg in its feed list - so after
the installed names are in, the addon asks for the whole dump
(`apk query --fields name,version,description --format json`, or `opkg list`) and
folds the sentences into both tables, saying how many arrived on the status
line. A package the index has nothing about is still listed with its name and
version; a guess at which description belongs to which name is worse than none.
The cut of one `apk info -v` line into `name` and `version` is the same care in
miniature: it splits at the first dash with a digit behind it, since a name
cannot start a segment with one and a version always does.

Its picture moved to the rule's own name in 1.3, to
`addon.store/assets/openwrt_package_manager/openwrt_package_managericon.png`,
so the store's card and the
tab's mark come from the one file. The catalog's `icon` key names that same
file, and `addon_common.find_addon_picture` reaches it in either folder, so the
picture is the same whether the addon is being written, built by the store or
shipped inside the program. It rides the
app's session (`"connection": "app"`, `"requires": ["ssh", "sftp"]`), and
`addon.store/_tests/test_openwrt_package_manager.py` covers the parsing of both
backends' answers and the rows each table is given.

### Store addon: Multi SSH

`addon.store/multi_ssh.py` is the store's first addon that says
`"connection": "own"` and means it shell by shell: one tab holds a row of
sub-tabs and every sub-tab is a session on a socket and a thread of its own, to
the same machine twice if asked. Nothing in it is a view of the connection the
user opened in the connection panel - pressing Connect there changes nothing
here, and nothing here moves it.

The tab is three things: a row at the top that types one command into every
session that is up, a **Quickstart** group on the left, and the sessions
themselves on the right, in a `tabs` container that starts with a
*No session* page standing in for nothing being open.

| | |
| --- | --- |
| **Connect** | Opens the selected quickstart entry: asks for the password and nothing else, adds a sub-page and brings the shell up on it |
| **New** | A session to any address, once - asked for as one line (`user@host:port` is read as it is written) and never saved |
| **Add / Edit / Remove** | Keeps the quickstart list: name, address, port, user, and a key file instead of a password if one is named |
| **Icon** | Gives an entry a picture of its own |
| **Reload from config** | Reads the file again, picking up entries edited outside the app |
| **Broadcast** | Types what is in the top field into each session that is up, Enter does the same |

The entries live in `addons/_data/multi_ssh/config.json` under `quickstart`, as
the list of objects they are, and are named in no `FIELDS` entry - which is what
lets them be a list at all: the Settings form starts from the file, so a key it
does not show is written back untouched, and the addon is free to add and drop
rows without a form being rebuilt around them. The same is true of an entry's
picture, which is *copied* into `addons/_data/multi_ssh/icons` from wherever
`api.ui.pick_file` found it and named after the entry, so the row keeps its icon
whatever happens to the file it came from, and a data folder moved to another
machine takes its icons with it.

A password is asked for every single time, with `api.ui.input(...,
secret=True)`, and is kept nowhere: not in `config.json`, not in the settings
dict, not in the state the addon can read back off the shell - because this
config file is plain JSON that people share in bug reports and copy onto other
machines, and the app's own credential store is the app's, not an addon's. What
the screen of a session shows is what its keys typed there go to.

Each sub-page is a line of its own - who it is connected to, how it is doing,
and **Connect** and **Disconnect** - over a `terminal` leaf. The shell reports
`ready`, `failed` and `closed` back to the addon, and what `closed` means is one
switch: **Close the tab when the link drops**, on, takes the page away with the
connection and puts the *No session* page back when it was the last one; off,
the page stays exactly where it was with its scrollback intact, the state line
reading `disconnected` and **Connect** asking for the password again. That is
the way to keep the output of a machine that drops, and a **Disconnect** press
reports `closed` like any other, so the one switch decides for both ways a link
goes. A broadcast to a session whose shell has
already gone is not pretended to work: the leaf refuses the write, the loop
counts it, marks that session down and says how many could not be reached.

The rest of the config is what a new session is offered - a user, a port, and
whether a host key nobody has seen is accepted or refused - and its picture is
found by the rule like every other addon's, at
`addon.store/assets/multi_ssh/multi_sshicon.png`.
`addon.store/_tests/test_multi_ssh.py` runs it against a fake row of sub-pages
that honours `add_page`, `remove_page` and `pages`, so the checks are about the
tabs the addon really opens and shuts, and that no value it saved anywhere holds
the password it was handed.

### Addons inside the built executable

The build packs the addons it finds in `addons` **inside the program**, and the
app reads that copy as a set of *shipped* addons:

- The workflow runs the addon tests first, then stages every addon it can
  find - a loose `.py`, a compiled program (`.exe` or `.bin`, with its
  same-named `.json` manifest beside it), and a folder holding `addon.py`,
  `main.py` or a compiled `addon.exe` / `addon.bin` / `<folder>.exe` - into
  `_addon_bundle`, minus any `__pycache__` or tests folder inside one, and
  hands it to PyInstaller as `--add-data="_addon_bundle:addons"`,
  so the files arrive in the archive that is unpacked when the program starts.
  `core.const.BUILTIN_ADDONS_DIR` is where it looks, and
  `HAS_BUILTIN_ADDONS` says whether a build has such a folder at all - in a
  source checkout the two folders are the same one directory.
- A shipped addon is an addon in every other respect: same manifest, same `api`,
  same events, same config file - with one flag set, `builtin`, that says the
  file belongs to the build. Nothing writes to it: the Details form is read-only,
  **Save** is refused, and there is no **Restart** in its Settings row.
- It is switched on and off from a Settings page of its own, beside the
  **Connection** page - and the
  choice stays per profile in `addons -> <id> -> enabled`. Its own settings are
  still written beside the `.exe`, in the writable `addons/_data/<id>/config.json`, so
  a shipped addon is configured per installation exactly like a dropped-in one.
- "Shipped" is decided by a stable list, `core.const.SHIPPED_ADDON_IDS`
  (`berry`, `addon_store`, `manager_update`, `theme_manager`),
  on top of the
  `builtin` flag, so a
  source checkout - where `HAS_BUILTIN_ADDONS` is off - still gives those addons
  a Settings page of their own rather than a row on the **Installed addons**
  page. The certificate, log, package and multi-ssh addons are not in that list:
  they live in `addon.store` and
  are installed from the store, which marks what it builds with an `origin` of
  its own so the app puts it on the **Installed addons** page instead. The web
  installer used to be in it; it is the store's **Berry web dashboard** now, and
  is named in `RETIRED_SHIPPED_ADDON_IDS` for that reason.
- Two addons of one id cannot both be *the* addon called `mytool`, and a file in
  the folder beats the copy inside the program - it is the one the user can read
  and change. The shipped copy is kept as the row's alternative, and the Addon
  Dev tab marks such a row `shipped`.

The rest of the frozen path model is unchanged:

- `addons` itself resolves to a folder **beside the `.exe`** (`BASE_DIR`), not
  inside the archive, so it is writable: drop files in it and press **Rescan**.
  The folder in a downloaded build - artifact or release archive - is empty
  apart from a note saying so.
- `from addon_api import ...` in an addon reaches the bundled copy of the
  module, because PyInstaller's frozen importer answers before the addon
  folder that the runtime puts on `sys.path`. The addon folder stays on the
  path first for its own sibling modules, which is how `mytool/helpers.py` is
  imported by `mytool/addon.py`. An addon that only needs the plain parts
  (`AddonError`, `jsonable`, the config helpers) should import `addon_common`
  instead: it is the same contract with nothing Qt or paramiko behind it, and
  it is what a compiled addon can build against.
- What an addon may import is what the bundle collected: the standard library,
  PyQt6 (`QtWidgets`/`QtGui`/`QtCore`, plus `QtWebEngineWidgets` when it is
  installed), `paramiko` and `keyring`. Anything else - `requests`,
  `serial`, a vendor SDK - is not in the `.exe`, so such an addon has to be a
  separate program in the folder rather than a `.py` file.
- A folder addon's own data files sit beside the `.exe` too, and
  `api.paths.data` is always a real writable directory.

`config` and `addons/_data` are created beside the `.exe` on the first
run, which is where the certificate addon's file ends up - also for an addon
that came inside the program, since its settings are not the build's to keep.

### What an upgrade clears

An addon that stops shipping does not stop existing. Its file is gone from the
build, but everything the *user's* side of the program wrote about it stays:

- a copy in the addons folder - a loose `.py`, a program the Addon Dev tab
  compiled (`.exe` or `.bin`), and the `<id>.json` manifest that came with it.
  Discovery reads that folder whatever is in it, so such a copy is not inert: it
  comes back as a drop-in addon row wearing a retired addon's code, which is the
  one place a file nobody ships can still be run;
- `addons/_data/<id>/` - the config the addon was given, its staging folder,
  whatever else it kept beside it;
- a record in every profile under `addons -> <id>`, saying whether it was
  switched on, whether it ran on a connection of its own, which transports it
  may use, and its own settings;
- and, for the web installer, a `remote_root` naming the folder it installed
  into.

A new version that simply drops the addon would leave all four behind, and the
user would meet a stale row in Settings and a folder nothing reads. So a retired
addon is named rather than forgotten. `core.const.RETIRED_SHIPPED_ADDON_IDS`
lists the ids that used to ship, and `ADDON_ID_TRANSLATIONS` says which addon
replaces each one - `web_installer` is answered by the store's `berry_web`. Two
things read those tables:

- `core.addon.prune_retired_addons(log)` runs once at startup, from
  `berry_manager.py` after the addon manager is built and before the tabs are
  populated, so it lands on the first launch of a new version rather than on
  somebody hunting for a cleanup. It removes the leftover program files and the
  sidecar manifest, and it *moves* `addons/_data/<id>` onto the successor's
  folder when that folder is not there yet - so an install that worked keeps its
  repository, its token and its keep list instead of starting over. When the
  successor's folder already exists the user has run the new addon, and the old
  folder goes. It returns the lines it acted on, which are logged as they are
  found, and touches nothing whose id it was not given.
- `core.ui._normalize_addons` folds the profile's record the same way as
  it is read: the retired id's switch and transports move onto the successor,
  the successor's own record wins wherever both exist, and the retired key goes.
  `_translate_remote_root` follows it, rewriting only the folder that was the
  shipped default - `/www_rasconf` to `/www_berry` - because a path somebody
  chose themselves is theirs, not a leftover of a renamed addon.

What the device holds is deliberately not swept. `/www_rasconf` is left serving
until the user presses **Move folder to this name**, which renames it, points
`uhttpd.<section>.home` at the new name and restarts - because a folder an
upgrade renames on its own is a dashboard that stops answering, and the old one
can always be renamed back.

`addons/_tests/test_retire.py` runs both halves against a folder it builds and
tears down: the sweep's moves and deletions, what it leaves alone, and the
config translation - a retired record folded onto its successor, a default path
moved, a chosen one kept.

## Logs

On-screen log lines are capped by `max_log_lines`. While **logging is enabled**
every message is also appended to `config/berry_manager.log`, and
you can export the current view with File > Save log to file. Disabling logging
in Settings stops the file from being written; the on-screen log still updates
so you keep operation feedback.
