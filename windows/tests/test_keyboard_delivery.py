"""No input is injected: every Win32 keyboard call uses a test double."""
import ctypes
import unittest
from unittest import mock

import keyboard_delivery as delivery


class CheckedKeyboardDeliveryTests(unittest.TestCase):
    def backend(self, *, inserted=None):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0
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

    def test_held_modifier_blocks_shortcut_before_any_input_is_sent(self):
        for key in delivery._MODIFIER_KEYS:
            with self.subTest(key=key):
                api = self.backend()
                api.GetAsyncKeyState.side_effect = (
                    lambda checked, held=key: -32768 if checked == held else 0)

                with self.assertRaises(delivery.ModifierHeldError):
                    delivery.Controller(api=api).shortcut(
                        [delivery.VK_LCONTROL], delivery.VK_V)

                api.SendInput.assert_not_called()

    def test_preflight_detects_held_key_without_injecting_input(self):
        for key in delivery._MODIFIER_KEYS:
            with self.subTest(key=key):
                api = self.backend()
                api.GetAsyncKeyState.side_effect = (
                    lambda checked, held=key: -32768 if checked == held else 0)

                self.assertTrue(delivery.paste_keys_held(api=api))
                api.SendInput.assert_not_called()

    def test_preflight_ignores_unreliable_recent_press_bit(self):
        api = self.backend()
        api.GetAsyncKeyState.return_value = 1

        self.assertFalse(delivery.paste_keys_held(api=api))
        self.assertEqual(api.GetAsyncKeyState.call_count,
                         len(delivery._MODIFIER_KEYS))
        api.SendInput.assert_not_called()

    def test_preflight_query_failure_is_content_free(self):
        api = self.backend()
        api.GetAsyncKeyState.side_effect = OSError("private keyboard detail")

        with self.assertRaises(delivery.ModifierStateError) as raised:
            delivery.paste_keys_held(api=api)

        self.assertNotIn("private", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        api.SendInput.assert_not_called()

    def test_recent_press_bit_alone_does_not_block_shortcut(self):
        api = self.backend()
        api.GetAsyncKeyState.return_value = 1

        delivery.Controller(api=api).shortcut(
            [delivery.VK_LCONTROL], delivery.VK_V)

        api.SendInput.assert_called_once()
        self.assertEqual(api.GetAsyncKeyState.call_count,
                         len(delivery._MODIFIER_KEYS))

    def test_modifier_query_error_blocks_shortcut_without_leaking_detail(self):
        api = self.backend()
        api.GetAsyncKeyState.side_effect = OSError("private keyboard detail")

        with self.assertRaises(delivery.ModifierStateError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V)

        api.SendInput.assert_not_called()
        self.assertNotIn("private", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_pre_submit_guard_brackets_final_modifier_check_before_input(self):
        api = self.backend()
        observed = []

        def final_guard():
            observed.append(api.GetAsyncKeyState.call_count)
            api.SendInput.assert_not_called()
            return True

        delivery.Controller(api=api).shortcut(
            [delivery.VK_LCONTROL], delivery.VK_V,
            before_submit=final_guard)

        self.assertEqual(observed, [len(delivery._MODIFIER_KEYS),
                                    2 * len(delivery._MODIFIER_KEYS)])
        self.assertEqual(api.GetAsyncKeyState.call_count,
                         2 * len(delivery._MODIFIER_KEYS))
        api.SendInput.assert_called_once()

    def test_focus_change_during_final_modifier_scan_blocks_shortcut(self):
        api = self.backend()
        state = {"focused": True, "guard_calls": 0}
        key_queries = 0

        def key_state(_key):
            nonlocal key_queries
            key_queries += 1
            if key_queries > len(delivery._MODIFIER_KEYS):
                state["focused"] = False
            return 0

        def final_guard():
            state["guard_calls"] += 1
            return True if state["focused"] else "focus-changed"

        api.GetAsyncKeyState.side_effect = key_state
        with self.assertRaises(delivery.PreSubmitCheckError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V,
                before_submit=final_guard)

        self.assertEqual(raised.exception.reason, "focus-changed")
        self.assertEqual(state["guard_calls"], 2)
        self.assertEqual(key_queries, 2 * len(delivery._MODIFIER_KEYS))
        api.SendInput.assert_not_called()

    def test_key_pressed_during_pre_submit_guard_blocks_shortcut(self):
        api = self.backend()
        state = {"held": False}
        api.GetAsyncKeyState.side_effect = (
            lambda key: -32768 if state["held"] and key == delivery.VK_LSHIFT else 0)

        def final_guard():
            state["held"] = True
            return True

        with self.assertRaises(delivery.ModifierHeldError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V,
                before_submit=final_guard)

        self.assertFalse(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.accepted_count, 0)
        api.SendInput.assert_not_called()

    def test_key_query_failure_after_pre_submit_guard_blocks_shortcut(self):
        api = self.backend()
        state = {"fail": False}

        def key_state(_key):
            if state["fail"]:
                raise OSError("private keyboard detail")
            return 0

        api.GetAsyncKeyState.side_effect = key_state

        def final_guard():
            state["fail"] = True
            return True

        with self.assertRaises(delivery.ModifierStateError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V,
                before_submit=final_guard)

        self.assertFalse(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.accepted_count, 0)
        self.assertNotIn("private", str(raised.exception))
        api.SendInput.assert_not_called()

    def test_failed_or_unavailable_pre_submit_guard_injects_nothing(self):
        def unavailable():
            raise OSError("private clipboard detail")

        for guard in (lambda: False,
                      lambda: None,
                      unavailable):
            with self.subTest(guard=guard):
                api = self.backend()
                with self.assertRaises(delivery.PreSubmitCheckError) as raised:
                    delivery.Controller(api=api).shortcut(
                        [delivery.VK_LCONTROL], delivery.VK_V,
                        before_submit=guard)
                api.SendInput.assert_not_called()
                self.assertNotIn("private", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)

    def test_named_pre_submit_failure_never_injects_or_exposes_details(self):
        for reason in ("focus-changed", "modifier-held", "clipboard-changed",
                       "private clipboard contents", ["private clipboard contents"]):
            with self.subTest(reason=reason):
                api = self.backend()
                with self.assertRaises(delivery.PreSubmitCheckError) as raised:
                    delivery.Controller(api=api).shortcut(
                        [delivery.VK_LCONTROL], delivery.VK_V,
                        before_submit=lambda: reason)
                expected = (reason if reason in (
                    "focus-changed", "modifier-held", "clipboard-changed")
                    else "delivery-check-unavailable")
                self.assertEqual(raised.exception.reason, expected)
                self.assertNotIn("private", str(raised.exception))
                api.SendInput.assert_not_called()

    def test_partial_shortcut_batch_is_reported_as_uncertain(self):
        api = self.backend(inserted=2)
        with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V)

        api.SendInput.assert_called_once()
        self.assertEqual(len(self.events), 4)
        self.assertTrue(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.accepted_count, 2)

    def test_nonzero_partial_counts_keep_conservative_cleanup(self):
        modifiers = (delivery.VK_LCONTROL, delivery.VK_LMENU,
                     delivery.VK_LSHIFT)
        for accepted in range(1, 8):
            with self.subTest(accepted=accepted):
                api = self.backend(inserted=accepted)
                with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
                    delivery.Controller(api=api).shortcut(modifiers, delivery.VK_V)
                self.assertTrue(raised.exception.cleanup_required)
                self.assertEqual(raised.exception.accepted_count, accepted)
                api.SendInput.assert_called_once()

    def test_failure_before_submission_requires_no_key_up_cleanup(self):
        api = self.backend()
        api.MapVirtualKeyW.side_effect = OSError("private mapping detail")

        with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
            delivery.Controller(api=api).shortcut(
                [delivery.VK_LCONTROL], delivery.VK_V)

        self.assertFalse(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.accepted_count, 0)
        self.assertNotIn("private", str(raised.exception))
        api.SendInput.assert_not_called()

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

    def test_unaccepted_event_reports_zero_accepted_events(self):
        api = self.backend(inserted=0)
        with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
            delivery.Controller(api=api).press(delivery.VK_LCONTROL)
        self.assertEqual(len(self.events), 1)
        self.assertFalse(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.accepted_count, 0)

    def test_native_exception_is_redacted_to_a_content_free_error(self):
        api = self.backend()
        api.SendInput.side_effect = OSError("sensitive synthetic detail")

        with self.assertRaises(delivery.KeyboardDeliveryError) as raised:
            delivery.Controller(api=api).press(delivery.VK_LCONTROL)

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.cleanup_required)
        self.assertIsNone(raised.exception.accepted_count)

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
        self.assertEqual(api.GetAsyncKeyState.argtypes, (ctypes.c_int,))
        self.assertIs(api.GetAsyncKeyState.restype, ctypes.c_int16)
        self.assertEqual(api.SendInput.argtypes, (
            ctypes.c_uint32,
            ctypes.POINTER(delivery._INPUT),
            ctypes.c_int,
        ))
        self.assertIs(api.SendInput.restype, ctypes.c_uint32)
        api.SendInput.assert_not_called()


class ToggleStopModifierWaitTests(unittest.TestCase):
    def test_toggle_stop_modifier_vks_maps_modifiers_and_ignores_function_keys(self):
        self.assertEqual(
            delivery.toggle_stop_modifier_vks("right alt"),
            frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
        )
        self.assertEqual(
            delivery.toggle_stop_modifier_vks("left ctrl"),
            frozenset((delivery.VK_LCONTROL, delivery.VK_CONTROL)),
        )
        self.assertEqual(
            delivery.toggle_stop_modifier_vks("right win"),
            frozenset((delivery.VK_RWIN,)),
        )
        self.assertEqual(delivery.toggle_stop_modifier_vks("f8"), frozenset())
        self.assertEqual(delivery.toggle_stop_modifier_vks(None), frozenset())

    def test_held_paste_keys_returns_down_modifier_vks(self):
        api = mock.Mock()
        api.GetAsyncKeyState.side_effect = (
            lambda vk: -32768 if vk in (delivery.VK_RMENU, delivery.VK_MENU) else 0
        )
        self.assertEqual(
            delivery.held_paste_keys(api=api),
            frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
        )

    def test_wait_skips_when_expected_vks_empty_or_already_clear(self):
        reads = []
        sleeps = []
        expected = delivery.toggle_stop_modifier_vks("right alt")

        # Hold mode / F-key: empty expected VKs never reads or sleeps.
        self.assertEqual(
            delivery.wait_for_toggle_stop_modifier_release(
                frozenset(),
                read_held_vks=lambda: reads.append(1) or frozenset((delivery.VK_RMENU,)),
                sleep=sleeps.append,
            ),
            "not-needed",
        )
        self.assertEqual(reads, [])
        self.assertEqual(sleeps, [])

        # Toggle stop modifier already released before delivery.
        self.assertEqual(
            delivery.wait_for_toggle_stop_modifier_release(
                expected,
                read_held_vks=lambda: frozenset(),
                sleep=sleeps.append,
            ),
            "not-needed",
        )
        self.assertEqual(sleeps, [])

    def test_wait_succeeds_on_early_release(self):
        expected = delivery.toggle_stop_modifier_vks("right alt")
        now = [10.0]
        sleeps = []
        samples = [
            frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
            frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
            frozenset(),
        ]
        index = [0]

        def read():
            value = samples[min(index[0], len(samples) - 1)]
            index[0] += 1
            return value

        def sleep(delta):
            sleeps.append(delta)
            now[0] += delta

        outcome = delivery.wait_for_toggle_stop_modifier_release(
            expected,
            read_held_vks=read,
            max_wait_sec=0.5,
            poll_sec=0.01,
            monotonic=lambda: now[0],
            sleep=sleep,
        )
        self.assertEqual(outcome, "released")
        self.assertEqual(sleeps, [0.01, 0.01])

    def test_wait_aborts_immediately_on_unrelated_modifier(self):
        expected = delivery.toggle_stop_modifier_vks("right alt")
        sleeps = []

        # Left Alt is distinct from Right Alt on Windows and must fail immediately.
        self.assertEqual(
            delivery.wait_for_toggle_stop_modifier_release(
                expected,
                read_held_vks=lambda: frozenset((delivery.VK_LMENU, delivery.VK_MENU)),
                sleep=sleeps.append,
            ),
            "unrelated",
        )
        # Right Alt + Ctrl held together must fail immediately.
        self.assertEqual(
            delivery.wait_for_toggle_stop_modifier_release(
                expected,
                read_held_vks=lambda: frozenset((delivery.VK_RMENU, delivery.VK_LCONTROL)),
                sleep=sleeps.append,
            ),
            "unrelated",
        )
        self.assertEqual(sleeps, [])

    def test_wait_times_out_cleanly_at_bound(self):
        expected = delivery.toggle_stop_modifier_vks("right alt")
        now = [20.0]
        sleeps = []

        def sleep(delta):
            sleeps.append(delta)
            now[0] += delta

        outcome = delivery.wait_for_toggle_stop_modifier_release(
            expected,
            read_held_vks=lambda: frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
            max_wait_sec=0.05,
            poll_sec=0.02,
            monotonic=lambda: now[0],
            sleep=sleep,
        )
        self.assertEqual(outcome, "timed-out")
        self.assertAlmostEqual(now[0] - 20.0, 0.05, places=6)
        self.assertEqual(len(sleeps), 3)

    def test_wait_aborts_when_should_continue_returns_false(self):
        expected = delivery.toggle_stop_modifier_vks("right alt")
        now = [30.0]
        sleeps = []

        def sleep(delta):
            sleeps.append(delta)
            now[0] += delta

        outcome = delivery.wait_for_toggle_stop_modifier_release(
            expected,
            read_held_vks=lambda: frozenset((delivery.VK_RMENU, delivery.VK_MENU)),
            max_wait_sec=0.5,
            poll_sec=0.01,
            monotonic=lambda: now[0],
            sleep=sleep,
            should_continue=lambda: len(sleeps) < 1,
        )
        self.assertEqual(outcome, "aborted")
        self.assertEqual(len(sleeps), 1)


if __name__ == "__main__":
    unittest.main()
