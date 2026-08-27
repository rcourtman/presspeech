import unittest
from unittest import mock

import app
import config


DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "hostapi": 0,
     "max_input_channels": 2},
    {"name": "Microphone (Yeti Nano)", "hostapi": 0,
     "max_input_channels": 2},
    {"name": "Microphone (Yeti Nano)", "hostapi": 1,
     "max_input_channels": 2},
    {"name": "Stereo Mix", "hostapi": 2, "max_input_channels": 2},
    {"name": "Microphone (Yeti Nano)", "hostapi": 2,
     "max_input_channels": 2},
    {"name": "Headset Microphone (HyperX 7.1 Audio)", "hostapi": 0,
     "max_input_channels": 2},
]

HOST_APIS = [
    {"name": "MME"},
    {"name": "Windows WASAPI"},
    {"name": "Windows WDM-KS"},
]


class UpdateWindowTests(unittest.TestCase):
    def make_window(self):
        window = app.ui.UpdateWindow.__new__(app.ui.UpdateWindow)
        window.app = mock.Mock()
        window.update = {"version": "0.1.1"}
        window.root = mock.Mock()
        window.events = app.ui.queue.Queue()
        window.cancel_download = app.threading.Event()
        window.download_lock = app.threading.Lock()
        window.downloaded_installer = None
        window.active_download_directory = None
        window.active_staging_path = None
        window.download_finished = app.threading.Event()
        window.download_finished.set()
        window.status = mock.Mock()
        window.progress = mock.MagicMock()
        window.progress.__getitem__.return_value = 100
        window.download_button = mock.Mock()
        return window

    def stage_ready_installer(self, window):
        directory = app.ui.tempfile.mkdtemp(
            prefix=app.ui.updates.UPDATE_DIRECTORY_PREFIX)
        path = app.os.path.join(directory, "installer.exe")
        with open(path, "wb") as handle:
            handle.write(b"verified installer")
        window.downloaded_installer = path
        window.events.put(("ready", path))
        return path

    def test_closing_window_cancels_an_active_download(self):
        window = self.make_window()

        window._close()

        self.assertTrue(window.cancel_download.is_set())
        window.root.destroy.assert_called_once_with()

    def test_download_worker_forwards_window_cancellation(self):
        window = self.make_window()

        def finish_download(_update, destination, *_args, **_kwargs):
            path = app.os.path.join(destination, "installer.exe")
            with open(path, "wb") as handle:
                handle.write(b"verified installer")
            return path

        with mock.patch.object(
                app.ui.updates, "download_update",
                side_effect=finish_download) as download:
            window._download_worker()

        cancelled = download.call_args.kwargs["cancelled"]
        self.assertFalse(cancelled())
        event = window.events.get_nowait()
        self.assertEqual(event, ("ready", window.downloaded_installer))
        self.assertTrue(app.os.path.exists(window.downloaded_installer))
        window._close()

    def test_close_racing_ready_event_removes_completed_installer(self):
        window = self.make_window()

        class ClosingQueue(app.ui.queue.Queue):
            def put(self, item, *args, **kwargs):
                window._close()
                return super().put(item, *args, **kwargs)

        window.events = ClosingQueue()
        created = {}

        def finish_download(_update, destination, *_args, **_kwargs):
            created["path"] = app.os.path.join(destination, "installer.exe")
            with open(created["path"], "wb") as handle:
                handle.write(b"verified installer")
            return created["path"]

        with mock.patch.object(
                app.ui.updates, "download_update",
                side_effect=finish_download):
            window._download_worker()

        installer = created["path"]
        self.assertFalse(app.os.path.exists(installer))
        self.assertFalse(app.os.path.exists(app.os.path.dirname(installer)))
        self.assertTrue(window.cancel_download.is_set())
        self.assertIsNone(window.downloaded_installer)
        self.assertEqual(window.events.get_nowait(), ("ready", installer))

    def test_declining_install_discards_completed_download(self):
        window = self.make_window()
        installer = self.stage_ready_installer(window)

        with mock.patch.object(app.ui.messagebox, "askyesno", return_value=False):
            window._poll()

        self.assertIsNone(window.downloaded_installer)
        self.assertFalse(app.os.path.exists(installer))
        self.assertFalse(app.os.path.exists(app.os.path.dirname(installer)))
        window.app.launch_update.assert_not_called()
        window.status.config.assert_called_with(text="Ready to download")
        window.progress.config.assert_called_with(value=0)
        window.download_button.config.assert_called_with(state="normal")

    def test_failed_install_launch_discards_completed_download(self):
        window = self.make_window()
        installer = self.stage_ready_installer(window)
        window.app.launch_update.side_effect = RuntimeError("launch failed")

        with mock.patch.object(app.ui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app.ui.messagebox, "showerror") as showerror:
            window._poll()

        self.assertIsNone(window.downloaded_installer)
        self.assertFalse(app.os.path.exists(installer))
        self.assertFalse(app.os.path.exists(app.os.path.dirname(installer)))
        window.app.launch_update.assert_called_once_with(installer, window.update)
        window.status.config.assert_called_with(text="Install failed")
        window.progress.config.assert_called_with(value=0)
        window.download_button.config.assert_called_with(state="normal")
        showerror.assert_called_once_with(
            "Update failed", "launch failed", parent=window.root)

    def test_hard_exit_hands_a_stalled_partial_download_to_cleanup(self):
        window = self.make_window()
        directory = app.ui.tempfile.mkdtemp(
            prefix=app.ui.updates.UPDATE_DIRECTORY_PREFIX)
        partial = app.os.path.join(
            directory,
            "Presspeech-Setup-1.2.3-x64.exe.random123.part")
        with open(partial, "wb") as handle:
            handle.write(b"partial installer")
        window.active_download_directory = directory
        window.active_staging_path = partial
        window.download_finished.clear()

        with mock.patch.object(
                window.download_finished, "wait", return_value=False) as wait, \
                mock.patch.object(
                    app.ui.updates,
                    "schedule_abandoned_download_cleanup") as cleanup:
            window.cancel_and_cleanup()

        self.assertTrue(window.cancel_download.is_set())
        wait.assert_called_once_with(timeout=1.0)
        cleanup.assert_called_once_with(partial)
        app.os.remove(partial)
        app.os.rmdir(directory)


class InputSelectionTests(unittest.TestCase):
    def make_app(self, selected="auto"):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.input_device = None
        instance.settings = {"input_device": selected}
        return instance

    def sounddevice_mocks(self):
        return (
            mock.patch.object(app.sd, "query_devices", return_value=DEVICES),
            mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS),
            mock.patch.object(app.sd, "check_input_settings", return_value=None),
            mock.patch.object(app.PresspeechApp, "_probe_input", return_value=True),
            mock.patch.object(app.PresspeechApp, "_log"),
        )

    def test_automatic_prefers_windows_sound_mapper_at_16khz(self):
        instance = self.make_app()
        patches = self.sounddevice_mocks()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            self.assertEqual(instance._get_input_device(), (0, 16000))

    def test_configured_device_uses_stable_host_and_name_selector(self):
        selector = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app(selector)
        patches = self.sounddevice_mocks()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            self.assertEqual(instance._get_input_device(), (1, 16000))

    def test_picker_excludes_stereo_mix_wdm_ks_and_hyperx(self):
        instance = self.make_app()
        with mock.patch.object(app.sd, "query_devices", return_value=DEVICES), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS):
            options = instance.input_device_options()
        labels = [label for label, _selector in options]
        self.assertFalse(any("Stereo Mix" in label for label in labels))
        self.assertFalse(any("WDM-KS" in label for label in labels))
        self.assertFalse(any("HyperX" in label for label in labels))

    def test_configured_device_never_falls_back_to_another_microphone(self):
        instance = self.make_app("MME::Missing microphone")
        patches = self.sounddevice_mocks()
        with patches[0], patches[1], patches[2], patches[3] as probe, patches[4]:
            self.assertIsNone(instance._get_input_device())
        probe.assert_not_called()


class TextRegressionTests(unittest.TestCase):
    def test_packaged_selftest_loads_every_lazy_runtime_dependency(self):
        loaded = {}

        def load_module(name):
            module = mock.Mock()
            for symbol in dict(app.PACKAGE_SMOKE_IMPORTS)[name]:
                setattr(module, symbol, object())
            loaded[name] = module
            return module

        with mock.patch.object(app.sys, "frozen", True, create=True), \
                mock.patch.object(app.importlib, "import_module",
                                  side_effect=load_module) as importer:
            app._package_selftest()

        self.assertEqual(
            [call.args[0] for call in importer.call_args_list],
            [name for name, _symbols in app.PACKAGE_SMOKE_IMPORTS],
        )
        self.assertEqual(set(loaded), {
            "comtypes", "ctranslate2", "faster_whisper", "librosa",
            "pycaw.constants", "pycaw.pycaw", "safetensors",
            "sentencepiece", "soundfile", "tokenizers", "torch",
            "transformers",
        })

    def test_packaged_selftest_redacts_import_exception_details(self):
        with mock.patch.object(app.sys, "frozen", True, create=True), \
                mock.patch.object(
                    app.importlib, "import_module",
                    side_effect=OSError(r"C:\\private\\build\\missing.dll")):
            with self.assertRaisesRegex(
                    RuntimeError, r"^packaged import unavailable: torch$") as caught:
                app._package_selftest()
        self.assertNotIn("private", str(caught.exception))

    def test_dictionary_rules_match_whole_phrases_only(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "dictionary": [["parake", "Parakeet"],
                           ["bass model", "base model"]],
            "remove_fillers": False,
            "british": False,
            "suffix": "none",
        }
        self.assertEqual(
            instance._apply_text("Parake beats the bass model."),
            "Parakeet beats the base model.")
        self.assertEqual(
            instance._apply_text("Parakeet is already correct."),
            "Parakeet is already correct.")

    def test_dictionary_replacements_are_inserted_literally(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "dictionary": [["project folder", r"C:\Users\me\Presspeech"],
                           ["capture group", r"\1"]],
            "remove_fillers": False,
            "british": False,
            "suffix": "none",
        }

        self.assertEqual(
            instance._apply_text("Open project folder, then type capture group."),
            r"Open C:\Users\me\Presspeech, then type \1.")

    def test_filler_removal_repairs_sentence_capitalization(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "dictionary": [],
            "remove_fillers": True,
            "british": False,
            "suffix": "none",
        }
        self.assertEqual(instance._apply_text("Um, it works."), "It works.")
        self.assertEqual(
            instance._apply_text(
                "This is the first sentence. Um this is the second sentence."),
            "This is the first sentence. This is the second sentence.")
        self.assertEqual(
            app._remove_fillers("This is not a boundary Um this stays lowercase."),
            "This is not a boundary this stays lowercase.")

    def test_filler_removal_cleans_punctuation_runs(self):
        self.assertEqual(
            app._remove_fillers("So, um, uh, I was going."),
            "So, I was going.")
        self.assertEqual(app._remove_fillers("That's all, um."), "That's all.")
        self.assertEqual(app._remove_fillers("Um? What?"), "What?")

    def test_filler_removal_preserves_real_words_and_compounds(self):
        for text in ("It works well.", "I might err.", "Yeah, uh-huh.",
                     "An ohm is a unit."):
            with self.subTest(text=text):
                self.assertEqual(app._remove_fillers(text), text)

    def test_input_device_default_is_automatic(self):
        self.assertEqual(config.DEFAULTS["input_device"], "auto")

    def test_default_precision_is_fp16(self):
        self.assertEqual(config.DEFAULTS["precision"], "fp16")

    def test_paste_delay_is_small_but_nonzero(self):
        self.assertGreater(app.PASTE_DELAY_SEC, 0)
        self.assertLessEqual(app.PASTE_DELAY_SEC, 0.01)

    def test_paste_routes_for_remote_clients(self):
        self.assertEqual(app._paste_route("Moonlight.exe"), "moonlight")
        self.assertEqual(app._paste_route("mstsc.exe"), "rdp")
        self.assertEqual(app._paste_route("msrdc.exe"), "rdp")
        self.assertEqual(app._paste_route("notepad.exe"), "local")

    def test_recording_remembers_foreground_paste_target(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = False
        instance.buffer = []
        instance._rec_epoch = 0
        instance._model_idle_epoch = 0
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "ready"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance._play_cue = mock.Mock()
        instance._wake_model_if_idle = mock.Mock()
        instance._schedule_recording_limit = mock.Mock()
        with mock.patch.object(app, "_foreground_process_name",
                               return_value="moonlight.exe"), \
                mock.patch.object(app.threading, "Thread"), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance.start_recording()
        self.assertEqual(instance._recording_target_process, "moonlight.exe")
        instance._schedule_recording_limit.assert_called_once_with(1)

    def test_recording_is_blocked_while_startup_model_is_loading(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "loading"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        with mock.patch.object(app, "_foreground_process_name") as foreground, \
                mock.patch.object(app.threading, "Thread") as worker:
            self.assertFalse(instance.start_recording())
        self.assertFalse(getattr(instance, "recording", False))
        foreground.assert_not_called()
        worker.assert_not_called()
        instance._set_indicator.assert_called_once_with("loading")
        instance._model_executor.submit.assert_not_called()

    def test_first_press_after_model_error_starts_one_retry_not_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "error"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        self.assertFalse(instance.start_recording())
        self.assertEqual(instance.model_status, "loading")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker)
        self.assertFalse(instance.start_recording())
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker)

    def test_audio_cues_default_on(self):
        self.assertTrue(config.DEFAULTS["audio_cues"])

    def test_playback_muting_defaults_on(self):
        self.assertTrue(config.DEFAULTS["mute_playback_while_recording"])

    def test_visual_indicator_defaults_on(self):
        self.assertTrue(config.DEFAULTS["visual_indicator"])

    def test_update_checks_default_on_and_first_run_setup_is_incomplete(self):
        self.assertTrue(config.DEFAULTS["check_updates"])
        self.assertFalse(config.DEFAULTS["setup_complete"])
        self.assertTrue(config.DEFAULTS["autostart"])

    def test_daily_update_check_interval(self):
        day = app.UPDATE_CHECK_INTERVAL_SEC
        self.assertFalse(app._update_check_due(1000, 1000 + day - 1))
        self.assertTrue(app._update_check_due(1000, 1000 + day))
        self.assertTrue(app._update_check_due("invalid", 1000))

    def test_update_is_revalidated_after_approval_before_launch(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.exit_app = mock.Mock()
        update = {"installer_digest": "a" * 64}
        installer = r"C:\Temp\Presspeech-Setup-0.1.7-x64.exe"
        events = []

        class Guard:
            def __enter__(self):
                events.append("locked")

            def __exit__(self, *_args):
                events.append("unlocked")

        with mock.patch.object(
                app.updates, "locked_verified_installer",
                return_value=Guard()) as verify, \
                mock.patch.object(app.subprocess, "Popen") as launch, \
                mock.patch.object(
                    app.updates, "schedule_installer_cleanup") as cleanup, \
                mock.patch.object(app.time, "sleep"):
            launch.side_effect = lambda *_args, **_kwargs: events.append("launched")
            instance.launch_update(installer, update)
        verify.assert_called_once_with(update, installer)
        launch.assert_called_once_with(
            [installer], cwd=app.os.path.dirname(installer))
        cleanup.assert_called_once_with(installer)
        self.assertEqual(events, ["locked", "launched", "unlocked"])
        instance.exit_app.assert_called_once_with()

    def test_exit_discards_a_completed_update_before_hard_exit(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._restore_playback_after_recording = mock.Mock()
        instance.indicator = mock.Mock()
        instance.listener = None
        instance.scratchpad = None
        instance.settings_window = None
        instance.setup_window = None
        instance.update_window = mock.Mock()
        instance.icon = None

        with mock.patch.object(app.os, "_exit") as hard_exit:
            instance.exit_app()

        instance.update_window.cancel_and_cleanup.assert_called_once_with()
        hard_exit.assert_called_once_with(0)

    def test_failed_launch_revalidation_never_runs_installer(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.exit_app = mock.Mock()
        with mock.patch.object(
                app.updates, "locked_verified_installer",
                side_effect=app.updates.UpdateError(
                    "installer changed after verification")), \
                mock.patch.object(app.subprocess, "Popen") as launch:
            with self.assertRaises(app.updates.UpdateError):
                instance.launch_update("installer.exe", {})
        launch.assert_not_called()
        instance.exit_app.assert_not_called()

    def test_diagnostics_exclude_private_dictionary_contents(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "input_device": "MME::Yeti Nano",
            "hotkey": "right alt",
            "trigger": "hold",
            "check_updates": True,
            "dictionary": [["private spoken phrase", "private replacement"]],
        }
        instance.transcriber = mock.Mock()
        instance.transcriber.backend = "parakeet"
        instance.transcriber._device = "cuda"
        instance.transcriber.model.dtype = "torch.float16"
        instance.input_device = (0, 16000)
        instance.model_status = "ready"
        diagnostics = instance.diagnostics_text()
        self.assertIn("Dictionary rule count: 1", diagnostics)
        self.assertIn("Model status: ready", diagnostics)
        self.assertNotIn("\\Users\\", diagnostics)
        self.assertNotIn("private spoken phrase", diagnostics)
        self.assertNotIn("private replacement", diagnostics)

    def test_visual_indicator_routes_states_without_stealing_app_logic(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"visual_indicator": True}
        instance.indicator = mock.Mock()
        instance._set_indicator("listening")
        instance._set_indicator("transcribing")
        instance._set_indicator(None)
        self.assertEqual(
            instance.indicator.mock_calls,
            [mock.call.show("listening"), mock.call.show("transcribing"),
             mock.call.hide()],
        )

    def test_frozen_autostart_runs_only_the_packaged_executable(self):
        command = app._autostart_command(
            r"C:\Program Files\Presspeech\Presspeech.exe",
            r"C:\ignored\app.py", frozen=True)
        self.assertEqual(
            command, r'"C:\Program Files\Presspeech\Presspeech.exe"')

    def test_source_autostart_runs_pythonw_with_app(self):
        with mock.patch.object(app.os.path, "exists", return_value=True):
            command = app._autostart_command(
                r"C:\Presspeech\.venv\Scripts\python.exe",
                r"C:\Presspeech\app.py", frozen=False)
        self.assertEqual(
            command,
            r'"C:\Presspeech\.venv\Scripts\pythonw.exe" "C:\Presspeech\app.py"')

    def test_playback_mute_restores_the_prior_endpoint_state(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"mute_playback_while_recording": True}
        instance.recording = True
        instance._rec_epoch = 4
        instance.lock = __import__("threading").Lock()
        instance._playback_mute_lock = __import__("threading").Lock()
        instance._playback_restore = None
        instance._log = mock.Mock()
        saved = [("endpoint-one", True), ("endpoint-two", False)]
        with mock.patch.object(app, "_mute_active_playback",
                               return_value=(saved, [])) as mute, \
                mock.patch.object(app, "_restore_playback_mutes",
                                  return_value=(2, [])) as restore:
            instance._mute_playback_for_recording(4)
            instance.recording = False
            instance._restore_playback_after_recording()
        mute.assert_called_once_with()
        restore.assert_called_once_with(saved)
        self.assertIsNone(instance._playback_restore)

    def test_start_cue_finishes_before_playback_mutes_and_mic_opens(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 4
        calls = mock.Mock()
        instance._play_cue_worker = calls.cue
        instance._mute_playback_for_recording = calls.mute
        instance._open_mic_worker = calls.open_mic
        instance._start_audio_worker(4)
        self.assertEqual(
            calls.mock_calls,
            [mock.call.cue("start"), mock.call.mute(4), mock.call.open_mic(4)],
        )

    def test_stale_audio_worker_cannot_attach_to_a_new_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance.input_device = None
        current_stream = object()
        instance.stream = current_stream

        def finish_old_device_search():
            instance._rec_epoch = 5
            return (2, 16000)

        instance._get_input_device = mock.Mock(side_effect=finish_old_device_search)
        with mock.patch.object(app.sd, "InputStream") as input_stream:
            instance._open_mic_worker(4)
        input_stream.assert_not_called()
        self.assertIs(instance.stream, current_stream)
        self.assertTrue(instance.recording)

    def test_audio_callback_rejects_chunks_from_a_stale_stream(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 5
        instance.buffer = []
        instance._peak_rms = 0.0
        chunk = __import__("numpy").ones((8, 1), dtype="float32")

        instance._audio_cb(chunk, 8, None, None, 4)
        self.assertEqual(instance.buffer, [])
        instance._audio_cb(chunk, 8, None, None, 5)
        self.assertEqual(len(instance.buffer), 1)

    def test_microphone_open_error_invalidates_cached_device_for_retry(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance.input_device = (3, 48000)
        instance.stream = None
        instance._get_input_device = mock.Mock(return_value=(3, 48000))
        instance._restore_playback_after_recording = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        timer = mock.Mock()
        instance._recording_limit_timer = timer
        with mock.patch.object(app.sd, "InputStream",
                               side_effect=OSError("device disconnected")):
            instance._open_mic_worker(7)
        self.assertFalse(instance.recording)
        self.assertIsNone(instance.input_device)
        self.assertIsNone(instance._recording_limit_timer)
        timer.cancel.assert_called_once_with()
        instance.notify.assert_called_once_with(
            "Microphone error", "device disconnected")

    def test_missing_microphone_cancels_recording_limit(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance.input_device = None
        instance.stream = None
        instance._get_input_device = mock.Mock(return_value=None)
        instance._restore_playback_after_recording = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        timer = mock.Mock()
        instance._recording_limit_timer = timer

        instance._open_mic_worker(7)

        self.assertFalse(instance.recording)
        self.assertIsNone(instance._recording_limit_timer)
        timer.cancel.assert_called_once_with()
        instance.notify.assert_called_once_with(
            "No microphone found",
            "Plug in a microphone or check Windows Sound settings "
            "(Recording tab), then try again.")

    def test_stale_microphone_error_does_not_cancel_new_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance.input_device = (3, 48000)
        current_stream = object()
        instance.stream = current_stream
        instance._get_input_device = mock.Mock(return_value=(3, 48000))
        instance._restore_playback_after_recording = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        def fail_after_new_recording(*_args, **_kwargs):
            instance._rec_epoch = 5
            raise OSError("old device disconnected")

        with mock.patch.object(app.sd, "InputStream",
                               side_effect=fail_after_new_recording):
            instance._open_mic_worker(4)
        self.assertTrue(instance.recording)
        self.assertIs(instance.stream, current_stream)
        instance.notify.assert_not_called()

    def test_audio_cues_are_valid_and_distinct_wav_data(self):
        self.assertTrue(app.CUE_SOUNDS["start"].startswith(b"RIFF"))
        self.assertTrue(app.CUE_SOUNDS["stop"].startswith(b"RIFF"))
        self.assertNotEqual(app.CUE_SOUNDS["start"], app.CUE_SOUNDS["stop"])

    def test_audio_cue_dispatch_does_not_block_caller(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        with mock.patch.object(app.threading, "Thread") as thread:
            instance._play_cue("start")
        thread.assert_called_once_with(
            target=instance._play_cue_worker, args=("start",), daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_recording_limit_timer_is_daemon_and_epoch_scoped(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance._recording_limit_timer = None
        with mock.patch.object(app.threading, "Timer") as timer:
            instance._schedule_recording_limit(7)
        timer.assert_called_once_with(
            app.MAX_RECORDING_SEC, instance._recording_limit_reached, (7,))
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()

    def test_stale_recording_limit_cannot_stop_a_new_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 8
        self.assertFalse(instance.stop_recording(expected_epoch=7))
        self.assertTrue(instance.recording)

    def test_stopping_recording_cancels_duration_timer(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance.buffer = []
        instance.stream = None
        instance.icon = None
        instance._recording_target_process = "notepad.exe"
        timer = mock.Mock()
        instance._recording_limit_timer = timer
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        self.assertTrue(instance.stop_recording(expected_epoch=7))
        self.assertIsNone(instance._recording_limit_timer)
        timer.cancel.assert_called_once_with()


class PostRollTests(unittest.TestCase):
    def make_app(self, value, peak=0.1, rate=16000):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.buffer = [__import__("numpy").full(
            (int(rate * app.POST_ROLL_TAIL_SEC), 1), value, dtype="float32")]
        instance.input_device = (0, rate)
        instance._peak_rms = peak
        return instance

    def test_quiet_tail_is_silence(self):
        instance = self.make_app(0.001)
        rms, threshold = instance._post_roll_tail()
        self.assertLessEqual(rms, threshold)

    def test_voiced_tail_keeps_recording(self):
        instance = self.make_app(0.03)
        rms, threshold = instance._post_roll_tail()
        self.assertGreater(rms, threshold)

    def test_release_keeps_the_capture_epoch_through_post_roll(self):
        instance = self.make_app(0.03)
        instance.recording = True
        instance._rec_epoch = 7
        instance._schedule_post_roll = mock.Mock()
        with mock.patch.object(app.time, "perf_counter", return_value=10.0):
            instance.request_stop()
        self.assertEqual(instance._rec_epoch, 7)
        instance._schedule_post_roll.assert_called_once_with(
            app.POST_ROLL_MIN_SEC, 7, 10.0)

    def test_maximum_window_stops_even_with_voiced_tail(self):
        instance = self.make_app(0.03)
        instance._rec_epoch = 7
        instance.stop_recording = mock.Mock()
        with mock.patch.object(app.time, "perf_counter", return_value=10.5), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._finish_after_roll(7, 10.0)
        instance.stop_recording.assert_called_once_with(expected_epoch=7)


class ModelIdleTests(unittest.TestCase):
    def test_idle_timer_queues_unload_on_permanent_model_executor(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"gpu_idle_unload_sec": 30}
        instance._model_idle_epoch = 4
        instance._model_executor = mock.Mock()

        with mock.patch.object(app.threading, "Timer") as timer:
            instance._schedule_model_idle_unload()

        timer.assert_called_once_with(
            30, instance._queue_model_idle_unload, (5,))
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()

        instance._queue_model_idle_unload(5)
        instance._model_executor.submit.assert_called_once_with(
            instance._unload_model_if_idle, 5)

    def test_stale_idle_timer_never_queues_model_work(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._model_idle_epoch = 5
        instance._model_executor = mock.Mock()

        instance._queue_model_idle_unload(4)

        instance._model_executor.submit.assert_not_called()

    def test_empty_transcription_refreshes_idle_deadline(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._last_model_use = 0.0
        instance._transcribe_worker_inner = mock.Mock(return_value=None)
        instance._schedule_model_idle_unload = mock.Mock()
        instance._set_indicator = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=123.0):
            instance._transcribe_worker([], "notepad.exe")

        self.assertEqual(instance._last_model_use, 123.0)
        instance._schedule_model_idle_unload.assert_called_once_with()
        instance._set_indicator.assert_called_once_with(None)


class StartupTests(unittest.TestCase):
    def test_startup_worker_preloads_configured_model(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        indicator_states = []
        instance._set_indicator.side_effect = lambda state: indicator_states.append(
            (state, instance.model_status))
        with mock.patch.object(app.PresspeechApp, "_log") as log:
            instance._preload_model_worker()
        instance.transcriber.load.assert_called_once_with(
            "parakeet-tdt-0.6b-v3", notify=instance.notify)
        instance.transcriber.warmup.assert_called_once_with(
            seconds=app.MODEL_WARMUP_SEC, all_buckets=True)
        ready_logs = [call.args[0] for call in log.call_args_list
                      if call.args and call.args[0].startswith("model ready:")]
        self.assertEqual(len(ready_logs), 1)
        self.assertEqual(
            instance._set_indicator.mock_calls,
            [mock.call("loading"), mock.call(None)],
        )
        self.assertEqual(instance.model_status, "ready")
        self.assertEqual(
            indicator_states, [("loading", "loading"), (None, "ready")])

    def test_startup_worker_exposes_model_error_without_crashing(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = RuntimeError("model unavailable")
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        indicator_states = []
        instance._set_indicator.side_effect = lambda state: indicator_states.append(
            (state, instance.model_status))
        with mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker()
        self.assertEqual(instance.model_status, "error")
        self.assertIn("model unavailable", instance.model_status_detail)
        self.assertEqual(
            indicator_states, [("loading", "loading"), (None, "error")])


if __name__ == "__main__":
    unittest.main()
