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
`manager_const.ASSETS_DIR` expects, so the icons are found in the unpacked
one-file build exactly as they are in a source checkout.
`--add-data="_addon_bundle:addons"` does the same for the addons that ship with
the program: the folder is staged by the workflow out of `addons` and lands
inside the archive under the name `manager_const.BUILTIN_ADDONS_DIR` reads.
`--add-data="_addon_harness:harness"` packs a plain copy of `addon_common.py`
and `addon_client.py` - the two modules an addon imports to reach the app -
where `manager_const.HARNESS_DIR` points at it. They have to lie on disk: the
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
counts as everything. Before the packaging, a step runs every `test_*.py` in
`addons/_tests` against the stubbed app - a failing test fails the build, and
the tests themselves are never packaged. The step after that stages the addons
out of `addons` into `_addon_bundle`, which is what gets packed inside the
executable: top-level `.py` addons, compiled addons (a loose `.exe` or `.bin`
with its `<name>.json` manifest, or a folder whose program is `addon.exe`,
`addon.bin` or `<folder>.exe`), and folder addons built from `addon.py` or
`main.py`; `__pycache__` and any `tests`/`_tests` folder are stripped from the
copy. The `addons` folder that ships beside the built program is left empty
for the user's own drop-ins.

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

Every module is imported statically at the top of its user, which is what lets
PyInstaller collect them. Commit a new module with the rest of
`utils/update util`, or the build fails on the missing import.

### Assets

`assets/` holds the pictures the program ships with - `icon.png` and
`icon2.png` for the logo, and the tab marks `defaulttabicon.png`,
`terminalicon.png`, `openwrtlogsicon.png` and `berryicon.png`. Nothing
hard-codes the folder: `manager_const.find_asset(name)` looks in `assets` beside
the resources, then the old top-level location, then beside the executable, so
an existing `icon.png` next to the `.exe` still overrides the built-in one. Add
a new image to `assets/`, ask for it with `find_asset`, and the GitHub workflow
picks it up without any change (it copies the whole folder into the bundle).
An addon reaches the same folder through `api.paths.assets`; a picture it names
for itself, like Berry's `berryicon.png`, is used when it is there and the
addon's own drawn SVG is used when it is not.

## Modules

Fifteen files, each holding one area of the app:

| Module | Holds |
| --- | --- |
| `berry_manager.py` | Entry point: Qt application, window icon, startup |
| `manager_gui.py` | `BerryManager` - menu, tab shell, connection panel, Settings tab, profiles and credentials, dialog-driven menu actions, log, shutdown |
| `manager_widgets.py` | Reusable pieces: `ToggleSwitch`, `ToggleGroup`, `CollapseGroup`, `TabTransportDots`, the shared file tree and the popup dialogs |
| `manager_browser.py` | Embedded web tabs and the HTTPS certificate switch |
| `manager_files.py` | The lightweight SFTP connection - the connect/disconnect probe, the `sftp_connected` flag and its events (Berry owns the file views) |
| `manager_terminal.py` | SSH tab - wiring of the terminal, quick commands, reboot watch |
| `manager_term.py` | `TerminalScreen` - the xterm-style emulator the SSH tab shows: escape-sequence parser, cell grid, key encoding |
| `manager_net.py` | paramiko connections, path safety and the background worker threads |
| `manager_config.py` | Reading, normalising and writing `config.json` |
| `manager_addons.py` | The addon runtime - discovery, loading, enable/disable, events, the Settings category with each addon's config panel, the executable-addon bridge and its callback tokens |
| `manager_addondev.py` | The Addon Dev tab - details, compile, test script, log and processes; shown by a Settings switch and read-only for the addons that ship inside the program |
| `addon_api.py` | The `api` object every addon receives and the declarative widget builder; the plain helpers are re-exported from `addon_common` |
| `addon_common.py` | The Qt-free half of the contract: errors, JSON results, the config and build-mark files. Imported by an addon, by a test and by the app |
| `addon_client.py` | The addon side of the pipe - the `api` a process addon uses, `serve()` for its own process, and the recording stub a test runs against |
| `manager_const.py` | Paths, defaults and the app stylesheet |

The three device tabs are mixins (`BrowserMixin`, `FileViewsMixin`,
`TerminalMixin`) that `BerryManager` inherits alongside `QMainWindow`, so the
code of each tab stays in its own file while the window keeps the shared state.
What the window itself owns - the menu, the Settings tab, the profiles and the
actions that open a dialog - is in `manager_gui.py`.

## Configuration file

The manager keeps everything it writes in one `config` folder beside the script
(`manager_const.CONFIG_DIR`): `config.json` - the profile index - one
`{profile}.json` holding each profile's complete settings,
`command_history.json`, `berry_manager.log` and the shared addon build record
`config/_build.json`. The folder is created on the way up, and a loose file
left in the program root by an older version is moved into it on the first
run. The folder is Git-ignored; `config.example.json` is the tracked template,
written out with a complete profile including the default tab order, so
copying it gives a working starting point rather than a list of empty keys.
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

The Settings tab is grouped into categories so that each section only shows the
options that belong to it. The **SSH** category can be ticked off: unchecking it
hides its options and removes the terminal from the window, and the state is
saved (`ssh_enabled`). The **File transfer (SFTP)** block and the **Custom tabs**
category are always shown. The file tab is no longer built into the app: the
bundled **Berry** addon browses files over this SFTP connection, and its on/off
switch lives with the other shipped addons in **Bundled with the program** below.
The connection panel is titled **Connection settings**; when the SFTP transport
is switched off (`sftp_enabled`) it drops the **Remote dir** line, because that
directory is the root Berry opens on. Nested toggles (logging) still
hide their own indented options while off.

- **Advanced connection settings** (folded away until you open it)
  - Read-only, and nothing here is saved. The program's own **SSH** and **SFTP**
    indicators - the two dots that used to sit in the window header - are here
    instead, each with the word **connected** or **closed** beside it, and under
    them one line per running addon naming the transport it has actually used
    through the device API and whether that connection is open or closed right
    now. An addon that has used neither says *no connection used yet*. The list
    is redrawn whenever a connection or an addon changes, so it is never a
    snapshot of when the page was opened. Both the dots and the whole category
    disappear when the two transports are switched off.
- **Startup & defaults** (always visible)
  - Default tab shown at startup (only the currently visible tabs are listed; it
    falls back to the first visible tab when the stored one is gone)
  - **Remember tab order** - keep the tabs in the order you drag them into for
    the next launch, with **Save current order** and **Reset to default order**
    beside it (`remember_tab_order`, `tab_order`). The saved order is a list of
    tab titles; a title that is not there at startup (an addon switched off, a
    web tab renamed) is simply skipped, and a tab the order does not know joins
    at the end, before the pinned Settings tab.
  - **Show the Addon Dev tab** - the addon index with its details, test and
    compile panels, and the Tools > Addon Dev entry that goes with it
    (`addon_dev_enabled`). Off, the tab and the menu entry are gone; the
    addons themselves are still switched on and off in Settings below.
- **File transfer (SFTP)** (always visible)
  - Auto-connect at startup (opens the SFTP connection when the app launches)
  - **Enable logging to file** - when off, nothing is written to
    `berry_manager.log`. While on it exposes the maximum on-screen log lines
    and the log file path. The on-screen pane under the tabs is switched
    separately, in View > **Show log console**.
- **SSH** (checkable tab)
  - Auto-connect at startup (opens the SSH terminal when the app launches)
  - Terminal font size (the terminal's own base size; View > Zoom in/out scales
    it together with everything else)
  - Reboot timing: **Wait before first retry** and **Retry every**, plus the
    option to be asked after a reboot is sent through the button or a quick
    command
  - Edit quick commands
- **Custom tabs** (always on)
  - **Tabs** - add, edit, or remove the embedded browser tabs. Each tab has a
    display name, an IP/hostname (leave blank to follow the active profile
    host), a port, a path, and http/https.
  - **Accept untrusted HTTPS certificates** - let the embedded browser load
    pages whose certificate it cannot verify (`ignore_cert_errors`). QtWebEngine
    reads this when it starts, so the manager offers to restart when you change
    it.
- **Bundled with the program** and **Installed addons**
  - The addons that ship inside the app each get their own category here, with
    their enable switch, their config panel and their connection indicators (see
    below). **Berry** is switched from here, which shows or hides its file tab.
    Addons you drop in later are listed under **Installed addons**.

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

Auto-connect is per protocol: **File transfer (SFTP)** and **SSH** each have
their own **Auto-connect at startup** option, and they are independent. With the
SFTP option on the app opens the SFTP connection at launch - the transport Berry
browses files over; with the SSH option on it opens the SSH terminal. Turning
both on starts both connections. The SSH option is ignored while the SSH tab is
switched off (unticking the category hides the option too).

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
addon panels - is multiplied by it, along with the terminal's font. Settings >
**Terminal font size** stays the terminal's own base, so the two combine: a
12 pt terminal at 125% draws at 15 pt. The percentage is saved per profile and
is applied when the window opens; nothing needs a restart.

### Full option list

| Key | Meaning |
| --- | --- |
| `profiles` | List of saved connection profiles |
| `active_profile` | Name of the profile currently loaded |
| `ssh_timeout` | Seconds for SSH connect/auth (3-300) |
| `logging_enabled` | Master switch for writing `berry_manager.log` |
| `show_hidden_files` | Include dot-files in Berry's device listing |
| `auto_connect_sftp` | Open the SFTP connection at startup |
| `auto_connect_ssh` | Open the SSH terminal at startup (only when the SSH tab is on) |
| `sftp_enabled` | Whether the SFTP transport is used at all (drives the Remote dir line and connect button) |
| `ssh_enabled` | Show the SSH tab (untick to hide it) |
| `addon_dev_enabled` | Show the Addon Dev tab and its Tools menu entry |
| `reboot_wait_seconds` | Downtime allowed for a reboot before the first reconnect attempt (0-600) |
| `reboot_retry_interval` | Seconds between reconnect attempts until the device answers (1-300) |
| `reboot_auto_watch` | Offer to watch for the device when a reboot command is sent |
| `default_tab` | Tab selected when the window opens (defaults to Berry; falls back to the first visible tab) |
| `remember_tab_order` | Keep the tabs in the order you drag them into, next time the app starts |
| `tab_order` | That order, as a list of tab titles; `[]` means the built-in arrangement (SSH, the web tabs, then the addons) |
| `terminal_font_size` | SSH terminal font point size (before the interface zoom) |
| `ui_zoom` | Interface density in percent, set by View > Zoom in/out (60-200, 100 as shipped) |
| `max_log_lines` | Lines kept in the on-screen log (the log file is not truncated) |
| `log_visible` | Show the log pane at the bottom of the window (View > Show log console) |
| `terminal_history_limit` | Quick-command runs remembered in `command_history.json` |
| `web_tabs` | List of embedded browser tabs (name, ip, port, path, scheme) |
| `addons` | Per-addon record for this profile: `enabled` and the addon's own `settings` |
| `ignore_cert_errors` | Load https pages whose certificate the embedded browser cannot verify |
| `use_alt_icon` | Show `icon2.png` instead of `icon.png` in the toolbar |
| `quick_commands` | Buttons shown above the terminal |

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

**Connect (SFTP)** in the connection panel opens the SFTP session Berry works
over; **Disconnect (SFTP)** closes it. The connection is a quick probe - it no
longer builds a file tree inside the app itself.

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

The quick-command buttons above the terminal type their command into the
shell, Enter included, when a shell is open - otherwise they run it over a
one-off connection and log the output. Every quick-command run is saved to
`command_history.json`; typed commands are the shell's own business now, so
its arrow-key history is what browses them.

SFTP and SSH have separate connection indicators - in Settings > Advanced
connection settings, folded away by default - and separate controls.
**Open SSH terminal** and **Disconnect** drive the live shell;
**Connect (SFTP)** and **Disconnect (SFTP)** open and close the SFTP session
Berry browses files over. Disconnecting marks the transport offline and fires
the `sftp_disconnected` event; Berry reopens its listing the next time you
connect.

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
after a reboot command** in the Settings tab. A `reboot` you type yourself is
not noticed: the terminal passes keystrokes straight through without reading
the line, so use the button or a quick command when you want the wait. Both
timings live in the SSH section of Settings and are saved to `config.json`.

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
when you press **Open folder** in Settings), so it exists even if you never add
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

Settings splits addons in two: a **Bundled with the program** category, one
section per shipped addon, and an **Installed addons** list for the drop-ins.
Each row carries the addon's enable switch, its version and author, what it said
about itself, a **Restart** button, and a **Show tab** button when the addon
opened one. A shipped addon gets the switch, its state and its config panel, plus
**Show tab** while it has one - but no **Restart**: restarting reads the addon's
file again, and that file is inside the program.
Switching an addon off stops it and takes
away everything it added - its tabs, its menu entries, its timers and its
listeners - without restarting the app; switching it on starts it again. The
new tab joins the row of tabs without being jumped to - being switched on is
not a request to look at it - and **Show tab**, or the addon's own menu entry,
is how you get there. What an addon keeps in its row - its config panel and
any controls it added - folds away while it is off. Switching one addon
rebuilds only that addon's row, so the list does not shift under you while you
are reading it. The choice is stored per profile in
`addons -> <id> -> enabled`, and a **Rescan** button picks up files that were
added or removed meanwhile.

Beside each addon's name are one or two small dots - **SSH** and **SFTP** - that
appear only once the addon has actually used that transport through the device
API. A dot is lit while the app's matching connection is live and grey while it
is down, so an addon that only ever talks over SSH never shows an SFTP dot.
Berry normally wears both, because its Device page lists files over SFTP and runs
chmod and find over SSH. The same information, spelled out in words rather than
in colours, is listed per running addon in **Advanced connection settings** at
the top of the Settings tab.

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
         "description": "What it does"}

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
| `on_widget(tab, id, event, payload)` | the user touched something in one of its tabs |
| `on_config(payload)` | its `addons/_data/<id>/config.json` was saved, by Settings or by itself |

The object in `init` is the only door into the app:

| Namespace | What it gives |
| --- | --- |
| `api.id`, `api.name`, `api.log(text)`, `api.error(text)` | identity and the shared log |
| `api.session` | everything the session knows: `profile()`, `settings()`, `status()`, `tabs()`, `current_tab()`, `web_url()`, `version()`, `app_stylesheet()` (already scaled to the current zoom), `zoom()`, `app_icon()`, and the two an installer needs: `rescan_addons()` (look at `addons` again on the next turn of the event loop, so the caller is not torn down mid-request) and `set_addon_enabled(id, enabled)` (write another addon's switch into the active profile, without starting or stopping anything) |
| `api.device` | SSH and SFTP: `run(command)`, `run_async(command, callback)`, `shell_send(line)`, `is_connected()`, `ensure_connected()`, `open()` (raw paramiko), and `list`, `exists`, `stat`, `read_text`, `read_bytes`, `write_text`, `write_bytes`, `append_text`, `mkdir`, `remove`, `rename`, `upload`, `download` |
| `api.tabs` | `add_web_tab`, `add_tab`, `close`, `rename`, `activate`, `names`, `exists`, `url`, `navigate`, `reload`, `view`, `widget`, `set_widget`, `widget_value`, `set_html`, `inject_css`, `inject_js`, `set_style`, `set_icon`, `icon`, `use_app_icon` |
| `api.ui` | `information`, `warning`, `error`, `question`, `input`, `choose`, `notify`, `menu_item`, `dialog`, `set_setting_widget`, `window()` |
| `api.events` | `on(name, callback)` (returns the unsubscribe callable), `off`, `names` |
| `api.settings` | the addon's own saved dict, per profile: `all`, `get`, `set`, `update`, `pop`, `remove` |
| `api.config` | its own file in `addons/_data/<id>`: `declare`, `file`, `path()`, `exists()`, `open_folder()`, `schema()`, `defaults()`, `stored()`, `all()`, `get`, `set`, `update`, `pop`, `reset`, `text`, `save_text` |
| `api.schedule` | `after(seconds, fn)` and `every(seconds, fn)`; both stop with the addon |
| `api.paths` | `addon`, `data` (a private folder, created on demand), `config` (this addon's config file), `configs` (the addon's own data folder, where that file sits), `base`, `addons`, `assets` (the pictures that ship inside the program), `log` |
| `api.peers` | `list()`, `info(id)`, `send(id, data)` to talk to another addon |

The events are `startup`, `shutdown`, `sftp_connected`, `sftp_disconnected`,
`ssh_connected`, `ssh_disconnected`, `operation_done`, `tab_changed`,
`profile_changed`, `settings_applied`, `addon_enabled`, `addon_disabled` and
`addon_config_changed`.

Device work runs in a small shared thread pool, so a slow command never
freezes the window. `run()` waits for the result while keeping the interface
responsive; `run_async()` hands it to a callback instead, which is what you
want from `init`. Relative file paths are confined to the profile's remote
directory, and `outside_root=True` opts out of that. Addons use the profile's
saved connection details, which means a password has to be in the connection
panel (or saved with **Remember password**) before a device call can connect.

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

`api.tabs.add_web_tab(name, url=...)` opens an embedded browser page that
belongs to the addon rather than to the profile: it is not written to
`config.json`, never appears in Settings > Custom tabs, is not rebuilt when you
switch profiles, and closes again when the addon is switched off. Settings >
Custom tabs stays exactly as you left it.

`api.tabs.add_tab(name, widget=...)` takes any Qt widget a Python addon builds,
with `style=` for a stylesheet of its own, so an addon's tab can look like
nothing else in the app. Adding one does not steal the view - the tab appears
in the row and `activate` is how an addon shows it when it really means to.
For a page, `set_html`, `inject_css` and `inject_js`
let an addon restyle or script what the device serves.

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

Three shipped addons use this: `addons/cert_manager.py` (a trophy),
`addons/berry.py` (a cluster of drupelets) and `addons/openwrt_logs.py` (a log
page lined in the device colours). Each holds its mark as an SVG string in
`TAB_ICON` for exactly that reason, and berry and openwrt_logs look for a real
picture first - `assets/berryicon.png` and `assets/openwrtlogsicon.png`,
through `api.paths.assets` - and wear their drawing only when the file is not
there. A tab that never names a mark is not left bare either: it wears the
program's default tab picture (`assets/defaulttabicon.png`), and the SSH tab
wears `assets/terminalicon.png`. Every tab mark is drawn larger than the bar
measures it at, so the pictures read clearly without the tabs growing.

A tab can instead wear the program's own logo: `api.tabs.use_app_icon(tab)`
points it at whatever `api.session.app_icon()` returns - the same picture the
window and toolbar wear - and it keeps pointing there, so switching to the
alternate icon in View changes the tab's mark in the same breath. Choosing a
mark with `set_icon` ends the follow; `use_app_icon(tab, False)` drops it and
takes the mark away. `addons/web_installer.py` does this, so its tab and the
program never disagree about what they look like.

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
`progress`, `spin`, `slider`, `spacer`, `web`. Common attributes: `id`, `text`,
`tooltip`, `style` (a Qt stylesheet, so the addon can theme its own tab),
`enabled`, `visible`, `font_size`, `items`, `rows`, `value`, `checked`,
`placeholder`, `readonly`, `minimum`, `maximum`, `columns`, `word_wrap`,
`expand` (claim the width its row or column leaves over - what lets one status
line stretch across a header instead of being squeezed by a spacer), and
`format` for a progress bar - the words painted inside it, where Qt expands
`%p`, `%v` and `%m` into the percentage and the numbers. `on`
lists the interactions to report back: `clicked`, `toggled`, `changed`,
`returned`, `selected`. `api.ui.dialog(spec)` and
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

`addons/example_uptime.py` is a working example: it adds a Tools menu entry
that asks the device for its uptime and logs the result every time a connection
comes up. Switching it off in Settings leaves it untouched on disk.

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
| **Log** | The lines this addon wrote with `api.log`, taken from `berry_manager.log`, with a text filter, an *errors only* filter and *follow*. |
| **Processes** | The pid, state, exit code, uptime, request count and command line of every addon running as a program; and what the app holds for the imported ones - their tabs, timers, menu entries and event names, plus the threads serving them. |

A table, five panes and the notes under them do not fit into a short window, so
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
`addons/<id>.py` - a header docstring, the `ADDON` dict, `FIELDS`/`DEFAULTS` for
its config, a tab built from a declarative spec with a button that reports back
through `on_widget`, and `start`, `shutdown`, `focus`, `on_config`,
`on_connected` and `on_event` hooks - together with `addons/_tests/test_<id>.py`
when it was asked for, and can switch the addon on straight away.

The test script drives the addon through `addon_client.stub_api`, the recording
stand-in for `api`: nothing is opened on screen, nothing reaches a device, and
the checks are about what the addon *asked for* (`api.called("tabs.add_tab")`,
`api.asked("device.run", "command")`, the lines in `api.logs`). It prints every
call and fails with a list of the checks that did not hold, so it is also
runnable on its own, or in CI, without the app. The GitHub workflow takes that
at its word: before every build it runs every `test_*.py` in `addons/_tests`,
and a failing test stops the build instead of shipping inside an executable.

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
files it fetched when the program it runs inside has none on disk. PyInstaller
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

### Shipped addon: Certificate manager

`addons/cert_manager.py` is a complete addon rather than a snippet, and it is
the one that needs the config file above. It adds a **Certificates** tab and a
Tools menu entry, and works on the certificate pair the device's web interface
serves - `/etc/ssl/mycert.crt` and `/etc/ssl/mycert.key` by default, both names
and the folder coming from `addons/_data/cert_manager/config.json`.

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
pipe. The tab's trophy is an SVG document held as text in `TAB_ICON` and sent
with `api.tabs.set_icon`, so a compiled copy still gets its mark - there is no
file beside it to lose and no Qt in it to draw with.

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

`addons/_tests/test_cert_manager.py` covers the text that reaches openssl, the
check that refuses a doomed request, the script for each kind of key, both
failure routes, the tab as Qt builds it and the icon it sends, and **Run test**
in the Addon Dev tab runs it.

### Shipped addon: Rasconf web installer

`addons/web_installer.py` adds a **Web installer** tab and a Tools menu entry. It
puts the dashboard the device serves - the tree under `src/www_rasconf` in this
project's repository - onto the device *from GitHub*, so a Pi can be given the
web interface from a machine that has the built `.exe` and no checkout of the
repository anywhere near it. The files go into the current profile's remote
folder - the root Berry opens on - over the connection panel's own SFTP
settings.

| Button | What it does |
| --- | --- |
| **Fetch files** | Asks GitHub what is in that folder at that branch or tag, downloads it into `addons/_data/web_installer/staging/`, asks the device what it already has, and fills the plan table. Writes nothing. |
| **Install** | The same, and then, after one confirmation that lists what will happen: back the folder up on the device, write the files, tidy away the leftovers if the config asks, `chmod 755` the CGI entry, run the restart command, and ask the device for its own page. |
| **Open dashboard** | The page in an embedded tab, following the profile's host |
| **Restart web server** | The configured restart command and nothing else |
| **Check page** | `curl`, or busybox `wget`, against `127.0.0.1` and then the address in the connection panel, and says whether the dashboard answered |
| **Save to config** | Keeps what is in the form in `addons/_data/web_installer/config.json` |
| **Reload from config** | Puts the saved values back into the form |
| **Stop** | Gives up on the job in hand; what has already gone across stays on the device |

The tab gives the whole width to what the job is doing: a header line carries
where the files come from and where they land beside a chip that says whether
the device is connected; a step flow across the top -
`○ Files → ○ Compare → ○ Backup → ○ Write → ○ Restart → ○ Check` - lights the
step running now, ticks the ones the route walked and dims the ones this job
never takes; and three badges count the plan up (*N to write*, *N already
there*, *N extra on device*) above a notebook of three pages - **Files**, the
plan table, **Log**, and **Settings**, the form that used to spend half the
width standing beside the plan in a split.
The plan table is one row per file with its size, what the device has of it and
what this job means to do - `new`, `already there`, `replaced`, `written again` -
the row tinted by that action, so an update is read before it is run, and a
look-around costs one API call and a download.

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
- Everything is config: the repository, the ref, the folder inside it, the
  subfolder of the remote directory, what is never sent, what is never deleted,
  the backup folder, the entry file, the restart commands, the port and path to
  check afterwards. Setting **Files come from** to **Local folder** installs
  from a folder on this machine - the profile's own source by default - and then
  needs no internet at all.
- The job runs one file per `api.schedule.after` tick, so the bar and the table
  move the whole way through. Each stage of the route - finding the files,
  downloading, asking the device, backing up, writing, tidying away, restarting,
  checking the page - owns a weighted slice of the bar and writes its word inside
  it, so the bar says what is happening and keeps moving while the device thinks;
  the last file of a download no longer lands the job at 95% with four stages
  still to go, and the bar ends full only when the route really is over. The two
  one-command buttons use the bar the same way, and the step flow above it
  follows the route stage by stage. The tab is a spec and its eight
  buttons report through `on_widget`, and a compiled copy of this addon cannot
  hold an SSH session (`device.open` is refused), so the same writing code sends
  each file with `device.upload` instead - one connection per file rather than
  one for the lot, which is the only thing that differs. Its mark follows the
  program's own icon, so the tab changes together with View > Alternate icon.

`addons/_tests/test_web_installer.py` drives the whole route - the tab built by
Qt (its notebook, step flow and badges included), a real fetch from GitHub, an
install against a faked device, the local
folder, a branch that is not there, and the bar watched stage by stage: that it
moves for every stage, names each one, never goes backwards and ends full - and
**Run test** in the Addon Dev tab runs it, skipping the parts that need GitHub
when this machine cannot reach it.

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
  under this addon's name in Settings > Bundled with the program: the starting
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

### Shipped addon: OpenWrt Log Viewer

`addons/openwrt_logs.py` adds one tab - **OpenWrt Logs** - with two pages:
**Single device** reads the log of the profile's own host, and **Multi
device** queries several hosts side by side, each device's rows wearing the
colour that device was given (the translucent wash is painted by the app, and
the Colour field in the form is a hex box whose swatch opens the same colour
wheel as `api.ui.pick_color`). Every line arrives over SSH with one command
per fetch: the log command, the row cap (50 to 50 000) and the timeout are
config, and auto-refresh pulls the log again on a timer. Search works two
ways - highlight the matches, or filter the list down to them - with case
sensitivity and regular expressions as switches. Like the other shipped
addons the tab is a spec and every control reports through `on_widget`, so a
compiled copy runs the same code; `addons/_tests/test_openwrt_logs.py` drives
it through the stub, including the picture the tab asks to wear.

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
  `manager_const.BUILTIN_ADDONS_DIR` is where it looks, and
  `HAS_BUILTIN_ADDONS` says whether a build has such a folder at all - in a
  source checkout the two folders are the same one directory.
- A shipped addon is an addon in every other respect: same manifest, same `api`,
  same events, same config file - with one flag set, `builtin`, that says the
  file belongs to the build. Nothing writes to it: the Details form is read-only,
  **Save** is refused, and there is no **Restart** in its Settings row.
- It is switched on and off from its own category under **Bundled with the
  program** - the same place the SSH category sits - and the
  choice stays per profile in `addons -> <id> -> enabled`. Its own settings are
  still written beside the `.exe`, in the writable `addons/_data/<id>/config.json`, so
  a shipped addon is configured per installation exactly like a dropped-in one.
- "Shipped" is decided by a stable list, `manager_const.SHIPPED_ADDON_IDS`
  (`berry`, `cert_manager`, `web_installer`, `openwrt_logs`), on top of the `builtin` flag, so a
  source checkout - where `HAS_BUILTIN_ADDONS` is off - still groups those addons
  under **Bundled with the program** rather than the drop-in list.
- Two addons of one id cannot both be *the* addon called `mytool`, and a file in
  the folder beats the copy inside the program - it is the one the user can read
  and change. The shipped copy is kept as the row's alternative, and the Addon
  Dev tab marks such a row `shipped`.

The rest of the frozen path model is unchanged:

- `addons` itself resolves to a folder **beside the `.exe`** (`BASE_DIR`), not
  inside the archive, so it is writable: drop files in it and press **Rescan**.
  The folder in the downloaded artifact is empty apart from a note saying so.
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

## Logs

On-screen log lines are capped by `max_log_lines`. While **logging is enabled**
every message is also appended to `config/berry_manager.log`, and
you can export the current view with File > Save log to file. Disabling logging
in Settings stops the file from being written; the on-screen log still updates
so you keep operation feedback.
