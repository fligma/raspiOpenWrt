# rasconf SFTP Manager

compile with :
`
pyinstaller --noconfirm --onefile --windowed --hidden-import secretstorage --hidden-import jeepney --icon="icon.png" --add-data="icon.png:." --add-data="config.example.json:." --hidden-import=PyQt6.QtWebEngineWidgets --hidden-import=PyQt6.QtWebEngineCore rasconf_manager.py

pyinstaller --noconfirm --onefile --windowed --icon="icon.png" --add-data="icon.png:." --add-data="config.example.json:." --collect-all PyQt6.QtWebEngineWidgets --collect-all PyQt6.QtWebEngineCore --hidden-import=PyQt6.QtWebEngineWidgets --hidden-import=PyQt6.QtWebEngineCore rasconf_manager.py
`

## Local settings

The manager loads `config.json` beside the script and saves host, port, username,
key path, remote directory, local source, and host-key preference there. That
file is Git-ignored; `config.example.json` is the tracked template. The initial
host is `192.168.1.1`. Passwords are never written to either JSON file.

Enable **Remember password securely** to save the SSH password in the operating
system credential store (Windows Credential Manager on Windows). Unchecking it
removes the saved credential for the current host, port, and username.

Add Git-ignore-style patterns to the `deploy_ignore` array in `config.json`,
then restart the manager. Patterns are relative to the local source directory:

```json
"deploy_ignore": [
   "*.bak",
   "cgi-bin/test.py",
   "assets/private/"
]
```

These rules filter **Deploy source**. When Mirror is enabled, matching remote
files are also protected from deletion. **Upload selected** intentionally
ignores these rules so you can send an excluded file manually.

When connecting without a password or selected private key, the manager asks for
the SSH password. Enter one to use password authentication, leave the prompt
blank to try the SSH agent/default keys, or cancel to abort the connection.

The **SSH** tab opens an interactive shell after authentication. Type a command
in the field at the bottom and press Enter or **Send**. **Disconnect** closes
the shell; file browsing and transfers remain in the **SFTP** tab.

## OpenWrt SFTP requirement

Dropbear provides SSH access but may not have an SFTP subsystem installed. If
the manager reports that SSH connected but SFTP startup failed, install the
server package on the Pi and reconnect:

```sh
apk update
apk add openssh-sftp-server
```

For older opkg-based OpenWrt releases, use `opkg update` followed by
`opkg install openssh-sftp-server` instead.

## Connect and deploy

1. Enter the Pi's address, SSH port, and username (commonly `root` on OpenWrt).
2. Enter an SSH password or choose a private key. Leave both empty to try the
   local SSH agent and default keys.
3. On a first connection, verify the Pi's host key independently, then enable
   **Trust and save an unknown host key on first connection**. Accepted keys are
   stored in `~/.ssh/known_hosts_rasconf`; later connections use strict checking.
4. Connect and refresh. Browse either tree to select files or directories for
   upload/download, or choose **Deploy source** to upload the full local tree.

Deploy overwrites matching remote files but leaves other remote files alone.
The **Mirror** option is unchecked by default; enable it only when you intend to
delete remote files and directories that do not exist in the local source.
Remote deletion from the file browser prompts for confirmation and deleting the
remote root is blocked.

The tool does not save SSH passwords or private-key passphrases.