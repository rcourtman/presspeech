"""No input is injected: every Win32 keyboard call uses a test double."""
import ctypes
import unittest
from unittest import mock

import keyboard_delivery as delivery


class CheckedKeyboardDeliveryTests(unittest.TestCase):
    def backend(self, *, inserted=None):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        self.events = []

        def send_input(count, pointer, size):
            events = ctypes.cast(
                pointer, ctypes.POINTER(delivery._INPUT * count)).contents
            for event in events:
                self.events.append({
                    "count": count,
                    "size": size,
                    "type": event.type,
                    "vk": event.value.ki.wVk,
                    "scan": event.value.ki.wScan,
                    "flags": event.value.ki.dwFlags,
                    "time": event.value.ki.time,
                    "extra": event.value.ki.dwExtraInfo,
                })
            return count if inserted is None else inserted

        api.SendInput.side_effect = send_input
        return api

    def test_press_sends_one_mapped_virtual_key_with_the_native_input_size(self):
        api = self.backend()
        delivery.Controller(api=api).press(delivery.VK_LCONTROL)

        api.MapVirtualKeyW.assert_called_once_with(
            delivery.VK_LCONTROL, delivery._MAPVK_VK_TO_VSC)
        self.assertEqual(self.events, [{
            "count": 1,
            "size": ctypes.sizeof(delivery._INPUT),
            "type": delivery._INPUT_KEYBOARD,
            "vk": delivery.VK_LCONTROL,
            "scan": 0x1D,
            "flags": 0,
            "time": 0,
            "extra": 0,
        }])

    def test_release_sets_keyup_without_changing_the_virtual_key(self):
        api = self.backend()
        delivery.Controller(api=api).release(delivery.VK_V)

        self.assertEqual(self.events[0]["vk"], delivery.VK_V)
        self.assertEqual(
            self.events[0]["flags"], delivery._KEYEVENTF_KEYUP)

    def test_shortcut_is_one_non_interleavable_send_input_batch(self):
        api = self.backend()
        delivery.Controller(api=api).shortcut(
            [delivery.VK_LCONTROL, delivery.VK_LMENU, delivery.VK_LSHIFT],
            delivery.VK_V)

        api.SendInput.assert_called_once()
        self.assertEqual(
            [(event["vk"], event["flags"]) for event in self.events],
            [
                (delivery.VK_LCONTROL, 0),
                (delivery.VK_LMENU, 0),
                (delivery.VK_LSHIFT, 0),
                (delivery.VK_V, 0),
                (delivery.VK_V, delivery._KEYEVENTF_KEYUP),
                (delivery.VK_LSHIFT, delivery._KEYEVENTF_KEYUP),
                (delivery.VK_LMENU, delivery._KEYEVENTF_KEYUP),
                (delivery.VK_LCONTROL, delivery._KEYEVENTF_KEYUP),
            ],
        )
        self.assertTrue(all(event["count"] == 8 for event in self.events))

    def test_partial_shortcut_batch_is_reported_as_uncertain(self):
        api = self.backend(inserted=2)
        with self.assertRaises(delivery.KeyboardDeliveryError):
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V)

        api.SendInput.assert_called_once()
        self.assertEqual(len(self.events), 4)

    def test_shortcut_rejects_ambiguous_key_sets_before_native_calls(self):
        for modifiers, key in (([], delivery.VK_V),
                               ([delivery.VK_LCONTROL, delivery.VK_LCONTROL],
                                delivery.VK_V),
                               ([delivery.VK_V], delivery.VK_V)):
            with self.subTest(modifiers=modifiers):
                api = self.backend()
                with self.assertRaises(ValueError):
                    delivery.Controller(api=api).shortcut(modifiers, key)
                api.MapVirtualKeyW.assert_not_called()
                api.SendInput.assert_not_called()

    def test_unaccepted_event_is_reported_as_uncertain_delivery(self):
        api = self.backend(inserted=0)
        with self.assertRaises(delivery.KeyboardDeliveryError):
            delivery.Controller(api=api).press(delivery.VK_LCONTROL)
        self.assertEqual(len(self.events), 1)

    def test_native_exception_is_redacted_to_a_content_free_error(self):
        api = self.backend()
        api.SendInput.side_effect = OSError("sensitive synthetic detail")

        with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
            delivery.Controller(api=api).press(delivery.VK_LCONTROL)

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_invalid_virtual_keys_are_rejected_before_native_calls(self):
        for virtual_key in (None, False, True, 0, 0xFF, -1, "V"):
            with self.subTest(virtual_key=virtual_key):
                api = self.backend()
                with self.assertRaises(ValueError):
                    delivery.Controller(api=api).press(virtual_key)
                api.MapVirtualKeyW.assert_not_called()
                api.SendInput.assert_not_called()

    def test_input_structure_matches_the_32_and_64_bit_win32_abi(self):
        pointer_size = ctypes.sizeof(ctypes.c_size_t)
        self.assertIn(pointer_size, (4, 8))
        self.assertEqual(
            ctypes.sizeof(delivery._INPUT), 28 if pointer_size == 4 else 40)
        self.assertEqual(
            delivery._INPUT.value.offset, 4 if pointer_size == 4 else 8)

    def test_windows_bindings_use_the_checked_input_pointer_signature(self):
        user32 = mock.Mock()
        with mock.patch.object(
                ctypes, "WinDLL", create=True, return_value=user32):
            api = delivery._WindowsAPI()

        self.assertEqual(api.MapVirtualKeyW.argtypes,
                         (ctypes.c_uint32, ctypes.c_uint32))
        self.assertIs(api.MapVirtualKeyW.restype, ctypes.c_uint32)
        self.assertEqual(api.SendInput.argtypes, (
            ctypes.c_uint32,
            ctypes.POINTER(delivery._INPUT),
            ctypes.c_int,
        ))
        self.assertIs(api.SendInput.restype, ctypes.c_uint32)
        api.SendInput.assert_not_called()


if __name__ == "__main__":
    unittest.main()
