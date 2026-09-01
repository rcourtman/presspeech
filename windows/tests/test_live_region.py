import unittest
import ctypes
from unittest import mock

import live_region


class LiveRegionTests(unittest.TestCase):
    def test_non_windows_bridge_is_an_import_safe_noop(self):
        regions = live_region.LiveRegions(platform="linux")

        self.assertFalse(regions.mark(123))
        self.assertFalse(regions.announce(123))
        self.assertFalse(regions.clear(123))

    def test_priority_is_validated_before_marking(self):
        regions = live_region.LiveRegions(platform="win32")

        with self.assertRaisesRegex(ValueError, "priority"):
            regions.mark(123, 99)

    def test_windows_mark_and_clear_use_thread_local_store(self):
        regions = live_region.LiveRegions(platform="win32")
        store = mock.Mock()
        regions._local.store = store

        self.assertTrue(regions.mark(123, live_region.ASSERTIVE))
        self.assertTrue(regions.clear(123))

        store.mark.assert_called_once_with(123, live_region.ASSERTIVE)
        store.clear.assert_called_once_with(123)

    def test_announcement_targets_the_control_client_object(self):
        regions = live_region.LiveRegions(platform="win32")
        windll = mock.Mock()

        with mock.patch.object(
                live_region.ctypes, "windll", windll, create=True):
            self.assertTrue(regions.announce(8123))

        args = windll.user32.NotifyWinEvent.call_args.args
        self.assertEqual(args[0], 0x8019)
        self.assertIsInstance(args[1], ctypes.c_void_p)
        self.assertEqual(args[1].value, 8123)
        self.assertEqual(args[2], -4)
        self.assertEqual(args[3], 0)

    def test_live_setting_is_passed_to_accprop_as_a_numeric_variant(self):
        store = live_region._LiveRegionStore()
        store._services = ctypes.c_void_p(99)
        call = mock.Mock(return_value=0)
        prototype = object()

        with mock.patch.object(
                live_region, "_set_hwnd_prop", return_value=prototype), \
                mock.patch.object(
                    live_region, "_method", return_value=call) as method:
            store.mark(8123, live_region.POLITE)

        method.assert_called_once_with(store._services, 6, prototype)
        args = call.call_args.args
        self.assertEqual(args[1].value, 8123)
        self.assertEqual(args[2:4], (0xFFFFFFFC, 0))
        self.assertEqual(args[4].Data1, 0xC12BCD8E)
        self.assertEqual(args[5].vt, 3)
        self.assertEqual(args[5].value, live_region.POLITE)


if __name__ == "__main__":
    unittest.main()
