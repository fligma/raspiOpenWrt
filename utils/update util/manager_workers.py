#!/usr/bin/env python3
"""Background threads that talk to the device: interactive shell, one-off commands and all SFTP work."""

from __future__ import annotations

import codecs
import hashlib
import posixpath
import queue
import shutil
import stat
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

import paramiko
from pathspec import GitIgnoreSpec
from PyQt6.QtCore import QThread, pyqtSignal

from manager_config import _coerce_int
from manager_const import BASE_DIR
from manager_net import (
    create_ssh_client,
    exec_remote,
    local_entries,
    make_remote_directories,
    remote_join,
    safe_relative_path,
)


# ---------------------------------------------------------------------------
# Interactive shell
# ---------------------------------------------------------------------------

class SSHShellWorker(QThread):
    """Owns one PTY channel and pumps bytes in both directions until it is stopped.

    Output arrives on received() as decoded text; send_line() queues input so the
    GUI thread never writes to the socket itself.
    """

    connected = pyqtSignal()
    received = pyqtSignal(str)
    failed = pyqtSignal(str)
    disconnected = pyqtSignal()
    command_finished = pyqtSignal(int, str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.outgoing: queue.Queue[str] = queue.Queue()
        self.stop_requested = threading.Event()
        self.channel: paramiko.Channel | None = None
        self.client: paramiko.SSHClient | None = None

    def send_line(self, text: str) -> None:
        """Queue a line for the shell; the Enter key is added for you."""
        self.outgoing.put(text + "\n")

    def stop(self) -> None:
        """Ask for a shutdown; closing the channel also breaks any blocked read."""
        self.stop_requested.set()
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass

    def run(self) -> None:
        """Connect, then alternate between reading output and flushing queued input.

        The short socket timeout and recv_ready() keep the loop responsive to the
        stop flag and to typed commands. An incremental decoder is needed because
        a multi-byte UTF-8 character can be split across two reads.
        """
        try:
            self.client = create_ssh_client(self.settings)
            self.channel = self.client.invoke_shell(term="xterm", width=120, height=32)
            self.channel.settimeout(0.1)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self.connected.emit()
            while not self.stop_requested.is_set() and not self.channel.closed:
                if self.channel.recv_ready():
                    data = decoder.decode(self.channel.recv(65536))
                    if data:
                        self.received.emit(data)
                try:
                    outgoing = self.outgoing.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.channel.sendall(outgoing.encode("utf-8"))
        except Exception as error:
            if not self.stop_requested.is_set():
                self.failed.emit(str(error))
        finally:
            if self.channel is not None:
                try:
                    self.channel.close()
                except Exception:
                    pass
            if self.client is not None:
                self.client.close()
            self.disconnected.emit()


# ---------------------------------------------------------------------------
# Reboot wait - used by the "Reboot device" button in the SSH tab
# ---------------------------------------------------------------------------

class RebootWaitWorker(QThread):
    """Wait out a device reboot and retry SSH until it answers again.

    If `reboot_command` is given the reboot is sent over a fresh connection
    first (best effort - the connection dying is expected); pass None when the
    command was already sent through the interactive shell. run() keeps the
    settings dict alive, so a successful probe persists the password and
    refreshes the credential before the GUI opens the new shell.
    """

    status = pyqtSignal(str)
    connected = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, settings: dict, initial_wait: int, retry_interval: int,
                 reboot_command: str | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.initial_wait = max(0, int(initial_wait))
        self.retry_interval = max(1, int(retry_interval))
        self.reboot_command = reboot_command
        self.stop_requested = threading.Event()

    def cancel(self) -> None:
        self.stop_requested.set()

    def _sleep(self, seconds: int) -> bool:
        """Interruptible sleep in 50 ms slices; True means cancelled."""
        deadline = time.monotonic() + seconds
        while not self.stop_requested.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.stop_requested.is_set()

    def _send_reboot(self) -> None:
        """Open a connection just long enough to fire the reboot command."""
        self.status.emit(f"Sending: {self.reboot_command}")
        client = None
        try:
            client = create_ssh_client(self.settings)
            exec_remote(client, self.reboot_command, timeout=10)
        except Exception:
            # The device drops the socket while shutting down; that is the point.
            pass
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def run(self) -> None:
        try:
            if self.reboot_command:
                self._send_reboot()
            if self.initial_wait > 0:
                self.status.emit(f"Device is rebooting, waiting {self.initial_wait}s before the first attempt...")
                if self._sleep(self.initial_wait):
                    return
            attempt = 0
            while not self.stop_requested.is_set():
                attempt += 1
                self.status.emit(f"Connection attempt {attempt} to {self.settings['host']}:{self.settings['port']}...")
                client = None
                try:
                    client = create_ssh_client(self.settings)
                    code, _, _ = exec_remote(client, "echo rasconf", timeout=10)
                    if code == 0:
                        self.status.emit("Device is back online.")
                        self.connected.emit()
                        return
                    reason = f"ssh responded with exit code {code}"
                except Exception as error:
                    reason = str(error) or type(error).__name__
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass
                if self.stop_requested.is_set():
                    return
                self.status.emit(f"Not reachable yet ({reason}), retrying in {self.retry_interval}s...")
                if self._sleep(self.retry_interval):
                    return
        except Exception as error:
            if not self.stop_requested.is_set():
                self.failed.emit(str(error))


# ---------------------------------------------------------------------------
# One-off commands - used by the quick buttons when no shell is open
# ---------------------------------------------------------------------------

class RemoteExecWorker(QThread):
    """Run a list of one-off commands over a fresh SSH connection, stopping at the first failure."""
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, settings: dict, commands: list[str], parent=None):
        super().__init__(parent)
        self.settings = settings
        self.commands = commands

    def run(self) -> None:
        client = None
        try:
            client = create_ssh_client(self.settings)
            for command in self.commands:
                self.log.emit(f"$ {command}")
                code, out, err = exec_remote(client, command, timeout=_coerce_int(self.settings.get("ssh_timeout"), 12, 3, 300) * 3)
                if out.strip():
                    self.log.emit(out.rstrip())
                if err.strip():
                    self.log.emit("[stderr] " + err.rstrip())
                if code != 0:
                    self.failed.emit(f"Command exited with code {code}: {command}")
                    return
            self.finished_ok.emit()
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if client is not None:
                client.close()


# ---------------------------------------------------------------------------
# SFTP work
#
# One thread per operation: `operation` picks what run() does and `options`
# carries the extras for that operation (selected paths, dry run, backups,
# mirror directions).
# ---------------------------------------------------------------------------

class SftpWorker(QThread):
    """Listing, transferring, deleting, renaming, deploying and backing up."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    stage = pyqtSignal(str, int, int)

    def __init__(self, operation: str, settings: dict, options: dict, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.settings = settings
        self.options = options

    def run(self) -> None:
        """Connect, hand over to perform(), and always close the session.

        `stage` records how far we got, so a failure can come with advice that
        matches the real problem - a missing SFTP subsystem is not a bad password.
        """
        client = None
        sftp = None
        stage = "SSH connection"
        try:
            client = create_ssh_client(self.settings)
            stage = "SFTP subsystem startup"
            sftp = client.open_sftp()
            stage = "SFTP operation"
            result = self.perform(sftp, client)
            self.succeeded.emit(result)
        except Exception as error:
            if stage == "SFTP subsystem startup":
                self.failed.emit(
                    "SSH connected, but the server did not start its SFTP subsystem. "
                    f"Details: {error}\n\n"
                    "On OpenWrt, install the SFTP server package and reconnect:\n"
                    "apk update && apk add openssh-sftp-server\n"
                    "(On older opkg-based releases: opkg update && opkg install openssh-sftp-server.)"
                )
            else:
                self.failed.emit(f"{stage} failed: {error}")
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()

    def perform(self, sftp: paramiko.SFTPClient, client: paramiko.SSHClient):
        """Dispatch on the operation name; tree-returning ops hand back a fresh listing."""
        operation = self.operation
        remote_root = self.settings["remote_root"]

        if operation == "list":
            return self.read_remote_tree(sftp, remote_root)
        if operation == "deploy":
            return self.deploy(sftp, client, remote_root)
        if operation == "upload":
            self.upload_selected(sftp, remote_root, self.options["paths"])
            return self.read_remote_tree(sftp, remote_root)
        if operation == "download":
            self.download_selected(sftp, remote_root, self.options["paths"])
            return None
        if operation == "delete":
            self.delete_selected(sftp, remote_root, self.options["paths"])
            return self.read_remote_tree(sftp, remote_root)
        if operation == "rename":
            self.rename_remote(sftp, self.options["path"], self.options["new_name"])
            return self.read_remote_tree(sftp, remote_root)
        raise ValueError(f"Unknown operation: {operation}")

    def read_remote_tree(self, sftp: paramiko.SFTPClient, directory: str) -> list[dict]:
        """Recursive listing in the nested shape the remote tree widget expects.
        Symlinks are skipped, which also keeps a link loop from hanging the walk."""
        try:
            attributes = sftp.listdir_attr(directory)
        except OSError:
            return []

        entries = []
        for attribute in sorted(attributes, key=lambda item: item.filename.lower()):
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            is_directory = stat.S_ISDIR(attribute.st_mode)
            child_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(child_path, self.settings["remote_root"])
            entry = {
                "name": attribute.filename,
                "path": relative,
                "directory": is_directory,
                "size": int(getattr(attribute, "st_size", 0) or 0),
                "mtime": int(getattr(attribute, "st_mtime", 0) or 0),
                "children": [],
            }
            if is_directory:
                entry["children"] = self.read_remote_tree(sftp, child_path)
            entries.append(entry)
        return entries

    def ensure_remote_root(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        """Create the deploy target if the device does not have it yet."""
        make_remote_directories(sftp, remote_root)

    def upload_relative_paths(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
        ignore_spec: GitIgnoreSpec | None = None,
        skip_unchanged: bool = False,
    ) -> tuple[int, int]:
        """Upload a subset of the local tree and return (done, total).

        An empty selection means the whole source. Directories are created first
        so every file already has somewhere to land, and with skip_unchanged set
        this is exactly what a normal deploy does.
        """
        source = Path(self.settings["source"])
        self.ensure_remote_root(sftp, remote_root)
        directories, files = local_entries(source, ignore_spec, self.settings.get("show_hidden_files", False))
        selected_paths = self.normalize_paths(selected_paths)

        def included(path: str) -> bool:
            return any(
                not selected or path == selected or path.startswith(selected + "/")
                for selected in selected_paths
            )

        planned_dirs = [r for r in directories if included(r)]
        planned_files = [r for r in files if included(r)]
        skipped = 0
        if skip_unchanged and planned_files:
            planned_files, unchanged = self.split_changed_files(sftp, remote_root, planned_files)
            skipped = len(unchanged)
            if skipped:
                self.progress.emit(f"Skipping {skipped} unchanged file(s) (already up to date on the server)")
        total = len(planned_dirs) + len(planned_files)
        done = 0

        for relative in planned_dirs:
            make_remote_directories(sftp, remote_join(remote_root, relative))
            done += 1
            self.stage.emit("Preparing directories", done, total)
        for relative in planned_files:
            local_path = source / Path(*PurePosixPath(relative).parts)
            destination = remote_join(remote_root, relative)
            make_remote_directories(sftp, posixpath.dirname(destination))
            self.progress.emit(f"Uploading {relative}")
            sftp.put(str(local_path), destination)
            done += 1
            self.stage.emit(f"Uploading {relative}", done, total)
        return done, total

    # ------------------------------------------------------------------
    # Deciding what counts as changed
    # ------------------------------------------------------------------

    def remote_file_stats(self, sftp: paramiko.SFTPClient, directory: str) -> dict[str, tuple[int, float]]:
        """Recursive metadata-only scan: {relative path: (size, mtime)}.
        Uses listdir_attr so no file contents are ever downloaded."""
        stats: dict[str, tuple[int, float]] = {}
        try:
            attributes = sftp.listdir_attr(directory)
        except OSError:
            return stats
        for attribute in attributes:
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            full_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(full_path, self.settings["remote_root"])
            if stat.S_ISDIR(attribute.st_mode):
                stats.update(self.remote_file_stats(sftp, full_path))
            else:
                size = int(getattr(attribute, "st_size", 0) or 0)
                mtime = float(getattr(attribute, "st_mtime", 0) or 0)
                stats[relative] = (size, mtime)
        return stats

    @staticmethod
    def _digest_stream(handle) -> str:
        """Hash a binary file-like object in RAM, chunk by chunk (nothing hits the disk)."""
        hasher = hashlib.md5(usedforsecurity=False)
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
        return hasher.hexdigest()

    def file_identical(self, sftp: paramiko.SFTPClient, local_path: Path, remote_full: str) -> bool:
        """Compare file contents by streaming both sides into in-RAM digests."""
        try:
            with open(local_path, "rb") as handle:
                local_digest = self._digest_stream(handle)
            with sftp.open(remote_full, "rb") as handle:
                remote_digest = self._digest_stream(handle)
        except OSError:
            return False
        return local_digest == remote_digest

    def split_changed_files(
        self, sftp: paramiko.SFTPClient, remote_root: str, files: list[str]
    ) -> tuple[list[str], list[str]]:
        """Return (changed, unchanged) using remote metadata only; contents are hashed
        in RAM only for the ambiguous case where the size matches but the timestamp
        differs or is unknown. paramiko's put() preserves mtime, so previous deploys
        from this tool compare exactly."""
        source = Path(self.settings["source"])
        remote_stats = self.remote_file_stats(sftp, remote_root)
        changed: list[str] = []
        unchanged: list[str] = []
        for relative in files:
            remote = remote_stats.get(relative)
            if remote is None:
                changed.append(relative)
                continue
            local_path = source / Path(*PurePosixPath(relative).parts)
            try:
                info = local_path.stat()
            except OSError:
                changed.append(relative)
                continue
            remote_size, remote_mtime = remote
            if info.st_size != remote_size:
                changed.append(relative)
                continue
            if remote_mtime and abs(remote_mtime - info.st_mtime) <= 2:
                unchanged.append(relative)
                continue
            if self.file_identical(sftp, local_path, remote_join(remote_root, relative)):
                unchanged.append(relative)
            else:
                changed.append(relative)
        return changed, unchanged

    # ------------------------------------------------------------------
    # Deploy planning
    # ------------------------------------------------------------------

    def collect_deploy_plan(
        self, sftp: paramiko.SFTPClient, remote_root: str
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Return (dirs_to_create, files_to_upload, stale_files, stale_dirs)."""
        source = Path(self.settings["source"])
        ignore_spec = GitIgnoreSpec.from_lines(self.settings.get("deploy_ignore", []))
        directories, files = local_entries(source, ignore_spec, self.settings.get("show_hidden_files", False))
        expected = set(directories + files)

        try:
            remote_entries = self.remote_inventory(sftp, remote_root)
        except OSError:
            remote_entries = []

        stale_files = [
            path for path, is_dir in remote_entries
            if not is_dir and path not in expected and not ignore_spec.match_file(path)
        ]
        stale_dirs = [
            path for path, is_dir in remote_entries
            if is_dir and path not in expected and not ignore_spec.match_file(path + "/")
        ]
        return directories, files, stale_files, stale_dirs

    def collect_local_only(
        self, sftp: paramiko.SFTPClient, remote_root: str
    ) -> tuple[list[str], list[str]]:
        """Return (local_files, local_dirs) that exist locally but not on the server,
        honouring the deploy ignore patterns. Intended for the 'delete local files
        not on server' mirror direction."""
        source = Path(self.settings["source"])
        ignore_spec = GitIgnoreSpec.from_lines(self.settings.get("deploy_ignore", []))
        directories, files = local_entries(source, ignore_spec, self.settings.get("show_hidden_files", False))

        try:
            remote_paths = {path for path, _is_dir in self.remote_inventory(sftp, remote_root)}
        except OSError:
            remote_paths = set()

        local_only_files = [path for path in files if path not in remote_paths]
        local_only_dirs = [path for path in directories if path not in remote_paths]
        return local_only_files, local_only_dirs

    def delete_local_paths(self, relative_files: list[str], relative_dirs: list[str]) -> int:
        """Delete the given source-relative files and directories from the local tree.
        Deepest paths first so directories are empty by the time they are removed."""
        source = Path(self.settings["source"])
        removed = 0
        for relative in sorted(set(relative_files) | set(relative_dirs),
                               key=lambda item: item.count("/"), reverse=True):
            target = source / Path(*PurePosixPath(relative).parts)
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                    self.progress.emit(f"Removing local directory {relative}")
                elif target.exists():
                    target.unlink()
                    self.progress.emit(f"Removing local file {relative}")
                else:
                    continue
                removed += 1
            except OSError as error:
                self.progress.emit(f"[warn] could not remove local {relative}: {error}")
        return removed

    def deploy(self, sftp: paramiko.SFTPClient, client: paramiko.SSHClient, remote_root: str) -> list[dict]:
        """The full deploy: optional backups, incremental upload, mirror cleanup,
        post-deploy commands.

        A dry run stops early and returns the plan instead of touching anything.
        Local-only entries are collected before the upload, otherwise the fresh
        uploads would look like server files and survive the local cleanup.
        """
        mirror_remote = bool(self.options.get("mirror_remote", self.options.get("mirror")))
        mirror_local = bool(self.options.get("mirror_local"))

        if self.options.get("dry_run"):
            directories, files, stale_files, stale_dirs = self.collect_deploy_plan(sftp, remote_root)
            files, unchanged_files = self.split_changed_files(sftp, remote_root, files)
            local_files, local_dirs = self.collect_local_only(sftp, remote_root) if mirror_local else ([], [])
            return {
                "dry_run": True,
                "directories": directories,
                "files": files,
                "unchanged": unchanged_files,
                "stale_files": stale_files if mirror_remote else [],
                "stale_dirs": stale_dirs if mirror_remote else [],
                "would_delete": bool(mirror_remote),
                "local_files": local_files if mirror_local else [],
                "local_dirs": local_dirs if mirror_local else [],
                "would_delete_local": bool(mirror_local),
            }

        pre_upload_local_files, pre_upload_local_dirs = (
            self.collect_local_only(sftp, remote_root) if mirror_local else ([], [])
        )

        if self.options.get("backup_local"):
            self.backup_local(sftp, remote_root)

        if self.options.get("backup"):
            self.backup_remote(sftp, remote_root)

        ignore_spec = GitIgnoreSpec.from_lines(self.settings.get("deploy_ignore", []))
        self.upload_relative_paths(sftp, remote_root, [""], ignore_spec, skip_unchanged=True)

        if mirror_remote:
            _, _, stale_files, stale_dirs = self.collect_deploy_plan(sftp, remote_root)
            for relative in sorted(stale_files, key=lambda item: item.count("/"), reverse=True):
                self.progress.emit(f"Removing remote file {relative}")
                try:
                    sftp.remove(remote_join(remote_root, relative))
                except OSError:
                    pass
            for relative in sorted(stale_dirs, key=lambda item: item.count("/"), reverse=True):
                try:
                    self.progress.emit(f"Removing remote directory {relative}")
                    sftp.rmdir(remote_join(remote_root, relative))
                except OSError:
                    pass

        if mirror_local:
            self.delete_local_paths(pre_upload_local_files, pre_upload_local_dirs)
            self.progress.emit("Refreshing local tree after local cleanup")

        for command in self.settings.get("post_deploy_commands", []):
            if not command.strip():
                continue
            self.progress.emit(f"Post-deploy: {command}")
            try:
                code, out, err = exec_remote(client, command, timeout=30)
                if out.strip():
                    self.progress.emit(out.strip())
                if code != 0:
                    self.progress.emit(f"[warn] command exited {code}: {err.strip()}")
            except Exception as error:
                self.progress.emit(f"[warn] post-deploy command failed: {error}")

        return self.read_remote_tree(sftp, remote_root)

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------

    def backup_remote(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        """Copy the current remote tree to a timestamped folder on the device."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = posixpath.normpath(
            posixpath.join(self.settings.get("backup_directory", "/tmp/rasconf_backups"), f"backup-{stamp}")
        )
        self.progress.emit(f"Backing up {remote_root} to {backup_dir}")
        make_remote_directories(sftp, backup_dir)
        entries = self.remote_inventory(sftp, remote_root)
        total = len(entries)
        done = 0
        for relative, is_directory in entries:
            target = remote_join(backup_dir, relative)
            source_path = remote_join(remote_root, relative)
            if is_directory:
                make_remote_directories(sftp, target)
            else:
                make_remote_directories(sftp, posixpath.dirname(target))
                try:
                    attr = sftp.stat(source_path)
                    if int(getattr(attr, "st_size", 0) or 0) > 200 * 1024 * 1024:
                        self.progress.emit(f"Skipping large file in backup: {relative}")
                        done += 1
                        continue
                except OSError:
                    pass
                sftp.get(source_path, target)
            done += 1
            self.stage.emit(f"Backup {relative}", done, total)
        self.prune_remote_backups(sftp)

    def backup_local(self, sftp: paramiko.SFTPClient, remote_root: str) -> None:
        """Save a timestamped local copy of the current remote tree before deploying."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_dir = Path(self.settings.get("local_backup_directory", str(BASE_DIR / "backups"))).expanduser()
        backup_dir = base_dir / f"remote-{datetime.now().strftime('%Y%m%d')}-{stamp}"
        self.progress.emit(f"Saving local copy of {remote_root} to {backup_dir}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        entries = self.remote_inventory(sftp, remote_root)
        total = len(entries)
        done = 0
        for relative, is_directory in entries:
            if relative:
                target = backup_dir / Path(*PurePosixPath(relative).parts)
            else:
                target = backup_dir
            source_path = remote_join(remote_root, relative)
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    attr = sftp.stat(source_path)
                    if int(getattr(attr, "st_size", 0) or 0) > 200 * 1024 * 1024:
                        self.progress.emit(f"Skipping large file in local backup: {relative}")
                        done += 1
                        continue
                except OSError:
                    pass
                sftp.get(source_path, str(target))
            done += 1
            self.stage.emit(f"Local backup {relative}", done, total)
        self.prune_local_backups(base_dir)

    def prune_remote_backups(self, sftp: paramiko.SFTPClient) -> None:
        """Delete the oldest remote backup folders once there are more than the kept count."""
        keep = int(self.settings.get("keep_last_n_backups", 5))
        if keep <= 0:
            return
        base = posixpath.normpath(self.settings.get("backup_directory", "/tmp/rasconf_backups"))
        try:
            names = sorted(name for name in sftp.listdir(base) if name.startswith("backup-"))
        except OSError:
            return
        for old in names[:-keep]:
            try:
                self._remove_remote_tree(sftp, posixpath.join(base, old))
                self.progress.emit(f"Removed old remote backup: {old}")
            except OSError:
                pass

    def prune_local_backups(self, base_dir: Path) -> None:
        """Same as prune_remote_backups, for the copies saved on this PC."""
        keep = int(self.settings.get("keep_last_n_backups", 5))
        if keep <= 0:
            return
        try:
            backups = sorted(
                path for path in base_dir.iterdir() if path.is_dir() and path.name.startswith("remote-")
            )
        except OSError:
            return
        for old in backups[:-keep]:
            try:
                shutil.rmtree(old)
                self.progress.emit(f"Removed old local backup: {old.name}")
            except OSError:
                pass

    @staticmethod
    def _remove_remote_tree(sftp: paramiko.SFTPClient, path: str) -> None:
        """rm -r on the far side, deepest entries first."""
        for attribute in sftp.listdir_attr(path):
            if attribute.filename in {".", ".."}:
                continue
            child = posixpath.join(path, attribute.filename)
            if stat.S_ISDIR(attribute.st_mode) and not stat.S_ISLNK(attribute.st_mode):
                SftpWorker._remove_remote_tree(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    def remote_inventory(self, sftp: paramiko.SFTPClient, directory: str) -> list[tuple[str, bool]]:
        """Flat (relative path, is directory) list of everything under a remote folder."""
        inventory: list[tuple[str, bool]] = []
        try:
            attributes = sftp.listdir_attr(directory)
        except OSError:
            return inventory
        for attribute in attributes:
            if attribute.filename in {".", ".."} or stat.S_ISLNK(attribute.st_mode):
                continue
            full_path = posixpath.join(directory, attribute.filename)
            relative = posixpath.relpath(full_path, self.settings["remote_root"])
            is_directory = stat.S_ISDIR(attribute.st_mode)
            inventory.append((relative, is_directory))
            if is_directory:
                inventory.extend(self.remote_inventory(sftp, full_path))
        return inventory

    # ------------------------------------------------------------------
    # Single operations driven from the tree widgets
    # ------------------------------------------------------------------

    def upload_selected(
        self, sftp: paramiko.SFTPClient, remote_root: str, selected_paths: list[str]
    ) -> None:
        """Upload the highlighted items, always replacing what is already there."""
        self.upload_relative_paths(sftp, remote_root, selected_paths, None)

    def download_selected(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
    ) -> None:
        """Copy the highlighted remote items into a local folder, keeping the tree shape."""
        destination_root = Path(self.options["destination"]).resolve()
        destination_root.mkdir(parents=True, exist_ok=True)

        collected: list[str] = []

        def walk(relative: str) -> None:
            attributes = sftp.stat(remote_join(remote_root, relative))
            if stat.S_ISDIR(attributes.st_mode):
                for child in sftp.listdir_attr(remote_join(remote_root, relative)):
                    if child.filename in {".", ".."} or stat.S_ISLNK(child.st_mode):
                        continue
                    child_relative = posixpath.join(relative, child.filename) if relative else child.filename
                    if stat.S_ISDIR(child.st_mode):
                        walk(child_relative)
                    else:
                        collected.append(child_relative)
            else:
                collected.append(relative)

        for relative in self.normalize_paths(selected_paths):
            walk(relative)

        total = len(collected)
        for index, relative in enumerate(collected, start=1):
            safe_relative_path(relative)
            remote_path = remote_join(remote_root, relative)
            local_path = (destination_root / Path(*PurePosixPath(relative).parts)).resolve()
            if destination_root not in local_path.parents and local_path != destination_root:
                raise ValueError(f"Unsafe download path: {relative}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.progress.emit(f"Downloading {relative}")
            sftp.get(remote_path, str(local_path))
            self.stage.emit(f"Downloading {relative}", index, total)

    def delete_selected(
        self,
        sftp: paramiko.SFTPClient,
        remote_root: str,
        selected_paths: list[str],
    ) -> None:
        """Recursively delete the highlighted remote items."""
        paths = self.normalize_paths(selected_paths)
        if not paths or "" in paths:
            raise ValueError("Select one or more remote files or directories, not the remote root")

        def remove(relative: str) -> None:
            full_path = remote_join(remote_root, relative)
            attributes = sftp.stat(full_path)
            if stat.S_ISDIR(attributes.st_mode):
                for child in sftp.listdir_attr(full_path):
                    if child.filename in {".", ".."} or stat.S_ISLNK(child.st_mode):
                        continue
                    remove(posixpath.join(relative, child.filename))
                sftp.rmdir(full_path)
            else:
                sftp.remove(full_path)

        for relative in paths:
            self.progress.emit(f"Deleting remote path {relative}")
            remove(relative)

    def rename_remote(self, sftp: paramiko.SFTPClient, relative: str, new_name: str) -> None:
        """Rename within the item's own directory; the parent never changes."""
        safe_relative_path(relative)
        if not relative:
            raise ValueError("Cannot rename the remote root")
        if "/" in new_name or new_name in {".", "..", ""}:
            raise ValueError("New name must be a plain filename")
        source_path = remote_join(self.settings["remote_root"], relative)
        parent = posixpath.dirname(relative)
        target_relative = posixpath.join(parent, new_name) if parent else new_name
        target_path = remote_join(self.settings["remote_root"], target_relative)
        sftp.rename(source_path, target_path)

    @staticmethod
    def normalize_paths(paths: list[str]) -> list[str]:
        """Clean up a tree selection: validate, sort shallowest first and drop
        anything already covered by a selected parent directory."""
        normalized = sorted({safe_relative_path(path) for path in paths}, key=lambda item: (item.count("/"), item))
        result: list[str] = []
        for path in normalized:
            if not any(parent == "" or path.startswith(parent + "/") for parent in result):
                result.append(path)
        return result
