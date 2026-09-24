"""Receive Windows lock/unlock and suspend/resume events without a visible UI.

Windows may silently remove a low-level keyboard hook while its owning thread
continues to run. The session listener requests a new hook after the user's
desktop becomes available; it never handles recording or listener work inside
the window procedure. Importing this module has no Win32 side effects.
"""

import ctypes
import os
import queue
import threading
from ctypes import wintypes as w


WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
WTS_CONSOLE_CONNECT = 0x0001
WTS_CONSOLE_DISCONNECT = 0x0002
WTS_REMOTE_CONNECT = 0x0003
WTS_REMOTE_DISCONNECT = 0x0004
WTS_SESSION_LOCK = 0x0007
WTS_SESSION_UNLOCK = 0x0008
RPC_S_INVALID_BINDING = 1702


def event_action(message, reason):
    """Map only relevant own-session events to a short application action."""
    if message == WM_WTSSESSION_CHANGE:
        if reason in (WTS_SESSION_LOCK, WTS_CONSOLE_DISCONNECT,
                      WTS_REMOTE_DISCONNECT):
            return "pause"
        if reason in (WTS_SESSION_UNLOCK, WTS_CONSOLE_CONNECT,
                      WTS_REMOTE_CONNECT):
            return "resume"
    elif message == WM_POWERBROADCAST:
        if reason == PBT_APMSUSPEND:
            return "pause"
        # Unlike PBT_APMRESUMEAUTOMATIC, this follows user activity. Do not
        # try to repair a hook merely because a sleeping PC woke in the dark.
        if reason == PBT_APMRESUMESUSPEND:
            return "resume"
    return None


class _POINT(ctypes.Structure):
    _fields_ = (("x", ctypes.c_int32), ("y", ctypes.c_int32))


class _MSG(ctypes.Structure):
    _fields_ = (
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _POINT),
        ("lPrivate", ctypes.c_uint32),
    )


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("style", ctypes.c_uint32),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int32),
        ("cbWndExtra", ctypes.c_int32),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    )


class _WindowsAPI:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        pointer = ctypes.c_void_p
        self.wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, pointer, ctypes.c_uint32,
            ctypes.c_size_t, ctypes.c_ssize_t)
        signatures = (
            (self.kernel32, "GetModuleHandleW", pointer, (w.LPCWSTR,)),
            (self.user32, "RegisterClassExW", ctypes.c_uint16,
             (ctypes.POINTER(_WNDCLASSEXW),)),
            (self.user32, "UnregisterClassW", w.BOOL,
             (w.LPCWSTR, pointer)),
            (self.user32, "CreateWindowExW", pointer,
             (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
              pointer, pointer, pointer, pointer)),
            (self.user32, "DestroyWindow", w.BOOL, (pointer,)),
            (self.user32, "DefWindowProcW", ctypes.c_ssize_t,
             (pointer, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t)),
            (self.user32, "GetMessageW", ctypes.c_int,
             (ctypes.POINTER(_MSG), pointer, ctypes.c_uint32,
              ctypes.c_uint32)),
            (self.user32, "DispatchMessageW", ctypes.c_ssize_t,
             (ctypes.POINTER(_MSG),)),
            (self.user32, "PostQuitMessage", None, (ctypes.c_int,)),
            (self.user32, "PostMessageW", w.BOOL,
             (pointer, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t)),
            (self.wtsapi32, "WTSRegisterSessionNotification", w.BOOL,
             (pointer, w.DWORD)),
            (self.wtsapi32, "WTSUnRegisterSessionNotification", w.BOOL,
             (pointer,)),
            (self.user32, "RegisterSuspendResumeNotification", pointer,
             (pointer, w.DWORD)),
            (self.user32, "UnregisterSuspendResumeNotification", w.BOOL,
             (pointer,)),
        )
        for library, name, result, arguments in signatures:
            function = getattr(library, name)
            function.restype = result
            function.argtypes = arguments
            setattr(self, name, function)

    @staticmethod
    def last_error():
        return ctypes.get_last_error()


class SessionEventMonitor:
    """Own one invisible top-level HWND and its message loop on one thread."""

    def __init__(self, on_pause, on_resume, on_error):
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_error = on_error
        self._lock = threading.Lock()
        self._stopping = False
        self._window = None
        self._api = None
        self._thread = None
        self._worker = None
        self._actions = queue.SimpleQueue()
        self._stop_event = threading.Event()
        self.ready = threading.Event()
        self.active = False

    def start(self):
        with self._lock:
            if self._thread is not None or self._stopping:
                return
            self._worker = threading.Thread(
                target=self._dispatch_events,
                name="presspeech-session-actions", daemon=True)
            self._thread = threading.Thread(
                target=self._run, name="presspeech-session-events", daemon=True)
            self._worker.start()
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stopping = True
            self._stop_event.set()
            api, window, thread, worker = (
                self._api, self._window, self._thread, self._worker)
        if api is not None and window:
            try:
                api.PostMessageW(window, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if (thread is not None and thread.ident is not None and
                thread is not threading.current_thread()):
            thread.join(timeout=1)
        self._actions.put(None)
        if (worker is not None and worker.ident is not None and
                worker is not threading.current_thread()):
            worker.join(timeout=1)

    def _dispatch_events(self):
        while True:
            action = self._actions.get()
            if action is None:
                return
            with self._lock:
                if self._stopping:
                    continue
            try:
                (self.on_pause if action == "pause" else self.on_resume)()
            except Exception:
                self._report_error("session action failed")

    def _report_error(self, reason):
        try:
            self.on_error(reason)
        except Exception:
            pass

    def _run(self):
        api = None
        window = None
        class_name = "PresspeechSessionEvents-%s-%s" % (os.getpid(), id(self))
        instance = None
        registered_class = False
        registered_session = False
        power_registration = None
        try:
            api = _WindowsAPI()
            instance = api.GetModuleHandleW(None)
            if not instance:
                raise OSError("module handle unavailable")

            def window_proc(hwnd, message, wparam, lparam):
                try:
                    if message == WM_CLOSE:
                        # Quit first. The finally block unregisters WTS and
                        # power notifications *before* destroying their HWND.
                        api.PostQuitMessage(0)
                        return 0
                    if message == WM_DESTROY:
                        api.PostQuitMessage(0)
                        return 0
                    action = event_action(message, wparam)
                    if action == "pause":
                        self._actions.put(action)
                        return 1 if message == WM_POWERBROADCAST else 0
                    if action == "resume":
                        self._actions.put(action)
                        return 1 if message == WM_POWERBROADCAST else 0
                except Exception:
                    # Never unwind a Python exception through a Win32 callback.
                    self._report_error("session callback failed")
                return api.DefWindowProcW(hwnd, message, wparam, lparam)

            callback = api.wndproc_type(window_proc)
            window_class = _WNDCLASSEXW(
                cbSize=ctypes.sizeof(_WNDCLASSEXW),
                lpfnWndProc=ctypes.cast(callback, ctypes.c_void_p).value,
                hInstance=instance,
                lpszClassName=class_name,
            )
            if not api.RegisterClassExW(ctypes.byref(window_class)):
                raise OSError("window class unavailable")
            registered_class = True
            # A message-only HWND misses broadcast power events. This
            # top-level HWND is never shown, focused, or given input.
            window = api.CreateWindowExW(
                0, class_name, None, 0, 0, 0, 0, 0,
                None, None, instance, None)
            if not window:
                raise OSError("notification window unavailable")
            with self._lock:
                self._api = api
                self._window = window
                stopping = self._stopping
            if stopping:
                return
            # At sign-in, Remote Desktop Services may not yet have published
            # TermSrvReadyEvent. Microsoft documents 1702 for that case.
            # Retry only that transient error, with a bounded interruptible
            # wait, rather than silently losing all later lock/unlock events.
            for attempt in range(10):
                registered_session = bool(
                    api.WTSRegisterSessionNotification(window, 0))
                if registered_session or api.last_error() != RPC_S_INVALID_BINDING:
                    break
                if attempt < 9 and self._stop_event.wait(0.5):
                    return
            if not registered_session:
                self._report_error("session notifications unavailable")
            power_registration = api.RegisterSuspendResumeNotification(
                window, 0)
            if not power_registration:
                self._report_error("power notifications unavailable")
            if not registered_session and not power_registration:
                return
            with self._lock:
                self.active = True
                self.ready.set()
            message = _MSG()
            while True:
                result = api.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        self._report_error("session message loop failed")
                    break
                api.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self._report_error("session monitor unavailable")
        finally:
            if api is not None:
                if registered_session:
                    try:
                        api.WTSUnRegisterSessionNotification(window)
                    except Exception:
                        pass
                if power_registration:
                    try:
                        api.UnregisterSuspendResumeNotification(
                            power_registration)
                    except Exception:
                        pass
                if window:
                    try:
                        api.DestroyWindow(window)
                    except Exception:
                        pass
                if registered_class:
                    try:
                        api.UnregisterClassW(class_name, instance)
                    except Exception:
                        pass
            with self._lock:
                self.active = False
                self._window = None
                self._api = None
                self.ready.set()
            self._actions.put(None)
