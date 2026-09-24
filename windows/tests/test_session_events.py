"""Windows session monitor policy and a side-effect-free Win32 loop fixture."""

import ctypes
import os
import queue
import threading
import unittest
from unittest import mock

import session_events as events


class SessionEventPolicyTests(unittest.TestCase):
    def test_only_relevant_session_and_interactive_power_events_are_used(self):
        for reason in (
                events.WTS_SESSION_LOCK, events.WTS_CONSOLE_DISCONNECT,
                events.WTS_REMOTE_DISCONNECT):
            self.assertEqual(events.event_action(
                events.WM_WTSSESSION_CHANGE, reason), "pause")
        for reason in (
                events.WTS_SESSION_UNLOCK, events.WTS_CONSOLE_CONNECT,
                events.WTS_REMOTE_CONNECT):
            self.assertEqual(events.event_action(
                events.WM_WTSSESSION_CHANGE, reason), "resume")
        self.assertEqual(events.event_action(
            events.WM_POWERBROADCAST, events.PBT_APMSUSPEND), "pause")
        self.assertEqual(events.event_action(
            events.WM_POWERBROADCAST, events.PBT_APMRESUMESUSPEND), "resume")
        for message, reason in ((events.WM_POWERBROADCAST, 0x12),
                                (events.WM_WTSSESSION_CHANGE, 0x5),
                                (events.WM_CLOSE, 0)):
            self.assertIsNone(events.event_action(message, reason))

    def test_message_structures_keep_pointer_width_fields(self):
        self.assertIs(dict(events._MSG._fields_)["wParam"], ctypes.c_size_t)
        self.assertIs(dict(events._MSG._fields_)["lParam"], ctypes.c_ssize_t)
        self.assertEqual(events._WNDCLASSEXW.lpfnWndProc.offset %
                         ctypes.sizeof(ctypes.c_void_p), 0)


class _FakeWindowsAPI:
    def __init__(self):
        self.messages = queue.Queue()
        self.calls = []
        self.window_ready = threading.Event()
        self.wndproc_type = ctypes.CFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_size_t, ctypes.c_ssize_t)
        self.callback = None
        self.session_registration = True
        self.session_results = None
        self.session_error = 0
        self.power_registration = 222

    def GetModuleHandleW(self, _name):
        return 123

    def RegisterClassExW(self, window_class):
        pointer = ctypes.cast(
            window_class, ctypes.POINTER(events._WNDCLASSEXW)).contents.lpfnWndProc
        self.callback = self.wndproc_type(pointer)
        self.calls.append("register-class")
        return 1

    def CreateWindowExW(self, *_args):
        self.calls.append("create-window")
        self.window_ready.set()
        return 111

    def WTSRegisterSessionNotification(self, window, flags):
        self.calls.append("register-session")
        assert (window, flags) == (111, 0)
        if self.session_results is not None:
            return self.session_results.pop(0)
        return self.session_registration

    def last_error(self):
        return self.session_error

    def RegisterSuspendResumeNotification(self, window, flags):
        self.calls.append("register-power")
        assert (window, flags) == (111, 0)
        return self.power_registration

    def GetMessageW(self, target, _window, _first, _last):
        message, reason = self.messages.get(timeout=2)
        if message is None:
            return 0
        data = ctypes.cast(target, ctypes.POINTER(events._MSG)).contents
        data.hwnd = 111
        data.message = message
        data.wParam = reason
        data.lParam = 0
        return 1

    def DispatchMessageW(self, target):
        data = ctypes.cast(target, ctypes.POINTER(events._MSG)).contents
        return self.callback(data.hwnd, data.message, data.wParam, data.lParam)

    def DefWindowProcW(self, *_args):
        return 0

    def PostQuitMessage(self, _code):
        self.calls.append("post-quit")
        self.messages.put((None, 0))

    def PostMessageW(self, window, message, _wparam, _lparam):
        self.calls.append("post-close")
        assert (window, message) == (111, events.WM_CLOSE)
        self.messages.put((events.WM_CLOSE, 0))
        return True

    def WTSUnRegisterSessionNotification(self, _window):
        self.calls.append("unregister-session")
        return True

    def UnregisterSuspendResumeNotification(self, _registration):
        self.calls.append("unregister-power")
        return True

    def DestroyWindow(self, _window):
        self.calls.append("destroy-window")
        return True

    def UnregisterClassW(self, _name, _instance):
        self.calls.append("unregister-class")
        return True


class SessionEventMonitorTests(unittest.TestCase):
    def test_dispatches_outside_window_proc_and_unregisters_before_destroy(self):
        api = _FakeWindowsAPI()
        received = []
        received_event = threading.Event()

        def observe(action):
            received.append((action, threading.current_thread().name))
            if len(received) == 2:
                received_event.set()

        monitor = events.SessionEventMonitor(
            lambda: observe("pause"), lambda: observe("resume"),
            lambda reason: received.append((reason, "error")))
        with mock.patch.object(events, "_WindowsAPI", return_value=api):
            monitor.start()
            try:
                self.assertTrue(api.window_ready.wait(1))
                api.messages.put((events.WM_WTSSESSION_CHANGE,
                                  events.WTS_SESSION_LOCK))
                api.messages.put((events.WM_WTSSESSION_CHANGE,
                                  events.WTS_SESSION_UNLOCK))
                self.assertTrue(received_event.wait(1))
            finally:
                monitor.stop()
        self.assertEqual([action for action, _thread in received],
                         ["pause", "resume"])
        self.assertTrue(all(thread == "presspeech-session-actions"
                            for _action, thread in received))
        self.assertLess(api.calls.index("unregister-session"),
                        api.calls.index("destroy-window"))
        self.assertLess(api.calls.index("unregister-power"),
                        api.calls.index("destroy-window"))
        self.assertIn("post-quit", api.calls)

    def test_power_path_survives_unavailable_session_registration(self):
        api = _FakeWindowsAPI()
        api.session_registration = False
        resumed = threading.Event()
        failures = []
        monitor = events.SessionEventMonitor(
            lambda: None, resumed.set, failures.append)
        with mock.patch.object(events, "_WindowsAPI", return_value=api):
            monitor.start()
            try:
                self.assertTrue(api.window_ready.wait(1))
                api.messages.put((events.WM_POWERBROADCAST,
                                  events.PBT_APMRESUMESUSPEND))
                self.assertTrue(resumed.wait(1))
            finally:
                monitor.stop()
        self.assertIn("session notifications unavailable", failures)
        self.assertNotIn("unregister-session", api.calls)
        self.assertIn("unregister-power", api.calls)

    def test_transient_remote_desktop_service_start_is_retried(self):
        api = _FakeWindowsAPI()
        api.session_results = [False, True]
        api.session_error = events.RPC_S_INVALID_BINDING
        monitor = events.SessionEventMonitor(
            lambda: None, lambda: None, lambda _reason: None)
        monitor._stop_event.wait = mock.Mock(return_value=False)
        with mock.patch.object(events, "_WindowsAPI", return_value=api):
            monitor.start()
            try:
                self.assertTrue(monitor.ready.wait(1))
                self.assertTrue(monitor.active)
            finally:
                monitor.stop()
        self.assertEqual(api.calls.count("register-session"), 2)
        monitor._stop_event.wait.assert_called_once_with(0.5)
        self.assertIn("unregister-session", api.calls)

    def test_stop_before_start_does_not_open_a_window(self):
        monitor = events.SessionEventMonitor(lambda: None, lambda: None,
                                             lambda _reason: None)
        monitor.stop()
        with mock.patch.object(events, "_WindowsAPI") as api:
            monitor.start()
        api.assert_not_called()

    @unittest.skipUnless(
        os.name == "nt" and
        os.environ.get("PRESSPEECH_NATIVE_SESSION_TEST") == "1",
        "native session monitor requires a disposable Windows test runner")
    def test_native_hidden_window_registers_and_closes(self):
        failures = []
        monitor = events.SessionEventMonitor(
            lambda: None, lambda: None, failures.append)
        monitor.start()
        try:
            self.assertTrue(monitor.ready.wait(5))
            self.assertTrue(monitor.active, failures)
        finally:
            monitor.stop()
        self.assertFalse(monitor.active)


if __name__ == "__main__":
    unittest.main()
