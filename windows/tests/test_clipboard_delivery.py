"""No Windows clipboard or input operations: all native calls use test doubles."""
import ctypes
import unittest
from unittest import mock

import clipboard_delivery as delivery


class ClipboardTransactionTests(unittest.TestCase):
    def backend(self):
        api = mock.Mock()
        self.state = state = {'sequence': 100, 'owner': 51, 'next': 70,
                              'buffers': {}, 'formats': {}, 'dirty': False}
        def allocate(flags, size):
            state['next'] += 1
            state['buffers'][state['next']] = ctypes.create_string_buffer(size)
            return state['next']
        def empty():
            state['formats'].clear(); state['sequence'] += 1
            return True
        def write(format_id, memory):
            state['formats'][format_id] = memory
            state['sequence'] += 1; state['dirty'] = True
            return memory
        def close():
            if state['dirty']:
                state['sequence'] += 3  # Real Windows synthesized formats.
                state['dirty'] = False
            return True
        api.CreateWindowExW.return_value = 51
        api.RegisterClipboardFormatW.return_value = 0xC001
        api.GlobalAlloc.side_effect = allocate
        api.GlobalLock.side_effect = lambda memory: ctypes.addressof(state['buffers'][memory])
        api.GlobalSize.side_effect = lambda memory: ctypes.sizeof(state['buffers'][memory])
        api.OpenClipboard.return_value = True
        api.EmptyClipboard.side_effect = empty
        api.SetClipboardData.side_effect = write
        api.GetClipboardData.side_effect = lambda format_id: state['formats'].get(format_id, 0)
        api.GetClipboardOwner.side_effect = lambda: state['owner']
        api.GetClipboardSequenceNumber.side_effect = lambda: state['sequence']
        api.CloseClipboard.side_effect = close
        api.DestroyWindow.return_value = True
        return api

    def test_unicode_receipt_handles_close_format_synthesis_without_freeing_transferred_memory(self):
        api = self.backend()
        receipt = delivery.write_text("Zażółć 🐈", api=api)
        self.assertEqual(receipt, delivery.WriteReceipt(106))
        memory = self.state['formats'][13]
        self.assertEqual(bytes(self.state['buffers'][memory]), "Zażółć 🐈\0".encode("utf-16-le"))
        names = [call[0] for call in api.mock_calls]
        self.assertLess(names.index("GlobalAlloc"), names.index("EmptyClipboard"))
        self.assertLess(names.index("CloseClipboard"), names.index("GetClipboardOwner"))
        self.assertLess(names.index("GetClipboardOwner"), names.index("GetClipboardSequenceNumber"))
        self.assertEqual(api.OpenClipboard.call_args_list, [mock.call(51), mock.call(51)])
        self.assertEqual(api.CloseClipboard.call_count, 2)
        self.assertEqual(api.SetClipboardData.call_count, 2)
        api.GlobalFree.assert_not_called()

    def assert_rejected_without_rewrite(self, api):
        with self.assertRaises(delivery.ClipboardError):
            delivery.write_text("synthetic transcript", api=api)
        api.EmptyClipboard.assert_called_once()
        self.assertEqual(api.SetClipboardData.call_count, 2)
        api.DestroyWindow.assert_called_once_with(51)
        api.GlobalFree.assert_not_called()  # Both blocks already transferred.

    def after_first_close(self, api, mutation):
        original = api.CloseClipboard.side_effect
        def close():
            result = original()
            if api.CloseClipboard.call_count == 1:
                mutation()
            return result
        api.CloseClipboard.side_effect = close

    def test_external_owner_even_with_cloned_nonce_and_text_is_not_adopted(self):
        api = self.backend()
        self.after_first_close(api, lambda: self.state.update(owner=99, sequence=999))
        self.assert_rejected_without_rewrite(api)
        api.GetClipboardData.assert_not_called()  # Do not read another owner's text.

    def test_same_owner_with_changed_nonce_is_not_adopted(self):
        api = self.backend()
        def mutation():
            self.state['buffers'][self.state['formats'][0xC001]].raw = b'x' * 32
            self.state['sequence'] = 999
        self.after_first_close(api, mutation)
        self.assert_rejected_without_rewrite(api)

    def test_preserved_nonce_with_changed_unicode_is_not_adopted(self):
        api = self.backend()
        def mutation():
            self.state['buffers'][self.state['formats'][13]].raw = b'x' * len("synthetic transcript\0".encode('utf-16-le'))
            self.state['sequence'] = 999
        self.after_first_close(api, mutation)
        self.assert_rejected_without_rewrite(api)

    def test_change_after_final_close_is_not_reported_as_owned_copy(self):
        api = self.backend(); original = api.CloseClipboard.side_effect
        def close():
            result = original()
            if api.CloseClipboard.call_count == 2:
                self.state['sequence'] += 1
            return result
        api.CloseClipboard.side_effect = close
        self.assert_rejected_without_rewrite(api)

    def test_set_failure_frees_only_untransferred_memory_without_rollback(self):
        for failed_write in (1, 2):
            with self.subTest(failed_write=failed_write):
                api = self.backend(); original = api.SetClipboardData.side_effect
                api.SetClipboardData.side_effect = lambda *args: 0 if api.SetClipboardData.call_count == failed_write else original(*args)
                with self.assertRaises(delivery.ClipboardError):
                    delivery.write_text("synthetic transcript", api=api)
                self.assertEqual(api.GlobalFree.call_count, 3 - failed_write)
                api.EmptyClipboard.assert_called_once()
                self.assertEqual(api.SetClipboardData.call_count, failed_write)
                api.CloseClipboard.assert_called_once()
                api.DestroyWindow.assert_called_once_with(51)

    def test_second_allocation_failure_preserves_clipboard_and_releases_first_block(self):
        api = self.backend(); original = api.GlobalAlloc.side_effect
        api.GlobalAlloc.side_effect = lambda *args: 0 if api.GlobalAlloc.call_count == 2 else original(*args)
        with self.assertRaises(delivery.ClipboardError):
            delivery.write_text("synthetic transcript", api=api)
        api.OpenClipboard.assert_not_called()
        api.EmptyClipboard.assert_not_called()
        api.GlobalFree.assert_called_once_with(71)
        api.DestroyWindow.assert_called_once_with(51)

    def test_locked_clipboard_times_out_without_mutation_or_memory_leak(self):
        api = self.backend(); api.OpenClipboard.return_value = False
        with self.assertRaises(delivery.ClipboardError):
            delivery.write_text("synthetic transcript", api=api,
                                monotonic=mock.Mock(side_effect=[10.0, 10.1, 10.51]), sleep=mock.Mock())
        self.assertEqual(api.OpenClipboard.call_count, 2)
        api.EmptyClipboard.assert_not_called(); api.CloseClipboard.assert_not_called()
        self.assertEqual(api.GlobalFree.call_count, 2)
        api.DestroyWindow.assert_called_once_with(51)

    def test_reacquisition_failure_does_not_rewrite_or_adopt_current_sequence(self):
        api = self.backend(); api.OpenClipboard.side_effect = [True, False]
        with self.assertRaises(delivery.ClipboardError):
            delivery.write_text("synthetic transcript", api=api,
                                monotonic=mock.Mock(side_effect=[10.0, 10.51]), sleep=mock.Mock())
        api.GetClipboardSequenceNumber.assert_not_called()
        api.EmptyClipboard.assert_called_once()
        self.assertEqual(api.SetClipboardData.call_count, 2)
        api.GlobalFree.assert_not_called()

    def test_zero_sequence_and_close_failure_do_not_report_success(self):
        for fault in ("GetClipboardSequenceNumber", "CloseClipboard"):
            with self.subTest(fault=fault):
                api = self.backend()
                getattr(api, fault).side_effect = lambda: 0
                with self.assertRaises(delivery.ClipboardError):
                    delivery.write_text("synthetic transcript", api=api)
                api.GlobalFree.assert_not_called()
                api.EmptyClipboard.assert_called_once()
                api.DestroyWindow.assert_called_once_with(51)

    def test_sequence_failure_and_untrusted_receipts_fail_closed(self):
        api = self.backend()
        for receipt in (None, 101, delivery.WriteReceipt(0)):
            self.assertFalse(delivery.is_current(receipt, api=api))
        api.GetClipboardSequenceNumber.side_effect = OSError("unavailable")
        self.assertFalse(delivery.is_current(delivery.WriteReceipt(101), api=api))

    def test_embedded_nul_is_rejected_before_any_native_operation(self):
        api = self.backend()
        with self.assertRaises(delivery.ClipboardError):
            delivery.write_text("do not truncate\0this", api=api)
        self.assertEqual(api.mock_calls, [])

    def test_ctypes_bindings_use_pointer_width_handles_without_loading_windows(self):
        user32, kernel32 = mock.Mock(), mock.Mock()
        with mock.patch.object(ctypes, "WinDLL", create=True, side_effect=[user32, kernel32]):
            api = delivery._WindowsAPI()
        from ctypes import wintypes
        self.assertIs(api.SetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(api.GetClipboardData.restype, wintypes.HANDLE)
        self.assertIs(api.GetClipboardOwner.restype, wintypes.HWND)
        self.assertIs(api.GlobalLock.restype, wintypes.LPVOID)
        self.assertEqual(api.GlobalAlloc.argtypes, [wintypes.UINT, ctypes.c_size_t])
        api.OpenClipboard.assert_not_called(); api.CreateWindowExW.assert_not_called()
