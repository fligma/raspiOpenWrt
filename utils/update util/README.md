# rasconf Manager

A desktop tool for deploying and managing the rasconf web interface on a
Raspberry Pi running OpenWrt. It talks to the device over SSH/SFTP and gives
you a file browser, an interactive terminal, embedded web views, and one-click
deploy with preview, backup, and post-deploy actions.

## Build the executable

```
pyinstaller --noconfirm --onefile --windowed --icon="icon.png" --add-data="icon.png:." --add-data="config.example.json:." --collect-all PyQt6.QtWebEngineWidgets --collect-all PyQt6.QtWebEngineCore --hidden-import=secretstorage --hidden-import=jeepney rasconf_manager.py
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

The Settings tab is organised into collapsible sections. The three toggles
below hide their related options (shown indented under the toggle) while they
are off, and turn the feature off completely:

- **Enable logging to file** - when off, nothing is written to
  `rasconf_manager.log`. While on it exposes the maximum on-screen log lines
  and the log file path.
- **Backup remote files before deploy** - when on, every deploy first copies
  the current remote tree into a timestamped folder under the remote backup
  directory. Exposes the remote backup directory and how many backups to keep.
- **Save a copy of the remote to this PC before deploy** (local backup) - when
  on, every deploy first downloads the current remote tree into a timestamped
  folder in the local backup directory. Exposes the local backup folder.

A plain **Interface & connection** section stays visible at all times:

- Web interface port and path (used to build the Web Interface tab URL)
- LuCI URL
- Default tab shown at startup
- Connect automatically at startup
- Confirm destructive operations (delete and mirror)
- Terminal font size

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
| `auto_connect` | Refresh the remote tree at startup |
| `default_tab` | Tab selected when the window opens |
| `terminal_font_size` | SSH terminal font point size |
| `max_log_lines` | Lines kept in the on-screen log (the log file is not truncated) |
| `terminal_history_limit` | Commands remembered in the terminal |
| `web_port` / `web_path` | rasconf web interface URL parts |
| `luci_url` | LuCI address for the LuCI tab |
| `quick_commands` | Buttons shown above the terminal |

Editing `deploy_ignore`, `post_deploy_commands`, and `quick_commands` is done
through the Tools menu or the buttons on the Settings tab; you no longer need
to open the JSON by hand.

## Deploying

1. Pick a profile and connect (the remote tree is the same action as Refresh).
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
