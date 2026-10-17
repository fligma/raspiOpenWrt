#!/usr/bin/env python3

"""The main window: menu, tab shell, connection panel, Settings tab, profiles and shutdown."""


from __future__ import annotations

from datetime import datetime
import http.client
import json
from pathlib import Path
import posixpath
import threading
from typing import Any

from PyQt6.QtCore import QEvent, QObject, QSize, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QPixmap, QResizeEvent, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.addon import AddonManager, connection_choice
from core.addon import AddonDevWidget
from core.views import BrowserMixin, cert_bypass_active, set_cert_bypass
from core.ui import (
    active_profile_name,
    create_profile,
    delete_profile,
    export_document,
    import_app_config,
    load_app_config,
    load_command_history,
    profile_names,
    rename_profile,
    save_app_config_static,
    set_active_profile,
)
from core.const import (
    APP_VERSION,
    BASE_DIR,
    DEFAULT_PROFILE,
    ICON_FILES,
    KEYRING_SERVICE,
    LOG_FILE,
    PROJECT_LICENSE_URL,
    PROJECT_RELEASE_URL,
    THEMES_DIR,
    UI_ZOOM_DEFAULT,
    UPDATE_TAG_PREFIX,
    find_asset,
    scaled_sheet,
    set_zoom,
    step_zoom,
    zoom_size,
)
from core.views import MainConnectionMixin
from core.net import (
    RebootWaitWorker,
    RemoteExecWorker,
    SSHShellWorker,
    SharedConnection,
    SharedSessionWorker,
    credential_id,
)
import core.ui
from core.views import TerminalMixin
from core.ui import (
    CollapseGroup,
    RightPinnedTabBar,
    RightPinnedTabWidget,
    TabCloseButton,
    TabPopoutButton,
    TabTransportDots,
    TRANSPORT_DOT,
    ToggleGroup,
    ToggleSwitch,
    refresh_theme_styles,
    settings_tab_icon,
    default_tab_icon,
    label_tab,
    set_tooltips_enabled,
    tab_caption,
    tip_on_title,
    tooltips_enabled,
    transport_dot,
)


#: The caption the Theme Manager addon gives its own tab; View > Theme looks for
#: it by this name so the menu can jump straight to the editor. The addon spells
#: the same words out in its own file.
THEME_MANAGER_TAB = "Theme Manager"


#: The caption the Addon Dev tab is known by. It usually wears no text on the
#: bar at all (see :meth:`MainWindow.ensure_addon_dev_tab`), so this is the one
#: place the name is spelled out for the menu entry and the pinning alike.
ADDON_DEV_TAB = "Addon Dev"


#: The row the Startup order list puts between the tabs the user places and the
#: run held against the right edge. The pinned tabs are listed as well - their
#: order along the edge is the user's too - and this row is what says where the
#: second run begins, so a commit knows which names to keep together.
ORDER_PINNED_HEAD = "Pinned at the right edge"


def split_order_rows(rows: list[str]) -> tuple[list[str], list[str]]:
    """The Startup list's rows as the two runs they are: movable tabs, then pinned.

    Everything above the heading names the order of the tabs the user places, and
    everything below it the order of the run at the right edge. A list with no
    heading is one run, which is what an order written before the pinned tabs
    could be arranged holds.
    """
    try:
        cut = rows.index(ORDER_PINNED_HEAD)
    except ValueError:
        return [row for row in rows if row], []
    return [row for row in rows[:cut] if row], [row for row in rows[cut + 1:] if row]


def sort_into_runs(rows: list[str],
                   pinned_titles: list[str]) -> tuple[list[str], list[str]]:
    """Put every row of the Startup list back in the run its tab belongs to.

    A pinned tab dragged up into the movable rows, or a movable one dropped in
    among the pinned, is put back rather than obeyed: the pinned run is furniture
    at the right edge, and only the order *inside* it is the user's to choose. A
    pinned tab the rows have lost its place for - one that arrived since the page
    was filled - is kept at the end of the run rather than dropped.
    """
    named = [row for row in rows if row and row != ORDER_PINNED_HEAD]
    pinned = [title for title in pinned_titles if title]
    movable = [title for title in named if title not in pinned]
    moved = [title for title in named if title in pinned]
    return movable, moved + [t for t in pinned if t not in moved]


#: The two logos read off disk, kept by file so a zoom step or an icon toggle
#: does not fetch and decode the picture again. QPixmap copies by reference and
#: Qt decodes small PNGs on its own cache anyway; what this saves is the disk
#: read, and it holds only the handful of shipped marks, never a growing set.
_LOGO_PIXMAPS: dict[str, QPixmap | bool] = {}


def logo_pixmap(path: Path) -> QPixmap | None:
    """The full-resolution picture at `path`, read once and remembered;
    ``None`` when the file cannot be decoded."""
    key = str(path)
    if key in _LOGO_PIXMAPS:
        # ``False`` is remembered as "no picture here", so a bad file is
        # probed once rather than on every call.
        held = _LOGO_PIXMAPS[key]
        return held if isinstance(held, QPixmap) else None
    made = QPixmap(key)
    image: QPixmap | bool = False if made.isNull() else made
    _LOGO_PIXMAPS[key] = image
    return None if image is False else image


# --- the profile's one git token: what it buys, and how to prove it works ---

#: GitHub's own door, and the one path that answers without costing a request:
#: ``/rate_limit`` says who the site thinks it is talking to. This is the only
#: call the program makes of its own - an addon reaching GitHub goes through
#: ``api.session.github_headers()`` and its own socket.
GITHUB_API_HOST = "api.github.com"
GITHUB_RATE_LIMIT_PATH = "/rate_limit"

#: The API version every request here spells out, so a change on GitHub's side
#: cannot silently arrive as a differently shaped answer.
GITHUB_API_VERSION = "2022-11-28"


def git_token_words(status: int, headers: dict, payload: dict,
                    sent: bool) -> str:
    """What to say about the profile's token, from what GitHub answered.

    Pure, so the wording can be read without a network: the answer names the
    identity GitHub gave the request and the two numbers worth knowing - the
    hour's allowance and what is left of it. A refused token is said as a
    refusal rather than as an anonymous answer, since the field is not empty and
    whoever typed it deserves to know it does not work.
    """
    if status == 401:
        reason = str(payload.get("message") or "the token was refused").strip()
        return f"GitHub refused the token: {reason}"
    if status != 200:
        return f"GitHub answered with {status}"
    core = (payload.get("resources") or {}).get("core") or {}
    limit = core.get("limit", "?")
    left = core.get("remaining", "?")
    scopes = str(headers.get("x-oauth-scopes") or "").strip()
    named = f", scopes: {scopes}" if scopes else ""
    if not sent:
        return (f"nothing was sent, so this is the anonymous answer: {limit} "
                f"requests an hour, {left} left")
    return (f"GitHub knows the token: {limit} requests an hour, {left} "
            f"left{named}")


class GitTokenWorker(QThread):
    """Ask GitHub who it is talking to; off the GUI thread, one answer, then done."""

    answered = pyqtSignal(str)
    stopped = pyqtSignal(str)

    def __init__(self, token: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.token = str(token or "").strip()

    def run(self) -> None:
        headers = {
            "User-Agent": f"BerryManager/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        conn = None
        try:
            conn = http.client.HTTPSConnection(GITHUB_API_HOST, timeout=15)
            conn.request("GET", GITHUB_RATE_LIMIT_PATH, headers=headers)
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
            got = {str(key).lower(): str(value)
                   for key, value in response.getheaders()}
        except (http.client.HTTPException, OSError, ValueError) as error:
            self.stopped.emit(f"GitHub did not answer: {error}")
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        self.answered.emit(git_token_words(
            status, got, payload if isinstance(payload, dict) else {},
            bool(self.token)))


class BerryManager(
    BrowserMixin,
    MainConnectionMixin,
    TerminalMixin,
    QMainWindow
):
    """The application window: menu, tab shell, connection panel, log and shutdown.

    The device tabs live in their own modules - core.views and
    core.views - and are mixed in here, along with the one main SSH/SFTP
    connection in core.views (Berry, a bundled addon, provides the file tab).
    Everything the window itself owns stays in this file: the menu, the Settings
    tab, the profiles and the actions that open a dialog. The reusable widgets
    and popups are in core.ui, and the addons a profile runs are handled
    by the AddonManager in core.addon.
    """

    #: This window has a bar of its own, so an addon whose page has been taken
    #: out into a window has somewhere to come back to. ``core.popout`` asks
    #: before it docks anything: a program launched as one addon's window
    #: (``core.popout.PopoutHost``) carries the same two names as ``False`` and
    #: is refused, since there is no bar behind it to return to.
    is_main_window = True
    can_dock_tabs = True

    def __init__(self) -> None:
        super().__init__()
        self.worker: SharedSessionWorker | None = None
        self.shell_worker: SSHShellWorker | None = None
        self.exec_worker: RemoteExecWorker | None = None
        self.reboot_worker: RebootWaitWorker | None = None
        # The one HTTP call this program makes itself, and only while a press of
        # the Connection page's Test button is waiting on an answer.
        self.git_token_worker: GitTokenWorker | None = None
        self.shared_conn = SharedConnection()
        self.tray: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.app_config = load_app_config()
        core.ui.progress(0.10, "Reading your settings")
        self.addons = AddonManager(self)
        self._cert_bypass_session = cert_bypass_active()
        self.current_term_color = "#e0e0e0"
        self.term_partial = ""
        self.term_cells: list[tuple[str, str]] = []
        self.term_pen = 0
        self.term_saved_pen = 0
        self.term_line_doc_pos = 0
        self.terminal_history: list[str] = load_command_history()
        self.terminal_history_pos = len(self.terminal_history)
        self.connected_once = False
        self.main_connected = False
        # The version the window is running, where a user looks first: the
        # About box says the same thing, from the same constant.
        self.setWindowTitle(f"Berry Manager V{APP_VERSION}")
        self.resize(1180, 780)

        # The look is decided before anything is drawn: the themes folder is
        # made if it is not there, and the profile's theme is read from it so
        # the first stylesheet the window ever sets is already the right one.
        core.ui.ensure_installed()
        core.ui.use(self.app_config.get("theme", "default"))
        core.ui.progress(0.25, "Fitting the look")

        self.build_menu()
        core.ui.progress(0.40, "Building the menu")
        self.build_ui()
        core.ui.progress(0.60, "Building the window")
        self.apply_runtime_settings()
        self.apply_window_icon()
        core.ui.progress(0.70, "Polishing the panels")
        self.build_tray()

        # The addon phase reports itself: reload() and start_done() name every
        # addon as they read it in and tell it to begin, across their own band
        # of the bar (see core.ui.addon_step).
        core.ui.progress(0.72, "Looking for addons")
        self.addons.reload()
        self.addons.start_done()
        self.sync_tab_transport_dots()
        self._natural_tab_order = self.capture_tab_order()
        self._natural_pinned_order = self.capture_pinned_order()
        self._apply_remembered_order()
        self._select_default_tab()
        core.ui.progress(0.95, "Arranging the tabs")

        # One main connection carries both transports, so one switch brings it
        # up at startup; the terminal attaches to it from there. It comes up for
        # whichever transport is switched on, and there is no point opening a
        # session when neither is.
        if self.connections_enabled() and bool(self.app_config.get("auto_connect")):
            if (bool(self.app_config.get("ssh_enabled", True))
                    or bool(self.app_config.get("sftp_enabled", True))):
                QTimer.singleShot(400, self.connect_remote)
            else:
                self.log("Not connecting at startup: SSH and SFTP are both off")

    def build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self.action_new_profile = QAction("New profile...", self)
        self.action_new_profile.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.action_new_profile.triggered.connect(self.new_profile)
        file_menu.addAction(self.action_new_profile)

        self.profile_menu = QMenu("Load profile", self)
        self.profile_menu.aboutToShow.connect(self.refresh_profile_menu)
        file_menu.addMenu(self.profile_menu)

        self.action_rename_profile = QAction("Rename current profile...", self)
        self.action_rename_profile.triggered.connect(self.rename_current_profile)
        file_menu.addAction(self.action_rename_profile)

        self.action_delete_profile = QAction("Delete current profile...", self)
        self.action_delete_profile.triggered.connect(self.delete_current_profile)
        file_menu.addAction(self.action_delete_profile)

        file_menu.addSeparator()
        self.action_export_config = QAction("Export configuration...", self)
        self.action_export_config.triggered.connect(self.export_config)
        file_menu.addAction(self.action_export_config)

        self.action_import_config = QAction("Import configuration...", self)
        self.action_import_config.triggered.connect(self.import_config)
        file_menu.addAction(self.action_import_config)

        file_menu.addSeparator()
        self.action_save_log = QAction("Save log to file...", self)
        self.action_save_log.triggered.connect(self.save_log_to_file)
        file_menu.addAction(self.action_save_log)

        file_menu.addSeparator()
        self.action_quit = QAction("Quit", self)
        self.action_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.action_quit.triggered.connect(self.close)
        file_menu.addAction(self.action_quit)

        edit_menu = menubar.addMenu("&Edit")
        self.action_clear_log = QAction("Clear log", self)
        self.action_clear_log.triggered.connect(lambda: self.log_output.clear())
        edit_menu.addAction(self.action_clear_log)
        self.action_clear_terminal = QAction("Clear terminal", self)
        self.action_clear_terminal.setShortcut(QKeySequence("Ctrl+L"))
        self.action_clear_terminal.triggered.connect(self.clear_terminal)
        edit_menu.addAction(self.action_clear_terminal)
        self.action_clear_history = QAction("Clear command history", self)
        self.action_clear_history.triggered.connect(self.clear_command_history)
        edit_menu.addAction(self.action_clear_history)

        view_menu = menubar.addMenu("&View")
        self.action_zoom_in = QAction("Zoom in", self)
        self.action_zoom_in.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.action_zoom_in.setToolTip(
            "Draw the whole interface one step larger")
        self.action_zoom_in.triggered.connect(self.zoom_in)
        view_menu.addAction(self.action_zoom_in)
        self.action_zoom_out = QAction("Zoom out", self)
        self.action_zoom_out.setShortcuts(
            [QKeySequence("Ctrl+-"), QKeySequence("Ctrl+_")])
        self.action_zoom_out.setToolTip(
            "Draw the whole interface one step smaller")
        self.action_zoom_out.triggered.connect(self.zoom_out)
        view_menu.addAction(self.action_zoom_out)
        self.action_zoom_reset = QAction("Reset zoom", self)
        self.action_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.action_zoom_reset.setToolTip(
            f"Back to {UI_ZOOM_DEFAULT}%")
        self.action_zoom_reset.triggered.connect(self.reset_zoom)
        view_menu.addAction(self.action_zoom_reset)
        view_menu.addSeparator()
        self.action_log_console = QAction("Show log console", self)
        self.action_log_console.setCheckable(True)
        self.action_log_console.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.action_log_console.setToolTip(
            "Hide or show the log pane at the bottom of the window. Logging goes "
            "on while it is out of sight, so nothing is missed.")
        self.action_log_console.setChecked(
            bool(self.app_config.get("log_visible", True)))
        self.action_log_console.toggled.connect(self.toggle_log_console)
        view_menu.addAction(self.action_log_console)
        view_menu.addSeparator()
        self.action_fullscreen = QAction("Fullscreen", self)
        self.action_fullscreen.setCheckable(True)
        self.action_fullscreen.setShortcut(QKeySequence("F11"))
        self.action_fullscreen.toggled.connect(self.set_fullscreen)
        view_menu.addAction(self.action_fullscreen)
        view_menu.addSeparator()
        self.action_alt_icon = QAction("Alternate icon", self)
        self.action_alt_icon.setCheckable(True)
        self.action_alt_icon.setChecked(bool(self.app_config.get("use_alt_icon", False)))
        self.action_alt_icon.setToolTip("Switch between the default and alternate toolbar icon")
        self.action_alt_icon.toggled.connect(self.toggle_window_icon)
        view_menu.addAction(self.action_alt_icon)
        view_menu.addSeparator()

        # Themes are listed as they are found, every time the menu opens: a
        # file dropped into ``themes`` while the program runs should be
        # pickable without a restart, the same way the profile list works.
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_group.triggered.connect(self._theme_chosen)
        self.theme_menu = QMenu("Theme", self)
        self.theme_menu.aboutToShow.connect(self.refresh_theme_menu)
        view_menu.addMenu(self.theme_menu)
        self.refresh_theme_menu()

        tools_menu = menubar.addMenu("&Tools")
        self.action_edit_quick = QAction("Edit quick commands...", self)
        self.action_edit_quick.triggered.connect(self.edit_quick_commands)
        tools_menu.addAction(self.action_edit_quick)
        tools_menu.addSeparator()
        self.action_addon_dev = QAction(ADDON_DEV_TAB, self)
        self.action_addon_dev.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.action_addon_dev.triggered.connect(self.show_addon_dev)
        tools_menu.addAction(self.action_addon_dev)
        self.action_addon_dev.setVisible(self.addon_dev_wanted())

        help_menu = menubar.addMenu("&Help")
        self.action_about = QAction("About", self)
        self.action_about.triggered.connect(self.show_about)
        help_menu.addAction(self.action_about)

    def build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        connection_group = ToggleGroup(
            "Connection", bool(self.app_config.get("master_connection", True)),
            body="box",
            tip="The master switch. Off closes the main connection and stops "
                "every addon from connecting, with the app's session or its own; "
                "on lets the Connect button and the addons work again.",
        )
        credentials = QWidget()
        connection_layout = QGridLayout(credentials)
        connection_layout.setHorizontalSpacing(10)
        connection_layout.setVerticalSpacing(8)
        self.host_input = QLineEdit(str(self.app_config["host"]))
        self.host_input.setPlaceholderText("IP or hostname, e.g. 192.168.1.1")
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.app_config["port"])
        self.username_input = QLineEdit(str(self.app_config["username"]))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Optional when using an SSH key")
        self.show_password_toggle = QToolButton()
        self.show_password_toggle.setText("Show")
        self.show_password_toggle.setCheckable(True)
        self.show_password_toggle.toggled.connect(
            lambda on: self.password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        self.remember_password_checkbox = QCheckBox("Remember password securely")
        self.remember_password_checkbox.setChecked(bool(self.app_config.get("remember_password", False)))
        self.remember_password_checkbox.setToolTip(
            "Stores the password in the operating system credential store, not in config.json."
        )
        self.key_input = QLineEdit(str(self.app_config["key_file"]))
        self.key_input.setPlaceholderText("Optional private key file")
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(self.choose_key_file)
        self.remote_root_input = QLineEdit(str(self.app_config["remote_root"]))
        self.remote_root_input.setPlaceholderText("/www_berry")
        self.trust_host_checkbox = QCheckBox("Trust and save unknown host key on first connection")
        self.trust_host_checkbox.setChecked(bool(self.app_config["trust_unknown_host"]))
        self.trust_host_checkbox.setToolTip(
            "Leave unchecked for strict known-host verification. Enable only when you have verified the device's identity."
        )
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(3, 300)
        self.timeout_input.setValue(int(self.app_config.get("ssh_timeout", 12)))
        self.timeout_input.setSuffix(" s")
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primaryAction")
        self.connect_button.setToolTip(
            "Open the app's one main connection: a single SSH session that "
            "carries SFTP too, with the profile's credentials below"
        )
        self.connect_button.clicked.connect(self.connect_remote)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("dangerAction")
        self.disconnect_button.setToolTip(
            "Close the main connection - the shell and the file transfer with it "
            "- and disable remote actions until you reconnect")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_remote)

        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password_input, 1)
        pw_row.addWidget(self.show_password_toggle)

        connection_layout.addWidget(QLabel("Host"), 0, 0)
        connection_layout.addWidget(self.host_input, 0, 1, 1, 2)
        connection_layout.addWidget(QLabel("Port"), 0, 2)
        connection_layout.addWidget(self.port_input, 0, 3)
        connection_layout.addWidget(QLabel("Username"), 1, 0)
        connection_layout.addWidget(self.username_input, 1, 1, 1, 2)
        connection_layout.addWidget(QLabel("Password"), 1, 2)
        connection_layout.addLayout(pw_row, 1, 3)
        connection_layout.addWidget(self.remember_password_checkbox, 2, 0, 1, 4)
        connection_layout.addWidget(QLabel("Private key"), 3, 0)
        connection_layout.addWidget(self.key_input, 3, 1, 1, 2)
        connection_layout.addWidget(key_browse, 3, 3)
        self.remote_dir_label = QLabel("Remote dir")
        self.remote_dir_label.setToolTip("Folder Berry opens on and browses. SSH does not use it.")
        connection_layout.addWidget(self.remote_dir_label, 4, 0)
        connection_layout.addWidget(self.remote_root_input, 4, 1, 1, 2)
        connection_layout.addWidget(QLabel("Timeout"), 4, 2)
        connection_layout.addWidget(self.timeout_input, 4, 3)
        conn_actions = QHBoxLayout()
        conn_actions.addWidget(self.connect_button)
        conn_actions.addWidget(self.disconnect_button)
        connection_layout.addWidget(self.trust_host_checkbox, 5, 0, 1, 2)
        connection_layout.addLayout(conn_actions, 5, 2, 1, 2)
        connection_group.body_layout.addWidget(credentials)

        # The main connection gets the start-up switch: it is the only session
        # the app opens, so one switch covers what SSH and SFTP each had.
        self.auto_connect_check = ToggleSwitch("Connect at startup")
        self.auto_connect_check.setToolTip(
            "Open the main connection by itself when the app starts, using this "
            "profile's host, user and remembered password. One switch covers "
            "both transports - SSH and SFTP come up together on that single "
            "session, and the SSH terminal attaches to it as a channel, so a "
            "shell arrives with it whenever the SSH tab is switched on. SFTP has "
            "no startup switch of its own. Nothing opens while both SSH and "
            "SFTP are switched off below."
        )
        self.auto_connect_check.setChecked(bool(self.app_config.get("auto_connect", False)))
        self.auto_connect_check.toggled.connect(self.on_auto_connect_toggled)
        connection_group.body_layout.addWidget(self.auto_connect_check)

        connection_group.switch.toggled.connect(self.on_master_connection_toggled)

        self.connection_group = connection_group

        self.tabs = RightPinnedTabWidget()

        self.tabs.setTabBar(RightPinnedTabBar())
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._order_tab_bar = self.tabs.tabBar()
        self._order_tab_bar.installEventFilter(self)

        self.tabs.tabCountChanged.connect(self._on_tab_count_changed)
        # The tabs and the log console share the window through a splitter, so
        # the console can be dragged to whatever height the moment asks for.
        self.console_split = QSplitter(Qt.Orientation.Vertical)
        self.console_split.addWidget(self.tabs)
        root_layout.addWidget(self.console_split, 1)

        self.build_ssh_tab()
        self._web_tab_entries = []
        self._transient_tab_entries = []
        self.build_web_tabs()
        # The tabs held against the right edge, in the order they registered.
        # Settings is always the last of the run and needs no entry: the window
        # knows it by name, so it cannot be unpinned by accident.
        self._pinned_pages: list[QWidget] = []
        self._pinned_labels: dict[QWidget, str] = {}
        self._pinned_icon_only: dict[QWidget, bool] = {}
        # An addon's own Settings page: its content widget mapped to the scroll
        # area that was built around it.
        self._addon_settings_pages: dict[QWidget, QScrollArea] = {}
        self.build_settings_tab()

        self.addon_dev = None

        self._gap_page = QWidget()
        self.tabs.addTab(self._gap_page, "")
        self.tabs.tabBar().setTabEnabled(self.tabs.indexOf(self._gap_page), False)
        # Added only now: a pinned tab belongs to the run beside Settings, and
        # that run does not exist until the gap does (see pin_tab).
        self.ensure_addon_dev_tab()
        self._sync_pinned_settings()
        self._stretch_tab_bar()

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.console_split.addWidget(self.log_output)
        # The console opens at the height it always had and the tabs keep the
        # rest; the handle between them is what makes either side resizable.
        self.console_split.setStretchFactor(0, 1)
        self.console_split.setStretchFactor(1, 0)
        self.console_split.setSizes([zoom_size(650), zoom_size(130)])
        self.apply_log_console_visibility()

        for field in (
            self.host_input,
            self.username_input,
            self.key_input,
            self.remote_root_input,
        ):
            field.editingFinished.connect(self.save_current_profile_edits)
        self.port_input.valueChanged.connect(self.save_current_profile_edits)
        self.timeout_input.valueChanged.connect(self.save_runtime_settings)
        self.trust_host_checkbox.stateChanged.connect(self.save_current_profile_edits)
        self.remember_password_checkbox.toggled.connect(self.remember_password_changed)

        self.setCentralWidget(central)
        root_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(640, 420)
        self.apply_zoom()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Pick a profile from File > Load profile, then connect to the device")
        self.load_saved_password()
        self.refresh_profile_menu()

    def ensure_main_tabs(self) -> None:
        """Add or remove the SSH core tab to match its enabled flag; Berry/browser/Settings handled elsewhere."""
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        self._set_main_tab(self.ssh_page, "SSH", ssh_on, 0)
        self.update_connection_ui()

    def _content_insert_index(self) -> int:
        """Where a new content tab belongs: just left of the pinned gap and its run."""
        gap = getattr(self, "_gap_page", None)
        if gap is not None:
            index = self.tabs.indexOf(gap)
            if index >= 0:
                return index
        settings = getattr(self, "settings_page", None)
        if settings is not None:
            index = self.tabs.indexOf(settings)
            if index >= 0:
                return index
        return self.tabs.count()

    # --- the pinned run beside Settings ---

    def _pinned_block(self) -> list[QWidget]:
        """The tabs that sit at the right edge, left to right: the gap, every
        pinned tab in the order the bar shows it, then Settings.

        Empty until the window is built enough to pin: a missing gap or a
        missing Settings page means nothing here is in its final place yet.
        A page the window no longer holds (an addon reloaded, a tab closed) is
        dropped here, so a registry cannot outlive the widget it named.

        The run is read off the bar rather than off the order it was registered
        in, which is what lets a pinned tab stay where it was dragged: the block
        only says who belongs at the edge and in what order they stand, so the
        pinning pass slides them there without arguing about the sequence.
        """
        gap = getattr(self, "_gap_page", None)
        settings = getattr(self, "settings_page", None)
        if gap is None or settings is None:
            return []
        alive = [page for page in getattr(self, "_pinned_pages", [])
                 if page is not settings and self.tabs.indexOf(page) >= 0]
        if len(alive) != len(getattr(self, "_pinned_pages", [])):
            self._pinned_pages = alive
            for page in list(getattr(self, "_pinned_labels", {})):
                if page not in alive:
                    self._pinned_labels.pop(page, None)
                    self._pinned_icon_only.pop(page, None)
        alive.sort(key=self.tabs.indexOf)
        return [gap] + alive + [settings]

    def pin_tab(self, page: QWidget, label: str = "",
                icon_only: bool = True) -> bool:
        """Hold one tab in the pinned run beside Settings, out of the drag order.

        The tab keeps its full name in its data (so the start-tab picker and any
        lookup by caption still find it) but may wear no caption at all, which is
        what makes a system tab read as furniture rather than as a page the user
        arranged. Returns False when there is nothing to pin it beside.
        """
        if page is None or self.tabs.indexOf(page) < 0:
            return False
        if getattr(self, "settings_page", None) is None \
                or getattr(self, "_gap_page", None) is None:
            return False
        if page is self.settings_page:
            return True
        if page not in self._pinned_pages:
            self._pinned_pages.append(page)
        text = str(label or "") or tab_caption(
            self.tabs.tabBar(), self.tabs.indexOf(page))
        self._pinned_labels[page] = text
        self._pinned_icon_only[page] = bool(icon_only)
        self._sync_pinned_settings()
        return True

    def unpin_tab(self, page: QWidget) -> None:
        """Let one tab back into the reorderable run, with its caption restored."""
        if page is None or page is getattr(self, "settings_page", None):
            return
        if page in getattr(self, "_pinned_pages", []):
            self._pinned_pages.remove(page)
        self._pinned_labels.pop(page, None)
        self._pinned_icon_only.pop(page, None)
        index = self.tabs.indexOf(page)
        if index >= 0:
            label_tab(self.tabs.tabBar(), index,
                      tab_caption(self.tabs.tabBar(), index))
        self._sync_pinned_settings()

    # --- a Settings page an addon owns ---

    def add_settings_page(self, title: str, body: QWidget) -> QWidget | None:
        """Give an addon a page of its own in the Settings bar.

        The page sits with the program's own categories and the shipped addons',
        ahead of the shared **Installed addons** page, and is wrapped in a scroll
        area the same way those are, so a long form stays usable however tall the
        window is. Returns the scroll host - the thing to hand back to
        :meth:`remove_settings_page` - or ``None`` when there is no Settings bar
        to put it in.
        """
        bar = getattr(self, "settings_tabs", None)
        if bar is None or body is None:
            return None
        host = QScrollArea()
        host.setWidgetResizable(True)
        host.setWidget(body)
        manager = getattr(self, "addons", None)
        installed = getattr(manager, "installed_scroll", None)
        at = bar.indexOf(installed) if installed is not None else -1
        bar.insertTab(at if at >= 0 else bar.count(), host, str(title or "Page"))
        self._addon_settings_pages[body] = host
        return host

    def remove_settings_page(self, body: QWidget) -> bool:
        """Take away one page and its scroll host; True when it was here."""
        bar = getattr(self, "settings_tabs", None)
        host = getattr(self, "_addon_settings_pages", {}).pop(body, None)
        if bar is None or host is None:
            return False
        index = bar.indexOf(host)
        if index >= 0:
            bar.removeTab(index)
        host.deleteLater()
        return True

    def _label_pinned_tabs(self) -> None:
        """Put every pinned tab's caption and tooltip back the way its owner asked.

        Called from the pinning pass itself, because a tab move can land a pinned
        tab on a new index after the bar has already measured the row.
        """
        bar = self.tabs.tabBar()
        settings = getattr(self, "settings_page", None)
        for page in self._pinned_block():
            index = self.tabs.indexOf(page)
            if index < 0 or page is getattr(self, "_gap_page", None):
                continue
            if page is settings:
                label_tab(bar, index, "Settings", icon_only=True,
                          tooltip="Settings")
                continue
            label = self._pinned_labels.get(page) or tab_caption(bar, index)
            label_tab(bar, index, label,
                      icon_only=bool(self._pinned_icon_only.get(page, True)),
                      tooltip=label)

    def _pinned_title(self, page: QWidget) -> str:
        """The full name one pinned tab is known by in a saved order.

        Read from the tab's data rather than its caption, because a tab pinned
        icon-only has no caption to read and still has to be named.
        """
        index = self.tabs.indexOf(page)
        return tab_caption(self.tabs.tabBar(), index) if index >= 0 else ""

    def _pinned_pages_from_bar(self) -> list[QWidget]:
        """The tabs held in the pinned run, left to right as the bar shows them.

        The gap and Settings are not part of it: one always leads the run and the
        other always closes it, so only the tabs between them can be placed.
        """
        gap = getattr(self, "_gap_page", None)
        settings = getattr(self, "settings_page", None)
        return [page for page in self._pinned_block()
                if page is not gap and page is not settings]

    def capture_pinned_order(self) -> list[str]:
        """The titles of the pinned tabs, in the order they stand along the right edge.

        Kept apart from :meth:`capture_tab_order` rather than merged into it, so
        a stored order can never name a pinned tab as one of the movable ones and
        drop it into the middle of the row on the next launch.
        """
        return [title for title in (self._pinned_title(page)
                                    for page in self._pinned_pages_from_bar())
                if title]

    def apply_pinned_order(self, order: list[str] | None) -> None:
        """Slide the pinned run into `order`, the gap still first and Settings still last.

        Only tabs that are pinned here are moved, and one the saved order does not
        name (an addon switched off, or installed since) falls to the end of the
        run rather than vanishing. The moves are the tab-at-a-time sort the
        pinning pass uses; the run is flush right already, so its middle begins on
        the slot just left of Settings.
        """
        pages = self._pinned_pages_from_bar()
        if not pages or not order:
            return
        left = list(pages)
        wanted: list[QWidget] = []
        for title in order:
            for page in left:
                if self._pinned_title(page) == title:
                    wanted.append(page)
                    left.remove(page)
                    break
        wanted += left
        if wanted == pages:
            return
        bar = self.tabs.tabBar()
        start = self.tabs.count() - len(wanted) - 1
        for offset, page in enumerate(wanted):
            index = self.tabs.indexOf(page)
            if index >= 0 and index != start + offset:
                bar.moveTab(index, start + offset)
        self._sync_pinned_settings()

    def _sync_pinned_settings(self) -> None:
        """Snap the gap and the pinned run back to the far right, then recompute the gap width.

        The block is moved a tab at a time, and a move is measured against every
        tab the bar holds now, so a tab that has just been added after Settings
        (or taken out of the middle) shifts the rest of the run along with it.
        One pass is therefore not always enough: the passes repeat until the bar
        agrees with the block, with the block's own length as the brake. The
        block's own order is what the bar already shows (see
        :meth:`_pinned_block`), so this pulls the run to the edge and leaves a
        pinned tab where the user put it along it.
        """
        bar = self.tabs.tabBar()
        block = self._pinned_block()
        gap = getattr(self, "_gap_page", None)
        if not block or gap is None:
            return
        start = self.tabs.count() - len(block)
        for _pass in range(len(block) + 1):
            settled = True
            for offset, page in enumerate(block):
                index = self.tabs.indexOf(page)
                if index < 0:
                    continue
                if index != start + offset:
                    bar.moveTab(index, start + offset)
                    settled = False
            if settled:
                break
        gi = self.tabs.indexOf(gap)
        if gi >= 0:
            bar.setTabEnabled(gi, False)
            if hasattr(bar, "set_gap_index"):
                bar.set_gap_index(gi, pinned=max(1, len(block) - 1))
        self._label_pinned_tabs()

    def _on_tab_count_changed(self, _count: int) -> None:
        """A tab was added or removed: defer a tick, then re-pin the gap and Settings pair."""
        if getattr(self, "_gap_page", None) is None:
            return
        QTimer.singleShot(0, self._repin_settings)

    def _repin_settings(self) -> None:
        """Re-sync the pinned pair after a deferred count change (see
        :meth:`_on_tab_count_changed`). No-op once the window is closing."""
        if getattr(self, "_gap_page", None) is None:
            return
        if self.tabs.indexOf(self._gap_page) < 0:
            return
        self._sync_pinned_settings()
        self._stretch_tab_bar()
        # A tab that arrived or left is the order list's business too.
        self._refresh_tab_order_list()

    def settle_tab_bar(self) -> None:
        """Re-pin the gap and its run now, rather than on the next turn of the loop.

        The bar holds its spare room in an empty gap tab and remembers that tab by
        *index*, so a tab leaving or joining the row to the left of it shifts what
        that index points at - and until the gap is pinned again, the real tab
        that slid into the stale index is measured as the gap is: as wide as the
        bar has room. A count change already schedules this one tick later; a
        popout moves several tabs in a row and cannot wait between them.
        """
        self._repin_settings()

    def restore_tab_layout(self) -> None:
        """Put every tab back in its saved position and re-pin Settings (called after the addon manager reloads)."""
        self._apply_remembered_order()
        self._repin_settings()

    def update_connection_ui(self) -> None:
        """Match the Connection panel to what is on, following the profile settings.

        The main connection is a single thing, so its buttons and its start-up
        switch follow the master switch alone; the SSH and SFTP lines say what
        each transport's own gate allows."""
        if not hasattr(self, "connect_button"):
            return                     # called from a signal before the panel exists
        master_on = self.connections_enabled()
        sftp_on = bool(self.app_config.get("sftp_enabled", True))
        ssh_on = bool(self.app_config.get("ssh_enabled", True))
        busy = self.worker is not None and self.worker.isRunning()
        connected = self.main_connected and master_on
        self.connect_button.setVisible(master_on)
        self.disconnect_button.setVisible(master_on)
        self.auto_connect_check.setVisible(master_on)
        self.connect_button.setEnabled(master_on and not busy and not connected)
        self.disconnect_button.setEnabled(master_on and not busy and connected)

        self.remote_dir_label.setVisible(sftp_on)
        self.remote_root_input.setVisible(sftp_on)
        self.sftp_caption.setVisible(sftp_on)
        self.sftp_dot.setVisible(sftp_on)
        self.sftp_state_label.setVisible(sftp_on)
        self.ssh_caption.setVisible(ssh_on)
        self.ssh_dot.setVisible(ssh_on)
        self.ssh_state_label.setVisible(ssh_on)

        self.refresh_terminal_buttons()

    def connections_enabled(self) -> bool:
        """The master switch: is the app allowed to talk to the device at all?"""
        return bool(self.app_config.get("master_connection", True))

    def on_master_connection_toggled(self, checked: bool) -> None:
        """The master switch: off closes the main connection and blocks all
        connecting, addons included; on allows it again but connects nothing."""
        self.app_config["master_connection"] = bool(checked)
        self.save_app_config()
        if not checked:
            self.close_all_connections()
        self.update_connection_ui()
        self.log("Connections enabled" if checked else "Connections switched off")
        self.addon_event("connections_enabled" if checked else "connections_disabled")

    def on_auto_connect_toggled(self, checked: bool) -> None:
        """The start-up switch: the main connection opens itself with the app.

        Saved straight away, like the master switch: it decides what the next
        start does, so there is nothing to apply."
        """
        self.app_config["auto_connect"] = bool(checked)
        self.save_app_config()
        self.log("Main connection will open at startup" if checked
                 else "Main connection will not open at startup")

    def close_all_connections(self) -> None:
        """Take the main connection down (the master switch went off)."""
        if self.shell_worker is not None and self.shell_worker.isRunning():
            self.shell_worker.stop()
        if self.worker is not None and self.worker.isRunning():
            return
        self.shared_conn.close()
        self.mark_main_closed("connections are switched off")

    def _set_main_tab(self, page: QWidget, title: str, want: bool, desired_index: int) -> None:
        current = self.tabs.indexOf(page)
        if want:
            if current == -1:
                self.tabs.insertTab(min(desired_index, self.tabs.count()), page, title)
                self.sync_tab_transport_dots()
        elif current != -1:
            self.tabs.removeTab(current)
            self.sync_tab_transport_dots()

    def show_addon_dev(self) -> None:
        """Jump to the Addon Dev tab, if the Settings switch shows it."""
        if self.addon_dev is not None:
            self.tabs.setCurrentWidget(self.addon_dev)

    def addon_dev_wanted(self) -> bool:
        """Is the Addon Dev tab wanted? The store's Addon manager writes the key."""
        return bool(self.app_config.get("addon_dev_enabled", True))

    def ensure_addon_dev_tab(self) -> None:
        """Add or drop the Addon Dev tab to match the Settings switch; off keeps the widget alive but hidden.

        The tab is furniture rather than a page the user arranged: it pins to the
        run at the right edge and shows only its mark there, like the store and
        the updater do. Its name still lives in the tab's data, so the start-tab
        picker, the tooltip and any lookup by caption keep finding it.
        """
        want = self.addon_dev_wanted()
        if want and self.addon_dev is None:
            self.addon_dev = AddonDevWidget(self)
        if self.addon_dev is not None:
            index = self.tabs.indexOf(self.addon_dev)
            if want and index == -1:
                self.tabs.insertTab(self._content_insert_index(),
                                    self.addon_dev, ADDON_DEV_TAB)
                self.tabs.tabBar().setTabIcon(
                    self.tabs.indexOf(self.addon_dev), default_tab_icon())
                self.pin_tab(self.addon_dev, ADDON_DEV_TAB, icon_only=True)
            elif not want and index != -1:
                # Unpinning moves the rest of the run back, so the index taken
                # before the move is not the one this tab sits at now.
                self.unpin_tab(self.addon_dev)
                self.tabs.removeTab(self.tabs.indexOf(self.addon_dev))
        self.action_addon_dev.setVisible(want)
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))

    def apply_runtime_settings(self) -> None:
        self.apply_zoom()
        self.ensure_main_tabs()
        self.ensure_addon_dev_tab()
        self.apply_log_console_visibility()
        self.apply_tooltip_switch()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))
        self._select_default_tab()
        self.rebuild_quick_bar()

    def apply_tooltip_switch(self) -> None:
        """Let the Appearance switch have its say over tool-tips, now.

        Nothing is saved here: the keeper is showing tips or it is not, and a
        profile that switched them off gets them off the moment it is loaded.
        """
        wanted = bool(self.app_config.get("tooltips_enabled", True))
        before = tooltips_enabled()
        if not set_tooltips_enabled(wanted):
            return
        if before != wanted:
            self.log("tool-tips " + ("are being shown again" if wanted
                                     else "will not be shown"))

    def _on_tooltips_toggled(self, _checked: bool = False) -> None:
        """Write and apply the tool-tip switch as it is moved, without waiting
        for Apply: it is a matter of what the window shows, not of anything that
        has to be rebuilt."""
        self.app_config["tooltips_enabled"] = self.tooltips_check.isChecked()
        self.save_app_config()
        self.apply_tooltip_switch()

    def _icon_path(self, filename: str) -> Path | None:
        """Resolve a shipped icon from the assets folder."""
        return find_asset(filename)

    def current_icon_path(self) -> Path | None:
        """The logo View > Alternate icon points at now; every place that shows it asks this one method."""
        primary, alternate = ICON_FILES
        wanted = alternate if bool(self.app_config.get("use_alt_icon")) else primary
        return self._icon_path(wanted) or self._icon_path(primary)

    def paint_header_logo(self) -> None:
        """The toolbar picture, drawn at the zoom the interface is using."""
        icon = getattr(self, "header_icon", None)
        if icon is None:
            return
        path = self.current_icon_path()
        if path is None:
            return
        side = zoom_size(22)
        image = logo_pixmap(path)
        if image is None:
            return
        icon.setPixmap(image.scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def apply_window_icon(self) -> None:
        """Show the selected icon in the top toolbar and as the window icon."""
        manager = getattr(self, "addons", None)
        if manager is not None:

            try:
                manager.refresh_app_icons()
            except Exception as error:
                self.log(f"addon tab icons: {error}")
        path = self.current_icon_path()
        if path is None:
            return
        image = logo_pixmap(path)
        icon = QIcon(image) if image is not None else QIcon(str(path))
        self.setWindowIcon(icon)
        QApplication.setWindowIcon(icon)
        self.paint_header_logo()
        self.refresh_tray_icon()

    # Tray icon and notices

    def build_tray(self) -> None:
        """The app's mark in the notification area, so a notice has somewhere to
        come from when the window is under everything else.

        Closing is untouched: the icon is an address for toasts and a place to
        raise or quit the program from, not a way to hide the window. Where
        there is no notification area nothing is built and every notice falls
        to the status line, which is what a plain window has always done.
        """
        if self.tray is not None:
            self.refresh_tray_icon()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        menu = QMenu(self)
        raise_action = QAction("Show Berry Manager", self)
        raise_action.setToolTip("Bring the window up in front of the others")
        raise_action.triggered.connect(self.raise_from_tray)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(raise_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray = QSystemTrayIcon(self)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        self.tray_menu = menu           # the window holds it, or Qt drops it
        self.tray = tray
        self.refresh_tray_icon()
        tray.show()

    def refresh_tray_icon(self) -> None:
        """Keep the tray mark the picture the window is wearing."""
        if self.tray is None:
            return
        path = self.current_icon_path()
        image = logo_pixmap(path) if path is not None else None
        icon = QIcon(image) if image is not None else self.windowIcon()
        if icon.isNull():               # a logo-less build still needs a mark
            icon = QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip(f"Berry Manager V{APP_VERSION}")

    def raise_from_tray(self) -> None:
        """Come back from whatever it was minimised to, under the pointer."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """A left click on the tray mark raises the window; the menu is the
        right click's, and Qt shows it itself."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.raise_from_tray()

    def notify_popup(self, title: str, text: str, ms: int = 6000) -> None:
        """One notice for the user: the status line always, and a toast from the
        tray icon on top of it while Settings allows them.

        Everything that calls this is a speaker the user cannot see - an addon
        through ``api.ui.popup``, or this program's own update check - so the
        title names who is talking and the toast repeats it. A toast that will
        not come out is never a failure: the status line and the log already
        carry the same words.
        """
        heading = str(title or "Berry Manager").strip() or "Berry Manager"
        body = str(text or "").strip()
        if not body:
            return
        try:
            held = max(1000, int(ms))
        except (TypeError, ValueError):
            held = 6000
        self.statusBar().showMessage(f"{heading}: {body}", held)
        self.log(f"notice ({heading}): {body}")
        if not bool(self.app_config.get("system_notifications", True)):
            return
        if self.tray is None or not self.tray.isVisible():
            return
        try:
            self.tray.showMessage(heading, body,
                                  QSystemTrayIcon.MessageIcon.Information, held)
        except Exception as error:          # a toast is never worth a traceback
            self.log(f"tray notice failed: {error}")

    def set_tabs_locked(self, locked: bool) -> bool:
        """Hold the window's tab bar on whatever page it is showing.

        An addon that swaps its tab over to a job's progress card asks for this
        while the job runs, so the pane being watched cannot be clicked away to
        - and gets the bar back on the same call with ``locked`` off. The hold
        covers the keyboard and the wheel as well as the click; a page the app
        itself asks for is refused too, which is the point. Nothing is remembered
        in the window's settings and nothing outlives the work: an addon stopping
        hands the bar back (see ``core.addon._clean``), so a lock cannot be left
        over a tab that is gone.
        """
        if not hasattr(self, "tabs"):
            return False
        try:
            from core.ui import set_tab_lock

            return set_tab_lock(self.tabs, bool(locked))
        except Exception as error:            # a bar that will not hold is a bar
            self.log(f"tab lock: {error}")
            return False

    def set_tabs_bar_visible(self, visible: bool) -> bool:
        """Show or hide the window's tab bar, leaving the pages themselves alone.

        An addon that hands the whole window to a job's progress view asks for
        this while the job runs - there is nothing to switch back to when one
        pane owns everything - and gets the bar back on the same call with
        ``visible`` on. Only the bar goes; the page stays exactly where it was,
        and nothing is remembered in the window's settings.
        """
        if not hasattr(self, "tabs"):
            return False
        try:
            self.tabs.tabBar().setVisible(bool(visible))
            if visible:
                self._stretch_tab_bar()
            return True
        except Exception as error:            # a bar that will not stow itself
            self.log(f"tab bar visibility: {error}")
            return False

    # Interface zoom

    def ui_zoom(self) -> int:
        """The saved density of the whole interface, in percent."""
        return int(self.app_config.get("ui_zoom", UI_ZOOM_DEFAULT))

    def terminal_points(self) -> int:
        """The terminal's font: the Settings size, drawn at the current zoom."""
        base = int(self.app_config.get("terminal_font_size", 10))
        return max(6, min(48, zoom_size(base, self.ui_zoom())))

    def zoom_in(self) -> None:
        self.zoom_to(step_zoom(self.ui_zoom(), +1))

    def zoom_out(self) -> None:
        self.zoom_to(step_zoom(self.ui_zoom(), -1))

    def reset_zoom(self) -> None:
        self.zoom_to(UI_ZOOM_DEFAULT)

    def zoom_to(self, zoom: int) -> None:
        """Move the whole view to the rung asked for, and remember it."""
        percent = step_zoom(zoom, 0)
        self.app_config["ui_zoom"] = percent
        self.apply_zoom()
        self.save_app_config()
        self.statusBar().showMessage(f"Interface zoom: {percent}%", 3000)

    def apply_zoom(self) -> None:
        """Draw every part of the window at the saved zoom, from the theme in force."""
        percent = set_zoom(self.ui_zoom())
        self.setStyleSheet(scaled_sheet(core.ui.stylesheet(), percent))
        # A few pieces are painted or styled outside the app sheet - a group
        # heading, the dots in a tab - and are re-poured from the theme here.
        refresh_theme_styles()
        # The log console is sized by its splitter now, so a zoom step leaves
        # whatever height the person dragged it to standing where they put it.
        font = getattr(self, "terminal_font", None)
        if font is not None and hasattr(self, "terminal"):
            font.setPointSize(self.terminal_points())
            self.terminal.set_font(font)
        self.paint_header_logo()

        if hasattr(self, "tabs"):
            self.zoom_tab_icons(percent)
            self.sync_tab_transport_dots()

        # An addon whose tabs were popped out owns a window - its own sheet, its
        # own header, its own page marks - and none of that is under this window's
        # stylesheet, so it is asked in the same pass to move to the new rung.
        try:
            from core import popout

            popout.follow_zoom(self, percent)
        except Exception as error:            # a window that will not redraw is
            self.log(f"popout zoom: {error}")  # no reason to stop the app's own

    def zoom_tab_icons(self, percent: int) -> None:
        """Scale the tab icons (addon ones included) from the style's baseline and the theme's mark size, so 100% under the default theme matches as shipped."""
        bar = self.tabs.tabBar()
        base = getattr(self, "_tab_icon_base", None)
        if base is None:
            base = max(1, bar.iconSize().width())
            self._tab_icon_base = base
        asked = zoom_size(base, percent)
        from_theme = core.ui.icon_px()
        if from_theme:
            asked = max(asked, zoom_size(from_theme, percent))
        bar.setIconSize(QSize(asked, asked))

        if hasattr(bar, "refit"):
            bar.refit()

    # Themes

    def theme_names(self) -> list[str]:
        """Every theme on disk, the one in force named first for a reader."""
        return core.ui.names()

    def refresh_theme_menu(self) -> None:
        """Fill View > Theme from the folder, marking the theme in force.

        Called as the menu opens rather than once at startup: a ``.theme`` file
        written by the Theme Manager, or dropped in by hand, should be pickable
        in the same run it appeared in.
        """
        menu = getattr(self, "theme_menu", None)
        if menu is None:
            return
        menu.clear()
        # A fresh group each pass: an action taken out of the old one is
        # deleted with the menu, and Qt unlinks it as it goes.
        group = QActionGroup(self)
        group.setExclusive(True)
        group.triggered.connect(self._theme_chosen)
        self.theme_group = group           # the window holds it, or Qt drops it
        chosen = core.ui.current_name()
        for name in core.ui.names():
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(name == chosen)
            action.setData(name)
            action.setToolTip(
                "Draw the window from "
                f"themes/{name}.theme - colours and the shape of the tabs.")
            group.addAction(action)
            menu.addAction(action)
        menu.addSeparator()
        open_action = QAction("Open the themes folder...", self)
        open_action.setToolTip(f"Where the ``.theme`` files live: {THEMES_DIR}")
        open_action.triggered.connect(self.open_themes_folder)
        menu.addAction(open_action)
        editor_action = QAction("Theme Manager...", self)
        editor_action.setToolTip(
            "The addon that makes and edits themes, with the templates and the "
            "documentation: it is switched on on its own page in Settings.")
        editor_action.triggered.connect(self.show_theme_manager)
        menu.addAction(editor_action)
        self._reload_theme_combo()

    def _theme_chosen(self, action: QAction) -> None:
        """One click in View > Theme."""
        self.apply_theme(action.data())

    def apply_theme(self, name: object) -> str:
        """Draw the window from one ``.theme`` file and remember it for this profile.

        Returns the theme actually used: a name with no file behind it falls
        back to the default, and the status line says so rather than quietly
        showing something else.
        """
        wanted = core.ui.safe_name(name) or "default"
        used = core.ui.use(wanted)
        self.app_config["theme"] = used
        self.apply_zoom()
        self.save_app_config()
        self.refresh_theme_menu()
        note = f"Theme: {used}" if used == wanted else f"No theme named {wanted}"
        self.statusBar().showMessage(note, 3000)
        self.log(note)
        self.addon_event("theme_changed", theme=used, wanted=wanted)
        return used

    def open_themes_folder(self) -> bool:
        """Show the themes folder in the system's file viewer; whether it really opened."""
        core.ui.ensure_installed()
        opened = core.ui.open_in_file_manager(THEMES_DIR)
        if not opened:
            self.log(f"the file viewer could not be opened at {THEMES_DIR}")
        return opened

    def show_theme_manager(self) -> None:
        """Come to the Theme Manager's own tab, or say where to switch it on.

        The tab may be standing in the bar or in a window of its own, which is
        what the addon's own setting decides; either place is the same editor.
        """
        if self.show_popped_tab(THEME_MANAGER_TAB):
            return
        bar = self.tabs.tabBar()
        for index in range(self.tabs.count()):
            if tab_caption(bar, index) == THEME_MANAGER_TAB:
                self.tabs.setCurrentIndex(index)
                return
        self.notify_popup("Themes", "The Theme Manager addon is not running: "
                                    "switch it on on the Theme Manager page in "
                                    "Settings.")

    def show_popped_tab(self, label: str) -> bool:
        """Bring the window that holds a tab of this name to the front, if one does.

        A page that left the bar for a popout window is not in the bar to be
        switched to, so every jump the app offers - View > Theme, the Settings
        link, a menu item - has to ask the windows as well. The addon owns the
        window it was popped into, so presenting it is all there is to do.
        """
        wanted = str(label)
        for window in list(getattr(self, "windows", {}).values()):
            try:
                if window is None or not window.has_page(wanted):
                    continue
                window.present()
                return True
            except (AttributeError, RuntimeError):
                continue
        return False

    def show_appearance_settings(self) -> bool:
        """Open Settings and stand on its Appearance page, where the theme in
        force is picked; whether such a page was there to reach."""
        page = getattr(self, "settings_page", None)
        bar = getattr(self, "settings_tabs", None)
        if page is None or bar is None:
            return False
        for index in range(bar.count()):
            if bar.tabText(index) == "Appearance":
                self.tabs.setCurrentWidget(page)
                bar.setCurrentIndex(index)
                return True
        return False

    def _reload_theme_combo(self) -> None:
        """Keep the Settings > Appearance picker in step with the folder."""
        combo = getattr(self, "theme_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(core.ui.names())
        index = combo.findText(core.ui.current_name())
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        note = getattr(self, "theme_note", None)
        if note is not None:
            data = core.ui.theme()
            blurb = str(data.get("blurb") or "").strip()
            who = str(data.get("author") or "").strip()
            tail = f" - {who}" if who else ""
            note.setText((blurb or "A theme is a handful of colours and the "
                                 "shape of the tabs, written as JSON.") + tail)

    def _on_theme_chosen_combo(self, name: str) -> None:
        """The Settings > Appearance picker moved."""
        self.apply_theme(name)

    def toggle_log_console(self, shown: bool) -> None:
        """View > Show log console: fold the bottom log pane away, or bring it back."""
        self.app_config["log_visible"] = bool(shown)
        self.apply_log_console_visibility()
        self.save_app_config()

    def apply_log_console_visibility(self) -> None:
        """Match the bottom log pane to the View switch; only the widget hides, logging continues."""
        pane = getattr(self, "log_output", None)
        if pane is not None:
            pane.setVisible(bool(self.app_config.get("log_visible", True)))

    def toggle_window_icon(self, checked: bool) -> None:
        self.app_config["use_alt_icon"] = bool(checked)
        self.save_app_config()
        self.apply_window_icon()

    def set_fullscreen(self, enabled: bool) -> None:
        self.showFullScreen() if enabled else self.showNormal()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "action_fullscreen"):
            self.action_fullscreen.blockSignals(True)
            self.action_fullscreen.setChecked(self.isFullScreen())
            self.action_fullscreen.blockSignals(False)
        super().changeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._stretch_tab_bar()

    def _stretch_tab_bar(self) -> None:
        """Hold the tab bar as wide as the page so the pinned Settings tab, with
        its elastic gap tab, sits flush against the right edge."""
        if not hasattr(self, "tabs"):
            return
        bar = self.tabs.tabBar()
        if isinstance(bar, RightPinnedTabBar):
            bar.setMinimumWidth(max(0, self.tabs.width() - 4))

    def fit_window_to_screen(self) -> None:
        """One-time startup guard: shrink the window only if the default size exceeds the screen; never repositions it."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        max_w = max(320, int(area.width() * 0.95))
        max_h = max(240, int(area.height() * 0.95))
        if self.width() > max_w or self.height() > max_h:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))

    def current_settings(self, silent: bool = False) -> dict | None:
        try:
            remote_root = self.remote_root_input.text().strip()
            if not remote_root.startswith("/"):
                raise ValueError("Remote directory must be an absolute path, such as /www_berry")
            remote_root = posixpath.normpath(remote_root)
            if remote_root == "/":
                raise ValueError("Remote directory cannot be the filesystem root")
            if not self.host_input.text().strip():
                raise ValueError("Enter the device hostname or IP address")
            if not self.username_input.text().strip():
                raise ValueError("Enter the SSH username")
        except ValueError as error:
            if not silent:
                QMessageBox.warning(self, "Check settings", str(error))
            return None
        return {
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "key_file": self.key_input.text().strip(),
            "remote_root": remote_root,
            "source": str(self.app_config.get("source", DEFAULT_PROFILE["source"])),
            "trust_unknown_host": self.trust_host_checkbox.isChecked(),
            "remember_password": self.remember_password_checkbox.isChecked(),
            "ssh_timeout": self.timeout_input.value(),
            "show_hidden_files": bool(self.app_config.get("show_hidden_files", False)),
        }

    def _live_transport_states(self) -> dict[str, bool]:
        """The app's live SSH/SFTP state; one open main session lights both dots, since it carries both transports."""
        shared = bool(self.shared_conn.active() or self.main_connected)
        return {"sftp": shared, "ssh": shared}

    def _transport_tab_pages(self) -> dict[QWidget, str]:
        """The tabs that carry transport dots, each mapped to its addon.

        An empty id means the app's own SSH terminal tab, which answers to the
        app-wide switches alone; an addon's tab also answers to the SSH and SFTP
        switches on that addon's own row."""
        pages: dict[QWidget, str] = {}
        ssh = getattr(self, "ssh_page", None)
        if ssh is not None and self.tabs.indexOf(ssh) >= 0:
            pages[ssh] = ""
        manager = getattr(self, "addons", None)
        if manager is not None:
            for addon in manager.addons:
                for entry in (addon.tabs or {}).values():
                    if entry.get("web"):
                        continue
                    page = entry.get("page")
                    if page is not None and self.tabs.indexOf(page) >= 0:
                        pages[page] = addon.id
        return pages

    def _tab_transport_states(self, addon_id: str) -> dict[str, str]:
        """What one tab's dots may say, in the same three states the addon's
        Settings row uses - and for the app's own terminal tab, with no addon.

        A transport the addon never declared is left out of the answer, so its
        dot is not drawn at all: an addon that only speaks SSH wears only the
        ``s`` mark.
        """
        manager = getattr(self, "addons", None)
        if manager is None:
            return {}
        addon = manager.find(addon_id) if addon_id else None
        if addon is None:
            declared = ("ssh", "sftp")
        else:
            declared = tuple(getattr(addon, "requires", ()) or ())
        return {transport: manager.transport_state(addon, transport)["mode"]
                for transport in ("ssh", "sftp") if transport in declared}

    def sync_tab_transport_dots(self) -> None:
        """Put an s/f dot beside each tab that owns a connection; rebuilt every pass since setTabButton takes ownership."""
        wanted = self._transport_tab_pages()
        bar = self.tabs.tabBar()
        keep: list[TabTransportDots] = []
        for index in range(self.tabs.count()):
            existing = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            # A close button belongs to the tab itself, not to this pass:
            # strip everything else and re-put the dots that are still wanted.
            # The name is read off the widget that wears it, so the two cannot
            # drift apart and leave every close button stripped.
            if existing is not None and existing.objectName() != TabCloseButton.NAME:
                bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
            page = self.tabs.widget(index)
            if page in wanted:
                dots = TabTransportDots()
                dots.set_state(self._tab_transport_states(wanted[page]))
                keep.append(dots)
                bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, dots)
        self._tab_dots = keep

        # The dots and the pop-out mark are the two things that live inside a
        # tab, and both answer to the same moments - a tab arrives, goes away or
        # changes hands - so one pass refreshes the other rather than every
        # caller having to remember both.
        self.sync_tab_popout_buttons()

        if hasattr(bar, "refit"):
            bar.refit()

    def _popout_tab_pages(self) -> dict[QWidget, Any]:
        """The tabs standing in the bar that belong to an addon with a window on.

        The interesting case is a page that has been docked back: everything else
        such an addon owns is already out of the bar, and a tab the app owns -
        the terminal, a browser tab, Settings - has no window to be popped into
        and is not listed here at all.
        """
        pages: dict[QWidget, Any] = {}
        manager = getattr(self, "addons", None)
        if manager is None:
            return pages
        for addon in manager.addons:
            if not addon.effective_popout:
                continue
            for entry in (addon.tabs or {}).values():
                page = entry.get("page")
                if page is not None and self.tabs.indexOf(page) >= 0:
                    pages[page] = addon
        return pages

    def sync_tab_popout_buttons(self) -> None:
        """Put a pop-out mark on the left of every tab that can leave the bar.

        Rebuilt whole, the way :meth:`sync_tab_transport_dots` builds the dots -
        ``setTabButton`` takes ownership of whatever it is handed, so the last
        pass is held in a name of this window's own until the next one replaces
        it. The mark goes on the tab's left because its right is where the
        connection dots and a close mark already live.
        """
        wanted = self._popout_tab_pages()
        bar = self.tabs.tabBar()
        keep: list[TabPopoutButton] = []
        for index in range(self.tabs.count()):
            held = bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
            if held is not None and held.objectName() != TabPopoutButton.NAME:
                continue              # that slot is somebody else's to fill
            addon = wanted.get(self.tabs.widget(index))
            if addon is None:
                if held is not None:
                    bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, None)
                continue
            button = TabPopoutButton(bar)
            button.clicked.connect(
                lambda _=False, a=addon, p=self.tabs.widget(index):
                self.pop_tab_out(a, p))
            keep.append(button)
            bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, button)
        self._popout_buttons = keep
        if hasattr(bar, "refit"):
            bar.refit()

    def pop_tab_out(self, addon: Any, page: QWidget) -> bool:
        """Take one addon's tab out of the bar and into its window; the mark's press."""
        manager = getattr(self, "addons", None)
        if manager is None or addon is None:
            return False
        for name, entry in (addon.tabs or {}).items():
            if entry.get("page") is page:
                return bool(manager.pop_tab(str(addon.id), str(name)))
        return False

    # Tab order

    def capture_tab_order(self) -> list[str]:
        """The titles of the reorderable tabs, left to right.

        The pinned gap and the whole run beside it - Settings and every system
        tab - are always last, so they are left out here and answered for by
        :meth:`capture_pinned_order`. Leaving one in would let a later apply drag
        a pinned tab back into the movable row, and would mix the two runs in the
        file the order is kept in.
        """
        bar = self.tabs.tabBar()
        pinned = getattr(bar, "is_pinned", None)
        titles = []
        for i in range(self.tabs.count()):
            if callable(pinned) and pinned(i):
                continue
            titles.append(tab_caption(bar, i))
        return titles

    def apply_tab_order(self, order: list[str] | None) -> None:
        """Drag the tabs into order (titles); missing titles are skipped and an omitted tab stays last."""
        if not order:
            return
        bar = self.tabs.tabBar()
        pinned = getattr(bar, "pinned_indices", None)
        held = set(pinned() if callable(pinned) else [])
        gap = getattr(self, "_gap_page", None)
        if gap is not None:
            held.add(self.tabs.indexOf(gap))
        target = 0
        moved = False
        for title in order:
            current = next(
                (i for i in range(target, self.tabs.count())
                 if i not in held and tab_caption(bar, i) == title),
                None,
            )
            if current is None:
                continue
            if current != target:
                bar.moveTab(current, target)
                moved = True
            target += 1
        if moved:
            self._sync_pinned_settings()
        if hasattr(bar, "refit"):
            bar.refit()

    def _apply_remembered_order(self) -> None:
        """Reorder to the stored arrangement when remembering is on; no-op until the built-in order is captured."""
        if getattr(self, "_natural_tab_order", None) is None:
            return
        if bool(self.app_config.get("remember_tab_order", True)):
            self.apply_tab_order(self.app_config.get("tab_order"))
            self.apply_pinned_order(self.app_config.get("pinned_tab_order"))
            self._sync_pinned_settings()

    def _select_default_tab(self, title: str | None = None) -> None:
        """Show the configured start tab, falling back to the first one."""
        wanted = title or self.app_config.get("default_tab", "Berry")
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            if tab_caption(bar, i) == wanted:
                self.tabs.setCurrentIndex(i)
                return
        if self.tabs.currentIndex() < 0 and self.tabs.count():
            self.tabs.setCurrentIndex(0)

    def _build_tab_order_controls(self) -> QWidget:
        """The order editor on the Startup page: the tabs on the bar, as rows.

        One row per tab the user may place, in the order the bar shows them, and
        then the run pinned to the right edge under a heading of its own. A row
        can be dragged, or selected and moved with the arrows, and either way the
        tabs follow at once and the order is written down then - there is nothing
        to remember to save. The two runs are kept apart: the heading is not a tab
        and cannot be picked up, and a row dragged across it is put back rather
        than obeyed, so a pinned tab can change its neighbours but never leave the
        edge it belongs to.
        """
        listed = QListWidget()
        listed.setObjectName("tabOrderList")
        listed.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        listed.setDefaultDropAction(Qt.DropAction.MoveAction)
        listed.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        listed.setUniformItemSizes(True)
        listed.setSpacing(zoom_size(2))
        # Both runs and the row between them are read at a glance, given that a
        # profile with its web tabs open has eight or nine to show.
        listed.setMaximumHeight(zoom_size(190))
        listed.setToolTip(
            "Every tab you can place, in the order they appear on the bar. Drag a "
            "row, or pick one and use the arrows: the tabs move straight away and "
            "this order is what the app starts with next time. The tabs under "
            "'Pinned at the right edge' only change places among themselves.")
        self.tab_order_list = listed
        # An internal drag is reported by the model, which is the one signal that
        # also covers the arrows' take-and-reinsert.
        model = listed.model()
        if model is not None:
            model.rowsMoved.connect(self._commit_tab_order_list)

        up = QPushButton("Up")
        up.setToolTip("Move the selected tab one place left on the bar")
        up.clicked.connect(lambda: self._move_order_row(-1))
        down = QPushButton("Down")
        down.setToolTip("Move the selected tab one place right on the bar")
        down.clicked.connect(lambda: self._move_order_row(1))
        reset = QPushButton("Reset to built-in order")
        reset.setToolTip("Put every tab back the way the program builds them, "
                         "the pinned run with them")
        reset.clicked.connect(self.reset_tab_order)
        buttons = QHBoxLayout()
        buttons.setSpacing(zoom_size(6))
        for control in (up, down, reset):
            buttons.addWidget(control)
        buttons.addStretch(1)
        button_wrap = QWidget()
        button_wrap.setLayout(buttons)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(zoom_size(4))
        column.addWidget(listed)
        column.addWidget(button_wrap)
        wrap = QWidget()
        wrap.setLayout(column)
        listed.setEnabled(bool(self.app_config.get("remember_tab_order", True)))
        self._refresh_tab_order_list()
        return wrap

    def _order_heading(self) -> QListWidgetItem:
        """The row that divides the two runs in the order list.

        It is drawn disabled and cannot be picked up, dragged or dropped on, so
        the only thing it can do is say where the pinned tabs begin - which is
        also what tells a commit which rows belong to which saved order.
        """
        head = QListWidgetItem(ORDER_PINNED_HEAD)
        head.setFlags(Qt.ItemFlag.NoItemFlags)
        head.setToolTip("These tabs stay at the right edge of the bar; drag them "
                        "to change the order among themselves only")
        return head

    def _refresh_tab_order_list(self) -> None:
        """Refill the rows from the bar, after a tab went in, came out or moved."""
        listed = getattr(self, "tab_order_list", None)
        if listed is None:
            return
        self._order_list_syncing = True
        try:
            listed.clear()
            for title in self.capture_tab_order():
                listed.addItem(QListWidgetItem(title))
            pinned = self.capture_pinned_order()
            if pinned:
                listed.addItem(self._order_heading())
                for title in pinned:
                    listed.addItem(QListWidgetItem(title))
        finally:
            self._order_list_syncing = False

    def _order_list_rows(self) -> list[str]:
        """What the list says the order is, top to bottom."""
        listed = getattr(self, "tab_order_list", None)
        if listed is None:
            return []
        return [listed.item(i).text() for i in range(listed.count())]

    def _commit_tab_order_list(self, *_args: Any) -> None:
        """Apply and store both runs the list shows, the moment either changes.

        The rows are sorted by which tabs are actually pinned rather than by the
        side of the heading they end up on, so a pinned row dragged up into the
        movable list moves the store along the edge instead of unpinning it.
        """
        if getattr(self, "_order_list_syncing", False):
            return
        if getattr(self, "_natural_tab_order", None) is None:
            return
        rows = self._order_list_rows()
        if not rows:
            return
        movable, pinned = sort_into_runs(rows, self.capture_pinned_order())
        if not movable and not pinned:
            return
        self.app_config["tab_order"] = movable
        self.app_config["pinned_tab_order"] = pinned
        self.save_app_config()
        self.apply_tab_order(movable)
        self.apply_pinned_order(pinned)
        # Reordering must not change what is on screen, so the tab being looked
        # at is put back where it was rather than left wherever the moves land.
        shown = next((tab_caption(self.tabs.tabBar(), i)
                      for i in range(self.tabs.count())
                      if self.tabs.widget(i) is self.tabs.currentWidget()), "")
        if shown:
            self._select_default_tab(shown)
        # A row that crossed the heading was put back rather than obeyed, so the
        # list is refilled to say what the bar really did. A move inside its own
        # run already shows the truth and is left alone, keeping the selection.
        if (movable, pinned) != split_order_rows(rows):
            self._refresh_tab_order_list()

    def _move_order_row(self, step: int) -> None:
        """One arrow click: trade the selected row for its neighbour in its own run.

        The heading is a wall rather than a neighbour: a pinned row is moved along
        the pinned run and a movable one along the movable rows, so the arrows give
        the same answer a drag inside one run gives.
        """
        listed = getattr(self, "tab_order_list", None)
        if listed is None:
            return
        row = listed.currentRow()
        if row < 0:
            return
        rows = listed.count()
        head = next((i for i in range(rows)
                     if listed.item(i) is not None
                     and listed.item(i).text() == ORDER_PINNED_HEAD), -1)
        low, high = (0, rows) if head < 0 else \
            ((0, head) if row < head else (head + 1, rows))
        to = row + int(step)
        if not (low <= to < high):
            return
        item = listed.takeItem(row)
        listed.insertItem(to, item)
        listed.setCurrentRow(to)
        self._commit_tab_order_list()

    def reset_tab_order(self) -> None:
        """Put the tabs back the way the program builds them and forget the
        saved arrangement (the remembered order is restored next launch too).

        The pinned run is reset with the rest, since it is remembered alongside
        it: one button answers for both, and there is no other way back for a
        pinned order that has stopped making sense.
        """
        natural = getattr(self, "_natural_tab_order", None) or []
        pinned = getattr(self, "_natural_pinned_order", None) or []
        self.apply_tab_order(natural)
        self.apply_pinned_order(pinned)
        self._sync_pinned_settings()
        self.app_config["tab_order"] = list(natural)
        self.app_config["pinned_tab_order"] = list(pinned)
        self.save_app_config()
        self._refresh_tab_order_list()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))
        self._select_default_tab()
        self.log("Tab order reset to default")

    def _maybe_persist_order(self) -> None:
        """After a drag, store the new order only when remembering is on and a
        tab really moved (selecting a tab must not write the config).

        The run is synced first, which pulls a pinned tab dragged out of the edge
        back into it and sends a movable one dragged past the gap the other way;
        what is left after that is the user's doing and so is worth writing down.
        """
        if getattr(self, "_natural_tab_order", None) is None:
            return
        if not bool(self.app_config.get("remember_tab_order", True)):
            return

        self._sync_pinned_settings()
        changed = False
        order = self.capture_tab_order()
        if order != (self.app_config.get("tab_order") or []):
            self.app_config["tab_order"] = order
            changed = True
        pinned = self.capture_pinned_order()
        if pinned != (self.app_config.get("pinned_tab_order") or []):
            self.app_config["pinned_tab_order"] = pinned
            changed = True
        if changed:
            self.save_app_config()
        # The list on the Startup page is a window onto the bar, so a drag there
        # has to show up here.
        self._refresh_tab_order_list()

    def _on_tab_order_toggled(self, checked: bool) -> None:
        """Settings > Startup: start (or stop) remembering the tab order. Switching
        it on keeps the arrangement that is on screen right now."""
        self.app_config["remember_tab_order"] = bool(checked)
        if checked:
            self.app_config["tab_order"] = self.capture_tab_order()
            self.app_config["pinned_tab_order"] = self.capture_pinned_order()
        self.save_app_config()
        listed = getattr(self, "tab_order_list", None)
        if listed is not None:
            listed.setEnabled(bool(checked))
        self.log("Tab order will be remembered" if checked
                 else "Tab order will not be remembered")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is getattr(self, "_order_tab_bar", None) \
                and event.type() == QEvent.Type.MouseButtonRelease:

            QTimer.singleShot(0, self._maybe_persist_order)
        return super().eventFilter(obj, event)

    def set_status_dot(self, which: str, mode: str) -> None:
        """Light or put out the app's own transport dot; they live under the connection panel now."""
        dot = self.ssh_dot if which == "ssh" else self.sftp_dot
        word = self.ssh_state_label if which == "ssh" else self.sftp_state_label
        online = mode == "online"
        dot.setObjectName("statusDotOnline" if online else "statusDotOffline")
        dot.setToolTip(f"{which.upper()}: "
                       + ("connected" if online else "not connected"))
        word.setText("connected" if online else "closed")
        word.setStyleSheet("color: #23d18b;" if online else "color: #c51a4a;")
        if online:
            self.connected_once = True
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        self.addons.refresh_connection_dots()
        self.sync_tab_transport_dots()

    def _transport_state_part(self, transport: str,
                              state: dict[str, str]) -> QWidget:
        """One ``SSH ● connected`` trio, for an addon's line in the status list.

        The state is the same answer as the dot beside that transport's switch on
        the addon's own row: ``connected`` while it is usable now, ``ready``
        while every switch is on but no connection stands up, ``off`` while a
        switch refuses it. The tool tip says whose connection it rides - the
        app's one main session carries both transports, and an addon with a
        connection of its own brings that up itself, per call.
        """
        mode = state.get("mode", "off")
        word_text = TRANSPORT_DOT.get(mode, TRANSPORT_DOT["off"])[1]
        colour = {"on": "color: #23d18b;",
                  "ready": "color: #ffb020;"}.get(mode, "color: #c51a4a;")
        row = QHBoxLayout()
        row.setSpacing(4)
        caption = QLabel(transport.upper())
        caption.setStyleSheet("color: #aaaaaa;")
        dot = QLabel("\u25CF")
        transport_dot(dot, mode)
        dot.setToolTip(state.get("tip", ""))
        word = QLabel(word_text)
        word.setStyleSheet(colour)
        for part in (caption, dot, word):
            row.addWidget(part)
        wrap = QWidget()
        wrap.setLayout(row)
        return wrap

    def refresh_connection_overview(self) -> None:
        """List running addons on the Settings Connection page with each declared transport's state and which connection it uses."""
        layout = getattr(self, "connection_overview_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            stale = item.widget()
            if stale is not None:
                stale.setParent(None)
                stale.deleteLater()
        manager = getattr(self, "addons", None)
        running = manager.loaded() if manager is not None else []
        if not running:
            empty = QLabel("No addons are running.")
            empty.setStyleSheet("color: #8f8f8f;")
            layout.addWidget(empty)
            return
        for addon in running:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(QLabel(addon.name or addon.id))
            declared = tuple(getattr(addon, "requires", ()) or ())
            used = [transport for transport in ("ssh", "sftp")
                    if transport in declared]
            # The same four answers the addon's row gives, out of the same one
            # decision, so the two lists cannot tell the user different things.
            saying = connection_choice(
                declared, bool(addon.is_bundled),
                str(getattr(addon, "connection", "")))
            if used:
                for transport in used:
                    row.addWidget(self._transport_state_part(
                        transport, manager.transport_state(addon, transport)))
                own_now = bool(getattr(addon, "uses_own", addon.own_connection))
                if saying == "own":
                    words = "on its own connection"
                    why = ("Its manifest declares \"connection\": \"own\", so it "
                           "opens a connection of its own for every call and never "
                           "the app's session: its dots say whether it may and "
                           "whether it has, not whether the app's session is up.")
                elif saying == "both":
                    # It may ride either, so the line says which it is on right
                    # now and where the choice is made.
                    words = ("on its own connection now" if own_now
                             else "on the main connection now")
                    why = ("Its manifest declares \"connection\": \"both\", so "
                           "either is allowed and the switch on its row picks one: "
                           "the app's one main session, or a connection of its own "
                           "per call, which then needs no Connect press.")
                else:
                    words = "on the main connection"
                    why = ("This addon shares the app's one main SSH/SFTP session, "
                           "so it is connected exactly when that session is and its "
                           "own switches are on.")
                mode = QLabel(words)
                mode.setStyleSheet("color: #8f8f8f;")
                mode.setToolTip(
                    why + "\nDeclared in its manifest: \"connection\": "
                    + repr(str(getattr(addon, "connection", "") or "")) + ".")
                row.addWidget(mode)
            else:
                quiet = QLabel("no device connection")
                quiet.setStyleSheet("color: #6a6a6a;")
                quiet.setToolTip("This addon declared no transport under "
                                 '\"requires\" in its manifest, so it reaches no '
                                 "device: what it does, it does on this machine.")
                row.addWidget(quiet)
            row.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(row)
            layout.addWidget(wrap)

    def log(self, message: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.log_output.append(timestamped)
        if self.app_config.get("logging_enabled", True):
            try:
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with LOG_FILE.open("a", encoding="utf-8") as handle:
                    handle.write(timestamped + "\n")
            except OSError:
                pass
        max_lines = int(self.app_config.get("max_log_lines", 500))
        document = self.log_output.document()
        while document.blockCount() > max_lines:
            cursor = QTextCursor(document.firstBlock())
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def addon_event(self, name: str, payload: dict | None = None, **extra: Any) -> None:
        """Tell the addons what happened; never lets a broken addon or manager-less app reach the caller."""
        manager = getattr(self, "addons", None)
        if manager is None:
            return
        data = dict(payload or {})
        data.update(extra)
        try:
            manager.notify(name, data)
        except Exception as error:
            self.log(f"addon event '{name}' failed: {error}")

    def _on_tab_changed(self, index: int) -> None:
        self.addon_event("tab_changed",
                         tab=tab_caption(self.tabs.tabBar(), index), index=index)

    def closeEvent(self, event: QCloseEvent) -> None:
        if getattr(self, "_closing", False):
            event.ignore()                    # already on the way down
            return
        # Ask first: cancelling here must not have taken the addons or the
        # connection down with it.
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Connection in progress",
                "The main connection is still being made. Abort and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._maybe_persist_order()
        self.save_current_profile_edits()
        self.addon_event("shutdown")
        # No thread may outlive the window: Qt aborts the process when a
        # QThread is destroyed mid-run, and a restart is exactly the close
        # that happens over live work - a connect in flight, a quick
        # command, a shell or a reboot watch. Each gets a polite ask, a
        # bounded wait and only then the hard stop; the closing flag keeps
        # their signals from popping dialogs or reconnecting meanwhile.
        self._stop_thread(self.worker)
        self._stop_thread(self.exec_worker)
        if self.reboot_worker is not None:
            self._stop_thread(self.reboot_worker, self.reboot_worker.cancel)
        if self.shell_worker is not None:
            self._stop_thread(self.shell_worker, self.shell_worker.stop)
        if self.addon_dev is not None:
            self.addon_dev.shutdown()         # no build child outlives the app
        # Addons last before the window goes: stopping a process bridge pumps
        # the event queue while it waits, so an addon mid-request can finish
        # cleanly instead of stalling this thread; anything still alive after
        # that is killed with its whole process tree.
        self.addons.shutdown_all()
        self.addons.bus.dead = True           # late jobs only reach deleted widgets
        self._quiesce_web_views()
        self._close_shared_conn()
        if self.tray is not None:
            self.tray.hide()    # a dead app must not leave a mark to click
        super().closeEvent(event)

    def _close_shared_conn(self) -> None:
        """Close the shared session without ever letting its internals
        outstall the shutdown: paramiko can block in close (joining threads
        that a killed worker left behind), and a dying app must not wait."""
        closer = threading.Thread(target=self.shared_conn.close,
                                  daemon=True, name="conn-closer")
        closer.start()
        closer.join(3)

    @staticmethod
    def _stop_thread(thread: QObject | None, ask: Any = None) -> None:
        """Bring one worker thread down for good: polite ask, interruption,
        a bounded wait, and terminate only as the last resort."""
        if thread is None or not thread.isRunning():
            return
        if callable(ask):
            try:
                ask()
            except Exception:
                pass
        thread.requestInterruption()
        if not thread.wait(6000):
            thread.terminate()
            thread.wait(1500)

    def _quiesce_web_views(self) -> None:
        """Stop every embedded page loading before the window is torn down."""
        for entry in list(getattr(self, "_web_tab_entries", [])) + \
                list(getattr(self, "_transient_tab_entries", [])):
            view = entry.get("view")
            if view is None:
                continue
            try:
                view.stop()
            except RuntimeError:
                pass

    # Settings tab

    def _make_toggle_group(self, title: str, checked: bool) -> tuple[ToggleGroup, QFormLayout]:
        """A toggle-headed group whose indented form body hides when off."""
        group = ToggleGroup(title, checked, body="form")
        return group, group.body_layout

    def _make_category(self, title: str, checked: bool, tip: str = "") -> tuple[ToggleGroup, QVBoxLayout]:
        """A top-level Settings category with a switch in its header; the
        caller wires the state to the matching tab."""
        group = ToggleGroup(title, checked, body="box", tip=tip)
        return group, group.body_layout

    def _build_git_token_box(self) -> QGroupBox:
        """The profile's one GitHub token: the field, a show switch and a test.

        Two things are bought with a token and the box names both - a private
        repository can be read, and GitHub's rate limit becomes the authenticated
        one rather than the shared anonymous bucket. Addons keep no token of their
        own in this version: the switch on an addon's row in the store's Addon
        manager decides whether ``api.session.git_token()`` answers it with this
        value, so an empty field leaves every addon anonymous, which is exactly
        what an addon with no token has always been.
        """
        box = QGroupBox("GitHub token")
        tip_on_title(box,
            "The one token this profile has, kept in the profile's own file - the "
            "same place the remembered-password switch is written. The addons "
            "switched on for it read it through the api; nothing here is sent "
            "anywhere but GitHub.")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        words = QLabel(
            "A token buys two things: a private repository can be read, and "
            "GitHub's rate limit becomes the authenticated one instead of the "
            "shared sixty requests an hour. Nothing of your own is needed for "
            "public addons - leave this blank and every one of them stays "
            "anonymous.")
        words.setStyleSheet("color: #aaaaaa;")
        words.setWordWrap(True)
        form.addRow(words)

        self.git_token_input = QLineEdit(str(self.app_config.get("git_token", "")))
        self.git_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.git_token_input.setPlaceholderText("ghp_... - optional")
        self.git_token_input.setToolTip(
            "A personal access token, classic or fine-grained, with at least read "
            "access to whatever repository the store or an addon is asked to "
            "reach. It is written to this profile's file and nowhere else.")
        self.show_git_token_check = QCheckBox("Show")
        self.show_git_token_check.setToolTip(
            "Write the field out in plain letters rather than dots, while you "
            "check what is in it. Nothing is sent by this switch.")
        self.show_git_token_check.toggled.connect(
            lambda on: self.git_token_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on
                else QLineEdit.EchoMode.Password))
        field = QHBoxLayout()
        field.addWidget(self.git_token_input, 1)
        field.addWidget(self.show_git_token_check)
        field_wrap = QWidget()
        field_wrap.setLayout(field)
        form.addRow("Token", field_wrap)

        self.git_token_test_button = QPushButton("Test")
        self.git_token_test_button.setToolTip(
            "Ask GitHub who it is talking to, through its own /rate_limit - the "
            "identity and the hour's allowance this token gives, or that nothing "
            "was sent at all. Costs no API request of the counted kind.")
        self.git_token_test_button.clicked.connect(self.test_git_token)
        self.git_token_state = QLabel("Not tested yet.")
        self.git_token_state.setStyleSheet("color: #aaaaaa;")
        self.git_token_state.setWordWrap(True)
        line = QHBoxLayout()
        line.addWidget(self.git_token_test_button)
        line.addWidget(self.git_token_state, 1)
        line_wrap = QWidget()
        line_wrap.setLayout(line)
        form.addRow(line_wrap)

        # Written the moment the field is left, as the other connection lines are:
        # an addon reading the token has to be given the one on screen.
        self.git_token_input.editingFinished.connect(self.save_git_token)
        return box

    def save_git_token(self, _value: object = None) -> None:
        """Keep the field's token in the profile, and say what that means here."""
        token = self.git_token_input.text().strip()
        self.app_config["git_token"] = token
        self.save_app_config()
        self.git_token_state.setText(
            "Saved for this profile." if token
            else "Nothing kept: every addon reaches GitHub anonymously.")

    def test_git_token(self) -> None:
        """Ask GitHub, off the GUI thread, whose requests these would be."""
        if self.git_token_worker is not None and self.git_token_worker.isRunning():
            return
        self.git_token_state.setText("Asking GitHub...")
        self.git_token_test_button.setEnabled(False)
        worker = GitTokenWorker(self.git_token_input.text(), parent=self)
        worker.answered.connect(self._git_token_answered)
        worker.stopped.connect(self._git_token_answered)
        worker.finished.connect(worker.deleteLater)
        self.git_token_worker = worker
        worker.start()

    def _git_token_answered(self, words: str) -> None:
        """Put GitHub's answer on the page and in the log, and free the button."""
        self.git_token_state.setText(words)
        self.git_token_test_button.setEnabled(True)
        self.git_token_worker = None
        self.log(f"github token: {words}")

    def _settings_category(self, title: str) -> QVBoxLayout:
        """One page of the Settings tab bar, with its own scrolling column."""
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(16)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(host)
        self.settings_tabs.addTab(area, title)
        return body

    def build_settings_tab(self) -> None:
        """Settings as a bar of its own: one page per section, one per shipped
        addon, and one for everything installed later."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        bar = QTabWidget()
        bar.setObjectName("settingsTabs")
        self.settings_tabs = bar
        outer.addWidget(bar, 1)
        connection_tab = self._settings_category("Connection")

        connection_group = self.connection_group
        panel_body = connection_group.body_layout

        status_group = CollapseGroup(
            "Connection status",
            tip="Read-only: the program's own SSH and SFTP state, and what every "
                "running addon has connected with. Nothing here changes a setting.",
        )
        status_body = status_group.body_layout

        self.ssh_caption = QLabel("SSH")
        self.ssh_caption.setStyleSheet("color: #aaaaaa;")
        self.ssh_dot = QLabel("\u25CF")
        self.ssh_dot.setObjectName("statusDotOffline")
        self.ssh_dot.setToolTip("SSH: not connected")
        self.ssh_state_label = QLabel("closed")
        self.ssh_state_label.setStyleSheet("color: #c51a4a;")
        self.sftp_caption = QLabel("SFTP")
        self.sftp_caption.setStyleSheet("color: #aaaaaa;")
        self.sftp_dot = QLabel("\u25CF")
        self.sftp_dot.setObjectName("statusDotOffline")
        self.sftp_dot.setToolTip("SFTP: not connected")
        self.sftp_state_label = QLabel("closed")
        self.sftp_state_label.setStyleSheet("color: #c51a4a;")
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        status_row.addWidget(self.ssh_caption)
        status_row.addWidget(self.ssh_dot)
        status_row.addWidget(self.ssh_state_label)
        status_row.addSpacing(12)
        status_row.addWidget(self.sftp_caption)
        status_row.addWidget(self.sftp_dot)
        status_row.addWidget(self.sftp_state_label)
        status_row.addStretch(1)
        status_wrap = QWidget()
        status_wrap.setLayout(status_row)
        status_body.addWidget(status_wrap)

        heading = QLabel("Running addons")
        heading.setStyleSheet("color: #8f8f8f;")
        status_body.addWidget(heading)
        self.connection_overview = QWidget()
        self.connection_overview_layout = QVBoxLayout(self.connection_overview)
        self.connection_overview_layout.setContentsMargins(0, 0, 0, 0)
        self.connection_overview_layout.setSpacing(4)
        status_body.addWidget(self.connection_overview)

        ssh_box = QGroupBox("SSH terminal")
        tip_on_title(ssh_box,
            "Options for the SSH terminal tab. The terminal is a shell on the "
            "main connection above, not a session of its own: the startup "
            "switch on that connection brings it up too.")
        ssh_body = QVBoxLayout(ssh_box)
        ssh_body.setContentsMargins(16, 12, 12, 10)
        ssh_body.setSpacing(8)

        self.ssh_tab_check = ToggleSwitch("Show the SSH terminal tab")
        self.ssh_tab_check.setToolTip(
            "Add or remove the SSH terminal tab. This switch is also the SSH "
            "gate for the addons and the device API: switched off, they are "
            "refused SSH work, and the terminal closes, until it is on. Each "
            "addon carries an SSH switch of its own on its row - Settings for a "
            "shipped addon, the store's Addon manager for an installed one - and "
            "both have to be on for that addon to use SSH. The master switch in "
            "this panel's header stops both protocols for everyone, and an "
            "addon may only work once the main connection is open."
        )
        self.ssh_tab_check.setChecked(bool(self.app_config.get("ssh_enabled", True)))
        ssh_body.addWidget(self.ssh_tab_check)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Terminal font size"))
        self.terminal_font_spin = QSpinBox()
        self.terminal_font_spin.setRange(6, 24)
        self.terminal_font_spin.setValue(int(self.app_config.get("terminal_font_size", 10)))
        font_row.addWidget(self.terminal_font_spin)
        font_row.addStretch(1)
        ssh_body.addLayout(font_row)

        reboot_form = QFormLayout()
        reboot_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        reboot_form.setHorizontalSpacing(12)
        reboot_form.setVerticalSpacing(8)
        self.reboot_wait_spin = QSpinBox()
        self.reboot_wait_spin.setRange(0, 600)
        self.reboot_wait_spin.setSuffix(" s")
        self.reboot_wait_spin.setValue(int(self.app_config.get("reboot_wait_seconds", 30)))
        self.reboot_wait_spin.setToolTip(
            "Time allowed for the device to go down before the first reconnect attempt"
        )
        reboot_form.addRow("Wait before first retry", self.reboot_wait_spin)
        self.reboot_retry_spin = QSpinBox()
        self.reboot_retry_spin.setRange(1, 300)
        self.reboot_retry_spin.setSuffix(" s")
        self.reboot_retry_spin.setValue(int(self.app_config.get("reboot_retry_interval", 10)))
        self.reboot_retry_spin.setToolTip("Seconds between reconnect attempts until the device answers")
        reboot_form.addRow("Retry every", self.reboot_retry_spin)
        self.reboot_auto_watch_check = ToggleSwitch("Offer to watch for the device after a reboot command")
        self.reboot_auto_watch_check.setToolTip(
            "Ask whether to start the wait-and-retry cycle when you send reboot "
            "(or shutdown -r) from the terminal"
        )
        self.reboot_auto_watch_check.setChecked(bool(self.app_config.get("reboot_auto_watch", True)))
        reboot_form.addRow(self.reboot_auto_watch_check)
        ssh_body.addLayout(reboot_form)

        edit_quick_btn = QPushButton("Edit quick commands...")
        edit_quick_btn.clicked.connect(self.edit_quick_commands)
        ssh_body.addWidget(edit_quick_btn)
        panel_body.addWidget(ssh_box)

        sftp_box = QGroupBox("File transfer (SFTP)")
        tip_on_title(sftp_box,
            "The SFTP channel of the main connection, the one Berry browses "
            "files with. Berry itself is switched on its own Settings page, "
            "beside this one.")
        sftp_body = QVBoxLayout(sftp_box)
        sftp_body.setContentsMargins(16, 12, 12, 10)
        sftp_body.setSpacing(8)

        self.sftp_enable_check = ToggleSwitch("Enable the SFTP connection")
        self.sftp_enable_check.setToolTip(
            "The gate for file transfer. Switched off, the Remote dir line goes "
            "away and the addons and the device API are refused SFTP work until "
            "it is on again; the main connection itself stays open for SSH. Each "
            "addon carries an SFTP switch of its own on its row - Settings for a "
            "shipped addon, the store's Addon manager for an installed one - and "
            "both have to be on for that addon to transfer files. The master "
            "switch in this panel's header stops both protocols for everyone."
        )
        self.sftp_enable_check.setChecked(bool(self.app_config.get("sftp_enabled", True)))
        self.sftp_enable_check.toggled.connect(self._on_sftp_enabled_toggled)
        sftp_body.addWidget(self.sftp_enable_check)
        panel_body.addWidget(sftp_box)

        panel_body.addWidget(status_group)
        connection_tab.addWidget(connection_group)
        # The token is the profile's business but not the device's, so it sits
        # beside the connection rather than inside it - and stays reachable when
        # the master switch hides everything above it.
        connection_tab.addWidget(self._build_git_token_box())
        self.refresh_connection_overview()

        startup_group = QGroupBox("Startup & defaults")
        startup_form = QFormLayout(startup_group)
        startup_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        startup_form.setHorizontalSpacing(12)
        startup_form.setVerticalSpacing(8)

        self.default_tab_combo = QComboBox()
        self.default_tab_combo.setToolTip("Tab selected when the app starts (only visible tabs are listed).")
        startup_form.addRow("Default tab", self.default_tab_combo)

        self.tab_order_check = ToggleSwitch("Remember tab order")
        self.tab_order_check.setToolTip(
            "Keep the tabs in the order you set them, next time the app "
            "starts. Turn off to use the built-in order on every launch."
        )
        self.tab_order_check.setChecked(bool(self.app_config.get("remember_tab_order", True)))
        self.tab_order_check.toggled.connect(self._on_tab_order_toggled)
        startup_form.addRow(self.tab_order_check)

        # The order is edited here rather than only by dragging the bar: what
        # you see is the list of tabs, what you drag is the order that gets
        # saved, and it takes effect the moment you let go.
        startup_form.addRow("Tab order", self._build_tab_order_controls())

        # The Addon Dev tab's switch used to sit here. It belongs with the rest
        # of the addon settings, which are now the store's Addon manager page, so
        # the key is written by set_addon_dev_enabled alone.

        self.notify_check = ToggleSwitch("Pop up notifications")
        self.notify_check.setToolTip(
            "Send a notice to the system's notification area as well as the "
            "status line: an addon speaking through api.ui.popup, and this "
            "program's own news, such as an update being available. The app "
            "holds an icon in the area while it runs, which is what the notice "
            "comes from - right-click it to quit, left-click to bring the "
            "window back. Switching this off leaves the window the only place "
            "a message appears."
        )
        self.notify_check.setChecked(
            bool(self.app_config.get("system_notifications", True)))
        startup_form.addRow(self.notify_check)

        self.splash_check = ToggleSwitch("Show the startup splash")
        self.splash_check.setToolTip(
            "The card that appears while the program is opening: the logo, "
            "the part of the boot it is loading and a bar with a percentage, "
            "painted from the theme in force. Switching it off opens the "
            "window straight away, with nothing on screen saying what the "
            "program is doing while it builds."
        )
        self.splash_check.setChecked(
            bool(self.app_config.get("splash_enabled", True)))
        startup_form.addRow(self.splash_check)

        self.logging_group, log_form = self._make_toggle_group(
            "Enable logging to file", bool(self.app_config.get("logging_enabled", True))
        )
        self.max_log_spin = QSpinBox()
        self.max_log_spin.setRange(50, 10000)
        self.max_log_spin.setValue(int(self.app_config.get("max_log_lines", 500)))
        log_form.addRow("Max lines kept on screen", self.max_log_spin)
        log_path_label = QLabel(str(LOG_FILE))
        log_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        log_path_label.setStyleSheet("color: #aaaaaa;")
        log_form.addRow("Log file", log_path_label)
        startup_form.addRow(self.logging_group)
        self._settings_category("Startup & defaults").addWidget(startup_group)

        # Appearance: the look is a file on disk, so this page only chooses it
        # and points at the folder - making one is the Theme Manager's job.
        appearance_group = QGroupBox("Theme")
        tip_on_title(appearance_group,
            "Every theme is one JSON file in the themes folder beside Berry "
            "Manager. The file sets the colours of the whole window and the "
            "shape of the tabs; an addon's own page keeps its own styling.")
        appearance_form = QFormLayout(appearance_group)
        appearance_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.theme_combo = QComboBox()
        self.theme_combo.setToolTip(
            "Draw the window from this theme; the choice is kept in the "
            "profile, so another profile can wear another look.")
        self.theme_combo.currentTextChanged.connect(self._on_theme_chosen_combo)
        appearance_form.addRow("Theme in force", self.theme_combo)
        theme_row = QHBoxLayout()
        open_theme_btn = QPushButton("Open the themes folder...")
        open_theme_btn.setToolTip(f"Show {THEMES_DIR} in the file viewer")
        open_theme_btn.clicked.connect(self.open_themes_folder)
        theme_row.addWidget(open_theme_btn)
        theme_editor_btn = QPushButton("Theme Manager...")
        theme_editor_btn.setToolTip(
            "Make and edit themes, from a template or from the look in force, "
            "with the documentation of every colour token and tab knob.")
        theme_editor_btn.clicked.connect(self.show_theme_manager)
        theme_row.addWidget(theme_editor_btn)
        theme_row.addStretch(1)
        theme_wrap = QWidget()
        theme_wrap.setLayout(theme_row)
        appearance_form.addRow(theme_wrap)
        self.theme_note = QLabel()
        self.theme_note.setStyleSheet("color: #aaaaaa;")
        self.theme_note.setWordWrap(True)
        appearance_form.addRow(self.theme_note)

        # Tool-tips are drawn by the app rather than by Qt, so the switch is the
        # app's to honour: off stops the bubble and the native tip both.
        tips_group = QGroupBox("Tool-tips")
        tip_on_title(tips_group,
            "The notes the window writes for its own controls. They are drawn "
            "by Berry Manager rather than by the toolkit, so this switch asks "
            "the program to stop drawing them - every tip, wherever it comes "
            "from, including an addon's.")
        tips_form = QFormLayout(tips_group)
        tips_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.tooltips_check = ToggleSwitch("Show tool-tips")
        self.tooltips_check.setToolTip(
            "The note that fades in beside the pointer over a control and "
            "follows it: what a button does, why a switch is worth moving, the "
            "whole of a word the interface had to shorten. Switching this off "
            "takes every one of them away, and nothing appears as the pointer "
            "moves about. The choice is kept in the profile.")
        self.tooltips_check.setChecked(
            bool(self.app_config.get("tooltips_enabled", True)))
        self.tooltips_check.toggled.connect(self._on_tooltips_toggled)
        tips_form.addRow(self.tooltips_check)
        # One page holds both groups: _settings_category makes a page per call,
        # so asking for "Appearance" twice would put a second one in the bar.
        appearance_tab = self._settings_category("Appearance")
        appearance_tab.addWidget(appearance_group)
        appearance_tab.addWidget(tips_group)
        self._reload_theme_combo()

        tabs_category = QGroupBox("Custom tabs")
        tip_on_title(tabs_category, "Embedded browser tabs. These are always shown.")
        tabs_body = QVBoxLayout(tabs_category)
        tabs_body.setContentsMargins(16, 12, 12, 10)
        self.tabs_combo = QComboBox()
        self.tabs_combo.setToolTip("Embeddable browser tabs shown after the main tabs.")
        add_tab_btn = QPushButton("Add...")
        add_tab_btn.clicked.connect(self.add_web_tab)
        edit_tab_btn = QPushButton("Edit")
        edit_tab_btn.clicked.connect(self.edit_web_tab)
        remove_tab_btn = QPushButton("Remove")
        remove_tab_btn.setObjectName("dangerAction")
        remove_tab_btn.clicked.connect(self.remove_web_tab)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.tabs_combo, 1)
        tabs_row.addWidget(add_tab_btn)
        tabs_row.addWidget(edit_tab_btn)
        tabs_row.addWidget(remove_tab_btn)
        tabs_body.addLayout(tabs_row)

        self.cert_bypass_check = ToggleSwitch("Accept untrusted HTTPS certificates")
        self.cert_bypass_check.setToolTip(
            "For a self-signed certificate the embedded browser refuses to load the page. When on, "
            "certificate errors are ignored in every web tab. QtWebEngine reads this switch when it "
            "starts, so the app restarts to apply a change. Installing the certificate under "
            "'Trusted Root Certification Authorities' in the Windows certificate store is the safer fix."
        )
        self.cert_bypass_check.setChecked(bool(self.app_config.get("ignore_cert_errors", False)))
        tabs_body.addWidget(self.cert_bypass_check)
        self.cert_bypass_state_label = QLabel()
        self.cert_bypass_state_label.setStyleSheet("color: #aaaaaa;")
        tabs_body.addWidget(self.cert_bypass_state_label)
        self._update_cert_bypass_state()
        self._settings_category("Custom tabs").addWidget(tabs_category)

        # The addons fill the rest of the bar: a page for each that came with the
        # program, then the shared page for the drop-ins and the installs.
        self.addons.build_settings_tabs(self.settings_tabs)

        save_btn = QPushButton("Apply settings")
        save_btn.setObjectName("primaryAction")
        save_btn.clicked.connect(self.apply_settings_from_form)
        outer.addWidget(save_btn)

        self.tabs.addTab(page, "Settings")
        self.settings_page = page

        self.tabs.tabBar().setTabIcon(self.tabs.indexOf(page), settings_tab_icon())
        self._reload_tabs_combo()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))

        self.ssh_tab_check.toggled.connect(self._on_tab_category_toggled)

    def _reload_default_tab_combo(self, select: str | None = None) -> None:
        """List every real tab as the possible startup tab, keeping the choice."""
        chosen = select or self.default_tab_combo.currentText() or self.app_config.get("default_tab", "Berry")
        self.default_tab_combo.blockSignals(True)
        self.default_tab_combo.clear()

        bar = self.tabs.tabBar()
        self.default_tab_combo.addItems(
            [tab_caption(bar, i) for i in range(self.tabs.count())
             if tab_caption(bar, i).strip()]
        )
        idx = self.default_tab_combo.findText(chosen)
        self.default_tab_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.default_tab_combo.blockSignals(False)

    def apply_settings_from_form(self) -> None:
        self.app_config["default_tab"] = self.default_tab_combo.currentText()
        self.app_config["auto_connect"] = self.auto_connect_check.isChecked()
        self.app_config["terminal_font_size"] = self.terminal_font_spin.value()
        self.app_config["max_log_lines"] = self.max_log_spin.value()
        self.app_config["logging_enabled"] = self.logging_group.isChecked()
        self.app_config["ssh_enabled"] = self.ssh_tab_check.isChecked()
        self.app_config["sftp_enabled"] = self.sftp_enable_check.isChecked()
        self.app_config["reboot_wait_seconds"] = self.reboot_wait_spin.value()
        self.app_config["reboot_retry_interval"] = self.reboot_retry_spin.value()
        self.app_config["reboot_auto_watch"] = self.reboot_auto_watch_check.isChecked()
        self.app_config["ignore_cert_errors"] = self.cert_bypass_check.isChecked()
        self.app_config["system_notifications"] = self.notify_check.isChecked()
        self.app_config["splash_enabled"] = self.splash_check.isChecked()
        self.app_config["tooltips_enabled"] = self.tooltips_check.isChecked()
        self.app_config["remember_tab_order"] = self.tab_order_check.isChecked()
        self.app_config["git_token"] = self.git_token_input.text().strip()
        self.save_app_config()
        self.apply_runtime_settings()
        self._apply_remembered_order()
        self._select_default_tab()
        self.refresh_web_tab_urls()
        self.log("Settings applied")
        self.addon_event("settings_applied")

        self._sync_cert_bypass()

    def _on_tab_category_toggled(self, _checked: bool = False) -> None:
        """Persist the SSH tab switch state, show or hide the terminal tab and
        close the shell when SSH goes off. (The Berry tab is switched from its
        own bundled addon category.)"""
        self.app_config["ssh_enabled"] = self.ssh_tab_check.isChecked()
        self.save_app_config()
        if not self.ssh_tab_check.isChecked() and self.shell_worker is not None \
                and self.shell_worker.isRunning():
            # The gate closed: the shell channel goes, the session stays.
            self.shell_worker.stop()
        self.ensure_main_tabs()
        self._apply_remembered_order()
        self._reload_default_tab_combo(select=self.app_config.get("default_tab", "Berry"))
        self.update_connection_ui()
        self.notify_transport_changed("ssh")

    def _on_sftp_enabled_toggled(self, _checked: bool = False) -> None:
        """Persist the SFTP gate: off hides its line and refuses SFTP work for
        the addons and the device API; the main connection stays as it is."""
        self.app_config["sftp_enabled"] = self.sftp_enable_check.isChecked()
        self.save_app_config()
        self.update_connection_ui()
        self.notify_transport_changed("sftp")

    def notify_transport_changed(self, transport: str) -> None:
        """Tell the addons that one app-wide transport gate moved, so a tab that
        shows a connection state can redraw it. Every addon has switches of its
        own as well, and the same event name covers both kinds."""
        self.addons.refresh_connection_dots()
        self.sync_tab_transport_dots()
        self.addon_event(
            "transport_changed", transport=transport, scope="app",
            enabled=bool(self.app_config.get(f"{transport}_enabled", True)))

    def set_addon_dev_enabled(self, on: bool) -> bool:
        """Write the Addon Dev tab switch, and show or hide the tab at once.

        The switch left the Startup form with the rest of the addon settings, so
        the store's Addon manager is what moves it - through
        ``api.session.set_app_setting`` - and this is the only place the key is
        written. The value is kept whether or not anything on screen moved, so an
        addon asking twice cannot leave the profile out of step with the bar.
        """
        wanted = bool(on)
        changed = bool(self.app_config.get("addon_dev_enabled", True)) != wanted
        self.app_config["addon_dev_enabled"] = wanted
        self.save_app_config()
        if changed:
            self.ensure_addon_dev_tab()
            self._apply_remembered_order()
            self.log("the Addon Dev tab " + ("is being shown again" if wanted
                                             else "has been hidden"))
        return wanted

    # Profiles and credentials

    def refresh_profile_menu(self) -> None:
        """Rebuild File > Load profile: one entry per profile, the active one ticked."""
        menu = getattr(self, "profile_menu", None)
        if menu is None:
            return
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = active_profile_name()
        names = profile_names()
        for name in names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.setToolTip("Load this profile's connection details and settings")
            action.triggered.connect(lambda _=False, text=name: self.load_profile(text))
            group.addAction(action)
        if not names:
            empty = menu.addAction("(no profiles)")
            empty.setEnabled(False)

    def load_profile(self, name: str) -> None:
        if not name or name == active_profile_name():
            return

        self.save_current_profile_edits()
        self.app_config = set_active_profile(name)
        self.reload_ui_from_config()
        self.log(f"Switched to profile '{name}'")

    def reload_ui_from_config(self) -> None:
        """Refresh every widget from self.app_config after a profile switch,
        a profile delete or a config import."""
        cfg = self.app_config
        # A profile names a different device, and the app holds exactly one main
        # connection: the session belongs to the profile that opened it.
        if self.main_connected or self.shared_conn.active():
            if self.shell_worker is not None and self.shell_worker.isRunning():
                self.shell_worker.stop()
            self.shared_conn.close()
            self.mark_main_closed("the profile changed")
        self.host_input.setText(str(cfg.get("host", DEFAULT_PROFILE["host"])))
        self.port_input.setValue(int(cfg.get("port", DEFAULT_PROFILE["port"])))
        self.username_input.setText(str(cfg.get("username", DEFAULT_PROFILE["username"])))
        self.key_input.setText(str(cfg.get("key_file", "")))
        self.remote_root_input.setText(str(cfg.get("remote_root", DEFAULT_PROFILE["remote_root"])))
        self.timeout_input.setValue(int(cfg.get("ssh_timeout", 12)))
        self.trust_host_checkbox.setChecked(bool(cfg.get("trust_unknown_host", False)))

        self.remember_password_checkbox.blockSignals(True)
        self.remember_password_checkbox.setChecked(bool(cfg.get("remember_password", False)))
        self.remember_password_checkbox.blockSignals(False)
        self.password_input.clear()
        self.load_saved_password()

        # The one git token travels with the profile, so a switch shows the other
        # profile's token - or none - and the field goes back to wearing dots.
        self.show_git_token_check.blockSignals(True)
        self.show_git_token_check.setChecked(False)
        self.show_git_token_check.blockSignals(False)
        self.git_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.git_token_input.setText(str(cfg.get("git_token", "")))
        self.git_token_state.setText("Not tested yet.")

        self.auto_connect_check.setChecked(bool(cfg.get("auto_connect", False)))
        self.logging_group.setChecked(bool(cfg.get("logging_enabled", True)))
        self.max_log_spin.setValue(int(cfg.get("max_log_lines", 500)))
        self.terminal_font_spin.setValue(int(cfg.get("terminal_font_size", 10)))
        self.reboot_wait_spin.setValue(int(cfg.get("reboot_wait_seconds", 30)))
        self.reboot_retry_spin.setValue(int(cfg.get("reboot_retry_interval", 10)))
        self.reboot_auto_watch_check.setChecked(bool(cfg.get("reboot_auto_watch", True)))
        self.cert_bypass_check.setChecked(bool(cfg.get("ignore_cert_errors", False)))

        self.tab_order_check.blockSignals(True)
        self.tab_order_check.setChecked(bool(cfg.get("remember_tab_order", True)))
        self.tab_order_check.blockSignals(False)

        self.notify_check.blockSignals(True)
        self.notify_check.setChecked(bool(cfg.get("system_notifications", True)))
        self.notify_check.blockSignals(False)

        self.splash_check.blockSignals(True)
        self.splash_check.setChecked(bool(cfg.get("splash_enabled", True)))
        self.splash_check.blockSignals(False)

        self.tooltips_check.blockSignals(True)
        self.tooltips_check.setChecked(bool(cfg.get("tooltips_enabled", True)))
        self.tooltips_check.blockSignals(False)

        set_cert_bypass(bool(cfg.get("ignore_cert_errors")))
        self._update_cert_bypass_state()
        self.action_alt_icon.setChecked(bool(cfg.get("use_alt_icon", False)))

        self.action_log_console.setChecked(bool(cfg.get("log_visible", True)))

        self.ssh_tab_check.setChecked(bool(cfg.get("ssh_enabled", True)))
        self.sftp_enable_check.setChecked(bool(cfg.get("sftp_enabled", True)))

        master_switch = self.connection_group.switch
        master_wanted = bool(cfg.get("master_connection", True))
        master_switch.blockSignals(True)
        master_switch.setChecked(master_wanted)
        master_switch.blockSignals(False)
        self.connection_group.body_widget.setVisible(master_wanted)
        if not self.connections_enabled():
            self.close_all_connections()

        self.rebuild_web_tabs()
        self._reload_tabs_combo()
        self.apply_runtime_settings()
        self.refresh_web_tab_urls()
        self.save_app_config()

        self.addons.reconcile()
        self._apply_remembered_order()
        self._select_default_tab()
        self.addon_event("profile_changed", profile=cfg.get("name", active_profile_name()))

    def new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in profile_names():
            QMessageBox.warning(self, "Duplicate", "A profile with that name already exists.")
            return
        self.save_current_profile_edits()

        self.app_config = create_profile(name, self.app_config)
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        self.log(f"Created profile '{active_profile_name()}' from the current settings")

    def rename_current_profile(self) -> None:
        current = active_profile_name()
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=current or "")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name != current and name in profile_names():
            QMessageBox.warning(self, "Duplicate", "Another profile already has that name.")
            return
        if rename_profile(current, name):
            self.refresh_profile_menu()
            self.log(f"Profile renamed to '{name}'")

    def delete_current_profile(self) -> None:
        current = active_profile_name()
        if len(profile_names()) <= 1:
            QMessageBox.information(self, "Cannot delete", "At least one profile must exist.")
            return
        answer = QMessageBox.question(
            self, "Delete profile",
            f"Delete profile '{current}'? (This removes its saved connection details and settings.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        working = delete_profile(current)
        if working is not None:
            self.app_config = working
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        self.log(f"Deleted profile '{current}'")

    def save_current_profile_edits(self, _value: object = None) -> None:
        cfg = self.app_config
        cfg["host"] = self.host_input.text().strip() or DEFAULT_PROFILE["host"]
        cfg["port"] = self.port_input.value()
        cfg["username"] = self.username_input.text().strip() or DEFAULT_PROFILE["username"]
        cfg["key_file"] = self.key_input.text().strip()
        cfg["remote_root"] = self.remote_root_input.text().strip() or DEFAULT_PROFILE["remote_root"]
        cfg["trust_unknown_host"] = self.trust_host_checkbox.isChecked()
        cfg["remember_password"] = self.remember_password_checkbox.isChecked()
        self.save_app_config()

    def save_app_config(self, _value: object = None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        save_app_config_static(self.app_config)

    def save_runtime_settings(self, _value: object = None) -> None:
        self.app_config["ssh_timeout"] = self.timeout_input.value()
        self.save_app_config()

    def current_credential_id(self) -> str:
        return credential_id(
            self.username_input.text().strip(),
            self.host_input.text().strip(),
            self.port_input.value(),
        )

    def load_saved_password(self) -> None:
        if not self.remember_password_checkbox.isChecked():
            return
        # keyring reads the OS credential store and costs a good slice of a
        # second to come in; it is only wanted when a saved password is
        # actually asked for, so it is not imported on the way up.
        import keyring
        try:
            password = keyring.get_password(KEYRING_SERVICE, self.current_credential_id())
        except Exception as error:
            self.log(f"Unable to read saved password from the OS credential store: {error}")
            return
        if password:
            self.password_input.setText(password)

    def remember_password_changed(self, remember: bool) -> None:
        self.save_current_profile_edits()
        import keyring          # read in on use, not at boot; see load_saved_password
        if remember:
            self.store_password()
            return
        try:
            keyring.delete_password(KEYRING_SERVICE, self.current_credential_id())
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as error:
            self.log(f"Unable to remove saved password from the OS credential store: {error}")

    def store_password(self) -> None:
        password = self.password_input.text()
        if not self.remember_password_checkbox.isChecked() or not password:
            return
        import keyring          # read in on use, not at boot; see load_saved_password
        try:
            keyring.set_password(KEYRING_SERVICE, self.current_credential_id(), password)
        except Exception as error:
            self.log(f"Unable to save password to the OS credential store: {error}")

    def choose_key_file(self, _checked: bool = False) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose SSH private key")
        if path:
            self.key_input.setText(path)
            self.save_current_profile_edits()

    # Menu actions that open a dialog

    def export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export configuration", str(BASE_DIR / "config-export.json"), "JSON files (*.json)"
        )
        if not path:
            return
        sanitized = export_document()
        try:
            Path(path).write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
            QMessageBox.information(
                self, "Exported",
                f"Saved to {path}\n\nAll profiles are included. Passwords are never stored in this file.",
            )
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))

    def import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import configuration", str(BASE_DIR), "JSON files (*.json)")
        if not path:
            return
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        if not isinstance(loaded, dict):
            QMessageBox.critical(self, "Import failed", "File does not contain a JSON object.")
            return
        self.app_config = import_app_config(loaded)
        self.refresh_profile_menu()
        self.reload_ui_from_config()
        QMessageBox.information(self, "Imported", "Configuration replaced from file.")

    def save_log_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log",
            str(BASE_DIR / f"berry_log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.log_output.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About Berry")
        # A release is published under its own tag page, so the link is the tag
        # put where GitHub shows its notes rather than the plain tag list.
        tag = f"{UPDATE_TAG_PREFIX}{APP_VERSION}"
        box.setText(
            f"<b>Berry Manager V{APP_VERSION}</b><br>"
            "Manage routers and devices over ssh&sftp<br>"
            f'GitHub: <a href="{PROJECT_RELEASE_URL}{tag}" '
            f'style="color: #c51a4a">{tag}</a><br>'
            f'License: <a href="{PROJECT_LICENSE_URL}" '
            'style="color: #c51a4a">MIT License</a><br><br>'
            "Copyright &copy; 2026 fligma. Licensed under the MIT License"
        )
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        path = self.current_icon_path()
        if path is not None:
            box.layout().activate()
            side = max(64, box.sizeHint().height())
            logo = QPixmap(str(path)).scaled(
                side, side,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            box.setIconPixmap(logo)
        box.exec()
