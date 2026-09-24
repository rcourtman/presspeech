"""Model-free focus identity tests, runnable without a Windows desktop."""

import ctypes
import sys
import types
import unittest
from unittest import mock

import paste_target


class TerminalReviewTests(unittest.TestCase):
    def test_line_breaks_require_review_for_known_console_window_owners(self):
        for owner in (
                "conhost.exe", "OpenConsole.exe", "WindowsTerminal.exe",
                "WindowsTerminalPreview.exe", "WindowsTerminalCanary.exe",
                "cmd.exe", "powershell.exe", "pwsh.exe",
                "wezterm-gui.exe", "mintty.exe", "alacritty.exe"):
            for text in ("say hello\n", "say hello\r", "one\r\ntwo"):
                with self.subTest(owner=owner, text=text):
                    self.assertTrue(paste_target.requires_terminal_review(
                        text, owner))

    def test_single_line_terminal_and_multiline_editor_remain_automatic(self):
        for owner in ("WindowsTerminal.exe", "wezterm-gui.exe",
                      "mintty.exe", "alacritty.exe"):
            with self.subTest(owner=owner):
                self.assertFalse(paste_target.requires_terminal_review(
                    "say hello ", owner))
        self.assertFalse(paste_target.requires_terminal_review(
            "first\nsecond", "notepad.exe"))
        self.assertFalse(paste_target.requires_terminal_review(
            "first\nsecond", ""))


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


class WindowCaptionTests(unittest.TestCase):
    def backend(self, title, *, foreground=100):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = foreground
        user32.GetWindowTextLengthW.return_value = len(title)

        def get_text(window, buffer, capacity):
            self.assertEqual(window, 100)
            self.assertGreater(capacity, len(title))
            buffer.value = title
            return len(title)

        user32.GetWindowTextW.side_effect = get_text
        return user32

    def test_only_private_fingerprint_of_complete_caption_is_retained(self):
        title = "Private document - Browser"
        user32 = self.backend(title)
        fingerprint = paste_target.window_caption_fingerprint(user32, 100)
        self.assertIsInstance(fingerprint, bytes)
        self.assertNotIn(title.encode(), fingerprint)
        self.assertEqual(
            fingerprint, paste_target.window_caption_fingerprint(user32, 100))
        self.assertEqual(user32.GetWindowTextW.argtypes, (
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int))
        self.assertIs(user32.GetWindowTextW.restype, ctypes.c_int)

    def test_changed_caption_has_different_fingerprint(self):
        first = paste_target.window_caption_fingerprint(
            self.backend("First page - Browser"), 100)
        second = paste_target.window_caption_fingerprint(
            self.backend("Second page - Browser"), 100)
        self.assertNotEqual(first, second)

    def test_empty_unavailable_or_truncated_caption_is_not_adopted(self):
        self.assertIsNone(paste_target.window_caption_fingerprint(
            self.backend(""), 100))
        self.assertIsNone(paste_target.window_caption_fingerprint(
            self.backend("Page", foreground=200), 100))
        user32 = self.backend("Page")
        user32.GetForegroundWindow.side_effect = [100, 200]
        self.assertIsNone(paste_target.window_caption_fingerprint(user32, 100))
        user32 = self.backend("Page")
        user32.GetWindowTextLengthW.return_value = 2

        def truncated(_hwnd, buffer, capacity):
            buffer.value = "Pag"
            return capacity - 1

        user32.GetWindowTextW.side_effect = truncated
        self.assertIsNone(paste_target.window_caption_fingerprint(user32, 100))
        user32 = self.backend("Page")
        user32.GetWindowTextLengthW.return_value = (
            paste_target._MAX_CAPTION_CHARS + 1)
        self.assertIsNone(paste_target.window_caption_fingerprint(user32, 100))
        user32 = self.backend("Page")
        user32.GetWindowTextW.side_effect = OSError("private window title")
        self.assertIsNone(paste_target.window_caption_fingerprint(user32, 100))


class StableFocusAndCaptionTests(unittest.TestCase):
    def test_same_caption_brackets_focus_query(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 100
        events = []

        def caption(_user32, _window):
            events.append("caption")
            return b"original"

        def focus(_user32, _window, _thread):
            events.append("focus")
            return 101

        self.assertEqual(paste_target.stable_focus_and_caption(
            user32, 100, 77, focus_reader=focus, caption_reader=caption),
            (101, b"original"))
        self.assertEqual(events, ["caption", "focus", "caption"])

    def test_title_change_or_disappearance_invalidates_entire_snapshot(self):
        for after in (b"other tab", None):
            with self.subTest(after=after):
                user32 = mock.Mock()
                user32.GetForegroundWindow.return_value = 100
                caption = mock.Mock(side_effect=[b"original", after])
                self.assertEqual(paste_target.stable_focus_and_caption(
                    user32, 100, 77,
                    focus_reader=mock.Mock(return_value=101),
                    caption_reader=caption), (None, None))

    def test_untitled_window_keeps_existing_focus_fallback(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 100
        self.assertEqual(paste_target.stable_focus_and_caption(
            user32, 100, 77,
            focus_reader=mock.Mock(return_value=101),
            caption_reader=mock.Mock(return_value=None)), (101, None))

    def test_foreground_change_invalidates_snapshot_even_without_title(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 200
        self.assertEqual(paste_target.stable_focus_and_caption(
            user32, 100, 77,
            focus_reader=mock.Mock(return_value=101),
            caption_reader=mock.Mock(return_value=None)), (None, None))

    def test_own_window_does_not_read_caption(self):
        focus = mock.Mock(return_value=101)
        caption = mock.Mock()
        user32 = mock.Mock()
        self.assertEqual(paste_target.stable_focus_and_caption(
            user32, 100, 77, read_caption=False,
            focus_reader=focus, caption_reader=caption), (101, None))
        caption.assert_not_called()


class FocusedEditIdentityTests(unittest.TestCase):
    def test_uia_reader_never_reads_field_text_and_releases_com(self):
        comtypes = types.ModuleType("comtypes")
        comtypes.__path__ = []
        client = types.ModuleType("comtypes.client")
        comtypes.client = client
        comtypes.CoInitialize = mock.Mock()
        comtypes.CoUninitialize = mock.Mock()
        library = types.SimpleNamespace(
            CUIAutomation=mock.sentinel.cuia,
            IUIAutomation=mock.sentinel.iuia)
        client.GetModule = mock.Mock(return_value=library)
        element = mock.Mock()
        element.CurrentControlType = 50004
        element.CurrentIsPassword = False
        element.GetRuntimeId.return_value = (7, 11)
        automation = mock.Mock()
        automation.GetFocusedElement.return_value = element
        client.CreateObject = mock.Mock(return_value=automation)
        with mock.patch.dict(sys.modules, {
                "comtypes": comtypes, "comtypes.client": client}):
            self.assertEqual(paste_target._read_uia_focused_edit(), (7, 11))
            element.CurrentIsPassword = True
            self.assertIsNone(paste_target._read_uia_focused_edit())
            element.CurrentIsPassword = False
            element.CurrentControlType = 50030  # page Document, not a field
            self.assertIsNone(paste_target._read_uia_focused_edit())
        self.assertEqual(element.GetRuntimeId.call_count, 1)
        client.CreateObject.assert_called_with(
            library.CUIAutomation, interface=library.IUIAutomation)
        self.assertEqual(comtypes.CoInitialize.call_count, 3)
        self.assertEqual(comtypes.CoUninitialize.call_count, 3)
        self.assertNotIn("CurrentName", element._mock_children)
        self.assertNotIn("CurrentValue", element._mock_children)

    def test_browser_owner_list_does_not_depend_on_a_private_caption(self):
        for name in ("chrome.exe", "chromium.exe", "MSEDGE.EXE",
                     "firefox.exe", "brave.exe",
                     "opera.exe", "vivaldi.exe", "arc.exe", "waterfox.exe"):
            self.assertTrue(paste_target.requires_edit_identity(name))
        for name in ("notepad.exe", "", None):
            self.assertFalse(paste_target.requires_edit_identity(name))

    def test_accepts_only_a_stable_opaque_edit_identity(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 100
        observer = mock.Mock(return_value=(101, b"title"))
        reader = mock.Mock(return_value=(7, -2, 19))
        self.assertEqual(paste_target.focused_edit_identity(
            user32, 100, 77, 101, b"title", element_reader=reader,
            observation_reader=observer), (7, -2, 19))
        observer.assert_called_once_with(user32, 100, 77)

    def test_disappearing_or_changed_focus_cannot_authorize_paste(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 100
        for later in ((None, None), (102, b"title"), (101, b"other")):
            with self.subTest(later=later):
                self.assertIsNone(paste_target.focused_edit_identity(
                    user32, 100, 77, 101, b"title",
                    element_reader=lambda: (7, 8),
                    observation_reader=lambda *_args: later))
        user32.GetForegroundWindow.return_value = 200
        self.assertIsNone(paste_target.focused_edit_identity(
            user32, 100, 77, 101, b"title",
            element_reader=lambda: (7, 8),
            observation_reader=lambda *_args: (101, b"title")))

    def test_missing_or_malformed_identity_fails_closed(self):
        user32 = mock.Mock()
        user32.GetForegroundWindow.return_value = 100
        observer = lambda *_args: (101, b"title")
        for identity in (None, (), (True,), ("private field name",),
                         tuple(range(65))):
            with self.subTest(identity=identity):
                self.assertIsNone(paste_target.focused_edit_identity(
                    user32, 100, 77, 101, b"title",
                    element_reader=lambda: identity,
                    observation_reader=observer))
        self.assertIsNone(paste_target.focused_edit_identity(
            user32, 100, 77, None, b"title",
            element_reader=mock.Mock(side_effect=AssertionError),
            observation_reader=observer))


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

    def test_caption_change_in_same_win32_control_fails_closed(self):
        first = self.target()._replace(caption_fingerprint=b"first")
        changed = self.target()._replace(caption_fingerprint=b"second")
        unavailable = self.target()._replace(caption_fingerprint=None)
        self.assertTrue(paste_target.matches(first, first))
        self.assertFalse(paste_target.matches(first, changed))
        self.assertFalse(paste_target.matches(first, unavailable))
        self.assertFalse(paste_target.matches(unavailable, first))
        # Uncaptioned apps retain the existing HWND/Win32-focus policy.
        self.assertTrue(paste_target.matches(unavailable, unavailable))

    def test_browser_same_caption_and_win32_focus_needs_same_edit(self):
        captured = paste_target.PasteTarget(
            "chrome.exe", 100, 41, 0, 101, b"same title", (7, 11))
        other_field = captured._replace(edit_identity=(7, 12))
        unavailable = captured._replace(edit_identity=None)
        self.assertTrue(paste_target.matches(captured, captured))
        self.assertFalse(paste_target.matches(captured, other_field))
        self.assertFalse(paste_target.matches(captured, unavailable))
        self.assertFalse(paste_target.matches(unavailable, unavailable))
        self.assertTrue(paste_target.matches(
            self.target()._replace(edit_identity=None), self.target()))

    def test_browser_without_usable_caption_cannot_authorize_paste(self):
        # UIA RuntimeIds may be reused. When the independent tab-title
        # signal is unavailable, matching edit IDs alone are not enough.
        for owner in ("chrome.exe", "msedge.exe", "firefox.exe"):
            with self.subTest(owner=owner):
                captured = paste_target.PasteTarget(
                    owner, 100, 41, 0, 101, None, (7, 11))
                self.assertFalse(paste_target.matches(captured, captured))
                self.assertFalse(paste_target.matches(
                    captured._replace(caption_fingerprint=b"title"),
                    captured))
        # A genuinely untitled native editor retains the window/control
        # fallback; this restriction is specific to recognized browsers.
        untitled_editor = self.target()._replace(caption_fingerprint=None)
        self.assertTrue(paste_target.matches(untitled_editor, untitled_editor))

    def test_executable_fallback_and_unidentified_owner(self):
        expected = self.target(process=0)
        self.assertTrue(paste_target.matches(expected, self.target(process=0)))
        self.assertFalse(paste_target.matches(expected, self.target(
            process=0, name="other.exe")))
        self.assertFalse(paste_target.matches(
            self.target(process=0, name=""), self.target(process=0, name="")))


class InputIntegrityPolicyTests(unittest.TestCase):
    def test_only_known_equal_or_lower_target_level_allows_delivery(self):
        for target, source, blocked in (
                (0x2000, 0x2000, False),
                (0x1000, 0x2000, False),
                (0x3000, 0x2000, True),
                (0, 0x2000, True),
                (0x2000, 0, True),
                (None, 0x2000, True),
                (0x2000, None, True),
                (True, 0x2000, True),
                (0x2000, True, True)):
            with self.subTest(target=target, source=source):
                self.assertEqual(
                    paste_target.input_integrity_blocks_delivery(target, source),
                    blocked)


if __name__ == "__main__":
    unittest.main()
