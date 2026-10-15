#!/usr/bin/env python3

"""The boot splash: one small framed card while the window is being built.

Everything expensive in starting up happens before the main window exists -
the settings are read, the theme resolved, the addons indexed, the tabs
built - so for a moment there is nothing on screen at all to say the program
is working. This module draws one card in the middle of the screen: the app's
logo, its name and version, a line naming what the boot is loading right now,
and a bar filling with a percentage. It is painted from the theme in force -
the same colours and the same corner radius the window wears - with a border
around the edge, and it carries no title bar, because it carries no window
furniture at all.

The splash is a module-level singleton. :func:`show` puts it on screen from
the entry point, ``BerryManager.__init__`` calls :func:`progress` between its
own steps without needing to know a splash exists, and :func:`finish` takes
the card away once the window is up. With no splash - switched off in
Settings, or never shown - :func:`progress` does nothing at all, so booting
without one is a normal path rather than an error.

One thing the file has to work around: all of the loading happens before the
event loop starts, so nothing repaints on its own. Every :func:`progress`
hands Qt a ``processEvents`` turn, and that is what actually moves the bar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

import manager_theme
from manager_const import APP_VERSION, ICON_FILES, find_asset, zoom_size

#: The card's own size at 100% zoom; the interface zoom multiplies it, like
#: every other hand-written number in the program.
CARD_WIDTH = 420
LOGO_SIDE = 110

#: The part of the bar the addon phase is spread over: one step per addon as
#: it is read in (``Loading``) and per addon as it is told to begin
#: (``Starting``). The window's own boot walks up to the first band and away
#: from the second; see ``AddonManager.reload`` and ``start_done``.
ADDON_BAND = (0.75, 0.88)
ADDON_START_BAND = (0.88, 0.93)


class _SplashCard(QWidget):
    """The frameless card itself: logo, name, the step being loaded, a bar."""

    def __init__(self, config: dict) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        # No title bar, no frame: the only edge this window has is the border
        # the stylesheet draws. Translucent so that border can be rounded.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("splashCard")
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(zoom_size(28), zoom_size(22),
                                zoom_size(28), zoom_size(18))
        body.setSpacing(zoom_size(8))

        self.logo = QLabel(card)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._place_logo(config)

        title = QLabel(f"Berry Manager V{APP_VERSION}", card)
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.step = QLabel("", card)
        self.step.setObjectName("splashStep")
        self.step.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.bar = QProgressBar(card)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")

        body.addWidget(self.logo)
        body.addWidget(title)
        body.addWidget(self.step)
        body.addWidget(self.bar)

        self.setFixedWidth(zoom_size(CARD_WIDTH))
        self.setStyleSheet(self._card_sheet())
        self.adjustSize()
        self._center_on_screen()

    def _place_logo(self, config: dict) -> None:
        """The same logo the window wears, alternate choice and all."""
        primary, alternate = ICON_FILES
        wanted = alternate if bool(config.get("use_alt_icon")) else primary
        path = find_asset(wanted) or find_asset(primary)
        if path is None:              # a build without pictures still opens
            self.logo.hide()
            return
        side = zoom_size(LOGO_SIDE)
        self.logo.setPixmap(QPixmap(str(path)).scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _card_sheet(self) -> str:
        """The theme's colours and corner, written out for this card alone:
        the window's own stylesheet is not applied until later in the boot."""
        radius = zoom_size(int(manager_theme.tab_metric("radius_px") or 6))
        inner = max(1, zoom_size(3))
        return f"""
        QWidget {{ background-color: transparent; }}
        QWidget#splashCard {{
            background-color: {manager_theme.color("surface")};
            border: {zoom_size(2)}px solid {manager_theme.color("accent")};
            border-radius: {radius}px;
        }}
        QLabel {{ background-color: transparent;
                  color: {manager_theme.color("text")}; }}
        QLabel#splashTitle {{ color: {manager_theme.color("bright")};
                              font-weight: 600; font-size: {zoom_size(15)}px; }}
        QLabel#splashStep {{ color: {manager_theme.color("muted")};
                             font-size: {zoom_size(11)}px; }}
        QProgressBar {{
            background-color: {manager_theme.color("soft")};
            border: 1px solid {manager_theme.color("border")};
            border-radius: {inner}px;
            height: {zoom_size(18)}px;
            text-align: center;
            color: {manager_theme.color("text")};
            font-size: {zoom_size(10)}px;
        }}
        QProgressBar::chunk {{
            background-color: {manager_theme.color("accent")};
            border-radius: {inner}px;
            margin: 1px;
        }}
        """

    def _center_on_screen(self) -> None:
        """Dead centre of the monitor the boot is happening on."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center() - self.rect().center())

    def report(self, fraction: float, label: str = "") -> None:
        """Move the bar to ``fraction`` (0..1) and name what is loading."""
        try:
            part = float(fraction)
        except (TypeError, ValueError):
            return
        self.bar.setValue(max(0, min(100, round(part * 100))))
        if label:
            self.step.setText(str(label))

    def show_card(self) -> None:
        self.show()
        self.raise_()


#: The one card the program ever shows; ``None`` means no splash is up, and
#: every call below answers that case by doing nothing.
_card: _SplashCard | None = None


def show(config: dict | None = None) -> None:
    """Put the splash on screen; obeys the Settings switch and does nothing twice.

    The card paints itself before returning, so the very first thing a double
    click produces is the splash - nothing heavy may start until it is up.
    """
    global _card
    if _card is not None:
        return
    cfg = config if isinstance(config, dict) else {}
    if not bool(cfg.get("splash_enabled", True)):
        return
    _card = _SplashCard(cfg)
    _card.show_card()
    progress(0.02, "Waking up")


def progress(fraction: float, label: str = "") -> None:
    """Report a boot step; harmless with no splash, and repaints right away.

    The event loop is not running while the window is built, so this is the
    only way the card can actually move: every report hands Qt a turn.
    """
    if _card is None:
        return
    _card.report(fraction, label)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def addon_step(number: int, total: int, name: str, *, verb: str = "Loading",
               band: tuple[float, float] = ADDON_BAND) -> None:
    """Name one addon as the boot starts it, spread across the bar's band.

    Called from the addon manager's own loops; with no splash up - switched
    off, or a rescan long after boot - this says nothing anywhere and the
    load runs exactly as it always did.
    """
    if _card is None or total <= 0:
        return
    low, high = band
    part = low + (high - low) * ((min(max(int(number), 1), total) - 1) / total)
    progress(part, f"{verb} addon {number} of {total}: {name}")


def finish() -> None:
    """Take the card away; the window is up and does the talking now."""
    global _card
    if _card is not None:
        _card.close()
        _card = None


def active() -> bool:
    """Whether a splash is up; for tests and for anyone who must not wait on one."""
    return _card is not None
