#!/usr/bin/env python3
"""The addon's own half of the process protocol, and a stub for testing one.

An executable addon - a compiled ``.py``, a Go binary, a shell script, anything
- talks to Berry Manager over its stdin and stdout. The app's side of that
conversation is ``ProcessBridge`` in :mod:`manager_addons`. This module is the
other side, written once so a Python addon needs no code change to run as its
own program:

```python
import addon_client

if __name__ == "__main__":                      # run as a process addon
    raise SystemExit(addon_client.serve(__file__))
```

``serve`` imports the addon file, hands it an ``api`` that forwards every call
to the app as one line of JSON, and answers what the app pushes:
``start``, ``event``, ``widget``, ``peer``, ``invoke`` and ``shutdown``.

The five things that cannot cross a pipe are handled like this:

``schedule.after`` / ``schedule.every``
    Kept here, on a plain timer thread, so the addon's own clock never depends
    on the app's event loop. The handle has ``.cancel()``.
``events.on``
    Kept here too; the app already pushes every event, so the only work is the
    local registry and calling ``on_event``/``on_<event>`` on the addon.
``device.run(block=False)``
    A job the app is still running is not something a second process can hold,
    so the answer comes back as a handle and this module wraps it in a
    ``RemoteFuture``: ``done()``, ``result()`` and ``cancel()`` ask the app about
    the job through the ``device.future_*`` methods. ``block=False`` therefore
    means the same thing in either runtime.
``device.run_async`` and ``ui.menu_item``
    Take a function, which JSON cannot carry. The request says which argument is
    the callback (``"callback": "on_done"``) and the app answers with a number;
    when it wants that function again it pushes ``{"op": "invoke", "token": 7}``.
``device.open`` / ``ui.window`` / ``tabs.add_tab(widget=...)``
    Refused with a message that says what to use instead. Raw paramiko, the real
    window and a QWidget are things only an in-process addon can hold.

``stub_api``
    The same shape of object, backed by a recording dict instead of a pipe: for
    an addon's test script, which checks what the addon *asked for* without
    opening a window or connecting to a device.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any

from addon_common import (
    API_VERSION,
    FUTURE_KEY,
    AddonError,
    addon_config_path,
    config_get,
    is_future_handle,
    jsonable,
    manifest_from,
    merge_config,
)

#: How long a request waits for the app before the addon gives up. The app can
#: be busy with a deploy, so this is generous on purpose.
REPLY_TIMEOUT = 1800.0

#: Methods whose callback argument travels back and forth as a token.
CALLBACK_ARGS = {"device.run_async": "on_done", "ui.menu_item": "callback"}

#: Things a second process genuinely cannot do, and what to do instead.
REFUSED = {
    "device.open": "a process addon cannot hold an SSH session; use device.run "
                   "or the file methods (list, read_text, write_text, ...)",
    "ui.window": "the window lives in the app's process; describe your controls "
                 "as a spec and use ui.dialog or tabs.add_tab(spec=...) instead",
}


def load_addon(path: str | Path, module_name: str | None = None) -> Any:
    """Import one addon file as its own module, the way the app does."""
    source = Path(path)
    if not source.is_file():
        raise AddonError(f"no such addon file: {source}")
    name = module_name or f"addon_{source.stem}"
    spec = importlib.util.spec_from_file_location(name, str(source))
    if spec is None or spec.loader is None:
        raise AddonError(f"{source.name} could not be read as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# --- The api object a process addon is handed ---

class _Method:
    """One namespaced call, e.g. ``api.device.run``."""

    def __init__(self, api: "ProcessAPI", namespace: str, name: str) -> None:
        self._api = api
        self._method = f"{namespace}.{name}" if namespace else name

    def __call__(self, *arguments: Any, **named: Any) -> Any:
        return self._api.request(self._method, arguments, named)


class RemoteFuture:
    """A job the app is still running, from the addon's side of the pipe.

    Wears the shape of ``concurrent.futures.Future`` - ``done()``, ``result()``
    and ``cancel()`` - because that is what an addon written against the
    in-process api already expects from ``device.run(block=False)``. Each
    question is one request answered by the bridge's own registry.
    """

    def __init__(self, api: "ProcessAPI", token: int) -> None:
        self._api = api
        self.token = int(token)

    def done(self) -> bool:
        return bool(self._api.request("device.future_done", (), {"token": self.token}))

    def result(self, timeout: Any = None) -> Any:
        return self._api.request("device.future_result", (),
                                 {"token": self.token, "timeout": timeout})

    def cancel(self) -> bool:
        return bool(self._api.request("device.future_cancel", (), {"token": self.token}))

    def __repr__(self) -> str:
        return f"<RemoteFuture handle {self.token}>"


class ProcessAPI:
    """The ``api`` object of an addon that runs as its own process."""

    #: Positional argument names per method, so calls cross the pipe as keywords.
    _POSITIONAL = {
        "device.run": ("command", "timeout", "block", "device"),
        "device.run_async": ("command", "on_done", "timeout", "device"),
        "device.future_done": ("token",),
        "device.future_result": ("token", "timeout"),
        "device.future_cancel": ("token",),
        "device.shell_send": ("line",),
        "device.is_connected": ("protocol",),
        "device.ensure_connected": ("protocol",),
        "device.mode": (),
        "device.set_mode": ("mode",),
        "device.bind": ("host", "port", "username", "password",
                        "key_file", "remote_root"),
        "device.unbind": (),
        "device.exists": ("path", "outside_root"),
        "device.stat": ("path", "outside_root"),
        "device.read_text": ("path", "encoding", "outside_root"),
        "device.read_bytes": ("path", "outside_root"),
        "device.write_text": ("path", "text", "encoding", "mkdir", "outside_root"),
        "device.write_bytes": ("path", "payload", "mkdir", "outside_root"),
        "device.append_text": ("path", "text", "outside_root"),
        "device.list": ("path", "outside_root"),
        "device.mkdir": ("path",),
        "device.remove": ("path", "recursive", "outside_root"),
        "device.rename": ("path", "new_path", "outside_root"),
        "device.upload": ("local", "remote_path", "outside_root"),
        "device.download": ("remote_path", "local", "outside_root"),
        "ui.information": ("title", "text"),
        "ui.warning": ("title", "text"),
        "ui.error": ("title", "text"),
        "ui.popup": ("title", "text"),
        "ui.question": ("title", "text", "default"),
        "ui.input": ("title", "label", "default", "secret"),
        "ui.choose": ("items",),
        "ui.notify": ("text", "ms"),
        "ui.progress_notice": ("title", "text", "value", "maximum"),
        "ui.progress_notice_close": (),
        "ui.menu_item": ("text", "callback", "menu", "shortcut"),
        "ui.dialog": ("spec", "title", "ok"),
        "ui.set_setting_widget": ("widget_or_spec",),
        "tabs.add_web_tab": ("name", "url", "ip", "port", "path", "scheme",
                             "position", "pinned", "icon_only"),
        "tabs.add_tab": ("name", "widget", "spec", "style", "position",
                         "pinned", "icon_only"),
        "tabs.close": ("name",),
        "tabs.set_pinned": ("name", "pinned", "icon_only"),
        "tabs.rename": ("name", "new_name"),
        "tabs.activate": ("name",),
        "tabs.exists": ("name",),
        "tabs.url": ("name",),
        "tabs.navigate": ("name", "url"),
        "tabs.reload": ("name",),
        "tabs.set_style": ("name", "css"),
        "tabs.set_html": ("name", "html"),
        "tabs.inject_css": ("name", "css"),
        "tabs.inject_js": ("name", "code"),
        "tabs.widget": ("name", "identifier"),
        "tabs.set_widget": ("name", "identifier", "prop", "value"),
        "tabs.widget_value": ("name", "identifier", "what"),
        "tabs.set_icon": ("name", "icon"),
        "tabs.icon": ("name",),
        "tabs.use_app_icon": ("name", "on"),
        "settings.get": ("key", "default"),
        "settings.set": ("key", "value"),
        "settings.update": ("values",),
        "settings.pop": ("key", "default"),
        "settings_pages.add": ("name", "title", "widget", "spec", "style"),
        "settings_pages.remove": ("name",),
        "settings_pages.exists": ("name",),
        "settings_pages.activate": ("name",),
        "settings_pages.title": ("name",),
        "settings_pages.set_title": ("name", "title"),
        "settings_pages.widget": ("name", "identifier"),
        "settings_pages.set_widget": ("name", "identifier", "prop", "value"),
        "settings_pages.widget_value": ("name", "identifier", "what"),
        "settings_pages.set_icon": ("name", "icon"),
        "config.get": ("key", "default"),
        "config.set": ("key", "value"),
        "config.update": ("values",),
        "config.pop": ("key", "default"),
        "config.declare": ("fields", "defaults", "title"),
        "config.save_text": ("raw",),
        "config.path": (),
        "ui.pick_color": ("title", "initial"),
        "ui.pick_file": ("title", "kind", "filter", "initial"),
        "session.web_url": ("tab",),
        "session.rescan_addons": (),
        "session.set_addon_enabled": ("addon_id", "enabled"),
        "session.addon_state": ("addon_id",),
        "session.set_addon_running": ("addon_id", "running"),
        "peers.info": ("addon_id",),
        "peers.send": ("addon_id", "data"),
        "theme.exists": ("name",),
        "theme.read": ("name",),
        "theme.raw": ("name",),
        "theme.colors": ("name",),
        "theme.color": ("token", "name", "default"),
        "theme.tabs": ("name",),
        "theme.metric": ("knob", "name"),
        "theme.stylesheet": ("name",),
        "theme.sheet_of": ("theme",),
        "theme.validate": ("theme",),
        "theme.template_text": ("name",),
        "theme.use": ("name",),
        "theme.save": ("name", "theme", "force"),
        "theme.copy": ("name", "target"),
        "theme.rename": ("name", "target"),
        "theme.remove": ("name",),
    }

    def __init__(self, addon_id: str = "", name: str = "") -> None:
        self.id = addon_id or os.environ.get("RASCONF_ADDON_ID", "addon")
        self.name = name or self.id
        self.api_version = API_VERSION
        self.lock = threading.Lock()
        self.counter = 0
        self.pending: dict[int, queue.Queue] = {}
        self.tokens: dict[int, object] = {}
        self.token_counter = 0
        self.inbox: queue.Queue = queue.Queue()
        self.reader: threading.Thread | None = None
        self.dispatcher: threading.Thread | None = None
        self.stopping = threading.Event()
        # Set the moment the app's pipe closes, before ``stopping``: a request
        # in flight fails at once instead of waiting out the reply timeout,
        # so a gone app never leaves this process running.
        self.app_gone = threading.Event()
        # The app's ``start`` line, kept under its own name so it does not
        # collide with the api.session namespace the addon calls.
        self.launch: dict = {}
        self.ready = threading.Event()           # init() has been called
        self.started = False                      # and the start hook has run
        self.listeners: dict[str, list] = {}
        self.module = None                      # the addon, set by serve()
        self.base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent
        for namespace in ("device", "ui", "tabs", "settings", "settings_pages",
                          "config", "peers", "session", "theme"):
            setattr(self, namespace, _Namespace(self, namespace))
        self.events = _Events(self)
        self.schedule = _Schedule(self)
        self.paths = _Paths(self)

    # --- the pipe ---

    def send(self, payload: dict) -> bool:
        """One JSON object per line out, flushed: the app is waiting on it."""
        line = json.dumps(jsonable(payload), default=str)
        try:
            with self.lock:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except (OSError, ValueError) as error:
            self.error(f"cannot reach the app: {error}")
            return False
        return True

    def request(self, method: str, arguments: tuple = (), named: dict | None = None) -> Any:
        """Ask the app to do something and wait for its answer, callbacks kept local under a token."""
        if method in REFUSED:
            raise AddonError(REFUSED[method])
        if self.app_gone.is_set():
            raise AddonError("the app closed the pipe")
        names = self._POSITIONAL.get(method, ())
        callback_argument = CALLBACK_ARGS.get(method)
        values: dict = {}
        callback = None

        def place(key: str, value: Any) -> None:
            nonlocal callback
            if callable(value):
                if key == callback_argument:
                    callback = value
                    return
                raise AddonError(f"{method}: '{key}' is not a callback in a "
                                 "process addon, and a function cannot be sent as a value")
            values[key] = value

        for name, value in zip(names, arguments):
            place(name, value)
        if len(arguments) > len(names):
            raise AddonError(f"{method}: pass the rest as keyword arguments "
                             f"(this process knows {', '.join(names) or 'none of them'})")
        for key, value in (named or {}).items():
            place(key, value)

        self.counter += 1
        identifier = self.counter
        answers: queue.Queue = queue.Queue(maxsize=1)
        self.pending[identifier] = answers
        message: dict = {"id": identifier, "api": method, "args": values}
        token = None
        if callback is not None:
            if not callback_argument:
                raise AddonError(f"{method} has no callback a second process could use")
            self.token_counter += 1
            token = self.token_counter
            self.tokens[token] = callback
            message["callback"] = callback_argument
            message["token"] = token
        if not self.send(message):
            self.pending.pop(identifier, None)
            if token is not None:
                self.tokens.pop(token, None)
            raise AddonError("the app is not listening any more")
        try:
            kind, result = answers.get(timeout=REPLY_TIMEOUT)
        except queue.Empty:
            self.pending.pop(identifier, None)
            if token is not None:
                self.tokens.pop(token, None)
            raise AddonError(f"the app did not answer '{method}' in time")
        self.pending.pop(identifier, None)
        if kind == "error":
            if token is not None:
                self.tokens.pop(token, None)
            raise AddonError(str(result))
        if is_future_handle(result):
            # A job the app started for us: what comes back is only its handle.
            return RemoteFuture(self, result[FUTURE_KEY])
        return result

    def read_loop(self) -> None:
        """Route the app's lines: answers to whoever asked, the rest to the dispatcher."""
        stream = sys.stdin
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            identifier = message.get("id")
            if identifier is not None and identifier in self.pending:
                kind = "error" if "error" in message else "ok"
                value = message.get("error") if kind == "error" else message.get("result")
                try:
                    self.pending[identifier].put((kind, value))
                except queue.Full:
                    pass
                continue
            self.inbox.put(message)
        # The app closed the pipe: nothing further will be answered, so fail
        # every waiting request at once, then tell the dispatcher to finish.
        self.app_gone.set()
        self._release_pending("the app closed the pipe")
        self.inbox.put({"op": "shutdown", "reason": "the app closed the pipe"})

    def _release_pending(self, reason: str) -> None:
        """Hand an error back to every thread still waiting on the app."""
        while self.pending:
            _identifier, answers = self.pending.popitem()
            try:
                answers.put_nowait(("error", reason))
            except queue.Full:
                pass

    def dispatch_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                message = self.inbox.get(timeout=0.25)
            except queue.Empty:
                continue
            self.handle(message)

    def handle(self, message: dict) -> None:
        op = str(message.get("op") or "")
        if op == "start":
            self.launch = dict(message.get("addon") or {})
            self.name = str(self.launch.get("name") or self.name)
            self._start_hook()
            return
        if op == "event":
            name = str(message.get("name") or "")
            payload = message.get("payload") or {}
            for listener in list(self.listeners.get(name, [])):
                guard_call(self, listener, payload, where=f"events.on('{name}')")
            self._fire("on_event", name, payload)
            hook = getattr(self.module, f"on_{name}", None) if self.module else None
            if callable(hook) and hook is not getattr(self.module, "on_event", None):
                guard_call(self, hook, payload, where=f"on_{name}")
            if name == "addon_config_changed" and payload.get("addon") == self.id:
                config_hook = getattr(self.module, "on_config", None) if self.module else None
                if callable(config_hook):
                    guard_call(self, config_hook, payload, where="on_config")
            return
        if op == "widget":
            hook = getattr(self.module, "on_widget", None) if self.module else None
            if callable(hook):
                guard_call(self, hook, message.get("tab"), message.get("id"),
                           message.get("event"), message.get("payload") or {},
                           where="on_widget")
            return
        if op == "peer":
            hook = getattr(self.module, "on_message", None) if self.module else None
            if callable(hook):
                guard_call(self, hook, message.get("from"), message.get("data"),
                           where="on_message")
            return
        if op == "invoke":
            callback = self.tokens.get(message.get("token"))
            if callable(callback):
                guard_call(self, callback, *(message.get("args") or []), where="callback")
            return
        if op == "shutdown":
            self._fire("shutdown")
            self.schedule.cancel_all()
            # Anything still in flight will not be answered any more: the app
            # is tearing the window down around its closing hook.
            self.app_gone.set()
            self._release_pending("the app is closing")
            self.stopping.set()
            return

    def traceback(self, where: str) -> None:
        self.error(f"{where}: {traceback.format_exc(limit=2).strip()}")

    def _fire(self, hook: str, *arguments: Any) -> None:
        if self.module is None:
            return
        function = getattr(self.module, hook, None)
        if callable(function):
            guard_call(self, function, *arguments, where=hook)

    def _start_hook(self) -> None:
        """Run the addon's ``start`` once it exists, fired by whichever of the load and the app's line arrives last."""
        if self.started or self.module is None or not self.ready.is_set():
            return
        self.started = True
        self._fire("start")

    # --- logging ---

    def log(self, text: Any) -> None:
        self.send({"op": "log", "log": str(text)})

    def error(self, text: Any) -> None:
        self.send({"op": "log", "log": f"error: {text}"})

    def manifest(self, manifest: dict) -> None:
        push = {"op": "manifest"}
        for key, value in (manifest or {}).items():
            push[str(key)] = jsonable(value)
        self.send(push)


def guard_call(api: ProcessAPI, function: Callable[..., Any], *arguments: Any,
               where: str = "") -> None:
    """Call addon code and keep its failure in the app's log, never as an exception across the pipe."""
    try:
        function(*arguments)
    except AddonError as error:
        api.error(f"{where}: {error}")
    except Exception:
        api.traceback(where or getattr(function, "__name__", "hook"))


class _Namespace:
    """``api.device`` and friends: every attribute is one remote method."""

    def __init__(self, api: ProcessAPI, name: str) -> None:
        self._api = api
        self._name = name

    def __getattr__(self, method: str) -> _Method:
        if method.startswith("_"):
            raise AttributeError(method)
        return _Method(self._api, self._name, method)


class _Events:
    """``api.events``: the registry is local because the app pushes the events."""

    def __init__(self, api: ProcessAPI) -> None:
        self._api = api

    def on(self, name: str, callback: Callable[..., Any]) -> Callable[[], None]:
        bucket = self._api.listeners.setdefault(str(name), [])
        bucket.append(callback)

        def detach() -> None:
            bucket[:] = [item for item in bucket if item is not callback]

        return detach

    def off(self, name: str, callback: Callable[..., Any] | None = None) -> int:
        bucket = self._api.listeners.get(str(name), [])
        before = len(bucket)
        if callback is None:
            bucket.clear()
        else:
            bucket[:] = [item for item in bucket if item is not callback]
        return before - len(bucket)

    def names(self) -> list[str]:
        return sorted(self._api.listeners)


class _Timer:
    """The handle ``api.schedule.after``/``every`` returns: has ``.cancel()``."""

    def __init__(self, api: ProcessAPI, seconds: float, callback: Callable[[], Any],
                 repeat: bool) -> None:
        self.api = api
        self.callback = callback
        self.seconds = max(0.05, float(seconds))
        self.repeat = bool(repeat)
        self.cancelled = threading.Event()
        self.timer: threading.Timer | None = None
        self._start()

    def _start(self) -> None:
        if self.cancelled.is_set():
            return
        self.timer = threading.Timer(self.seconds, self._fire)
        self.timer.daemon = True
        self.timer.start()

    def _fire(self) -> None:
        if self.cancelled.is_set():
            return
        guard_call(self.api, self.callback, where="schedule")
        if self.repeat:
            self._start()

    def cancel(self) -> None:
        self.cancelled.set()
        if self.timer is not None:
            self.timer.cancel()


class _Schedule:
    """``api.schedule``: plain threads, so the addon owns its own clock."""

    def __init__(self, api: ProcessAPI) -> None:
        self._api = api
        self.handles: list[_Timer] = []

    def after(self, seconds: float, callback: Callable[[], Any]) -> _Timer:
        return self._track(_Timer(self._api, float(seconds), callback, False))

    def every(self, seconds: float, callback: Callable[[], Any]) -> _Timer:
        return self._track(_Timer(self._api, max(0.1, float(seconds)), callback, True))

    def _track(self, timer: _Timer) -> _Timer:
        self.handles.append(timer)
        return timer

    def cancel_all(self) -> None:
        for timer in self.handles:
            timer.cancel()
        self.handles.clear()


class _Paths:
    """``api.paths``, straight from the environment the app set for us."""

    def __init__(self, api: ProcessAPI) -> None:
        self._api = api

    @staticmethod
    def _env(variable: str, fallback: Path) -> Path:
        raw = str(os.environ.get(variable, "")).strip()
        return Path(raw) if raw else fallback

    @property
    def addon(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def data(self) -> Path:
        folder = self._env("RASCONF_ADDON_DATA", self._api.base_dir / "addons" / "_data"
                           / self._api.id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def config(self) -> Path:
        raw = str(os.environ.get("RASCONF_ADDON_CONFIG", "")).strip()
        return Path(raw) if raw else addon_config_path(self._api.id)

    @property
    def configs(self) -> Path:
        return self.config.parent

    @property
    def base(self) -> Path:
        return self._env("RASCONF_BASE", self._api.base_dir)

    @property
    def addons(self) -> Path:
        return self._env("RASCONF_ADDONS", self.base / "addons")

    @property
    def assets(self) -> Path:
        """The pictures packed with this program: its own bundle first, else the app's."""
        bundle = getattr(sys, "_MEIPASS", None)          # a onefile build unpacks here
        if bundle:
            own = Path(str(bundle)) / "assets"
            if own.is_dir():
                return own
        return self._env("RASCONF_ASSETS", self.base / "assets")

    @property
    def themes(self) -> Path:
        """The themes folder the window is painted from, made on the way."""
        folder = self._env("RASCONF_THEMES", self.base / "themes")
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return folder

    @property
    def log(self) -> Path:
        return self._env("RASCONF_LOG", self.base / "berry_manager.log")


# --- Serving ---

def serve(target: str | Path, addon_id: str = "") -> int:
    """Run one addon file as a process addon until the app stops it."""
    api = ProcessAPI(addon_id or os.environ.get("RASCONF_ADDON_ID", ""))
    # The reader only routes; the dispatcher runs the hooks. Neither is the
    # init thread, so the addon and the app can call each other while starting.
    api.reader = threading.Thread(target=api.read_loop, daemon=True, name="addon-in")
    api.reader.start()
    api.dispatcher = threading.Thread(target=api.dispatch_loop, daemon=True,
                                      name="addon-hooks")
    api.dispatcher.start()
    try:
        api.module = load_addon(target) if isinstance(target, (str, Path)) else target
    except BaseException as error:
        api.error(f"could not be loaded: {type(error).__name__}: {error}")
        traceback.print_exc()
        return 2
    if not api.id or api.id == "addon":
        api.id = api.module.__name__.removeprefix("addon_")
    manifest = manifest_from(api.module)
    api.name = str(manifest.get("name") or api.id)
    api.manifest(manifest)
    wanted = manifest.get("api")
    try:
        if wanted is not None and int(wanted) > API_VERSION:
            api.error(f"needs api version {wanted}, this build has {API_VERSION}")
            return 3
    except (TypeError, ValueError):
        pass
    init = getattr(api.module, "init", None)
    if not callable(init):
        api.error("the addon has no init(api) function")
        return 4
    guard_call(api, init, api, where="init")
    # init is over: any start line from the import can now fire.
    api.ready.set()
    api._start_hook()
    # Wait until the dispatcher has seen a shutdown, or the pipe has closed.
    while not api.stopping.wait(0.2):
        pass
    api.schedule.cancel_all()
    api.listeners.clear()
    api.tokens.clear()
    return 0


# --- Testing one addon without the app ---

def _stub_rename(name: str, target: str, manager_theme: Any) -> str:
    """``theme.rename`` as the app does it: copy first, then drop the old file."""
    fresh = manager_theme.copy(name, target)
    manager_theme.remove(name)
    return fresh


def _stub_reference() -> list[dict]:
    """``theme.reference`` read off the live API class itself.

    ``ThemeAPI.reference`` introspects its own class and never asks a window for
    anything, so calling it unbound answers a stub with exactly the words the
    app would: the reference a theme editor shows cannot fall behind the API it
    describes. With no Qt beside the stub there is no class to read, and the
    answer is the empty list every other stub reply falls back to.
    """
    try:
        from addon_api import ThemeAPI

        return list(ThemeAPI.reference(None) or [])
    except Exception:            # no addon_api here: nothing to describe
        return []


class StubAPI:
    """An ``api`` that records what it was asked for, for testing an addon without the app."""

    def __init__(self, addon_id: str = "test", config: dict | None = None,
                 settings: dict | None = None) -> None:
        self.id = addon_id
        self.name = addon_id
        self.api_version = API_VERSION
        self.calls: list[tuple] = []
        self.logs: list[str] = []
        self.results: dict[str, object] = {}
        self.config_values: dict = dict(config or {})
        self.settings_values: dict = dict(settings or {})
        self.declared: dict = {}
        self.base_dir = Path(__file__).resolve().parent
        self.timers = _Schedule(self)
        # The name an addon calls, so a test can drive a job that paces itself
        # through ``api.schedule.after``.
        self.schedule = self.timers
        for namespace in ("device", "ui", "tabs", "settings", "settings_pages",
                          "config", "peers", "session", "events", "theme"):
            setattr(self, namespace, _StubNamespace(self, namespace))
        # The real folders, from the same environment a compiled addon reads.
        self.paths = _Paths(self)

    # --- what the addon did ---

    def record(self, method: str, arguments: Any, named: Any) -> Any:
        self.calls.append((method, tuple(arguments), dict(named)))
        if method in self.results:
            return self.results[method]
        return self.default(method, arguments, named)

    def answer(self, method: str, value: Any) -> "StubAPI":
        """Say what ``api.<method>`` should return. Chains, for a test."""
        self.results[str(method)] = value
        return self

    def called(self, method: str) -> bool:
        return any(where == method for where, _a, _k in self.calls)

    def times(self, method: str) -> int:
        return len([1 for where, _a, _k in self.calls if where == method])

    def last(self, method: str) -> tuple | None:
        for entry in reversed(self.calls):
            if entry[0] == method:
                return entry
        return None

    def argument(self, method: str, name: str, arguments: Any, named: Any,
                 default: Any = None) -> Any:
        """One argument of a call, whether it was given by name or by position."""
        if name in named:
            return named[name]
        names = ProcessAPI._POSITIONAL.get(method, ())
        if name in names and names.index(name) < len(arguments):
            return arguments[names.index(name)]
        return default

    def asked(self, method: str, key: str, default: Any = None) -> Any:
        """One argument of the last call to ``method``, by name or position."""
        entry = self.last(method)
        if entry is None:
            return default
        _method, arguments, named = entry
        return self.argument(method, key, arguments, named, default)

    def log(self, text: Any) -> None:
        self.logs.append(str(text))

    def error(self, text: Any) -> None:
        self.logs.append(f"error: {text}")

    # --- the look, answered from the real files where they can be read ---

    def theme_answer(self, method: str, ask: Callable[[str, Any], Any]) -> Any:
        """A ``theme.*`` call, answered by :mod:`manager_theme` if it is importable.

        The module needs no window and no Qt, so a test sees the same palettes,
        knobs and files the app would answer with - in the folder the
        ``RASCONF_THEMES`` variable points at, which a test sets to its own
        before anything is read. With no app beside the addon (a compiled
        build on its own), the canned answers below keep the shape of every
        reply without touching a file.
        """
        try:
            import manager_theme
        except ImportError:
            manager_theme = None
        wanted = ask("name", "")
        if manager_theme is not None:
            answers = {
                "theme.name": lambda: manager_theme.current_name(),
                "theme.names": manager_theme.names,
                "theme.exists": lambda: manager_theme.exists(wanted),
                "theme.folder": lambda: str(manager_theme.folder()),
                "theme.read": lambda: manager_theme.theme(wanted or None),
                "theme.raw": lambda: manager_theme.raw(wanted),
                "theme.colors": lambda: manager_theme.colors(wanted or None),
                "theme.color": lambda: manager_theme.color(
                    ask("token", ""), wanted or None, ask("default", "")),
                "theme.tabs": lambda: manager_theme.tabs(wanted or None),
                "theme.metric": lambda: manager_theme.tab_metric(
                    ask("knob", ""), wanted or None),
                "theme.icon_px": manager_theme.icon_px,
                "theme.chrome_px": manager_theme.chrome_px,
                "theme.shows_captions": manager_theme.shows_captions,
                "theme.shows_dots": manager_theme.shows_dots,
                "theme.stylesheet": lambda: manager_theme.stylesheet(wanted or None),
                "theme.sheet_of": lambda: manager_theme.sheet_of(ask("theme", {})),
                "theme.validate": lambda: manager_theme.validate(ask("theme", {})),
                "theme.groups": lambda: [
                    {"title": title,
                     "tokens": [{"name": token,
                                 "color": manager_theme.color(token),
                                 "hint": manager_theme.token_hint(token)}
                                 for token in tokens]}
                    for title, tokens in manager_theme.groups()],
                "theme.token_knobs": lambda: [
                    {"name": knob, "value": value, "kind": "switch"
                     if isinstance(value, bool) else "pixels", "hint": hint}
                    for knob, value, hint in manager_theme.knobs()],
                "theme.reference": _stub_reference,
                "theme.templates": manager_theme.templates,
                "theme.template_text": lambda: manager_theme.template_text(wanted),
                "theme.use": lambda: manager_theme.use(wanted),
                "theme.save": lambda: str(manager_theme.write(
                    ask("name", ""), ask("theme", {}) or {},
                    force=bool(ask("force", False))).stem),
                "theme.copy": lambda: manager_theme.copy(ask("name", ""),
                                                         ask("target", "")),
                "theme.rename": lambda: _stub_rename(
                    ask("name", ""), ask("target", ""), manager_theme),
                "theme.remove": lambda: manager_theme.remove(wanted),
            }
            answer = answers.get(method)
            if answer is not None:
                return answer()
        # No app module beside the addon: a well-formed nothing, in the shape
        # the app would answer in, so an addon's guards see the same types.
        if method == "theme.name":
            return "default"
        if method in ("theme.names", "theme.groups", "theme.token_knobs",
                      "theme.templates", "theme.reference", "theme.validate"):
            return [] if method != "theme.names" else ["default"]
        if method in ("theme.colors", "theme.tabs", "theme.read", "theme.raw"):
            return {}
        if method in ("theme.exists", "theme.remove"):
            return False
        if method in ("theme.shows_captions", "theme.shows_dots"):
            return True
        if method in ("theme.icon_px", "theme.chrome_px"):
            return 24 if method == "theme.icon_px" else 34
        if method in ("theme.use", "theme.copy", "theme.rename", "theme.save"):
            return str(ask("target", "") or wanted or "")
        if method in ("theme.stylesheet", "theme.sheet_of", "theme.template_text",
                      "theme.folder", "theme.color"):
            return ""
        return None

    # --- sensible answers ---

    def default(self, method: str, arguments: Any, named: Any) -> Any:
        """What an addon normally hears back from a running app; ``answer`` overrides it."""
        ask = lambda name, fallback=None: self.argument(method, name, arguments,
                                                        named, fallback)
        if method in ("device.run", "device.append_text", "device.write_text",
                      "device.write_bytes"):
            if method == "device.run":
                return {"code": 0, "out": "", "err": ""}
            return 0
        if method == "device.run_async":
            result = {"code": 0, "out": "", "err": ""}
            callback = ask("on_done")
            if callable(callback):
                callback(result)
            return result
        if method == "device.list":
            return []
        if method in ("device.exists", "device.shell_send", "device.ensure_connected",
                      "device.mkdir", "device.remove", "device.rename", "device.upload",
                      "device.download", "tabs.close", "tabs.activate", "tabs.rename",
                      "peers.send", "settings.remove"):
            return True
        if method in ("device.is_connected", "tabs.exists", "config.exists",
                      "settings_pages.exists"):
            return bool(self.config_values) if method == "config.exists" else False
        if method == "device.read_text":
            return ""
        if method == "device.stat":
            return None
        if method.startswith("ui."):
            if method == "ui.question":
                return True
            if method in ("ui.input", "ui.pick_color", "ui.pick_file"):
                # A box that asks for something answers with nothing at all:
                # the same as a person pressing Cancel.
                return ""
            return None                       # a notice, a menu entry, a dialog
        if method.startswith("tabs."):
            if method in ("tabs.add_tab", "tabs.add_web_tab"):
                return ask("name", "tab")
            if method == "tabs.names":
                return []
            # ``widget``/``view``/``widget_value`` address real Qt objects; a test
            # gets nothing, so the addon's own ``is not None`` guard does the rest.
            if method in ("tabs.widget", "tabs.view", "tabs.widget_value"):
                return None
            if method in ("tabs.set_icon", "tabs.icon", "tabs.use_app_icon",
                          "tabs.set_pinned"):
                # The app would say whether the mark was read; a test reads the call.
                return True
            return ""
        if method.startswith("settings_pages."):
            if method == "settings_pages.add":
                return ask("name", "page")
            if method == "settings_pages.names":
                return []
            if method in ("settings_pages.remove", "settings_pages.activate",
                          "settings_pages.set_title", "settings_pages.set_widget",
                          "settings_pages.set_icon"):
                return True
            if method in ("settings_pages.widget", "settings_pages.widget_value"):
                return None
            return ""
        if method.startswith("settings."):
            if method == "settings.all":
                return dict(self.settings_values)
            if method == "settings.get":
                return config_get(self.settings_values, ask("key", ""), ask("default"))
            return None
        if method.startswith("config."):
            if method == "config.declare":
                defaults = ask("defaults", {}) or {}
                self.declared = {"fields": ask("fields", []) or [],
                                 "defaults": dict(defaults)}
                return merge_config(defaults, self.config_values)
            if method in ("config.all", "config.update", "config.reset"):
                return merge_config(self.declared.get("defaults"), self.config_values)
            if method == "config.defaults":
                return dict(self.declared.get("defaults") or {})
            if method == "config.stored":
                return dict(self.config_values)
            if method == "config.schema":
                return dict(self.declared)
            if method == "config.get":
                values = merge_config(self.declared.get("defaults"), self.config_values)
                return config_get(values, ask("key", ""), ask("default"))
            if method in ("config.text", "config.save_text"):
                return json.dumps(merge_config(self.declared.get("defaults"),
                                               self.config_values), indent=2)
            if method in ("config.path", "config.file"):
                return self.paths.config
            return None
        if method.startswith("session."):
            if method == "session.tabs":
                return []
            if method == "session.version":
                return "addon test"
            if method == "session.current_tab":
                return ""
            if method == "session.app_stylesheet":
                return ""
            if method == "session.zoom":
                # A test has no window to scale, so it reports the shipped 100%.
                return 100
            if method == "session.app_icon":
                # A test has no window, so there is no logo of its own to read.
                return ""
            if method in ("session.addon_state", "session.set_addon_running"):
                # With no app beside it, nothing is installed and nothing runs.
                return {"installed": False, "enabled": False, "running": False}
            return {} if method in ("session.profile", "session.settings",
                                    "session.status") else None
        if method.startswith("peers."):
            return [] if method == "peers.list" else None
        if method.startswith("theme."):
            return self.theme_answer(method, ask)
        if method.startswith("events."):
            if method == "events.on":
                return lambda: None
            if method == "events.names":
                return []
            return 0
        return None


class _StubNamespace:
    def __init__(self, api: StubAPI, name: str) -> None:
        self._api = api
        self._name = name

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*arguments: Any, **named: Any) -> Any:
            return self._api.record(f"{self._name}.{method}", arguments, named)

        call.__name__ = method
        return call


def stub_api(addon_id: str = "test", config: dict | None = None,
             settings: dict | None = None) -> StubAPI:
    """A recording stand-in for ``api``, for an addon's own test script."""
    return StubAPI(addon_id=addon_id, config=config, settings=settings)
