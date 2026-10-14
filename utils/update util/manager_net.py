#!/usr/bin/env python3
"""Talk to the device: connections, path safety, remote exec and the background threads."""

from __future__ import annotations

import codecs
from pathlib import PurePosixPath
import posixpath
import queue
import threading
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal
import paramiko

from manager_config import _coerce_int
from manager_const import KNOWN_HOSTS_FILE

# --- path helpers ---
# Paths sit under the remote root; ../.. can't escape it.

def safe_relative_path(value: str) -> str:
    """Validate a source-relative path, rejecting absolute paths and traversal."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {value}")
    return path.as_posix() if value else ""


def remote_join(root: str, relative: str) -> str:
    """Append a validated relative path to the remote root."""
    safe_relative_path(relative)
    return posixpath.join(root, relative) if relative else root


def make_remote_directories(sftp: paramiko.SFTPClient, path: str) -> None:
    """mkdir -p on the far side, skipping segments that already exist."""
    if not path.startswith("/"):
        raise ValueError("The remote directory must be an absolute path")
    current = "/"
    for part in path.strip("/").split("/"):
        if not part:
            continue
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


# --- connecting ---

def credential_id(username: str, host: str, port: int) -> str:
    """Key used to look a password up in the OS credential store."""
    return f"{username}@{host}:{port}"


def create_ssh_client(settings: dict) -> paramiko.SSHClient:
    """Open and authenticate a connection from the profile settings; host-key policy follows 'trust unknown host'."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if KNOWN_HOSTS_FILE.exists():
        client.load_host_keys(str(KNOWN_HOSTS_FILE))
    if settings["trust_unknown_host"]:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    password = settings["password"] or None
    key_file = settings["key_file"] or None
    timeout = _coerce_int(settings.get("ssh_timeout"), 12, 3, 300)
    # Hunt agent/default keys only if the user gave none; else
    # paramiko tries every key on file before failing.
    client.connect(
        hostname=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=password,
        key_filename=key_file,
        look_for_keys=not password and not key_file,
        allow_agent=not password and not key_file,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    if settings["trust_unknown_host"]:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(KNOWN_HOSTS_FILE))
    return client


def exec_remote(client: paramiko.SSHClient, command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run one non-interactive command, returning (code, out, err); both streams read to EOF so a full buffer can't deadlock."""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    del stdin
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


# --- the app's main connection ---

class SharedConnection:
    """One long-lived SSH session the whole app shares, with an SFTP channel; a re-entrant lock serialises the single SFTP session."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.client: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self.key = ""

    @staticmethod
    def _key_of(settings: dict) -> str:
        return credential_id(str(settings.get("username", "")),
                             str(settings.get("host", "")),
                             int(settings.get("port", 22)))

    def active(self) -> bool:
        """Is a live session open right now?"""
        with self.lock:
            if self.client is None:
                return False
            transport = self.client.get_transport()
            return bool(transport is not None and transport.is_active())

    def open(self, settings: dict) -> None:
        """Connect, keeping an existing same-device session. Blocking: call from a worker thread; a session for another host/user is closed first."""
        with self.lock:
            key = self._key_of(settings)
            if self.active():
                if self.key == key:
                    return
                self._close_locked()
            self.client = create_ssh_client(settings)
            try:
                self.sftp = self.client.open_sftp()
            except Exception:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
                raise
            self.key = key

    def run(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """One non-interactive command over the shared session."""
        with self.lock:
            if not self.active():
                raise RuntimeError("the app's main SSH session is not open")
            return exec_remote(self.client, command, timeout)

    def open_shell(self, cols: int, rows: int) -> paramiko.Channel:
        """A PTY channel on the main session, for the terminal tab to attach to.

        The channel belongs to the session, not to the caller: closing it never
        takes the connection, or the SFTP channel, down with it."""
        with self.lock:
            if not self.active():
                raise RuntimeError("the app's main connection is not open")
            return self.client.invoke_shell(term="xterm-256color",
                                            width=cols, height=rows)

    def close(self) -> None:
        # A bounded grab: a connect killed mid-run can leave this lock held,
        # and the closing app must not wait on a thread that no longer is.
        if not self.lock.acquire(True, timeout=5):
            return
        try:
            self._close_locked()
        finally:
            self.lock.release()

    def _close_locked(self) -> None:
        for handle in (self.sftp, self.client):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                pass
        self.sftp = None
        self.client = None
        self.key = ""


class SharedSessionWorker(QThread):
    """Open the shared session off the GUI thread and report the outcome."""

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, shared: SharedConnection, settings: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shared = shared
        self.settings = settings

    def run(self) -> None:
        if self.isInterruptionRequested():
            return                 # the app closed while this was queued
        try:
            self.shared.open(self.settings)
        except Exception as error:
            if self.isInterruptionRequested():
                return             # a cancel at close is not an error news
            self.failed.emit(str(error) or type(error).__name__)
        else:
            if self.isInterruptionRequested():
                return
            self.succeeded.emit()


# --- interactive shell ---

class SSHShellWorker(QThread):
    """One PTY channel of the app's main connection, pumped both ways until stopped.

    The shell is a view of the main session rather than a connection of its
    own: the session is opened by the Connect button, or at startup, and by
    nothing else - an addon cannot bring it up. Closing the shell leaves the
    session, and SFTP, running. The GUI queues input, never writes the socket."""

    connected = pyqtSignal()
    received = pyqtSignal(str)
    failed = pyqtSignal(str)
    disconnected = pyqtSignal()
    command_finished = pyqtSignal(int, str)

    def __init__(self, shared: SharedConnection, cols: int = 80, rows: int = 24,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shared = shared
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        self.outgoing: queue.Queue = queue.Queue()
        self.stop_requested = threading.Event()
        self.channel: paramiko.Channel | None = None

    def send(self, text: str) -> None:
        """Queue raw text for the shell, exactly as typed - no Enter added."""
        self.outgoing.put(text)

    def send_line(self, text: str) -> None:
        """Queue a line for the shell; the Enter key is added for you."""
        self.outgoing.put(text + "\n")

    def resize(self, cols: int, rows: int) -> None:
        """Queue a new terminal shape; the PTY follows on the worker thread."""
        self.outgoing.put(("resize", int(cols), int(rows)))

    def stop(self) -> None:
        """Ask for a shutdown; closing the channel also breaks any blocked read."""
        self.stop_requested.set()
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass

    def run(self) -> None:
        """Attach to the main session, then alternate reading output and flushing queued input; the short timeout keeps it responsive to stop and typing."""
        try:
            if self.stop_requested.is_set():
                return
            self.channel = self.shared.open_shell(self.cols, self.rows)
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
                if isinstance(outgoing, tuple):       # a queued resize
                    self.cols, self.rows = outgoing[1], outgoing[2]
                    self.channel.resize_pty(width=self.cols, height=self.rows)
                    continue
                self.channel.sendall(outgoing.encode("utf-8"))
        except Exception as error:
            if not self.stop_requested.is_set():
                self.failed.emit(str(error))
        finally:
            # Only the channel goes: the session underneath belongs to the app.
            if self.channel is not None:
                try:
                    self.channel.close()
                except Exception:
                    pass
            self.disconnected.emit()


# --- reboot wait (the SSH tab's Reboot button) ---

class RebootWaitWorker(QThread):
    """Wait out a device reboot and retry SSH until it answers; optionally fire the reboot over a fresh connection first."""

    status = pyqtSignal(str)
    connected = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, settings: dict, initial_wait: int, retry_interval: int,
                 reboot_command: str | None = None, parent: QObject | None = None) -> None:
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
            # The device drops the socket on shutdown; that's the point.
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


# --- one-off commands (quick buttons, no shell) ---

class RemoteExecWorker(QThread):
    """Run a list of one-off commands, over the main connection when one is open and over a fresh one otherwise; stops at the first failure."""
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, settings: dict, commands: list[str],
                 shared: SharedConnection | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.commands = commands
        self.shared = shared

    def _run_one(self, command: str, timeout: int) -> tuple[int, str, str]:
        """One command, on the app's session if this worker was given a live one."""
        if self.shared is not None and self.shared.active():
            return self.shared.run(command, timeout)
        client = create_ssh_client(self.settings)
        try:
            return exec_remote(client, command, timeout)
        finally:
            client.close()

    def run(self) -> None:
        try:
            for command in self.commands:
                if self.isInterruptionRequested():
                    return         # the app is closing; run nothing more
                self.log.emit(f"$ {command}")
                code, out, err = self._run_one(
                    command, _coerce_int(self.settings.get("ssh_timeout"), 12, 3, 300) * 3)
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
