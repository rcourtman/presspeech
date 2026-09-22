"""Recording ownership and timeout regressions during device refresh."""
import threading
import unittest
from unittest import mock
import app
from audio_backend import AudioBackend


class RescanLifecycleRegression(unittest.TestCase):
    def test_failed_probe_cleanup_can_be_retried_after_app_discards_stream(self):
        native = mock.Mock()
        native.InputStream.return_value.start.side_effect = OSError("device disconnected")
        native.InputStream.return_value.close.side_effect = [OSError("temporarily busy"), None]
        audio = AudioBackend(native)
        with mock.patch.object(app, "AUDIO_BACKEND", audio):
            self.assertIsNone(app.PresspeechApp._probe_input_level(2, 16000))
        # Production probe cleanup swallows close failure and drops its local
        # stream reference. The controller must still own a safe retry handle.
        with audio.operation() as token:
            self.assertTrue(audio.rescan(token, lambda: True))
        self.assertEqual(native.InputStream.return_value.close.call_count, 2)
        native._terminate.assert_called_once()

    def test_expired_discovery_does_not_terminate_new_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance.input_device = None
        instance.settings = {"input_device": "auto"}
        instance._log = mock.Mock()
        current_stream = mock.Mock()
        instance.stream = None

        def finish_expired_search(_selected):
            # The earlier discovery was slow. The user released/repressed and
            # the newer worker has already attached its microphone stream.
            instance._rec_epoch = 5
            instance.stream = current_stream
            instance.input_device = (2, 16000)
            return None

        instance._find_input_device = mock.Mock(side_effect=finish_expired_search)
        with mock.patch.object(app.sd, "_terminate", create=True) as terminate, \
                mock.patch.object(app.sd, "_initialize", create=True):
            instance._open_mic_worker(4)
        terminate.assert_not_called()
        self.assertIs(instance.stream, current_stream)
        self.assertTrue(instance.recording)


    def test_recording_deadline_finishes_while_native_rescan_is_slow(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance.input_device = None
        instance.settings = {"input_device": "auto"}
        instance.stream = None
        instance.buffer = []
        instance.icon = None
        instance._recording_input_device = None
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_limit_timer = mock.Mock()
        for name in ("_restore_playback_after_recording", "_play_cue",
                     "_show_no_speech_feedback", "_log",
                     "_schedule_model_idle_unload"):
            setattr(instance, name, mock.Mock())
        instance._find_input_device = mock.Mock(return_value=None)
        entered = threading.Event()
        finish = threading.Event()
        expired = threading.Event()

        def initialize():
            entered.set()
            self.assertTrue(finish.wait(2))

        def expire():
            instance._recording_limit_reached(4)
            expired.set()

        worker = threading.Thread(target=instance._open_mic_worker, args=(4,))
        deadline = threading.Thread(target=expire)
        with mock.patch.object(app.sd, "_terminate", create=True), \
                mock.patch.object(app.sd, "_initialize", create=True,
                                  side_effect=initialize):
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                deadline.start()
                self.assertTrue(expired.wait(1))
                self.assertFalse(instance.recording)
                self.assertTrue(worker.is_alive())
            finally:
                finish.set()
                worker.join(2)
                if deadline.ident is not None:
                    deadline.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(instance.stream)


if __name__ == "__main__":
    unittest.main()
