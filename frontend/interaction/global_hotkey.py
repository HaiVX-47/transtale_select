from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import select
import threading
import time
import uuid

from PyQt6.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)

_PORTAL_NAME = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
_REQUEST_IFACE = "org.freedesktop.portal.Request"
_SESSION_IFACE = "org.freedesktop.portal.Session"


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", _XKeyEvent), ("pad", ctypes.c_long * 24)]


class GlobalHotkey(QObject):
    """Global Ctrl+D listener with Wayland portal and X11 fallback."""

    activated = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._activation_lock = threading.Lock()
        self._shortcut_down = False
        self._last_activation = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="global-ctrl-d-hotkey",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    @staticmethod
    def resolve_current_selection():
        """Read the selected text once, when Ctrl+D requests translation."""
        from .selection.resolver import SelectionResolver

        return SelectionResolver().resolve(include_geometry=True)

    def _emit_activation(self) -> None:
        # Treat a held key as one activation. Portal Deactivated / X11 KeyRelease
        # re-arms it; a time debounce alone allows long key repeats to toggle twice.
        with self._activation_lock:
            now = time.monotonic()
            if self._shortcut_down:
                return
            # Some portal/compositor combinations emit a Deactivated/Activated
            # pair for key-repeat. Keep a short release debounce as a second
            # guard so holding Ctrl+D never cycles the state repeatedly.
            if now - self._last_activation < 0.8:
                return
            self._shortcut_down = True
            self._last_activation = now
        self.activated.emit()

    def _release_activation(self) -> None:
        with self._activation_lock:
            self._shortcut_down = False

    def _run(self) -> None:
        is_wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )
        if is_wayland:
            try:
                self._run_portal()
                return
            except Exception:
                # XGrabKey cannot intercept input in native Wayland clients.
                # Falling back there would leave Ctrl+D leaking into editors
                # while pretending the global shortcut is active.
                logger.exception("Wayland global-shortcut portal unavailable; Ctrl+D is inactive.")
                return

        try:
            self._run_x11()
        except Exception:
            logger.exception("Could not register global Ctrl+D hotkey.")

    def _run_portal(self) -> None:
        from gi.repository import Gio, GLib

        context = GLib.MainContext.new()
        context.push_thread_default()
        connection = None
        session_path: str | None = None
        activated_subscription = 0
        deactivated_subscription = 0
        guard_stop = threading.Event()
        guard_thread: threading.Thread | None = None
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            token = uuid.uuid4().hex
            create_options = {
                "handle_token": GLib.Variant("s", f"create_{token}"),
                "session_handle_token": GLib.Variant("s", f"session_{token}"),
            }
            created = self._portal_request(
                connection,
                context,
                "CreateSession",
                GLib.Variant("(a{sv})", (create_options,)),
                self._stop_event,
                f"create_{token}",
            )
            session_path = created.get("session_handle")
            if not session_path:
                raise RuntimeError("Shortcut portal returned no session handle.")

            activated_subscription = connection.signal_subscribe(
                _PORTAL_NAME,
                _SHORTCUTS_IFACE,
                "Activated",
                _PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_portal_activated,
                None,
            )
            deactivated_subscription = connection.signal_subscribe(
                _PORTAL_NAME,
                _SHORTCUTS_IFACE,
                "Deactivated",
                _PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_portal_deactivated,
                None,
            )

            # Reuse the desktop's previously approved shortcut when possible.
            # This keeps later app launches quiet; BindShortcuts is needed for
            # the first registration or when the saved binding is unavailable.
            list_token = f"list_{token}"
            try:
                listed = self._portal_request(
                    connection,
                    context,
                    "ListShortcuts",
                    GLib.Variant(
                        "(oa{sv})",
                        (
                            session_path,
                            {"handle_token": GLib.Variant("s", list_token)},
                        ),
                    ),
                    self._stop_event,
                    list_token,
                )
                shortcuts = listed.get("shortcuts", ())
            except Exception:
                logger.debug("Could not restore a saved shortcut; binding it again.", exc_info=True)
                shortcuts = ()

            shortcut = self._find_shortcut(shortcuts)
            if shortcut is not None and not self._is_ctrl_d(shortcut):
                logger.warning(
                    "Saved Ctrl+D shortcut has unexpected trigger %r; rebinding.",
                    shortcut[1].get("trigger_description", ""),
                )
                shortcut = None

            if shortcut is None:
                shortcut_properties = {
                    "description": GLib.Variant("s", "Toggle selection and translate mode"),
                    "preferred_trigger": GLib.Variant("s", "CTRL+d"),
                }
                bind_token = f"bind_{token}"
                bound = self._portal_request(
                    connection,
                    context,
                    "BindShortcuts",
                    GLib.Variant(
                        "(oa(sa{sv})sa{sv})",
                        (
                            session_path,
                            [("toggle_selection_translate", shortcut_properties)],
                            "",
                            {"handle_token": GLib.Variant("s", bind_token)},
                        ),
                    ),
                    self._stop_event,
                    bind_token,
                )
                shortcuts = bound.get("shortcuts", ())
                shortcut = self._find_shortcut(shortcuts)
            if shortcut is None or not self._is_ctrl_d(shortcut):
                actual = (
                    shortcut[1].get("trigger_description", "")
                    if shortcut is not None
                    else "<not bound>"
                )
                raise RuntimeError(
                    f"The desktop did not bind Ctrl+D (actual trigger: {actual!r})."
                )

            logger.info(
                "Registered global shortcut with trigger %s.",
                shortcut[1].get("trigger_description", "Ctrl+D"),
            )
            # Wayland/XWayland environments may still forward the key event to
            # XWayland clients. Grab it there as a sink, without emitting a
            # second activation, so editors never receive a stream of `d`s.
            guard_thread = threading.Thread(
                target=self._run_x11,
                kwargs={"emit_activated": False, "stop_event": guard_stop},
                name="xwayland-ctrl-d-key-sink",
                daemon=True,
            )
            guard_thread.start()
            timer = GLib.timeout_source_new(150)
            timer.set_callback(lambda *_args: not self._stop_event.is_set())
            timer.attach(context)
            while not self._stop_event.is_set():
                context.iteration(True)
            timer.destroy()
        finally:
            guard_stop.set()
            if guard_thread is not None and guard_thread.is_alive():
                guard_thread.join(timeout=1.0)
            if connection is not None and activated_subscription:
                connection.signal_unsubscribe(activated_subscription)
            if connection is not None and deactivated_subscription:
                connection.signal_unsubscribe(deactivated_subscription)
            if connection is not None and session_path:
                try:
                    connection.call_sync(
                        _PORTAL_NAME,
                        session_path,
                        _SESSION_IFACE,
                        "Close",
                        GLib.Variant("()", ()),
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None,
                    )
                except Exception:
                    pass
            context.pop_thread_default()

    @staticmethod
    def _find_shortcut(shortcuts):
        return next(
            (item for item in shortcuts if item[0] == "toggle_selection_translate"),
            None,
        )

    @staticmethod
    def _is_ctrl_d(shortcut) -> bool:
        properties = shortcut[1]
        trigger = properties.get("trigger_description", "")
        tokens = re.findall(r"[a-z]+", trigger.casefold())
        # GNOME describes the accelerator as "Press <Control>d"; other
        # desktops commonly return "Ctrl+D" or "Control+d".
        tokens = [token for token in tokens if token not in {"press", "release"}]
        return tokens in (["ctrl", "d"], ["control", "d"])

    def _portal_request(self, connection, context, method, parameters, stop_event, handle_token):
        from gi.repository import Gio, GLib

        response: dict[str, object] = {}
        sender = connection.get_unique_name()
        if not sender:
            raise RuntimeError("Session bus did not assign a unique connection name.")
        sender_token = sender.lstrip(":").replace(".", "_")
        request_path = f"{_PORTAL_PATH}/request/{sender_token}/{handle_token}"

        def on_response(_connection, _sender, _path, _interface, _member, args, _data):
            code, values = args.unpack()
            response["code"] = code
            response["values"] = values

        subscription = connection.signal_subscribe(
            _PORTAL_NAME,
            _REQUEST_IFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
            None,
        )
        # Register the match on D-Bus before invoking the portal. CreateSession
        # can respond immediately, so subscribing after call_sync loses Response.
        connection.flush_sync(None)
        try:
            reply = connection.call_sync(
                _PORTAL_NAME,
                _PORTAL_PATH,
                _SHORTCUTS_IFACE,
                method,
                parameters,
                None,
                Gio.DBusCallFlags.NONE,
                15000,
                None,
            )
            returned_path = reply.unpack()[0]
            if returned_path != request_path:
                raise RuntimeError(
                    f"Portal returned unexpected request path {returned_path!r}."
                )
        except Exception:
            connection.signal_unsubscribe(subscription)
            raise
        timer = GLib.timeout_source_new(150)
        timer.set_callback(lambda *_args: not stop_event.is_set())
        timer.attach(context)
        deadline = time.monotonic() + 15.0
        try:
            while "code" not in response and not stop_event.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for {method} response.")
                context.iteration(True)
        finally:
            timer.destroy()
            connection.signal_unsubscribe(subscription)

        if stop_event.is_set():
            raise RuntimeError("Hotkey registration was stopped.")
        if response.get("code") != 0:
            raise RuntimeError(f"Desktop rejected {method} (response {response.get('code')}).")
        return response.get("values", {})

    def _on_portal_activated(self, _connection, _sender, _path, _interface, _member, args, _data):
        _session_path, shortcut_id, _timestamp, _options = args.unpack()
        if shortcut_id == "toggle_selection_translate":
            self._emit_activation()

    def _on_portal_deactivated(self, _connection, _sender, _path, _interface, _member, args, _data):
        _session_path, shortcut_id, _timestamp, _options = args.unpack()
        if shortcut_id == "toggle_selection_translate":
            self._release_activation()

    def _run_x11(self, *, emit_activated: bool = True, stop_event=None) -> None:
        library_name = ctypes.util.find_library("X11")
        if not library_name:
            raise RuntimeError("libX11 is unavailable.")
        x11 = ctypes.CDLL(library_name)
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_uint
        x11.XGrabKey.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        x11.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XPending.argtypes = [ctypes.c_void_p]
        x11.XPending.restype = ctypes.c_int
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x11.XConnectionNumber.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        display = x11.XOpenDisplay(None)
        if not display:
            raise RuntimeError("Could not connect to the X11/XWayland display.")

        root = x11.XDefaultRootWindow(display)
        keysym = x11.XStringToKeysym(b"d")
        keycode = int(x11.XKeysymToKeycode(display, keysym))
        if not keycode:
            x11.XCloseDisplay(display)
            raise RuntimeError("Could not resolve the D key on this keyboard layout.")

        control_mask = 1 << 2
        lock_masks = (0, 1 << 1, 1 << 4, (1 << 1) | (1 << 4))
        modifiers = tuple(control_mask | mask for mask in lock_masks)
        try:
            for mask in modifiers:
                x11.XGrabKey(display, keycode, mask, root, 0, 1, 1)
            x11.XSync(display, 0)
            logger.info("Registered Ctrl+D with the X11/XWayland server.")
            connection_fd = x11.XConnectionNumber(display)
            event = _XEvent()
            stop_event = stop_event or self._stop_event
            while not stop_event.is_set():
                readable, _, _ = select.select([connection_fd], [], [], 0.15)
                if not readable:
                    continue
                while x11.XPending(display):
                    x11.XNextEvent(display, ctypes.byref(event))
                    if event.xkey.keycode != keycode:
                        continue
                    if event.type == 2 and event.xkey.state & control_mask:
                        if emit_activated:
                            self._emit_activation()
                    elif event.type == 3:
                        self._release_activation()
        finally:
            for mask in modifiers:
                x11.XUngrabKey(display, keycode, mask, root)
            x11.XSync(display, 0)
            x11.XCloseDisplay(display)


__all__ = ["GlobalHotkey"]
