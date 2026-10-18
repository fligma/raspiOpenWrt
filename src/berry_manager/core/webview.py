#!/usr/bin/env python3
"""The Chromium-backed widgets of the embedded browser.

These are kept out of :mod:`core.views` on purpose. That module is imported
before the splash is up, so a module-level ``PyQt6.QtWebEngine*`` import there
would load the whole Chromium stack on every start - the single largest slice
of the boot cost - whether or not the session ever opens a browser tab. Here the
two classes live until the first web tab is built, and :meth:`
core.views.BrowserMixin._build_web_like_tab` reaches them by importing this
module on demand. A build with no PyQt6-WebEngine installed leaves both names
``None``, exactly as the module-level pair used to when the import failed.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, QUrl
from PyQt6.QtGui import QShowEvent

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage
except ImportError:
    QWebEnginePage = None


if QWebEnginePage is not None:
    class WebTabPage(QWebEnginePage):
        """A page that lets a click wanting its own tab or window - a
        ``target=_blank`` link, a script's ``window.open`` - get a real,
        closable tab; the plain view would swallow the request in silence."""

        def __init__(self, browser) -> None:
            super().__init__()
            self._browser = browser

        def createWindow(self, feature):        # noqa: ARG002 - one home for every kind
            try:
                return self._browser.open_requested_tab()
            except Exception:
                return None
else:
    WebTabPage = None


if QWebEngineView is not None:
    class QuietWebView(QWebEngineView):
        """A web view that never asks the window for more room than it has.

        Booting the browser process moves a plain view's size hints: the
        page announces a preferred size and the layout minimums the window
        obeys rise with it, so opening a browser tab resizes the whole
        window on its own. The view fills whatever room it is given, and
        that is the only size it should ever have asked for - both hints
        are pinned at nothing here, and the layout's own stretch keeps
        the page as big as the tab it sits in.

        The hints are only the layout's half of the boot. The other half is
        native: the moment this view's Chromium window is embedded the top
        level is re-placed around it, and a window that is re-placed loses
        its snap to a screen edge and springs back to the size it kept
        before. A fresh view therefore arms that guard as it comes up, and
        every address put to it arms one too (the guard lives with the plain
        widgets in :mod:`core.views`, so it is reached lazily here - by the
        time a view paints, that module is long since loaded)."""

        #: Whether this view has ever been seen: the embed disturbs a window
        #: once in its life, so only the first showing is worth guarding.
        _snap_armed = False

        def minimumSizeHint(self) -> QSize:
            return QSize(0, 0)

        def sizeHint(self) -> QSize:
            return QSize(0, 0)

        def showEvent(self, event: QShowEvent) -> None:
            super().showEvent(event)
            if not self._snap_armed:
                self._snap_armed = True
                self._steady()

        def setUrl(self, url: QUrl) -> None:
            self._steady()
            super().setUrl(url)

        def setHtml(self, *args, **kwargs) -> None:
            self._steady()
            super().setHtml(*args, **kwargs)

        def _steady(self) -> None:
            """Hand the window a breath of protection against the embed. The
            guard lives with the plain widgets in :mod:`core.views`, so it is
            imported here rather than at the top: this module is pulled in from
            inside ``core.views``, and a top-level import back the other way
            would find that module only half-loaded."""
            from core.views import steady_snap
            steady_snap(self)
else:
    QuietWebView = None
