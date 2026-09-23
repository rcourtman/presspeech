"""Model-free focus identity tests, runnable without a Windows desktop."""

import ctypes
import unittest
from unittest import mock

import paste_target


class FocusedChildTests(unittest.TestCase):
    def backend(self, *, active=100, focused=101, foreground=100):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = foreground

        def query(thread_identifier, pointer):
            self.assertEqual(thread_identifier, 77)
            info = ctypes.cast(
                pointer, ctypes.POINTER(paste_target._GUIThreadInfo)).contents
            self.assertEqual(info.cbSize, ctypes.sizeof(paste_target._GUIThreadInfo))
            info.hwndActive = active
            info.hwndFocus = focused
            return 1

        user32.GetGUIThreadInfo.side_effect = query
        return user32

    def test_guithreadinfo_uses_fixed_win32_widths(self):
        expected = 72 if ctypes.sizeof(ctypes.c_void_p) == 8 else 48
        self.assertEqual(ctypes.sizeof(paste_target._GUIThreadInfo), expected)
        self.assertEqual(paste_target._GUIThreadInfo.hwndFocus.offset,
                         16 if expected == 72 else 12)

    def test_captures_foreground_threads_focused_control(self):
        user32 = self.backend()
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 77), 101)
        self.assertEqual(user32.GetGUIThreadInfo.argtypes, (
            ctypes.c_uint32, ctypes.POINTER(paste_target._GUIThreadInfo)))
        self.assertIs(user32.GetGUIThreadInfo.restype, ctypes.c_int)

    def test_refuses_focus_from_another_active_or_foreground_window(self):
        for active, foreground in ((200, 100), (100, 200)):
            with self.subTest(active=active, foreground=foreground):
                user32 = self.backend(active=active, foreground=foreground)
                self.assertEqual(
                    paste_target.focused_child_handle(user32, 100, 77), 0)

    def test_missing_focus_failed_query_and_missing_thread_are_not_adopted(self):
        self.assertEqual(paste_target.focused_child_handle(
            self.backend(focused=0), 100, 77), 0)
        user32 = self.backend()
        user32.GetGUIThreadInfo.return_value = 0
        user32.GetGUIThreadInfo.side_effect = None
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 77), 0)
        user32.GetGUIThreadInfo.side_effect = OSError("sensitive target title")
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 77), 0)
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 0), 0)
        self.assertEqual(paste_target.focused_child_handle(user32, 0, 77), 0)


class PasteTargetMatchTests(unittest.TestCase):
    def target(self, focus=101, *, process=41, window=100, name="notepad.exe"):
        return paste_target.PasteTarget(name, window, process, 0, focus)

    def test_different_child_or_missing_current_focus_does_not_match(self):
        expected = self.target()
        self.assertTrue(paste_target.matches(expected, self.target()))
        self.assertFalse(paste_target.matches(expected, self.target(102)))
        self.assertFalse(paste_target.matches(expected, self.target(0)))
        self.assertTrue(paste_target.same_window(expected, self.target(102)))

    def test_same_child_in_reused_window_or_other_window_does_not_match(self):
        expected = self.target()
        self.assertFalse(paste_target.matches(expected, self.target(process=42)))
        self.assertFalse(paste_target.matches(expected, self.target(window=200)))

    def test_unknown_original_focus_preserves_window_level_policy(self):
        expected = self.target(0)
        self.assertTrue(paste_target.matches(expected, self.target(101)))
        self.assertTrue(paste_target.matches(expected, self.target(0)))
        self.assertFalse(paste_target.matches(expected, self.target(window=200)))

    def test_executable_fallback_and_unidentified_owner(self):
        expected = self.target(process=0)
        self.assertTrue(paste_target.matches(expected, self.target(process=0)))
        self.assertFalse(paste_target.matches(expected, self.target(
            process=0, name="other.exe")))
        self.assertFalse(paste_target.matches(
            self.target(process=0, name=""), self.target(process=0, name="")))


if __name__ == "__main__":
    unittest.main()
