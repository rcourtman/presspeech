"""Dictation and Setup checks while another press refreshes PortAudio."""
import contextlib
import queue
import threading
import unittest
from unittest import mock
import app
import ui
from audio_backend import AudioBackend

SELECTED = "MME::Microphone (USB Mic)"
SELECTED_LABEL = "Microphone (USB Mic) — MME (device 2)"
OTHER_LABEL = "Microphone (Realtek Audio) — MME (device 3)"


class DeviceRefreshStatusRegression(unittest.TestCase):
    def setUp(self):
        self.native = mock.Mock()
        patcher = mock.patch.object(
            app, "AUDIO_BACKEND", AudioBackend(self.native))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_instance(self, found=None):
        # The release fixture from test_audio_rescan_lifecycle: these mocks let
        # stop_recording end a capture that has no audio yet.
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = False
        instance._capture_ready = True
        instance._rec_epoch = 4
        instance.input_device = None
        instance.settings = {"input_device": SELECTED}
        instance.stream = None
        instance.buffer = []
        instance.icon = None
        instance._recording_input_device = None
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_limit_timer = mock.Mock()
        instance._starting_recording = False
        instance._canceling_recording = False
        instance.transcribing = False
        instance._microphone_check_in_progress = False
        for name in ("_restore_playback_after_recording", "_play_cue",
                     "_show_no_speech_feedback", "_log",
                     "_schedule_model_idle_unload", "_set_indicator",
                     "notify"):
            setattr(instance, name, mock.Mock())
        self.found = found
        instance._find_input_device = mock.Mock(
            side_effect=lambda _selected, probe=None: self.found)
        return instance

    @staticmethod
    def press(instance):
        with instance.lock:
            instance._rec_epoch += 1
            instance.recording = True
            return instance._rec_epoch

    @contextlib.contextmanager
    def refresh_in_progress(self, instance):
        """An earlier press found no input and its native reset is slow."""
        entered = threading.Event()
        finish = threading.Event()

        def initialize():
            entered.set()
            self.assertTrue(finish.wait(2))

        self.native._initialize.side_effect = initialize
        self.found = None
        epoch = self.press(instance)
        worker = threading.Thread(
            target=instance._open_mic_worker, args=(epoch,))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            # Nothing reached Listening, so the user lets go of the hotkey.
            instance.stop_recording(expected_epoch=epoch)
            self.assertFalse(instance.recording)
            yield
            # The refresh makes the reconnected microphone visible again.
            self.found = (2, 16000)
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_repress_during_refresh_is_postponed_then_opens_selected_input(self):
        genuine = self.make_instance(found=(2, 16000))
        self.native.InputStream.side_effect = OSError("device disconnected")
        self.assertFalse(genuine._open_mic_worker(self.press(genuine)))
        failure_title = genuine.notify.call_args.args[0]
        self.native.InputStream.reset_mock(side_effect=True)

        instance = self.make_instance()
        with self.refresh_in_progress(instance):
            self.assertFalse(instance._open_mic_worker(self.press(instance)))
            self.native.InputStream.assert_not_called()
            self.native._terminate.assert_called_once_with()
        instance.notify.assert_called_once()
        # The press opened nothing: it must not be reported like an input
        # that failed to open, with its privacy and other-input advice.
        self.assertNotEqual(instance.notify.call_args.args[0], failure_title)
        self.assertFalse(instance.recording)

        self.assertTrue(instance._open_mic_worker(self.press(instance)))
        self.assertEqual(self.native.InputStream.call_args.kwargs["device"], 2)
        self.assertEqual(
            self.native.InputStream.call_args.kwargs["samplerate"], 16000)
        self.assertIsNotNone(instance.stream)
        instance.notify.assert_called_once()
        self.assertEqual(instance.settings["input_device"], SELECTED)
        self.native._terminate.assert_called_once_with()

    def test_check_microphone_during_refresh_is_postponed_and_keeps_picker(self):
        values = {"Automatic (recommended)": app.AUTO_INPUT_DEVICE,
                  SELECTED_LABEL: SELECTED, OTHER_LABEL: "MME::Other"}
        for window_class, picker_name in ((ui.SetupWindow, "device"),
                                          (ui.SettingsWindow, "var_device")):
            with self.subTest(window=window_class.__name__):
                instance = self.make_instance()
                window = window_class.__new__(window_class)
                window.app = instance
                window.root = mock.Mock()
                window.microphone_events = queue.Queue()
                window.device_values = dict(values)
                picker = mock.Mock()
                picker.get.return_value = SELECTED_LABEL
                setattr(window, picker_name, picker)
                window.microphone_checking = True
                window.check_microphone_button = mock.Mock()
                window.later_button = mock.Mock()
                window.microphone_status = mock.Mock()
                window._microphone_busy_feedback = False
                with mock.patch.object(
                        instance, "input_device_options",
                        wraps=instance.input_device_options) as options, \
                        self.refresh_in_progress(instance):
                    ui._run_microphone_check(
                        instance, SELECTED, window.microphone_events)
                with mock.patch.object(ui, "_set_accessible_text") as text, \
                        mock.patch.object(ui, "_set_control_state"):
                    window._poll_microphone_events()
                # Discovery during a refresh lists only fallback choices;
                # the picker must keep every input and the saved selection.
                options.assert_not_called()
                picker.config.assert_not_called()
                picker.set.assert_not_called()
                self.assertEqual(window.device_values, values)
                shown = text.call_args.args[1]
                self.assertEqual(shown, ui._microphone_result_text(
                    app.MICROPHONE_CHECK_REFRESHING))
                self.assertNotEqual(shown, ui._microphone_result_text(
                    app.MICROPHONE_CHECK_UNAVAILABLE))
                self.assertNotEqual(shown, ui._microphone_result_text(
                    app.MICROPHONE_CHECK_BUSY))
                self.assertFalse(window._microphone_busy_feedback)
                self.assertFalse(instance._microphone_check_in_progress)
                self.assertEqual(instance.settings["input_device"], SELECTED)
        self.native.InputStream.assert_not_called()

    def test_picker_listed_during_refresh_keeps_choice_without_unplugged_label(
            self):
        instance = self.make_instance()
        with self.refresh_in_progress(instance):
            refreshing = instance.input_device_options()
        with mock.patch.object(app.sd, "query_devices", create=True,
                               return_value=[]), \
                mock.patch.object(app.sd, "query_hostapis", create=True,
                                  return_value=[]):
            unplugged = instance.input_device_options()
        self.assertEqual(refreshing[0][1], app.AUTO_INPUT_DEVICE)
        # Opening Setup or Settings during a refresh must not switch inputs.
        self.assertEqual(refreshing[-1][1], SELECTED)
        self.assertEqual(unplugged[-1][1], SELECTED)
        self.assertNotEqual(refreshing[-1][0], unplugged[-1][0])
        self.assertIn("refreshing", refreshing[-1][0])

    def test_check_microphone_during_dictation_stays_busy(self):
        instance = self.make_instance()
        events = queue.Queue()
        instance.recording = True
        with mock.patch.object(instance, "input_device_options") as options:
            ui._run_microphone_check(instance, SELECTED, events)
        self.assertEqual(events.get_nowait(),
                         (SELECTED, app.MICROPHONE_CHECK_BUSY, None))
        options.assert_not_called()
        self.native._terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
