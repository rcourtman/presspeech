import threading
import unittest
from unittest import mock

from audio_backend import AudioBackend, AudioBackendBusy


class AudioBackendTests(unittest.TestCase):
    def setUp(self):
        self.native = mock.Mock()
        self.audio = AudioBackend(self.native)

    def rescan(self):
        with self.audio.operation() as token:
            return self.audio.rescan(token, lambda: True)

    def test_another_device_query_prevents_reset(self):
        with self.audio.operation():
            self.assertFalse(self.rescan())
        self.native._terminate.assert_not_called()
        self.assertTrue(self.rescan())

    def test_outdated_owner_cannot_reset_even_without_other_users(self):
        with self.audio.operation() as token:
            self.assertFalse(self.audio.rescan(token, lambda: False))
        self.native._terminate.assert_not_called()

    def test_constructor_reserves_backend_before_stream_exists(self):
        entered = threading.Event()
        finish = threading.Event()
        streams = []

        def construct(**_kwargs):
            entered.set()
            self.assertTrue(finish.wait(2))
            return mock.Mock()

        self.native.InputStream.side_effect = construct
        worker = threading.Thread(target=lambda: streams.append(
            self.audio.open_input_stream(device=2)))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.rescan())
            self.native._terminate.assert_not_called()
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        streams[0].close()
        self.assertTrue(self.rescan())

    def test_active_stream_prevents_reset_until_native_close_finishes(self):
        entered = threading.Event()
        finish = threading.Event()
        stream = self.audio.open_input_stream(device=2)

        def close():
            entered.set()
            self.assertTrue(finish.wait(2))

        self.native.InputStream.return_value.close.side_effect = close
        worker = threading.Thread(target=stream.close)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.rescan())
            self.native._terminate.assert_not_called()
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.rescan())
        stream.close()  # Idempotent ownership release.
        self.native.InputStream.return_value.close.assert_called_once()

    def test_failed_constructor_releases_its_reservation(self):
        self.native.InputStream.side_effect = OSError("device disappeared")
        with self.assertRaises(OSError):
            self.audio.open_input_stream(device=2)
        self.assertTrue(self.rescan())

    def test_failed_close_retains_protection_until_a_successful_retry(self):
        stream = self.audio.open_input_stream(device=2)
        self.native.InputStream.return_value.close.side_effect = [OSError("busy"), None]
        with self.assertRaises(OSError):
            stream.close()
        self.assertFalse(self.rescan())
        stream.close()
        self.assertTrue(self.rescan())

    def test_initialization_failure_can_be_retried_without_extra_termination(self):
        self.native._initialize.side_effect = [OSError("temporarily unavailable"), None]
        with self.assertRaises(OSError):
            self.rescan()
        self.assertTrue(self.rescan())
        self.native._terminate.assert_called_once()
        self.assertEqual(self.native._initialize.call_count, 2)

    def test_slow_reset_does_not_block_another_caller(self):
        entered = threading.Event()
        finish = threading.Event()
        rejected = threading.Event()

        def initialize():
            entered.set()
            self.assertTrue(finish.wait(2))

        def query():
            try:
                with self.audio.operation():
                    pass
            except AudioBackendBusy:
                rejected.set()

        self.native._initialize.side_effect = initialize
        resetter = threading.Thread(target=self.rescan)
        resetter.start()
        caller = threading.Thread(target=query)
        try:
            self.assertTrue(entered.wait(1))
            caller.start()
            self.assertTrue(rejected.wait(1))
            self.assertTrue(resetter.is_alive())
        finally:
            finish.set()
            resetter.join(2)
            if caller.ident is not None:
                caller.join(2)
        self.assertFalse(resetter.is_alive())
        self.assertFalse(caller.is_alive())


if __name__ == "__main__":
    unittest.main()
