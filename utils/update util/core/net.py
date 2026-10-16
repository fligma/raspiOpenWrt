#!/usr/bin/env python3
"""The way to a device, and the screen it is typed into: the SSH workers, the
remote commands, and the terminal that turns the bytes coming back into lines.

The workers carry a connection and answer in signals; the screen knows escape
sequences and nothing about sockets. They are one file because every terminal
here is an SSH one, and what comes down the pipe has to be read the same way
at both ends.
"""

from __future__ import annotations


###############################################################################
# was manager_net.py
# Talk to the device: connections, path safety, remote exec and the background threads.
###############################################################################


import codecs
from pathlib import PurePosixPath
import posixpath
import queue
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from core.ui import _coerce_int
from core.const import KNOWN_HOSTS_FILE

if TYPE_CHECKING:
    # paramiko is half a second of imports and it is only needed the one time
    # a connection is actually opened - inside ``create_ssh_client``, which
    # reads it itself. Named here only for the annotations below, which a
    # module-wide ``from __future__ import annotations`` keeps from running.
    import paramiko

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
    import paramiko     # read on use, not at boot; see the note at the top
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


# --- a shell on a connection of its own ---

class OwnShellWorker(QThread):
    """One PTY shell that owns its whole connection: it opens the socket, pumps
    it both ways and closes it again, all on this thread.

    SSHShellWorker above is a view of the app's main session and can only ever
    be one of those. This is the other shape - a session in its own right - and
    it is what an addon that wants several terminals side by side needs: two of
    these to different boxes are two unrelated connections, and neither takes
    the app's own session down when it is closed.

    The settings dict is the app's usual shape (host, port, username and either
    a password or a key file); whatever it leaves out falls back to the normal
    defaults, so an addon only has to name the machine. ``state`` says what is
    attached, and never carries the password."""

    connected = pyqtSignal()
    received = pyqtSignal(str)
    failed = pyqtSignal(str)
    disconnected = pyqtSignal()

    #: What a short dict has to hold at minimum to reach a machine at all.
    DEFAULTS = {"port": 22, "username": "", "password": "", "key_file": "",
                "trust_unknown_host": True, "ssh_timeout": 12}

    def __init__(self, settings: dict, cols: int = 80, rows: int = 24,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        # A half-written dict from an addon is normal: fill the rest in.
        self.settings = dict(self.DEFAULTS)
        for name, value in dict(settings or {}).items():
            if value is not None and value != "":
                self.settings[name] = value
        self.settings["port"] = _coerce_int(self.settings.get("port"), 22, 1, 65535)
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        self.outgoing: queue.Queue = queue.Queue()
        self.stop_requested = threading.Event()
        self.channel: paramiko.Channel | None = None
        self.client: paramiko.SSHClient | None = None
        self.state: dict = {"connected": False,
                           "host": str(self.settings.get("host", "")),
                           "port": int(self.settings["port"]),
                           "username": str(self.settings.get("username", ""))}

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
        """Open the connection, then alternate reading output and flushing queued input; the short timeout keeps it responsive to typing and to stop."""
        try:
            if self.stop_requested.is_set():
                return
            self.client = create_ssh_client(self.settings)
            self.channel = self.client.invoke_shell(term="xterm-256color",
                                                    width=self.cols,
                                                    height=self.rows)
            self.channel.settimeout(0.1)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self.state["connected"] = True
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
                self.failed.emit(str(error) or type(error).__name__)
        finally:
            # Everything this worker opened goes with it: nothing of the app's.
            self.state["connected"] = False
            for handle in (self.channel, self.client):
                if handle is None:
                    continue
                try:
                    handle.close()
                except Exception:
                    pass
            self.channel = None
            self.client = None
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


###############################################################################
# was manager_term.py
# The terminal of the SSH tab: a small xterm-style screen the shell can drive.
#
# The widget keeps a grid of character cells and understands the escape
# sequences full-screen programs actually use - cursor addressing, scroll
# regions, the alternate screen that nano and friends switch to, colours, and
# the "where is your cursor?" query a program waits on before it draws. Keys
# typed while it has focus go out as the raw bytes a real terminal would send,
# so ``^G`` in nano is ``^G`` on the device.
#
# Nothing here knows about SSH or paramiko: the widget talks to the rest of
# the app through four signals - ``send_data`` for every byte the shell asked
# for or the user typed, ``resized`` when its grid changes shape, ``bell`` and
# ``title_changed`` for the polite noise - and through ``feed()`` for whatever
# arrives from the device.
#
###############################################################################


from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFocusEvent, QFont, QFontMetrics, QKeyEvent,
                         QMouseEvent, QPaintEvent, QPainter, QResizeEvent,
                         QWheelEvent)
from PyQt6.QtWidgets import QApplication, QScrollBar, QSizePolicy, QWidget

# Colours: a dark ground with a balanced 16-colour set (the Tango scheme), so
# every ANSI colour - the blue and the dim greys above all - reads on the dark
# background; then the standard 6x6x6 cube and grey ramp for 256-colour output.
# The terminal keeps its own look and does not follow the app theme.
DEFAULT_FG = "#e6e6e6"
DEFAULT_BG = "#14161a"
SELECTION_COLOR = QColor(70, 130, 200, 100)
PALETTE16 = ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b",
             "#06989a", "#d3d7cf", "#555753", "#ef2929", "#8ae234", "#fce94f",
             "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]

HISTORY_MAX = 2000          # rows kept above the screen when nothing is full-screen

# Cell attributes, packed into one int per cell.
BOLD, DIM, ITALIC, UNDERLINE, BLINK, REVERSE, HIDDEN, STRIKE = (1, 2, 4, 8, 16, 32, 64, 128)

# Cell colours: -1 default, 0..255 palette index, above that packed rrggbb+256.


def _palette() -> list[QColor]:
    """The standard 256-colour table."""
    colors = [QColor(name) for name in PALETTE16]
    steps = (0, 95, 135, 175, 215, 255)
    for red in steps:
        for green in steps:
            for blue in steps:
                colors.append(QColor(red, green, blue))
    for level in range(24):
        grey = 8 + level * 10
        colors.append(QColor(grey, grey, grey))
    return colors


PALETTE = _palette()


def _cell_color(value: int, default: QColor) -> QColor:
    if value < 0:
        return default
    if value < 256:
        return PALETTE[value]
    packed = value - 256
    return QColor((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF)


def _blend(color: QColor, toward: QColor, weight: float) -> QColor:
    """``color`` mixed ``weight`` of the way toward ``toward`` (0 keeps it, 1
    is fully ``toward``). Used to dim text toward the background rather than
    darken it to black, so a dim line is soft but still readable."""
    out = QColor(color)
    out.setRedF(color.redF() * (1 - weight) + toward.redF() * weight)
    out.setGreenF(color.greenF() * (1 - weight) + toward.greenF() * weight)
    out.setBlueF(color.blueF() * (1 - weight) + toward.blueF() * weight)
    return out


def _blank_cell(fg: int = -1, bg: int = -1, flags: int = 0) -> tuple:
    return (" ", fg, bg, flags)


def _blank_row(width: int) -> list:
    return [_blank_cell() for _ in range(width)]


class TerminalScreen(QWidget):
    """One cell grid, one cursor, and the escape brain that keeps them right."""

    send_data = pyqtSignal(str)          # bytes for the shell: keys, replies
    resized = pyqtSignal(int, int)       # new (columns, rows) for the PTY
    bell = pyqtSignal()
    title_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._font = QFont("Consolas", 10)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._metrics = QFontMetrics(self._font)
        self._cw = max(1, self._metrics.horizontalAdvance("W"))
        self._ch = max(1, self._metrics.height())
        self._ascent = self._metrics.ascent()

        self.cols, self.rows = 80, 24
        self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
        self.history: list[list] = []
        self.cr = self.cc = 0             # cursor, row then column
        self.pen = (-1, -1, 0)            # fg, bg, attribute bits
        self.saved_cursor = (0, 0, -1, -1, 0)
        self.top_margin, self.bottom_margin = 0, self.rows - 1
        self.tabs = set(range(8, self.cols, 8))
        self.wrap_next = False
        self.origin_mode = False
        self.autowrap = True
        self.app_cursor = False
        self.cursor_visible = True
        self.bracketed_paste = False
        self.alternate = False
        self._main_state = None           # everything the alternate screen displaced

        # Parser: one character at a time, so a chunk may end mid-sequence.
        self._state = "ground"
        self._collect = ""
        self._intermediate = ""
        self._private = ""
        self._osc_after_escape = False

        self._back = 0                    # rows of history currently showing
        self._bar = QScrollBar(Qt.Orientation.Vertical, self)
        self._bar.setVisible(False)
        self._bar.valueChanged.connect(self._scroll_to)
        self._bar_shown = False           # whether the bar is in the grid's way
        self._refitting = False           # guards the resize triggered by a bar toggle

        self._blink_on = True
        self._blink = QTimer(self)
        self._blink.setInterval(500)
        self._blink.timeout.connect(self._toggle_blink)

        self._select_from: tuple[int, int] | None = None
        self._select_to: tuple[int, int] | None = None
        self.title = ""

    # --- size and fonts ---

    def grid_size(self) -> tuple[int, int]:
        return self.cols, self.rows

    def set_font(self, font: QFont) -> None:
        """Adopt a font (the app's Consolas + size rule) and recompute cells."""
        self._font = QFont(font)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._metrics = QFontMetrics(self._font)
        self._cw = max(1, self._metrics.horizontalAdvance("W"))
        self._ch = max(1, self._metrics.height())
        self._ascent = self._metrics.ascent()
        self._refit()

    def font_size(self) -> int:
        return self._font.pointSize()

    def sizeHint(self) -> QSize:
        return QSize(self.cols * self._cw, self.rows * self._ch)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._refit()
        super().resizeEvent(event)

    def _refit(self) -> None:
        # Reserve the scrollbar's width while it is in the way so a column is
        # never painted underneath it: the bar is an overlay child, so width()
        # still spans the whole widget and the grid has to give up that room.
        cols = max(2, (self.width() - self._bar_width()) // self._cw)
        rows = max(2, self.height() // self._ch)
        if (cols, rows) == (self.cols, self.rows):
            return
        self._resize_grid(cols, rows)
        self.resized.emit(self.cols, self.rows)

    def _bar_width(self) -> int:
        """The room the vertical scrollbar takes when it is over the grid."""
        if self.alternate or not self.history:
            return 0
        return self._bar.sizeHint().width()

    def _resize_grid(self, cols: int, rows: int) -> None:
        """Carry the grid across a shape change, keeping what still fits."""
        moved = [(_row + [_blank_cell() for _ in range(cols)])[:cols]
                 for _row in self.lines[:rows]]
        moved += [_blank_row(cols) for _ in range(max(0, rows - len(self.lines)))]
        self.lines = moved
        self.cols, self.rows = cols, rows
        self.bottom_margin = rows - 1
        self.tabs = set(range(8, cols, 8))
        self.cr = min(self.cr, rows - 1)
        self.cc = min(self.cc, cols - 1)
        self._back = min(self._back, len(self.history))
        self._update_bar()
        self.update()

    # --- feeding output ---

    def feed(self, text: str) -> None:
        """One chunk of device output. The view follows it if it was at bottom."""
        for ch in text:
            self._step(ch)
        # Any output brings the cursor back for a full beat, the way a real
        # terminal does: a program that keeps the screen busy - nano redrawing
        # on every keystroke - is never left with a cursor caught mid-blink.
        self._blink_on = True
        if self._back:
            self._back = 0
            self._bar.setValue(0)
        self.update()

    def write_local(self, text: str) -> None:
        """A message from the app itself, drawn exactly like device output."""
        self.feed(text)

    def clear(self) -> None:
        """Screen and history away; the shell is where the user put it."""
        self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
        self.history.clear()
        self.cr = self.cc = 0
        self._back = 0
        self._update_bar()
        self.update()

    # --- the parser, one character at a time ---

    def _step(self, ch: str) -> None:
        state = self._state
        if state == "ground":
            self._ground(ch)
        elif state == "escape":
            self._escape(ch)
        elif state == "csi":
            self._csi_char(ch)
        elif state == "osc":
            self._osc_char(ch)
        elif state == "charset":       # ESC ( B and friends: swallow one more
            self._state = "ground"

    def _ground(self, ch: str) -> None:
        code = ord(ch)
        if ch == "\x1b":
            self._state = "escape"
        elif ch == "\n":
            self._line_feed()
        elif ch == "\r":
            self.cc = 0
            self.wrap_next = False
        elif ch == "\x08":
            self.cc = max(0, self.cc - 1)
            self.wrap_next = False
        elif ch == "\x09":
            self._tab()
        elif ch == "\x07":
            self.bell.emit()
        elif ch in ("\x0b", "\x0c"):
            self._line_feed()
        elif code < 32:
            pass                       # any other control byte: quietly ignored
        else:
            self._put(ch)

    def _escape(self, ch: str) -> None:
        if ch == "[":
            self._state = "csi"
            self._collect, self._intermediate, self._private = "", "", ""
        elif ch == "]":
            self._state = "osc"
            self._collect = ""
            self._osc_after_escape = False
        elif ch in ("(", ")", "#", "*", "+"):
            self._state = "charset"    # character set selection: understood, unused
        elif ch == "7":
            self.saved_cursor = (self.cr, self.cc, *self.pen)
            self._state = "ground"
        elif ch == "8":
            self._move(self.saved_cursor[0], self.saved_cursor[1])
            self.pen = tuple(self.saved_cursor[2:])
            self._state = "ground"
        elif ch == "D":                # IND: down one line, scrolling if needed
            self._line_feed()
            self._state = "ground"
        elif ch == "E":                # NEL: down and back to the margin
            self._line_feed()
            self.cc = 0
            self._state = "ground"
        elif ch == "M":                # RI: up one line, scrolling the other way
            self._reverse_index()
            self._state = "ground"
        elif ch == "c":                # soft reset: a fresh screen is enough
            self.clear()
            self.pen = (-1, -1, 0)
            self._state = "ground"
        else:
            self._state = "ground"     # = and > (keypad modes) change nothing here

    def _csi_char(self, ch: str) -> None:
        code = ord(ch)
        if 0x40 <= code <= 0x7E:
            self._csi_run(ch)
            self._state = "ground"
        elif 0x30 <= code <= 0x3F:
            if ch == "?" and not self._collect and not self._private:
                self._private = "?"
            else:
                self._collect += ch
        elif 0x20 <= code <= 0x2F:
            self._intermediate += ch
        else:                          # stray control byte inside a sequence
            self._state = "ground"
            self._ground(ch)

    def _osc_char(self, ch: str) -> None:
        if self._osc_after_escape:
            self._osc_after_escape = False
            if ch == "\\":
                self._osc_run()
                self._state = "ground"
                return
            # not a string terminator after all; carry on collecting
        if ch == "\x07":
            self._osc_run()
            self._state = "ground"
        elif ch == "\x1b":
            self._osc_after_escape = True
        elif len(self._collect) < 4096:
            self._collect += ch

    def _osc_run(self) -> None:
        head, _, body = self._collect.partition(";")
        if head in ("0", "1", "2"):    # icon and window title: keep the title
            self.title = body
            self.title_changed.emit(body)

    # --- the CSI dictionary ---

    def _numbers(self) -> list[int]:
        out = []
        for piece in self._collect.split(";"):
            digits = "".join(c for c in piece if c.isdigit())
            out.append(int(digits) if digits else 0)
        return out

    def _at(self, params: list[int], index: int, default: int = 1) -> int:
        return params[index] if index < len(params) and params[index] else default

    def _csi_run(self, final: str) -> None:
        params = self._numbers()
        private = self._private == "?"
        if self._intermediate == " " and final == "q":     # DECSCUSR cursor shape
            return
        if final == "H" or final == "f":                   # CUP / HVP
            self._move(self._at(params, 0) - 1, self._at(params, 1) - 1, address=True)
        elif final == "A":
            self._move(self.cr - max(1, params[0] if params else 1), self.cc)
        elif final == "B":
            self._move(self.cr + max(1, params[0] if params else 1), self.cc)
        elif final == "C":
            self._move(self.cr, self.cc + max(1, params[0] if params else 1))
        elif final == "D":
            self._move(self.cr, self.cc - max(1, params[0] if params else 1))
        elif final == "d":                                 # VPA
            self._move(self._at(params, 0) - 1, self.cc, address=True)
        elif final == "G" or final == "`":                 # HPA / HPB
            self._move(self.cr, self._at(params, 0) - 1, address=True)
        elif final == "J":
            self._erase_display(self._at(params, 0, 0))
        elif final == "K":
            self._erase_line(self._at(params, 0, 0))
        elif final == "L":
            self._insert_lines(self._at(params, 0))
        elif final == "M":
            self._delete_lines(self._at(params, 0))
        elif final == "P":
            self._delete_chars(self._at(params, 0))
        elif final == "@":
            self._insert_chars(self._at(params, 0))
        elif final == "X":
            self._erase_chars(self._at(params, 0))
        elif final == "S":
            self._scroll_region(-self._at(params, 0))
        elif final == "T":
            self._scroll_region(self._at(params, 0))
        elif final == "r":                                 # DECSTBM scroll region
            top = self._at(params, 0, 1) - 1
            bottom = self._at(params, 1, self.rows) - 1
            if top < bottom and bottom < self.rows:
                self.top_margin, self.bottom_margin = top, bottom
                self._move(self.top_margin, 0, address=True)
                self.saved_cursor = (self.cr, self.cc, *self.pen)
        elif final == "m":
            self._sgr(params)
        elif final == "h" or final == "l":
            self._set_mode(private, final == "h", params)
        elif final == "n" and not private:                 # DSR
            if params and params[0] == 6:
                self.send_data.emit(f"\x1b[{self.cr + 1};{self.cc + 1}R")
        elif final == "c":
            # DA: answers keep old-school programs from waiting forever.
            if private:
                self.send_data.emit("\x1b[?62;1;2;6;9c")
            elif params and params[0] == 0 and self._collect.startswith(">"):
                self.send_data.emit("\x1b[>1;95;0c")       # DA2
            else:
                self.send_data.emit("\x1b[?1;2c")          # DA1, a VT102 with options
        elif final == "s" and not private:
            self.saved_cursor = (self.cr, self.cc, *self.pen)
        elif final == "u" and not private:
            self._move(self.saved_cursor[0], self.saved_cursor[1])
            self.pen = tuple(self.saved_cursor[2:])
        elif final == "t":
            pass                                           # window manipulation: ignored
        elif final == "Z":
            self._back_tabs(self._at(params, 0))

    def _set_mode(self, private: bool, on: bool, params: list[int]) -> None:
        if not private:
            return                                          # IRM etc.: not emulated
        for mode in params or [0]:
            if mode == 1:
                self.app_cursor = on                        # application cursor keys
            elif mode == 6:
                self.origin_mode = on
                self._move(self.top_margin if on else 0, 0, address=True)
            elif mode == 7:
                self.autowrap = on
            elif mode == 12:
                pass                                        # blink, already fast enough
            elif mode == 25:
                self.cursor_visible = on
            elif mode == 1048:
                if on:
                    self.saved_cursor = (self.cr, self.cc, *self.pen)
                else:
                    self._move(self.saved_cursor[0], self.saved_cursor[1])
            elif mode in (47, 1047, 1049):
                self._alternate(on, full=mode == 1049)
            elif mode == 2004:
                self.bracketed_paste = on
            # mouse modes (1000..1016) and the rest: noted, not forwarded

    def _alternate(self, on: bool, full: bool) -> None:
        """The screen nano lives on: a fresh grid, the shell's grid kept aside."""
        if on and not self.alternate:
            saved = (self.lines, self.cr, self.cc, self.top_margin, self.bottom_margin,
                     self.saved_cursor, self.wrap_next)
            keep_modes = (self.app_cursor, self.autowrap, self.bracketed_paste)
            self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
            self.cr = self.cc = 0
            self.top_margin, self.bottom_margin = 0, self.rows - 1
            self.saved_cursor = (0, 0, -1, -1, 0)
            self.wrap_next = False
            if full:
                self.saved_cursor = (self.cr, self.cc, *self.pen)
            self._main_state = (saved, keep_modes)
            self.alternate = True
            self._blink_on = True
        elif on is False and self.alternate and self._main_state is not None:
            (saved, keep_modes) = self._main_state
            (self.lines, self.cr, self.cc, self.top_margin, self.bottom_margin,
             self.saved_cursor, self.wrap_next) = saved
            (self.app_cursor, self.autowrap, self.bracketed_paste) = keep_modes
            self._main_state = None
            self.alternate = False
        self._update_bar()
        self.update()

    # --- screen surgery ---

    def _move(self, row: int, col: int, address: bool = False) -> None:
        if address and self.origin_mode:
            row += self.top_margin
        self.cr = max(0, min(row, self.rows - 1))
        self.cc = max(0, min(col, self.cols - 1))
        self.wrap_next = False

    def _tab(self) -> None:
        stop = next((t for t in sorted(self.tabs) if t > self.cc), self.cols - 1)
        self.cc = min(stop, self.cols - 1)
        self.wrap_next = False

    def _back_tabs(self, count: int) -> None:
        for _ in range(max(1, count)):
            earlier = [t for t in self.tabs if t < self.cc]
            self.cc = max(earlier) if earlier else 0

    def _put(self, ch: str) -> None:
        if self.wrap_next:
            # The character that is already on the row filled it, so the line
            # turns here - and the pending wrap is spent with it. Left standing,
            # every character after the first turn would take a row of its own,
            # which is what a cursor that "gets behind" the typing really is.
            self.wrap_next = False
            self.cc = 0
            self._line_feed()
        if self.bottom_margin < self.rows - 1 and self.cr > self.bottom_margin:
            self.cr = self.bottom_margin
        elif self.origin_mode and self.cr < self.top_margin:
            self.cr = self.top_margin
        self.lines[self.cr][self.cc] = (ch, *self.pen)
        if self.cc + 1 >= self.cols:
            if self.autowrap:
                self.cc = self.cols - 1
                self.wrap_next = True
        else:
            self.cc += 1

    def _line_feed(self) -> None:
        if self.cr == self.bottom_margin:
            self._scroll_up(1, force=True)
        elif self.cr < self.rows - 1:
            self.cr += 1

    def _reverse_index(self) -> None:
        if self.cr == self.top_margin:
            self._scroll_down(1, force=True)
        elif self.cr > 0:
            self.cr -= 1

    def _scroll_up(self, count: int, force: bool = False) -> None:
        """Roll the region up: lines leave at the top, blanks arrive at the bottom."""
        top, bottom = self.top_margin, self.bottom_margin
        count = min(count, bottom - top + 1)
        gone = [row[:] for row in self.lines[top:top + count]]
        for _ in range(count):
            del self.lines[top]
            self.lines.insert(bottom, _blank_row(self.cols))
        if top == 0 and not self.alternate and (force or bottom == self.rows - 1):
            self.history.extend(gone)
            if len(self.history) > HISTORY_MAX:
                del self.history[:len(self.history) - HISTORY_MAX]
            self._update_bar()

    def _scroll_down(self, count: int, force: bool = False) -> None:
        top, bottom = self.top_margin, self.bottom_margin
        count = min(count, bottom - top + 1)
        for _ in range(count):
            del self.lines[bottom]
            self.lines.insert(top, _blank_row(self.cols))

    def _scroll_region(self, delta: int) -> None:
        """SU (positive) scrolls content up, SD down, inside the margins."""
        if delta > 0:
            self._scroll_up(delta)
        elif delta < 0:
            self._scroll_down(-delta)

    def _insert_lines(self, count: int) -> None:
        if not (self.top_margin <= self.cr <= self.bottom_margin):
            return
        for _ in range(min(count, self.bottom_margin - self.cr + 1)):
            del self.lines[self.bottom_margin]
            self.lines.insert(self.cr, _blank_row(self.cols))

    def _delete_lines(self, count: int) -> None:
        if not (self.top_margin <= self.cr <= self.bottom_margin):
            return
        for _ in range(min(count, self.bottom_margin - self.cr + 1)):
            del self.lines[self.cr]
            self.lines.insert(self.bottom_margin, _blank_row(self.cols))

    def _insert_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        blank = _blank_cell()
        for _ in range(min(count, self.cols - self.cc)):
            row.insert(self.cc, blank)
            row.pop()

    def _delete_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        for _ in range(min(count, self.cols - self.cc)):
            del row[self.cc]
            row.append(_blank_cell())

    def _erase_chars(self, count: int) -> None:
        row = self.lines[self.cr]
        blank = _blank_cell()
        for col in range(self.cc, min(self.cc + max(1, count), self.cols)):
            row[col] = blank

    def _erase_line(self, mode: int) -> None:
        # Erased cells take the pen's bg, so a reverse line clears to the margin.
        blank = _blank_cell(bg=self.pen[1])
        row = self.lines[self.cr]
        if mode == 0:
            for col in range(self.cc, self.cols):
                row[col] = blank
        elif mode == 1:
            for col in range(0, min(self.cc + 1, self.cols)):
                row[col] = blank
        else:
            self.lines[self.cr] = [_blank_cell(bg=self.pen[1]) for _ in range(self.cols)]

    def _erase_display(self, mode: int) -> None:
        blank = _blank_cell(bg=self.pen[1])
        if mode == 0:
            self._erase_line(0)
            for row in self.lines[self.cr + 1:]:
                row[:] = [blank] * self.cols
        elif mode == 1:
            self._erase_line(1)
            for row in self.lines[:self.cr]:
                row[:] = [blank] * self.cols
        else:
            self.lines = [_blank_row(self.cols) for _ in range(self.rows)]
            if mode == 3:
                self.history.clear()
                self._update_bar()

    def _blank_cell(self, fg: int = -1, bg: int = -1, flags: int = 0) -> tuple:
        return _blank_cell(fg, bg, flags)

    # --- colours and attributes ---

    def _sgr(self, params: list[int]) -> None:
        fg, bg, flags = self.pen
        index = 0
        values = params or [0]
        while index < len(values):
            code = values[index]
            if code == 0:
                fg, bg, flags = -1, -1, 0
            elif code == 1:
                flags |= BOLD
            elif code == 2:
                flags |= DIM
            elif code == 3:
                flags |= ITALIC
            elif code == 4:
                flags |= UNDERLINE
            elif code == 5 or code == 6:
                flags |= BLINK
            elif code == 7:
                flags |= REVERSE
            elif code == 8:
                flags |= HIDDEN
            elif code == 9:
                flags |= STRIKE
            elif code == 21:
                flags &= ~UNDERLINE
            elif code in (22, 24):
                flags &= ~(BOLD | DIM) if code == 22 else ~UNDERLINE
            elif code == 23:
                flags &= ~ITALIC
            elif code == 27:
                flags &= ~REVERSE
            elif code == 28:
                flags &= ~HIDDEN
            elif code == 29:
                flags &= ~STRIKE
            elif 30 <= code <= 37:
                fg = code - 30
            elif code == 38:
                fg, index = self._extended_color(values, index, fg)
            elif code == 39:
                fg = -1
            elif 40 <= code <= 47:
                bg = code - 40
            elif code == 48:
                bg, index = self._extended_color(values, index, bg)
            elif code == 49:
                bg = -1
            elif 90 <= code <= 97:
                fg = code - 82                       # bright 8..15
            elif 100 <= code <= 107:
                bg = code - 92
            # 53 overline and the like: noticed, not drawn
            index += 1
        self.pen = (fg, bg, flags)

    def _extended_color(self, values: list[int], index: int, current: int) -> tuple[int, int]:
        """38/48 with its sub-parameters; returns the new pen value and cursor."""
        kind = values[index + 1] if index + 1 < len(values) else 5
        if kind == 5:
            number = values[index + 2] if index + 2 < len(values) else 0
            return number, index + 2
        if kind == 2:
            red = values[index + 2] if index + 2 < len(values) else 0
            green = values[index + 3] if index + 3 < len(values) else 0
            blue = values[index + 4] if index + 4 < len(values) else 0
            return 256 + (red << 16 | green << 8 | blue), index + 4
        return current, index

    # --- scrolling the view back into history ---

    def _update_bar(self) -> None:
        room = 0 if self.alternate else len(self.history)
        self._bar.setMaximum(room)
        self._bar.setVisible(room > 0)
        self._bar.setGeometry(self.width() - self._bar.sizeHint().width() - 2, 2,
                              self._bar.sizeHint().width(), self.height() - 4)
        if self._back > room:
            self._back = room
        self._bar.setValue(self._back)
        shown = room > 0
        if shown != self._bar_shown and not self._refitting:
            # The bar has just come over the grid (or left it): re-lay the grid
            # once so no column hides under it, or sits wasted beside it. The
            # flag keeps this off the _refit -> _resize_grid -> _update_bar loop.
            self._bar_shown = shown
            self._refitting = True
            try:
                self._refit()
            finally:
                self._refitting = False

    def _scroll_to(self, value: int) -> None:
        self._back = value
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.history or self.alternate:
            return
        steps = 3 * (1 if event.angleDelta().y() < 0 else -1)
        self._back = max(0, min(len(self.history), self._back + steps))
        self._bar.setValue(self._back)
        self.update()
        event.accept()

    def _visible_rows(self) -> list:
        """The lines under the window, history first when scrolled back."""
        history = [] if self.alternate else self.history
        total = history + self.lines
        end = len(total) - self._back
        start = max(0, end - self.rows)
        shown = total[start:end]
        return [_blank_row(self.cols) for _ in range(self.rows - len(shown))] + shown

    def _view_top_index(self) -> int:
        history = [] if self.alternate else self.history
        total = history + self.lines
        return max(0, len(total) - self._back - self.rows)

    # --- drawing ---

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), QColor(DEFAULT_BG))
        rows = self._visible_rows()
        view_top = self._view_top_index()
        fonts: dict[int, QFont] = {}
        for row_index, row in enumerate(rows):
            y = row_index * self._ch
            col = 0
            while col < len(row):
                cell = row[col]
                fg, bg, flags = cell[1], cell[2], cell[3]
                run = col
                while run + 1 < len(row) and (row[run + 1][1], row[run + 1][2], row[run + 1][3]) == (fg, bg, flags):
                    run += 1
                # background first, then the glyphs of the run in its font;
                # reverse video swaps the two, defaults crossing over
                if flags & REVERSE:
                    bg_color = _cell_color(fg, QColor(DEFAULT_FG))
                    text_color = _cell_color(bg, QColor(DEFAULT_BG))
                else:
                    bg_color = _cell_color(bg, QColor(DEFAULT_BG))
                    text_color = _cell_color(fg, QColor(DEFAULT_FG))
                    if flags & BOLD and 0 <= fg < 8:
                        text_color = PALETTE[fg + 8]      # bold 0..7 picks the bright colour
                    if flags & DIM:
                        text_color = _blend(text_color, bg_color, 0.4)
                if bg_color != QColor(DEFAULT_BG):
                    painter.fillRect(QRect(col * self._cw, y, (run - col + 1) * self._cw, self._ch), bg_color)
                text = "".join(row[c][0] for c in range(col, run + 1)).rstrip()
                if flags & HIDDEN:
                    text = ""
                if text:
                    font = fonts.get(flags)
                    if font is None:
                        font = self._font_for(flags, fonts)
                    painter.setFont(font)
                    painter.setPen(text_color)
                    place = (Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignBottom)
                    if self._runs_straight(font, text):
                        painter.drawText(
                            QRect(col * self._cw + 1, y,
                                  (run - col + 1) * self._cw, self._ch),
                            place, text)
                    else:
                        # One glyph per cell, each at its own grid position.
                        # A run drawn in one go is laid out by the font's own
                        # advances, and a character the monospace face does not
                        # carry - a box-drawing line, a check mark, an
                        # ideograph - comes from a fallback with a width of its
                        # own, which slides every glyph after it sideways. The
                        # cursor is placed by counting cells, so the two
                        # disagree and the block lands behind the words that
                        # were actually typed, further back with every odd
                        # glyph on the line.
                        for offset, glyph in enumerate(text):
                            if glyph == " ":
                                continue
                            painter.drawText(
                                QRect((col + offset) * self._cw + 1, y,
                                      self._cw, self._ch), place, glyph)
                col = run + 1
        self._paint_selection(painter, view_top, rows)
        self._paint_cursor(painter)
        painter.end()

    def _font_for(self, flags: int, cache: dict) -> QFont:
        font = QFont(self._font)
        font.setBold(bool(flags & BOLD))
        font.setItalic(bool(flags & ITALIC))
        font.setUnderline(bool(flags & UNDERLINE))
        font.setStrikeOut(bool(flags & STRIKE))
        cache[flags] = font
        return font

    def _runs_straight(self, font: QFont, text: str) -> bool:
        """Whether this run would sit on the grid if it were drawn in one go.

        Measured with the font actually about to draw it: an ordinary line of
        characters costs a single ``drawText``, which is how the whole screen is
        painted, and only the lines carrying a glyph the face does not own pay
        for being placed cell by cell.
        """
        if not text:
            return True
        return QFontMetrics(font).horizontalAdvance(text) == len(text) * self._cw

    def _paint_selection(self, painter: QPainter, view_top: int, rows: list) -> None:
        if self._select_from is None or self._select_to is None:
            return
        first, last = self._selection_span()
        for absolute in range(first[0], last[0] + 1):
            row_index = absolute - view_top
            if not (0 <= row_index < len(rows)):
                continue
            start_col = 0
            end_col = self.cols - 1
            if absolute == first[0]:
                start_col = first[1]
            if absolute == last[0]:
                end_col = last[1]
            if start_col > end_col:
                continue
            painter.fillRect(QRect(start_col * self._cw, row_index * self._ch,
                                   (end_col - start_col + 1) * self._cw, self._ch),
                             SELECTION_COLOR)

    def _paint_cursor(self, painter: QPainter) -> None:
        if not self.cursor_visible or self._back:
            return
        if not self._blink_on and self.hasFocus():
            return
        rect = QRect(self.cc * self._cw, self.cr * self._ch, self._cw, self._ch)
        if self.hasFocus():
            # a solid block in the cell's own colours, so it reads on any background
            cell = self.lines[self.cr][self.cc]
            if cell[3] & REVERSE:
                fill, glyph = _cell_color(cell[2], QColor(DEFAULT_BG)), _cell_color(cell[1], QColor(DEFAULT_FG))
            else:
                fill, glyph = _cell_color(cell[1], QColor(DEFAULT_FG)), _cell_color(cell[2], QColor(DEFAULT_BG))
            painter.fillRect(rect, fill)
            painter.setPen(glyph)
            painter.drawText(rect.adjusted(1, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, cell[0])
        else:
            # unfocused: a hollow box, drawn against the cell's own colour so it
            # stays visible on any background, light or dark
            cell = self.lines[self.cr][self.cc]
            base = _cell_color(cell[2], QColor(DEFAULT_BG))
            edge = QColor(DEFAULT_FG) if base.lightnessF() < 0.5 \
                else QColor(30, 30, 30)
            painter.setPen(edge)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    # --- selection ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_from = self._select_to = self._cell_at(event.position().toPoint())
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._select_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._select_to = self._cell_at(event.position().toPoint())
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        cell = self._cell_at(event.position().toPoint())
        absolute = self._view_top_index() + cell[0]
        history = [] if self.alternate else self.history
        total = history + self.lines
        if 0 <= absolute < len(total):
            row = total[absolute]
            col = cell[1]
            if not row[col][0].strip():
                return
            start = end = col
            word = lambda ch: ch.isalnum() or ch in "._-/"
            while start > 0 and word(row[start - 1][0]):
                start -= 1
            while end + 1 < len(row) and word(row[end + 1][0]):
                end += 1
            self._select_from = (cell[0], start)
            self._select_to = (cell[0], end)
            self.update()

    def _cell_at(self, point: QPoint) -> tuple[int, int]:
        row = max(0, min(self.rows - 1, point.y() // self._ch))
        col = max(0, min(self.cols - 1, point.x() // self._cw))
        return (int(row), int(col))

    def _selection_span(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Selected cells as (absolute row, col) ends, first always before last."""
        view_top = self._view_top_index()
        a = (view_top + self._select_from[0], self._select_from[1])
        b = (view_top + self._select_to[0], self._select_to[1])
        if a > b:
            a, b = b, a
        return a, b

    def selected_text(self) -> str:
        if self._select_from is None or self._select_to is None:
            return ""
        first, last = self._selection_span()
        history = [] if self.alternate else self.history
        total = history + self.lines
        out: list[str] = []
        for absolute in range(first[0], last[0] + 1):
            if not (0 <= absolute < len(total)):
                continue
            row = total[absolute]
            start = first[1] if absolute == first[0] else 0
            end = last[1] if absolute == last[0] else len(row) - 1
            out.append("".join(row[col][0] for col in range(start, end + 1)).rstrip())
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)

    # --- typing ---

    def focusInEvent(self, event: QFocusEvent) -> None:
        self._blink_on = True
        self._blink.start()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._blink.stop()
        self._blink_on = True
        self.update()
        super().focusOutEvent(event)

    def _toggle_blink(self) -> None:
        if self.cursor_visible:
            self._blink_on = not self._blink_on
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Clipboard keys carry the shift so they cannot eat ^C and ^V.
        if ctrl and shift and key == Qt.Key.Key_C:
            text = self.selected_text()
            if text:
                QApplication.clipboard().setText(text)
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert):
            self._paste(QApplication.clipboard().text())
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock):
            return

        data = self._key_bytes(key, mods, event.text())
        if data is None:
            return
        if self._back:
            self._back = 0
            self._bar.setValue(0)
        self.send_data.emit(data)

    def _paste(self, text: str) -> None:
        if not text:
            return
        if self.bracketed_paste:
            self.send_data.emit("\x1b[200~" + text.replace("\r\n", "\r").replace("\n", "\r") + "\x1b[201~")
        else:
            self.send_data.emit(text.replace("\r\n", "\r").replace("\n", "\r"))

    def _key_bytes(self, key: int, mods: Qt.KeyboardModifier, text: str) -> str | None:
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        modifier_code = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return "\r"
        if key == Qt.Key.Key_Tab:
            return "\x1b[Z" if shift else "\t"
        if key == Qt.Key.Key_Backtab:
            return "\x1b[Z"
        if key == Qt.Key.Key_Backspace:
            return "\x08" if ctrl else "\x7f"
        if key == Qt.Key.Key_Escape:
            return "\x1b\x1b" if alt else "\x1b"

        arrows = {Qt.Key.Key_Left: "D", Qt.Key.Key_Right: "C",
                  Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B"}
        if key in arrows:
            letter = arrows[key]
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}{letter}"
            return (f"\x1bO{letter}" if self.app_cursor else f"\x1b[{letter}")
        if key == Qt.Key.Key_Home:
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}H"
            return "\x1bOH" if self.app_cursor else "\x1b[H"
        if key == Qt.Key.Key_End:
            if modifier_code > 1:
                return f"\x1b[1;{modifier_code}F"
            return "\x1bOF" if self.app_cursor else "\x1b[F"
        simple = {Qt.Key.Key_Insert: "2", Qt.Key.Key_Delete: "3",
                  Qt.Key.Key_PageUp: "5", Qt.Key.Key_PageDown: "6"}
        if key in simple:
            if modifier_code > 1:
                return f"\x1b[{simple[key]};{modifier_code}~"
            return f"\x1b[{simple[key]}~"
        function = {Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ",
                    Qt.Key.Key_F3: "\x1bOR", Qt.Key.Key_F4: "\x1bOS",
                    Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~",
                    Qt.Key.Key_F7: "\x1b[18~", Qt.Key.Key_F8: "\x1b[19~",
                    Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~",
                    Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~"}
        if key in function:
            return function[key]

        # Ctrl+letter is the control byte, settled before the fall-through below.
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key - Qt.Key.Key_A + 1)
        if ctrl:
            literal = {Qt.Key.Key_BracketLeft: "\x1b", Qt.Key.Key_BracketRight: "\x1d",
                       Qt.Key.Key_Backslash: "\x1c", Qt.Key.Key_Space: "\x00",
                       Qt.Key.Key_Question: "\x7f"}
            if key in literal:
                return literal[key]

        if text:
            ch = text[0]
            if ctrl and len(text) == 1 and 32 <= ord(ch) < 127:
                return "\x00" if ch == " " else chr(ord(ch) & 0x1F)
            if ord(ch) >= 32:
                return ("\x1b" + ch) if alt else ch
        return None
