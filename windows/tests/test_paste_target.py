"""Model-free focus identity tests, runnable without a Windows desktop."""

import ctypes
import unittest
from unittest import mock

import paste_target


class FocusedChildTests(unittest.TestCase):
    def backend(self, *, active=100, focused=101, foreground=100, root=100):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = foreground
        user32.GetAncestor.return_value = root

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
        self.assertEqual(user32.GetAncestor.argtypes,
                         (ctypes.c_void_p, ctypes.c_uint32))
        self.assertIs(user32.GetAncestor.restype, ctypes.c_void_p)
        user32.GetAncestor.assert_called_once_with(101, 2)

    def test_accepts_focus_on_the_foreground_window_itself(self):
        user32 = self.backend(focused=100)
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 77), 100)
        user32.GetAncestor.assert_called_once_with(100, 2)

    def test_refuses_focus_outside_active_window_or_invalid_focus_handle(self):
        for root in (200, 0):
            with self.subTest(root=root):
                user32 = self.backend(root=root)
                self.assertIsNone(
                    paste_target.focused_child_handle(user32, 100, 77))
        user32 = self.backend()
        user32.GetAncestor.side_effect = OSError("private window title")
        self.assertIsNone(paste_target.focused_child_handle(user32, 100, 77))

    def test_two_incoherent_focus_roots_cannot_authorize_window_only_paste(self):
        user32 = self.backend(root=200)
        captured_focus = paste_target.focused_child_handle(user32, 100, 77)
        delivery_focus = paste_target.focused_child_handle(user32, 100, 77)
        captured = paste_target.PasteTarget("notepad.exe", 100, 41, 0, captured_focus)
        delivery = paste_target.PasteTarget("notepad.exe", 100, 41, 0, delivery_focus)
        self.assertFalse(paste_target.matches(captured, delivery))

    def test_refuses_focus_from_another_active_or_foreground_window(self):
        for active, foreground in ((200, 100), (100, 200)):
            with self.subTest(active=active, foreground=foreground):
                user32 = self.backend(active=active, foreground=foreground)
                self.assertIsNone(
                    paste_target.focused_child_handle(user32, 100, 77))

    def test_refuses_foreground_change_during_focus_validation(self):
        user32 = self.backend()
        user32.GetForegroundWindow.side_effect = [100, 200]
        self.assertIsNone(paste_target.focused_child_handle(user32, 100, 77))

    def test_completed_query_without_child_preserves_window_level_fallback(self):
        user32 = self.backend(focused=0)
        self.assertEqual(paste_target.focused_child_handle(user32, 100, 77), 0)
        user32.GetAncestor.assert_not_called()

    def test_failed_query_and_missing_thread_are_not_adopted(self):
        user32 = self.backend()
        user32.GetGUIThreadInfo.return_value = 0
        user32.GetGUIThreadInfo.side_effect = None
        self.assertIsNone(paste_target.focused_child_handle(user32, 100, 77))
        user32.GetGUIThreadInfo.side_effect = OSError("sensitive target title")
        self.assertIsNone(paste_target.focused_child_handle(user32, 100, 77))
        self.assertIsNone(paste_target.focused_child_handle(user32, 100, 0))
        self.assertIsNone(paste_target.focused_child_handle(user32, 0, 77))


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

    def test_two_completed_queries_without_child_preserve_window_level_policy(self):
        expected = self.target(0)
        self.assertTrue(paste_target.matches(expected, self.target(0)))
        self.assertFalse(paste_target.matches(expected, self.target(window=200)))

    def test_failed_focus_query_never_matches_even_when_both_fail(self):
        expected = self.target(None)
        self.assertTrue(paste_target.same_window(expected, self.target(None)))
        for current in (self.target(None), self.target(0), self.target(101)):
            with self.subTest(current=current.focus_handle):
                self.assertFalse(paste_target.matches(expected, current))
                self.assertFalse(paste_target.matches(current, expected))

    def test_two_failed_gui_queries_cannot_authorize_same_window_paste(self):
        user32 = mock.Mock()
        user32.GetGUIThreadInfo.return_value = 0
        captured_focus = paste_target.focused_child_handle(user32, 100, 77)
        delivery_focus = paste_target.focused_child_handle(user32, 100, 77)
        captured = self.target(captured_focus)
        delivery = self.target(delivery_focus)
        self.assertFalse(paste_target.matches(captured, delivery))

    def test_focus_identity_available_only_after_capture_fails_closed(self):
        expected = self.target(0)
        self.assertFalse(paste_target.matches(expected, self.target(101)))
        self.assertFalse(paste_target.matches(self.target(101), self.target(0)))

    def test_executable_fallback_and_unidentified_owner(self):
        expected = self.target(process=0)
        self.assertTrue(paste_target.matches(expected, self.target(process=0)))
        self.assertFalse(paste_target.matches(expected, self.target(
            process=0, name="other.exe")))
        self.assertFalse(paste_target.matches(
            self.target(process=0, name=""), self.target(process=0, name="")))


if __name__ == "__main__":
    unittest.main()
