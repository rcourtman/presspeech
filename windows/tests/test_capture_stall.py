"""A microphone that stops delivering buffers after the recording is ready."""
import threading
import time
import unittest
from unittest import mock

import app
from audio_backend import AudioBackend


STALL_SEC = 0.5
CHECK_SEC = 0.02
BLOCK_FRAMES = 320  # 20 ms at 16 kHz.
BLOCK_SEC = 0.02
TRANSCRIPT = "words heard before the microphone stopped"


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def speech_block():
    return app.np.array([0.02] * BLOCK_FRAMES, dtype=app.np.float32)


class FakeInputStream:
    """Delivers callbacks like PortAudio until the test stops the flow."""

    def __init__(self, native, callback=None, **_kwargs):
        self.calls = native.calls
        self.callback = callback
        self.delivered = 0
        self.last_delivery = 0.0
        self.stopped = False
        self.closed = False
        self.flowing = threading.Event()
        if native.flow_on_start:
            self.flowing.set()
        self.idle = threading.Event()
        self.halted = threading.Event()

    def start(self):
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while not self.halted.is_set():
            if not self.flowing.is_set():
                self.idle.set()
                self.flowing.wait(0.01)
                continue
            self.deliver()
            time.sleep(BLOCK_SEC)

    def deliver(self):
        self.callback(speech_block(), BLOCK_FRAMES, None, None)
        self.delivered += 1
        self.last_delivery = time.perf_counter()

    def stall(self):
        """Stop delivering, as a lost endpoint does, and return when it began."""
        self.flowing.clear()
        self.idle.clear()
        assert self.idle.wait(1)
        return self.last_delivery

    def resume(self):
        self.flowing.set()

    def stop(self):
        self.stopped = True
        self.halted.set()
        self.calls.append("stream.stop")

    def close(self):
        self.closed = True
        self.halted.set()
        self.calls.append("stream.close")


class FakePortAudio:
    def __init__(self):
        self.calls = []
        self.streams = []
        self.flow_on_start = True
        self._terminate = mock.Mock()
        self._initialize = mock.Mock()

    def InputStream(self, **kwargs):
        stream = FakeInputStream(self, **kwargs)
        self.streams.append(stream)
        return stream


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


class Indicator:
    def __init__(self):
        self.events = []

    def show(self, state):
        self.events.append(("show", state))

    def hide(self):
        self.events.append(("hide", None))

    def show_temporary(self, state, _seconds):
        self.events.append(("temporary", state))

    @property
    def last(self):
        return self.events[-1] if self.events else None


class CaptureStallTests(unittest.TestCase):
    def setUp(self):
        self.native = FakePortAudio()
        self.audio = AudioBackend(self.native)
        for name, value in (("CAPTURE_STALL_TIMEOUT_SEC", STALL_SEC),
                            ("CAPTURE_STALL_CHECK_SEC", CHECK_SEC),
                            ("AUDIO_BACKEND", self.audio)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            app, "_foreground_paste_target",
            return_value=app.PasteTarget("notepad.exe", 1234))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.apps = []
        # Runs before the patches above are undone.
        self.addCleanup(self.finish_background_work)

    def finish_background_work(self):
        for instance in self.apps:
            instance.cancel_recording()
        for stream in self.native.streams:
            stream.halted.set()
        for thread in threading.enumerate():
            if thread.name == "presspeech-capture-watch":
                thread.join(2)
        for instance in self.apps:
            self.assertTrue(wait_for(
                lambda: not instance._canceling_recording and
                not instance.transcribing))
        self.assertFalse(any(
            thread.name == "presspeech-capture-watch"
            for thread in threading.enumerate()))

    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.settings = {
            "input_device": "auto", "audio_cues": True,
            "visual_indicator": True, "model": "parakeet-tdt-0.6b-v3",
            "max_recording_seconds": 120}
        instance.recording = False
        instance.transcribing = False
        instance._canceling_recording = False
        instance.stream = None
        instance.buffer = []
        instance.scratchpad = None
        instance._rec_epoch = 0
        instance._model_idle_epoch = 0
        instance._last_model_use = 0.0
        instance._recording_limit_timer = None
        instance._recording_paste_target = app.PasteTarget("", 0)
        instance._recording_scratchpad = None
        instance._recording_input_device = None
        instance._undelivered_lock = threading.Lock()
        instance._undelivered_dictations = []
        instance.indicator = Indicator()
        instance.icon = mock.Mock()
        instance.idle_icon = object()
        instance.rec_icon = object()
        instance.notifications = []
        instance.notify = lambda title, message: instance.notifications.append(
            (title, message))
        instance._log = mock.Mock()
        instance.transcriber = mock.Mock(last_timing={})
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.return_value = TRANSCRIPT
        instance._model_executor = InlineExecutor()
        instance._paste = mock.Mock()
        instance._apply_text = lambda text: text
        instance._dictation_model_ready = mock.Mock(return_value=True)
        instance._get_input_device = mock.Mock(return_value=(2, 16000))
        instance._play_cue_worker = mock.Mock()
        instance._play_cue = lambda name: self.native.calls.append(
            "cue:%s" % name)
        instance._restore_playback_after_recording = (
            lambda: self.native.calls.append("restore playback"))
        for name in ("_wake_model_if_idle", "_mute_playback_for_recording",
                     "_schedule_model_idle_unload",
                     "_capture_benchmark_if_armed", "open_delivery_recovery"):
            setattr(instance, name, mock.Mock())
        self.apps.append(instance)
        return instance

    def start_ready(self, instance, *, flowing=True):
        self.native.flow_on_start = flowing
        self.assertTrue(instance.start_recording())
        self.assertTrue(wait_for(lambda: instance.stream is not None))
        stream = self.native.streams[-1]
        if not flowing:
            stream.deliver()  # The first buffer only proves the device opened.
        self.assertTrue(wait_for(lambda: instance._capture_ready))
        return stream

    def speak(self, instance, blocks=15):
        # Enough accepted audio to reach the recognizer (at least 250 ms).
        self.assertTrue(wait_for(lambda: len(instance.buffer) >= blocks))

    def titles(self, instance):
        return [title for title, _message in instance.notifications]

    def test_stall_ends_listening_within_bound_and_explains_the_hold(self):
        instance = self.make_app()
        stream = self.start_ready(instance)
        self.speak(instance)

        stalled_at = stream.stall()
        time.sleep(STALL_SEC * 0.4)
        self.assertTrue(instance.recording, "a brief gap ended the recording")
        self.assertEqual(instance.indicator.last, ("show", "listening"))
        self.assertTrue(wait_for(lambda: not instance.recording, STALL_SEC + 2))
        self.assertLess(time.perf_counter() - stalled_at, STALL_SEC + 1)
        self.assertTrue(wait_for(lambda: not instance.transcribing))

        self.assertEqual(instance.indicator.events[-2:],
                         [("show", "transcribing"), ("hide", None)])
        self.assertIs(instance.icon.icon, instance.idle_icon)
        instance._paste.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, [TRANSCRIPT])
        self.assertEqual(self.titles(instance), ["Dictation needs review"])
        self.assertIn("may be incomplete", instance.notifications[0][1])

    def test_hold_release_soon_after_stall_is_not_pasted_as_complete(self):
        # The post-roll maximum passes long before this bound, so only the
        # stop gesture's own liveness check can catch the possible loss.
        bound = app.POST_ROLL_MAX_SEC + 3 * STALL_SEC
        patcher = mock.patch.object(app, "CAPTURE_STALL_TIMEOUT_SEC", bound)
        patcher.start()
        self.addCleanup(patcher.stop)
        instance = self.make_app()
        stream = self.start_ready(instance)
        self.speak(instance)

        stream.stall()
        time.sleep(BLOCK_SEC)
        instance.request_stop()
        self.assertTrue(wait_for(
            lambda: not instance.recording and not instance.transcribing,
            bound + 2))

        instance._paste.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, [TRANSCRIPT])
        self.assertEqual(self.titles(instance), ["Dictation needs review"])

    def test_long_dictation_with_brief_gaps_is_pasted(self):
        instance = self.make_app()
        stream = self.start_ready(instance)
        started = time.perf_counter()
        for _ in range(4):
            time.sleep(STALL_SEC / 2)
            stream.stall()
            time.sleep(STALL_SEC * 0.4)
            stream.resume()
        self.assertGreater(time.perf_counter() - started, STALL_SEC * 3)
        self.assertTrue(instance.recording)

        instance.request_stop()
        self.assertTrue(wait_for(
            lambda: not instance.recording and not instance.transcribing))

        instance._paste.assert_called_once()
        self.assertEqual(instance._paste.call_args.args[0], TRANSCRIPT)
        self.assertEqual(instance._undelivered_dictations, [])
        self.assertEqual(instance.notifications, [])
        self.assertFalse(instance._input_overflowed)

    def test_silence_after_cancel_raises_no_stall(self):
        instance = self.make_app()
        stream = self.start_ready(instance)
        self.speak(instance, blocks=3)

        self.assertTrue(instance.cancel_recording())
        self.assertTrue(wait_for(lambda: stream.closed))
        time.sleep(STALL_SEC + 4 * CHECK_SEC)

        self.assertFalse(instance.recording)
        self.assertEqual(instance.notifications, [])
        self.assertFalse(instance._input_overflowed)
        instance._paste.assert_not_called()
        instance.transcriber.transcribe.assert_not_called()
        self.assertEqual(instance.indicator.last, ("hide", None))

    def test_silence_after_a_complete_stop_raises_no_late_stall(self):
        instance = self.make_app()
        self.start_ready(instance)
        self.speak(instance)

        instance.request_stop()
        self.assertTrue(wait_for(
            lambda: not instance.recording and not instance.transcribing))
        time.sleep(STALL_SEC + 4 * CHECK_SEC)

        instance._paste.assert_called_once()
        self.assertEqual(instance.notifications, [])
        self.assertEqual(instance._undelivered_dictations, [])
        self.assertFalse(instance._input_overflowed)

    def test_new_recording_is_not_judged_by_the_previous_one(self):
        instance = self.make_app()
        self.start_ready(instance)
        self.speak(instance, blocks=3)
        self.assertTrue(instance.cancel_recording())
        self.assertTrue(wait_for(lambda: not instance._canceling_recording))

        # The next microphone is slow to start. Silence before readiness
        # belongs to the first-buffer timeout, not to either watchdog.
        self.native.flow_on_start = False
        self.assertTrue(instance.start_recording())
        self.assertTrue(wait_for(lambda: len(self.native.streams) == 2))
        time.sleep(STALL_SEC * 1.5)
        self.assertTrue(instance.recording)
        self.assertEqual(instance.notifications, [])

        current = self.native.streams[-1]
        current.resume()
        self.assertTrue(wait_for(lambda: instance._capture_ready))
        self.speak(instance)
        time.sleep(STALL_SEC)
        self.assertTrue(instance.recording)
        instance.request_stop()
        self.assertTrue(wait_for(
            lambda: not instance.recording and not instance.transcribing))

        instance._paste.assert_called_once()
        self.assertEqual(instance.notifications, [])
        self.assertEqual(instance._undelivered_dictations, [])

    def test_late_callback_from_stalled_stream_does_not_revive_capture(self):
        instance = self.make_app()
        stalled = self.start_ready(instance, flowing=False)
        for _ in range(3):
            stalled.deliver()  # Too short to transcribe, then nothing.
        self.assertTrue(wait_for(lambda: not instance.recording, STALL_SEC + 2))
        self.assertTrue(wait_for(lambda: not instance.transcribing))
        self.assertEqual(self.titles(instance), ["Audio capture incomplete"])
        instance.transcriber.transcribe.assert_not_called()

        indicator_events = list(instance.indicator.events)
        sequence = instance._audio_sequence
        stalled.deliver()
        self.assertFalse(instance.recording)
        self.assertEqual(instance.buffer, [])
        self.assertEqual(instance._audio_sequence, sequence)
        self.assertEqual(instance.indicator.events, indicator_events)

        self.native.flow_on_start = False
        self.assertTrue(instance.start_recording())
        self.assertTrue(wait_for(lambda: len(self.native.streams) == 2))
        stalled.deliver()  # The old endpoint wakes during the new start.
        time.sleep(0.05)
        self.assertFalse(instance._first_audio_callback.is_set())
        self.assertFalse(instance._capture_ready)
        self.assertEqual(instance.buffer, [])
        self.native.streams[-1].resume()
        self.assertTrue(wait_for(lambda: instance._capture_ready))
        self.assertEqual(self.titles(instance), ["Audio capture incomplete"])

    def test_stalled_stream_is_closed_and_releases_its_backend_lease(self):
        instance = self.make_app()
        stream = self.start_ready(instance)
        self.speak(instance)
        with self.audio.operation() as token:
            self.assertFalse(self.audio.rescan(token, lambda: True))

        stream.stall()
        self.assertTrue(wait_for(
            lambda: not instance.recording and not instance.transcribing,
            STALL_SEC + 2))

        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertIsNone(instance.stream)
        # Close capture before playback returns and the stop cue sounds, so
        # neither can reach a transcript.
        self.assertEqual(self.native.calls, [
            "stream.stop", "stream.close", "restore playback", "cue:stop"])
        with self.audio.operation() as token:
            self.assertTrue(self.audio.rescan(token, lambda: True))
        self.native._terminate.assert_called_once_with()


class UnseenStallTests(unittest.TestCase):
    """A stall the watchdog has not sampled yet, under a controlled clock."""

    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.settings = {"audio_cues": True, "visual_indicator": True,
                             "model": "parakeet-tdt-0.6b-v3"}
        instance.recording = True
        instance.transcribing = False
        instance._rec_epoch = 7
        instance._capture_ready = True
        instance._capture_ready_at = 9.0
        instance._stop_before_ready = False
        instance._first_audio_callback = threading.Event()
        instance._input_overflowed = False
        # Enough accepted speech to reach the recognizer.
        instance.buffer = [speech_block() for _ in range(15)]
        instance._peak_rms = 0.02
        instance._audio_sequence = 15
        instance._last_audio_callback_started_at = 10.0
        instance.stream = None
        instance.icon = None
        instance._recording_input_device = (0, 16000)
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_scratchpad = None
        instance._recording_limit_timer = None
        instance._last_model_use = 0.0
        instance._undelivered_lock = threading.Lock()
        instance._undelivered_dictations = []
        instance.indicator = Indicator()
        instance.notifications = []
        instance.notify = lambda title, message: instance.notifications.append(
            (title, message))
        instance._log = mock.Mock()
        instance.transcriber = mock.Mock(last_timing={})
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.return_value = TRANSCRIPT
        instance._model_executor = InlineExecutor()
        instance._paste = mock.Mock()
        instance._apply_text = lambda text: text
        for name in ("_play_cue", "_restore_playback_after_recording",
                     "_schedule_model_idle_unload",
                     "_capture_benchmark_if_armed", "open_delivery_recovery"):
            setattr(instance, name, mock.Mock())
        return instance

    @staticmethod
    def at(seconds):
        return mock.patch.object(app.time, "perf_counter", return_value=seconds)

    def assert_retained_not_pasted(self, instance):
        self.assertFalse(instance.recording)
        self.assertFalse(instance.transcribing)
        instance._paste.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, [TRANSCRIPT])
        self.assertEqual([title for title, _ in instance.notifications],
                         ["Dictation needs review"])

    def assert_pasted(self, instance):
        self.assertFalse(instance.recording)
        self.assertFalse(instance.transcribing)
        instance._paste.assert_called_once()
        self.assertEqual(instance._paste.call_args.args[0], TRANSCRIPT)
        self.assertEqual(instance._undelivered_dictations, [])
        self.assertEqual(instance.notifications, [])

    def test_buffer_resuming_after_unseen_stall_is_not_pasted_as_complete(self):
        # Input returns just after the bound, before a delayed watchdog tick;
        # the gap must not vanish when the timestamp moves on.
        instance = self.make_app()
        resumed = 10.0 + app.CAPTURE_STALL_TIMEOUT_SEC + 0.1
        with self.at(resumed):
            instance._audio_cb(speech_block(), BLOCK_FRAMES, None, None, 7)
        with self.at(resumed + BLOCK_SEC):
            self.assertTrue(instance.stop_recording(expected_epoch=7))
        self.assert_retained_not_pasted(instance)

    def test_buffer_resuming_inside_the_bound_is_pasted(self):
        instance = self.make_app()
        resumed = 10.0 + app.CAPTURE_STALL_TIMEOUT_SEC * 0.9
        with self.at(resumed):
            instance._audio_cb(speech_block(), BLOCK_FRAMES, None, None, 7)
        with self.at(resumed + BLOCK_SEC):
            self.assertTrue(instance.stop_recording(expected_epoch=7))
        self.assert_pasted(instance)

    def test_recording_cap_after_unseen_stall_is_not_pasted_as_complete(self):
        # The duration timer can claim the stop before the watchdog samples
        # a stall that has already passed the bound.
        instance = self.make_app()
        with self.at(10.0 + app.CAPTURE_STALL_TIMEOUT_SEC + 0.1):
            instance._recording_limit_reached(7)
        self.assert_retained_not_pasted(instance)

    def test_recording_cap_on_live_input_is_pasted(self):
        instance = self.make_app()
        with self.at(10.0 + BLOCK_SEC):
            instance._recording_limit_reached(7)
        self.assert_pasted(instance)

    def test_watchdog_stop_stays_incomplete_when_input_returns_at_the_bound(self):
        # A buffer that began just inside the bound lands after the watchdog
        # has decided to stop. Capture still ends without a stop gesture, so
        # whatever the user says next is missing from this dictation.
        instance = self.make_app()
        bound = 10.0 + app.CAPTURE_STALL_TIMEOUT_SEC
        stop_recording = instance.stop_recording

        def input_returns_before_stop(**kwargs):
            with self.at(bound - BLOCK_SEC):
                instance._audio_cb(speech_block(), BLOCK_FRAMES, None, None, 7)
            with self.at(bound + 2 * BLOCK_SEC):
                return stop_recording(**kwargs)

        instance.stop_recording = input_returns_before_stop
        with self.at(bound + BLOCK_SEC), \
                mock.patch.object(app, "CAPTURE_STALL_CHECK_SEC", 0):
            instance._watch_capture(7)
        self.assert_retained_not_pasted(instance)


class PostRollLivenessTests(unittest.TestCase):
    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance._capture_ready = True
        instance._capture_ready_at = 9.0
        instance._stop_before_ready = False
        instance._first_audio_callback = threading.Event()
        instance._input_overflowed = False
        instance.buffer = [app.np.array(
            [0.03] * int(16000 * app.POST_ROLL_TAIL_SEC), dtype=app.np.float32)]
        instance._recording_input_device = (0, 16000)
        instance._peak_rms = 0.1
        instance._audio_sequence = 3
        instance._last_audio_callback_started_at = 9.9
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()
        instance._log = mock.Mock()
        return instance

    def test_release_after_recent_stall_waits_then_marks_incomplete(self):
        instance = self.make_app()
        with mock.patch.object(app.time, "perf_counter", return_value=10.5):
            instance._finish_after_roll(7, 10.0, 3)
        instance.stop_recording.assert_not_called()
        instance._schedule_post_roll.assert_called_once_with(
            app.POST_ROLL_CHECK_SEC, 7, 10.0, 3)
        self.assertFalse(instance._input_overflowed)

        stalled_at = 9.9 + app.CAPTURE_STALL_TIMEOUT_SEC
        with mock.patch.object(app.time, "perf_counter", return_value=stalled_at):
            instance._finish_after_roll(7, 10.0, 3)
        instance.stop_recording.assert_called_once_with(expected_epoch=7)
        self.assertTrue(instance._input_overflowed)

    def test_slow_but_live_input_after_release_stays_complete(self):
        instance = self.make_app()
        with mock.patch.object(app.time, "perf_counter", return_value=10.5):
            instance._finish_after_roll(7, 10.0, 3)
        instance.stop_recording.assert_not_called()

        with mock.patch.object(app.time, "perf_counter", return_value=10.6):
            instance._audio_cb(app.np.array([0.001] * 320, dtype=app.np.float32),
                               320, None, None, 7)
        with mock.patch.object(app.time, "perf_counter", return_value=10.62):
            instance._finish_after_roll(7, 10.0, 3)
        instance.stop_recording.assert_called_once_with(expected_epoch=7)
        self.assertFalse(instance._input_overflowed)

    def test_newer_recording_is_not_marked_by_an_old_post_roll(self):
        instance = self.make_app()
        measure_tail = instance._post_roll_tail

        def stop_and_restart_during_check():
            # The old recording stops and a newer one becomes ready between
            # the post-roll's epoch check and its stall verdict.
            tail = measure_tail()
            instance._rec_epoch = 8
            return tail

        instance._post_roll_tail = stop_and_restart_during_check
        stalled_at = 9.9 + app.CAPTURE_STALL_TIMEOUT_SEC
        with mock.patch.object(app.time, "perf_counter", return_value=stalled_at):
            instance._finish_after_roll(7, 10.0, 3)
        self.assertFalse(instance._input_overflowed)
        instance._schedule_post_roll.assert_not_called()
        # The epoch-checked stop is a no-op for the newer recording.
        instance.stop_recording.assert_called_once_with(expected_epoch=7)


if __name__ == "__main__":
    unittest.main()
