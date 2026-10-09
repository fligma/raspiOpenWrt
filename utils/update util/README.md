![SCREENSHOT.png](https://raw.githubusercontent.com/fligma/raspiOpenWrt/refs/heads/main/media/SCREENSHOT.png)
# rasconf Manager

A desktop tool for deploying and managing the rasconf web interface on a
Raspberry Pi running OpenWrt. It talks to the device over SSH/SFTP and gives
you a file browser, an interactive terminal, embedded web views, and one-click
deploy with preview, backup, and post-deploy actions.

## Build the executable

```
pyinstaller --noconfirm --onefile --windowed --icon="icon.png" --add-data="icon.png:." --add-data="icon2.png:." --add-data="config.example.json:." --collect-all PyQt6.QtWebEngineWidgets --collect-all PyQt6.QtWebEngineCore --hidden-import=secretstorage --hidden-import=jeepney rasconf_manager.py
```

PyQt6-WebEngine is optional. If it is not installed, the Web Interface and
LuCI tabs show the URL and an "Open in browser" button instead of an embedded
page, and the rest of the tool works normally.

## Configuration file

The manager reads and writes `config.json` beside the script. That file is
Git-ignored; `config.example.json` is the tracked template. Existing
single-host config files are upgraded automatically to the profile format the
first time you run the new version.

Passwords and private-key passphrases are never written to `config.json`.
When "Remember password securely" is on, the password is stored in the
operating system credential store (Windows Credential Manager on Windows),
keyed by `user@host:port`.

### Profiles

Connection details are grouped into named profiles (host, port, username,
key, remote directory, source, host-key and remember-password flags). Use the
profile selector at the top of the window, or the File menu, to create, save,
rename, and delete profiles. Switching profiles loads its settings, clears the
password field, and reloads any saved credential.

### Settings tab

The Settings tab is grouped into categories by tab so that each section only
shows the options that belong to it. The **SFTP** and **SSH** categories can be
ticked off: unchecking one hides its options and removes that tab from the
window, and the state is saved (`sftp_enabled` / `ssh_enabled`). The
**Custom tabs** category is always on. Nested toggles (logging, remote backup,
local backup, mirror) still hide their own indented options while off.

- **Startup & defaults** (always visible)
  - Default tab shown at startup (only the currently visible tabs are listed)
- **SFTP** (checkable tab)
  - Auto-connect at startup (opens the SFTP connection when the app launches)
  - Confirm destructive operations (delete and mirror)
  - **Enable logging to file** - when off, nothing is written to
    `rasconf_manager.log`. While on it exposes the maximum on-screen log lines
    and the log file path.
  - **Backup remote files before deploy** - when on, every deploy first copies
    the current remote tree into a timestamped folder under the remote backup
    directory. Exposes the remote backup directory and how many backups to keep.
  - **Save a copy of the remote to this PC before deploy** (local backup) - when
    on, every deploy first downloads the current remote tree into a timestamped
    folder in the local backup directory. Exposes the local backup folder.
  - **Mirror cleanup on deploy** (destructive, off by default)
  - Edit deploy ignore patterns and post-deploy commands
- **SSH** (checkable tab)
  - Auto-connect at startup (opens the SSH terminal when the app launches)
  - Terminal font size
  - Reboot timing: **Wait before first retry** and **Retry every**, plus the
    option to be asked after sending a reboot command
  - Edit quick commands
- **Custom tabs** (always on)
  - **Tabs** - add, edit, or remove the embedded browser tabs. Each tab has a
    display name, an IP/hostname (leave blank to follow the active profile
    host), a port, a path, and http/https.

Auto-connect is now per protocol: each tab has its own **Auto-connect at
startup** option, and they are independent. With the SFTP option on the app
opens the SFTP connection at launch; with the SSH option on it opens the SSH
terminal. Turning both on starts both connections. An option is ignored while
its tab is switched off (unticking the category hides the option too), so if
both tabs are off nothing auto-connects.

The View menu has an **Alternate icon** toggle that switches the toolbar and
window icon between `icon.png` and `icon2.png`; the choice is saved to
`use_alt_icon`.

Deploy-time backups are now driven entirely from this tab (the old per-deploy
checkbox on the SFTP tab is gone). The SFTP tab shows a small label telling you
whether remote/local backup is currently active.

### Full option list

| Key | Meaning |
| --- | --- |
| `profiles` | List of saved connection profiles |
| `active_profile` | Name of the profile currently loaded |
| `ssh_timeout` | Seconds for SSH connect/auth (3-300) |
| `deploy_ignore` | Git-ignore-style patterns applied to Deploy |
| `post_deploy_commands` | Shell commands run on the device after each deploy |
| `logging_enabled` | Master switch for writing `rasconf_manager.log` |
| `remote_backup_enabled` | Back up the remote tree before each deploy |
| `local_backup_enabled` | Save a copy of the remote to this PC before each deploy |
| `backup_directory` | Remote directory where timestamped backups are written |
| `local_backup_directory` | Local folder where timestamped remote copies are saved |
| `keep_last_n_backups` | Oldest backups removed beyond this count (0 = keep all) |
| `confirm_destructive` | Ask before delete/mirror operations |
| `show_hidden_files` | Include dot-files in the local browser and deploys |
| `auto_connect_sftp` | Open the SFTP connection at startup (only when the SFTP tab is on) |
| `auto_connect_ssh` | Open the SSH terminal at startup (only when the SSH tab is on) |
| `sftp_enabled` | Show the SFTP tab (untick to hide it) |
| `ssh_enabled` | Show the SSH tab (untick to hide it) |
| `reboot_wait_seconds` | Downtime allowed for a reboot before the first reconnect attempt (0-600) |
| `reboot_retry_interval` | Seconds between reconnect attempts until the device answers (1-300) |
| `reboot_auto_watch` | Offer to watch for the device when a reboot command is sent |
| `default_tab` | Tab selected when the window opens |
| `terminal_font_size` | SSH terminal font point size |
| `max_log_lines` | Lines kept in the on-screen log (the log file is not truncated) |
| `terminal_history_limit` | Commands remembered in the terminal |
| `web_tabs` | List of embedded browser tabs (name, ip, port, path, scheme) |
| `use_alt_icon` | Show `icon2.png` instead of `icon.png` in the toolbar |
| `quick_commands` | Buttons shown above the terminal |

Editing `deploy_ignore`, `post_deploy_commands`, and `quick_commands` is done
through the Tools menu or the buttons on the Settings tab; you no longer need
to open the JSON by hand.

## Deploying

1. Pick a profile and connect with **Connect (SFTP)** (the same action refreshes
   the remote tree). **Disconnect (SFTP)** closes the session and disables the
   remote actions until you reconnect.
2. Choose **Deploy source** to sync the local source tree, or select individual
   files and use **Upload selected**. Deploy only uploads files that actually
   changed: size and modified-time are compared from remote directory metadata
   (no downloads), and when those disagree but the size matches, both files are
   hashed in RAM to avoid needless transfers. Files matching `deploy_ignore`
   are never uploaded.
3. **Preview deploy** (Ctrl+Shift+D) runs a dry pass and lists the directories,
   changed files, unchanged files that will be skipped, and (with mirror
   cleanup on) items that would be deleted on either side. Nothing is written.
4. **Mirror cleanup** (Settings tab, off by default) has two independent
   options: *Delete server files not on local* removes remote items absent
   locally, and *Delete local files not on server* removes local items absent
   on the remote (compared before the upload starts). Ignore-patterned files
   are never touched in either direction.
5. **Deploy-time backups** (configured in Settings) run before uploading:
   *Remote backup* copies the current remote tree to the remote backup
   directory, and *Local backup* downloads it to your PC. Both skip files over
   200 MB to stay quick, and both prune old backups beyond `keep_last_n_backups`.
6. **Post-deploy commands** run automatically after a successful deploy, which
   is handy for reloading the web server (for example `killall -HUP uhttpd`).

Deploy overwrites changed remote files and leaves unchanged ones untouched.
`Upload selected` intentionally ignores both the `deploy_ignore` rules and the
change detection so you can always force-send an excluded file by hand.

## SSH terminal

The SSH tab opens an interactive shell with basic ANSI colour rendering. Type
a command and press Enter or Send. Ctrl+Up and Ctrl+Down walk through command
history, which is saved to `command_history.json` and restored next time. The
quick-command buttons above the terminal run a command in the shell if one is
open, otherwise they execute it over a one-off connection and log the output.

SFTP and SSH have separate connection indicators in the header and separate
disconnect controls. **Open SSH terminal** and **Disconnect** drive the live
shell; **Connect (SFTP)** and **Disconnect (SFTP)** drive the file-transfer
session. Because SFTP opens a fresh connection per operation, Disconnect (SFTP)
clears the remote view and locks the upload/download/deploy buttons until you
connect again.

## Rebooting the device

**Reboot device** in the SSH tab sends `reboot` and then waits for the router to
come back: it holds off for `reboot_wait_seconds` (the downtime the device is
allowed to take), then tries SSH every `reboot_retry_interval` seconds until the
device answers, and reopens the terminal session. Progress is printed in the
terminal with a `[reboot]` prefix, and **Cancel reboot wait** stops the cycle
early.

Typing `reboot` (or `sudo reboot`, `busybox reboot`, `shutdown -r ...`) in the
terminal, or running a quick command that looks like one, offers to start the
same watch - the offer is controlled by **Offer to watch for the device after a
reboot command** in the Settings tab. Both timings live in the SSH section of
Settings and are saved to `config.json`.

## File browser extras

Both trees show file size and modified time. The filter box hides entries that
do not match a substring or a `*`/`?` glob. Right-click the remote tree to
download, delete, rename, or copy a path. "Show hidden" toggles dot-files.

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

## Logs

On-screen log lines are capped by `max_log_lines`. While **logging is enabled**
every message is also appended to `rasconf_manager.log` beside the script, and
you can export the current view with File > Save log to file. Disabling logging
in Settings stops the file from being written; the on-screen log still updates
so you keep operation feedback.
