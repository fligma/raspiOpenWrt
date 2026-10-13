![Berry Manager screenshot](media/SCREENSHOT.png)

# Berry Manager & the Rasconf dashboard

Everything for running a Raspberry Pi as an OpenWrt router and looking after it
from your desktop. The repository holds two products that work together:

| Part | Lives in | What it is |
| --- | --- | --- |
| **Rasconf dashboard** | `src/www_rasconf` | A small web UI that runs *on the device*: Python CGI pages served by uhttpd, with interface status, wireless clients, temperature, load, traffic and storage, behind a PIN login |
| **Berry Manager** | `utils/update util` | A PyQt6 desktop app that manages the device over SSH/SFTP: named profiles, a real terminal, file browsing, embedded web tabs, and an addon system |

Berry Manager is the manager; the dashboard is one of the things it manages.
The bundled **Web installer** addon can install the dashboard onto a device
straight from this repository - no checkout needed on the machine doing the
installing.

## The device side: Rasconf dashboard

`src/www_rasconf` is the tree that is deployed to the device's
`/www_rasconf`, served by uhttpd on port 8989:

- `cgi-bin/index.py` - the CGI entry point; `?action=api` answers with the
  same data as JSON for scripts and for the manager's own views.
- `cgi-bin/` siblings (`auth.py`, `store.py`, `sysinfo.py`, `netinfo.py`,
  `pages.py`) - PIN/session auth, saved settings, system and network info,
  and the HTML templates.
- `cgi-bin/hash.file` - the SHA-256 of the PIN. It belongs to the device:
  installers never overwrite it, and it is created on first run if missing.

Setup commands, the uhttpd configuration and the page/API layout are in
[src/www_rasconf/README.md](src/www_rasconf/README.md). A ready-to-flash
OpenWrt image for the Raspberry Pi 3/Zero 2 (the bcm2710 factory ext4 image)
sits in `fresh_install/`.

## The desktop side: Berry Manager

`utils/update util` is the whole application - fifteen small modules, one
entry point (`berry_manager.py`) and no build step to run it:

```sh
cd "utils/update util"
pip install -r requirements.txt
python berry_manager.py
```

What it gives you:

- **Profiles** in the File menu - every profile (host, port, keys, and all
  settings) lives in its own `{profile}.json`, so switching devices switches
  everything; passwords go to the operating system's credential store, never
  to a file.
- **SSH terminal** - a real xterm-style emulator: `nano`, `htop` and colour
  prompts work, quick-command buttons sit above it, and a reboot watch
  reconnects when the device comes back.
- **Files over SFTP**, custom embedded browser tabs (including the dashboard
  itself, with a self-signed-certificate switch), interface zoom, and a dark
  theme with a colour wheel for anything coloured.
- **Addons** - the app's extension system, described below.

Packaging is one PyInstaller command, and the GitHub workflow
(`.github/workflows/build.yml`) runs it for Windows and Linux on a push to
`main` that changes the app or the addons shipped inside it - after running
every addon's test script. A push that only brings an addon for the **store**
just rewrites the store's catalog and builds no executables, and a push that
touches neither (the web dashboard, the pictures, a README) does nothing at
all. The artifact is `berry_manager(.exe)` plus an empty `addons` folder for
your own drop-ins.
The full documentation - modules, every settings key, HTTPS certificate
handling, the build - is in
[utils/update util/README.md](<utils/update util/README.md>).

## The addon API

An addon is a file you drop in the `addons` folder beside the program; Berry
Manager gives it a tab, a menu, the device connection and its own config file.
The same Python file runs imported into the app, or compiled into its own
program that the app talks to over stdin/stdout with one JSON object per line
- so addons can be written in any language, and a Python addon can be built
into an executable without changing its code.

Everything an addon needs arrives through one object:

```python
def init(api):
    api.tabs.add_tab("My tool", spec={...})       # declare a page, no Qt
    api.device.run("uptime")                      # SSH on the profile's host
    api.events.on("ssh_connected", on_connected)  # the app's own events
    api.log("ready")                              # the shared log
```

`api.device` (SSH/SFTP, threads and all), `api.tabs` (own tabs, web tabs,
widgets built from declarative specs, tab icons), `api.ui` (dialogs, menus,
a colour wheel), `api.config`/`api.settings` (per-installation JSON file and
per-profile notes), `api.schedule`, `api.peers` (addons talking to each
other) and `api.session`/`api.paths` make up the full surface - each is
documented with examples in the app README's **Addons** chapter.

Three addons ship inside the program and are switched on and off under
**Bundled with the program** in Settings:

- **Berry** - the file manager: device and local pages in one tab, with a
  syntax-coloured editor.
- **Web installer** - installs this repository's `src/www_rasconf` onto the
  device from GitHub, with a plan table before anything is written.
- **Berry addon store** - the store itself: reads the catalog published in
  `utils/update util/addon.store`, then downloads, tests and compiles any
  addon listed there into a program of its own in `addons`.

Everything else comes from that store - **Certificate manager**, **OpenWrt
Log Viewer**, **Hello from the store** - and an install is not a second-class
addon. The store writes an `addons/<id>.json` manifest beside the program it
built (its name, version, author and the transports it needs) and seeds its
settings form, so the moment it is switched on it sits under **Installed
addons** in Settings with the same switch, restart, own-connection option and
config panel as the bundled ones. Deleting it from the store takes the program
and that manifest back and leaves `addons/_data/<id>` alone, so a reinstall
keeps your settings.

The **Addon Dev** tab (Tools > Addon Dev) works on addon files themselves:
read the manifest and source, run a generated test script against a recording
stub, and compile an addon into its own program. Each addon keeps its settings
in `addons/_data/<id>/config.json`, so a hand edit is always possible and the
app reads it back without a restart.

## Repository layout

```
src/www_rasconf/     the web dashboard deployed to the device
utils/update util/   Berry Manager: the desktop app, its bundled addons and the addon store
fresh_install/       OpenWrt factory image for a fresh Pi
media/               logo and screenshots
.github/workflows/   the Windows + Linux build pipeline
Bkup/                older backups of the manager
```

## License

MIT - see [LICENSE](LICENSE).
