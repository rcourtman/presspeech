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


class SingleInstanceActivationTests(unittest.TestCase):
    def make_app(self, setup_complete=False):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"setup_complete": setup_complete}
        instance.update_window = None
        instance.setup_window = None
        instance.settings_window = None
        instance.scratchpad = None
        instance._log = mock.Mock()
        return instance

    @staticmethod
    def kernel32():
        kernel32 = mock.Mock()
        kernel32.CreateEventW.return_value = 101
        kernel32.CreateMutexW.return_value = 202
        return kernel32

    def test_first_instance_keeps_mutex_and_activation_event(self):
        instance = self.make_app()
        kernel32 = self.kernel32()

        with mock.patch.object(
                app.ctypes, "set_last_error", create=True) as clear_error, \
                mock.patch.object(
                    app.ctypes, "get_last_error", return_value=0, create=True):
            self.assertTrue(instance._single_instance(kernel32))

        clear_error.assert_called_once_with(0)
        self.assertEqual(instance._mutex_handle, 202)
        self.assertEqual(instance._activation_event_handle, 101)
        kernel32.SetEvent.assert_not_called()
        kernel32.CloseHandle.assert_not_called()

    def test_later_instance_signals_first_and_releases_its_handles(self):
        instance = self.make_app()
        kernel32 = self.kernel32()

        with mock.patch.object(
                app.ctypes, "set_last_error", create=True) as clear_error, \
                mock.patch.object(
                    app.ctypes, "get_last_error", return_value=183, create=True):
            self.assertFalse(instance._single_instance(kernel32))

        clear_error.assert_called_once_with(0)
        kernel32.SetEvent.assert_called_once_with(101)
        self.assertEqual(
            kernel32.CloseHandle.call_args_list,
            [mock.call(202), mock.call(101)],
        )

    def test_activation_watcher_dispatches_each_signaled_request(self):
        instance = self.make_app()
        instance._activation_event_handle = 101
        instance._activate_from_launch = mock.Mock()
        kernel32 = mock.Mock()
        kernel32.WaitForSingleObject.side_effect = [0, 258]

        instance._watch_activation_requests(kernel32)

        instance._activate_from_launch.assert_called_once_with()
        self.assertEqual(kernel32.WaitForSingleObject.call_count, 2)

    def test_repeat_launch_restores_existing_window_before_opening_another(self):
        instance = self.make_app(setup_complete=True)
        instance.setup_window = mock.Mock()
        instance.open_settings = mock.Mock()

        with mock.patch.object(app.ui, "present_window") as present:
            instance._activate_from_launch()

        present.assert_called_once_with(instance.setup_window)
        instance.open_settings.assert_not_called()

    def test_repeat_launch_opens_setup_until_first_run_is_complete(self):
        instance = self.make_app(setup_complete=False)
        instance.open_setup = mock.Mock()
        instance.open_settings = mock.Mock()

        instance._activate_from_launch()

        instance.open_setup.assert_called_once_with()
        instance.open_settings.assert_not_called()

    def test_repeat_launch_opens_settings_after_first_run(self):
        instance = self.make_app(setup_complete=True)
        instance.open_setup = mock.Mock()
        instance.open_settings = mock.Mock()

        instance._activate_from_launch()

        instance.open_settings.assert_called_once_with()
        instance.open_setup.assert_not_called()

    def test_update_command_restores_existing_prompt_without_another_check(self):
        instance = self.make_app()
        instance.update_window = mock.Mock()
        instance._update_lock = mock.Mock()

        with mock.patch.object(app.ui, "present_window") as present:
            instance.check_for_updates()

        present.assert_called_once_with(instance.update_window)
        instance._update_lock.acquire.assert_not_called()


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

    def test_failed_probe_closes_the_created_microphone_stream(self):
        stream = mock.Mock()
        stream.start.side_effect = OSError("device became unavailable")

        with mock.patch.object(app.sd, "InputStream", return_value=stream):
            self.assertFalse(app.PresspeechApp._probe_input(3, 16000))

        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()

    def test_setup_check_freshly_probes_without_replacing_cached_input(self):
        instance = self.make_app()
        instance.input_device = (9, 48000)
        instance._find_input_device = mock.Mock(return_value=(1, 16000))

        self.assertTrue(instance.check_input_device("MME::USB microphone"))

        instance._find_input_device.assert_called_once_with(
            "MME::USB microphone")
        self.assertEqual(instance.input_device, (9, 48000))

    def test_setup_check_reports_device_enumeration_failure_safely(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(
            side_effect=OSError("private device detail"))
        instance._log = mock.Mock()

        self.assertFalse(instance.check_input_device("auto"))

        instance._log.assert_called_once_with(
            "microphone readiness check failed: private device detail")

    def test_microphone_recovery_opens_supported_windows_settings_uris(self):
        instance = self.make_app()
        with mock.patch.object(app.os, "startfile", create=True) as startfile:
            self.assertTrue(instance.open_microphone_privacy_settings())
            self.assertTrue(instance.open_default_input_settings())

        self.assertEqual(
            startfile.call_args_list,
            [
                mock.call("ms-settings:privacy-microphone"),
                mock.call("ms-settings:sound-defaultinputproperties"),
            ],
        )

    def test_windows_settings_launch_failure_has_manual_recovery(self):
        instance = self.make_app()
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        with mock.patch.object(
                app.os, "startfile", create=True,
                side_effect=OSError("no URI handler")):
            self.assertFalse(instance.open_microphone_privacy_settings())

        instance.notify.assert_called_once_with(
            "Could not open Windows Settings",
            "Open Settings manually and search for Microphone privacy or "
            "Sound input settings.",
        )


class HotkeyRegressionTests(unittest.TestCase):
    def make_app(self, hotkey="right alt", trigger="hold"):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"hotkey": hotkey, "trigger": trigger}
        instance._key_held = False
        instance._pressed_keys = set()
        instance._held_hotkey_keys = frozenset()
        instance._held_hotkey_trigger = None
        instance._suppress_escape_keyup = False
        instance._injecting_keys = False
        instance.listener = mock.Mock()
        instance.recording = False
        instance.start_recording = mock.Mock()
        instance.request_stop = mock.Mock()
        instance.cancel_recording = mock.Mock()
        instance._log = mock.Mock()
        return instance

    def test_left_alt_does_not_treat_altgr_as_the_hotkey(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"hotkey": "left alt"}

        self.assertTrue(instance._is_hotkey(app.pkb.Key.alt_l))
        self.assertFalse(instance._is_hotkey(app.pkb.Key.alt_gr))
        self.assertFalse(instance._is_hotkey(app.pkb.Key.alt_r))

        instance.settings["hotkey"] = "right alt"
        self.assertTrue(instance._is_hotkey(app.pkb.Key.alt_gr))
        self.assertTrue(instance._is_hotkey(app.pkb.Key.alt_r))
        self.assertFalse(instance._is_hotkey(app.pkb.Key.alt_l))

    def test_every_settings_hotkey_has_an_input_mapping(self):
        self.assertEqual(set(config.HOTKEYS), set(app.KEY_MAP))

    def test_altgr_chord_does_not_start_right_alt_dictation(self):
        instance = self.make_app()

        instance._on_press(app.pkb.Key.ctrl_l)
        instance._on_press(app.pkb.Key.alt_gr)
        instance._on_release(app.pkb.Key.alt_gr)
        instance._on_release(app.pkb.Key.ctrl_l)

        instance.start_recording.assert_not_called()
        instance.request_stop.assert_not_called()
        self.assertFalse(instance._key_held)
        self.assertEqual(instance._pressed_keys, set())

    def test_bare_right_alt_still_starts_and_stops_dictation(self):
        instance = self.make_app()

        instance._on_press(app.pkb.Key.alt_gr)
        instance._on_release(app.pkb.Key.alt_r)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertEqual(instance._pressed_keys, set())

    def test_escape_requests_cancellation_without_becoming_a_hotkey(self):
        instance = self.make_app()
        instance.recording = True

        instance._on_press(app.pkb.Key.esc)
        instance._on_release(app.pkb.Key.esc)

        instance.cancel_recording.assert_called_once_with()
        instance.start_recording.assert_not_called()
        instance.request_stop.assert_not_called()
        self.assertEqual(instance._pressed_keys, set())

    def test_escape_passes_through_when_not_recording(self):
        instance = self.make_app()
        event = mock.Mock(vkCode=app.VK_ESCAPE)

        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYUP, event)

        instance.listener.suppress_event.assert_not_called()
        self.assertFalse(instance._suppress_escape_keyup)

    def test_cancel_escape_suppresses_down_repeats_and_paired_keyup(self):
        instance = self.make_app()
        instance.recording = True
        event = mock.Mock(vkCode=app.VK_ESCAPE)

        instance._win32_event_filter(app.WM_KEYDOWN, event)
        # The on-press callback cancels recording before key repeat and key-up.
        instance.recording = False
        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYUP, event)
        instance._win32_event_filter(app.WM_KEYUP, event)

        self.assertEqual(instance.listener.suppress_event.call_count, 3)
        self.assertFalse(instance._suppress_escape_keyup)

    def test_escape_filter_ignores_other_virtual_keys(self):
        instance = self.make_app()
        instance.recording = True

        instance._win32_event_filter(
            app.WM_KEYDOWN, mock.Mock(vkCode=0x41))

        instance.listener.suppress_event.assert_not_called()

    def test_modifier_release_during_paste_does_not_leave_altgr_state_stuck(self):
        instance = self.make_app()

        instance._on_press(app.pkb.Key.ctrl_l)
        instance._injecting_keys = True
        instance._on_release(app.pkb.Key.ctrl_l)
        instance._injecting_keys = False
        instance._on_press(app.pkb.Key.alt_gr)
        instance._on_release(app.pkb.Key.alt_r)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertEqual(instance._pressed_keys, set())

    def test_hotkey_release_during_paste_completes_held_transaction(self):
        instance = self.make_app()

        instance._on_press(app.pkb.Key.alt_gr)
        instance._injecting_keys = True
        instance._on_release(app.pkb.Key.alt_r)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertFalse(instance._key_held)
        self.assertEqual(instance._held_hotkey_keys, frozenset())
        self.assertIsNone(instance._held_hotkey_trigger)
        self.assertEqual(instance._pressed_keys, set())

    def test_hold_release_uses_hotkey_and_trigger_captured_on_press(self):
        instance = self.make_app()

        instance._on_press(app.pkb.Key.alt_gr)
        instance.settings.update({"hotkey": "left ctrl", "trigger": "toggle"})
        instance._on_release(app.pkb.Key.alt_r)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertFalse(instance._key_held)
        self.assertEqual(instance._held_hotkey_keys, frozenset())
        self.assertIsNone(instance._held_hotkey_trigger)

    def test_toggle_release_does_not_adopt_new_hold_mode(self):
        instance = self.make_app(trigger="toggle")

        instance._on_press(app.pkb.Key.alt_r)
        instance.settings["trigger"] = "hold"
        instance._on_release(app.pkb.Key.alt_gr)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_not_called()
        self.assertFalse(instance._key_held)


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
            "onnxruntime", "pycaw.constants", "pycaw.pycaw", "safetensors",
            "sentencepiece", "soundfile", "tokenizers", "torch",
            "tk_uia", "transformers",
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

    def test_dictionary_prefers_longer_overlapping_phrases(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "dictionary": [["parakeet", "bird"],
                           ["parakeet tdt", "Parakeet TDT"]],
            "remove_fillers": False,
            "british": False,
            "suffix": "none",
        }

        self.assertEqual(
            instance._apply_text("Parakeet TDT and parakeet."),
            "Parakeet TDT and bird.")

    def test_dictionary_does_not_rewrite_replacement_text(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "dictionary": [["project folder", "second phrase"],
                           ["second phrase", "rewritten"]],
            "remove_fillers": False,
            "british": False,
            "suffix": "none",
        }

        self.assertEqual(
            instance._apply_text("project folder and second phrase"),
            "second phrase and rewritten")

    def test_dictionary_runtime_keeps_the_rule_count_bounded(self):
        rules = [["unused-%d" % index, "replacement"]
                 for index in range(config.MAX_DICTIONARY_RULES)]
        rules.append(["target phrase", "changed"])

        self.assertEqual(
            app._apply_dictionary_rules("target phrase", rules),
            "target phrase")

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
        target = app.PasteTarget("moonlight.exe", 1234)
        with mock.patch.object(app, "_foreground_paste_target",
                               return_value=target), \
                mock.patch.object(app.threading, "Thread"), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance.start_recording()
        self.assertEqual(instance._recording_paste_target, target)
        instance._schedule_recording_limit.assert_called_once_with(1)

    def test_recording_owns_the_current_scratchpad_destination(self):
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
        instance.scratchpad = mock.Mock()
        instance.scratchpad.window_handle = 1234
        instance._wake_model_if_idle = mock.Mock()
        instance._schedule_recording_limit = mock.Mock()
        with mock.patch.object(
                app, "_foreground_paste_target",
                return_value=app.PasteTarget(
                    "presspeech.exe", 1234, app.os.getpid())), \
                mock.patch.object(app.threading, "Thread"), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance.start_recording()

        self.assertIs(instance._recording_scratchpad, instance.scratchpad)

    def test_background_scratchpad_does_not_redirect_another_apps_dictation(self):
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
        instance.scratchpad = mock.Mock()
        instance.scratchpad.window_handle = 4321
        instance._wake_model_if_idle = mock.Mock()
        instance._schedule_recording_limit = mock.Mock()
        with mock.patch.object(
                app, "_foreground_paste_target",
                return_value=app.PasteTarget("notepad.exe", 1234, 99)), \
                mock.patch.object(app.threading, "Thread"), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance.start_recording()

        self.assertIsNone(instance._recording_scratchpad)

    def test_closed_scratchpad_transcript_is_discarded_not_pasted(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        original_scratchpad = mock.Mock()
        original_scratchpad.root = None
        instance.scratchpad = None
        instance._paste = mock.Mock()
        instance._log = mock.Mock()

        instance._deliver_text(
            "private test transcript", app.PasteTarget("notepad.exe", 1234),
            original_scratchpad)

        original_scratchpad.append_text.assert_not_called()
        instance._paste.assert_not_called()
        instance._log.assert_called_once_with(
            "scratchpad transcription discarded; window closed")

    def test_captured_open_scratchpad_receives_its_transcript(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        scratchpad = mock.Mock()
        scratchpad.root = mock.Mock()
        instance.scratchpad = scratchpad
        instance._paste = mock.Mock()
        target = app.PasteTarget("presspeech.exe", 1234, app.os.getpid())

        with mock.patch.object(
                app, "_foreground_paste_target", return_value=target):
            instance._deliver_text(
                "private test transcript", target, scratchpad)

        scratchpad.append_text.assert_called_once_with(
            "private test transcript")
        instance._paste.assert_not_called()

    def test_focus_change_does_not_append_to_captured_scratchpad(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        scratchpad = mock.Mock()
        scratchpad.root = mock.Mock()
        instance.scratchpad = scratchpad
        instance._paste = mock.Mock()
        target = app.PasteTarget("presspeech.exe", 1234, app.os.getpid())

        with mock.patch.object(
                app, "_foreground_paste_target",
                return_value=app.PasteTarget("notepad.exe", 4321, 99)):
            instance._deliver_text(
                "private test transcript", target, scratchpad)

        scratchpad.append_text.assert_not_called()
        instance._paste.assert_called_once_with(
            "private test transcript", target)

    def test_opening_scratchpad_does_not_redirect_queued_normal_dictation(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.scratchpad = mock.Mock()
        instance._paste = mock.Mock()

        target = app.PasteTarget("notepad.exe", 1234)
        instance._deliver_text("normal transcript", target, None)

        instance._paste.assert_called_once_with(
            "normal transcript", target)
        instance.scratchpad.append_text.assert_not_called()

    def test_focus_change_copies_transcript_without_pasting(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234)

        with mock.patch.object(app.pyperclip, "copy") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    return_value=app.PasteTarget("calculator.exe", 5678)), \
                mock.patch.object(app.pkb, "Controller") as controller:
            instance._paste("private transcript", target)

        copy.assert_called_once_with("private transcript")
        controller.assert_not_called()
        instance.notify.assert_called_once_with(
            "Transcript copied, not pasted",
            "The focused window changed while Presspeech was transcribing. "
            "Paste from the clipboard when ready.")
        instance._log.assert_called_once_with(
            "paste skipped; focus changed from notepad.exe to calculator.exe")

    def test_missing_recording_target_copies_without_pasting(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        with mock.patch.object(app.pyperclip, "copy") as copy, \
                mock.patch.object(app.time, "sleep") as sleep, \
                mock.patch.object(
                    app, "_foreground_paste_target") as foreground, \
                mock.patch.object(app.pkb, "Controller") as controller:
            instance._paste("private transcript", app.PasteTarget("", 0))

        copy.assert_called_once_with("private transcript")
        sleep.assert_not_called()
        foreground.assert_not_called()
        controller.assert_not_called()
        instance._log.assert_called_once_with(
            "paste skipped; no foreground window was captured")
        instance.notify.assert_called_once_with(
            "Transcript copied, not pasted",
            "Presspeech couldn't identify the window focused when recording "
            "began. Paste from the clipboard when ready.")

    def test_reused_window_handle_from_another_process_never_receives_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234, 41)
        replacement = app.PasteTarget("notepad.exe", 1234, 42)

        with mock.patch.object(app.pyperclip, "copy") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    return_value=replacement), \
                mock.patch.object(app.pkb, "Controller") as controller:
            instance._paste("private transcript", target)

        copy.assert_called_once_with("private transcript")
        controller.assert_not_called()
        instance.notify.assert_called_once_with(
            "Transcript copied, not pasted",
            "The focused window changed while Presspeech was transcribing. "
            "Paste from the clipboard when ready.")

    def test_focus_change_during_shortcut_never_emits_paste_key(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._injecting_keys = False
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        # Moonlight has the longest shortcut (Ctrl+Alt+Shift+V), making
        # modifier cleanup and the final pre-V check especially important.
        target = app.PasteTarget("moonlight.exe", 1234, 41)
        replacement = app.PasteTarget("calculator.exe", 5678, 42)

        with mock.patch.object(app.pyperclip, "copy") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    side_effect=[target, replacement]), \
                mock.patch.object(app.pkb, "Controller") as controller:
            instance._paste("private transcript", target)

        keyboard = controller.return_value
        copy.assert_called_once_with("private transcript")
        keyboard.press.assert_has_calls([
            mock.call(app.pkb.Key.ctrl_l),
            mock.call(app.pkb.Key.alt_l),
            mock.call(app.pkb.Key.shift_l),
        ])
        self.assertEqual(keyboard.press.call_count, 3)
        keyboard.release.assert_has_calls([
            mock.call(app.pkb.Key.shift_l),
            mock.call(app.pkb.Key.alt_l),
            mock.call(app.pkb.Key.ctrl_l),
        ])
        self.assertEqual(keyboard.release.call_count, 3)
        self.assertNotIn(mock.call("v"), keyboard.press.call_args_list)
        self.assertNotIn(mock.call("v"), keyboard.release.call_args_list)
        self.assertFalse(instance._injecting_keys)
        instance.notify.assert_called_once_with(
            "Transcript copied, not pasted",
            "The focused window changed while Presspeech was transcribing. "
            "Paste from the clipboard when ready.")
        instance._log.assert_called_once_with(
            "paste skipped; focus changed from moonlight.exe to calculator.exe")

    def test_original_window_still_receives_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._injecting_keys = False
        instance._log = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234, 41)

        with mock.patch.object(app.pyperclip, "copy"), \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(app.pkb, "Controller") as controller:
            instance._paste("transcript", target)

        keyboard = controller.return_value
        keyboard.press.assert_has_calls(
            [mock.call(app.pkb.Key.ctrl_l), mock.call("v")])
        keyboard.release.assert_has_calls(
            [mock.call("v"), mock.call(app.pkb.Key.ctrl_l)])

    def test_recording_is_blocked_while_startup_model_is_loading(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "loading"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        with mock.patch.object(app, "_foreground_paste_target") as foreground, \
                mock.patch.object(app.threading, "Thread") as worker:
            self.assertFalse(instance.start_recording())
        self.assertFalse(getattr(instance, "recording", False))
        foreground.assert_not_called()
        worker.assert_not_called()
        instance._set_indicator.assert_called_once_with("loading")
        instance._model_executor.submit.assert_not_called()

    def test_recording_is_blocked_until_previous_paste_delivery_finishes(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = False
        instance.transcribing = True
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "ready"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()

        with mock.patch.object(app, "_foreground_paste_target") as foreground, \
                mock.patch.object(app.threading, "Thread") as worker:
            self.assertFalse(instance.start_recording())

        self.assertFalse(instance.recording)
        foreground.assert_not_called()
        worker.assert_not_called()
        instance._set_indicator.assert_called_once_with("transcribing")
        instance._log.assert_called_once_with(
            "dictation ignored; previous transcription is still being delivered")

    def test_recording_is_blocked_until_cancellation_cleanup_finishes(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._canceling_recording = True
        instance.transcribing = False
        instance._log = mock.Mock()

        with mock.patch.object(app, "_foreground_paste_target") as foreground:
            self.assertFalse(instance.start_recording())

        foreground.assert_not_called()
        instance._log.assert_called_once_with(
            "dictation ignored; canceled recording is still closing")

    def test_recording_rechecks_cancellation_under_lock(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance._canceling_recording = False
        instance.transcribing = False
        instance.recording = False
        instance._dictation_model_ready = mock.Mock(return_value=True)

        def cancellation_started():
            instance._canceling_recording = True
            return app.PasteTarget("notepad.exe", 1234)

        with mock.patch.object(
                app, "_foreground_paste_target",
                side_effect=cancellation_started):
            self.assertFalse(instance.start_recording())

        self.assertFalse(instance.recording)

    def test_first_press_after_model_error_starts_one_retry_not_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "error"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        self.assertFalse(instance.start_recording())
        self.assertEqual(instance.model_status, "loading")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker)
        self.assertFalse(instance.start_recording())
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker)

    def test_explicit_model_retry_is_single_flight(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "error"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()

        self.assertTrue(instance.retry_model())
        self.assertFalse(instance.retry_model())

        self.assertEqual(instance.model_status, "loading")
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
        self.assertIn("Windows UI Automation: not initialized", diagnostics)
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
            "Microphone error",
            "Presspeech couldn't open the selected input. Check Settings > "
            "System > Sound > Input and Windows microphone privacy settings, "
            "including 'Let desktop apps access your microphone', then try "
            "again. Details: device disconnected")

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
            "Check Settings > System > Sound > Input, then enable microphone "
            "access for desktop apps in Windows privacy settings and try again.")

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
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
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

    def test_cancel_discards_capture_and_restores_recording_resources(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._canceling_recording = False
        instance.transcribing = False
        instance._rec_epoch = 7
        instance.buffer = [
            __import__("numpy").ones(4800, dtype="float32")]
        instance._peak_rms = 0.4
        instance._recording_paste_target = app.PasteTarget(
            "notepad.exe", 1234)
        instance._recording_scratchpad = mock.Mock()
        stream = mock.Mock()
        instance.stream = stream
        instance.icon = mock.Mock()
        idle_icon = object()
        instance.idle_icon = idle_icon
        timer = mock.Mock()
        instance._recording_limit_timer = timer
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_executor = mock.Mock()

        with mock.patch.object(app.pyperclip, "copy") as copy, \
                mock.patch.object(app.threading, "Thread") as worker:
            self.assertTrue(instance.cancel_recording())

        self.assertFalse(instance.recording)
        self.assertTrue(instance._canceling_recording)
        self.assertFalse(instance.transcribing)
        self.assertEqual(instance.buffer, [])
        self.assertEqual(instance._peak_rms, 0.0)
        self.assertEqual(
            instance._recording_paste_target, app.PasteTarget("", 0))
        self.assertIsNone(instance._recording_scratchpad)
        self.assertIsNone(instance.stream)
        self.assertIsNone(instance._recording_limit_timer)
        worker.assert_called_once_with(
            target=instance._cancel_recording_worker,
            args=(stream, timer),
            name="presspeech-cancel-recording",
            daemon=True,
        )
        worker.return_value.start.assert_called_once_with()
        instance._cancel_recording_worker(stream, timer)
        timer.cancel.assert_called_once_with()
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()
        self.assertIs(instance.icon.icon, idle_icon)
        instance._restore_playback_after_recording.assert_called_once_with()
        instance._play_cue.assert_called_once_with("stop")
        instance._set_indicator.assert_called_once_with(None)
        instance._schedule_model_idle_unload.assert_called_once_with()
        instance._model_executor.submit.assert_not_called()
        copy.assert_not_called()
        self.assertFalse(instance._canceling_recording)

    def test_cancel_closes_the_active_stream_before_restoring_playback(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance._canceling_recording = True
        instance.icon = None
        instance._play_cue = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        calls = mock.Mock()
        stream = mock.Mock()
        stream.stop.side_effect = calls.stream_stop
        stream.close.side_effect = calls.stream_close
        instance._restore_playback_after_recording = calls.restore

        instance._cancel_recording_worker(stream, None)

        self.assertEqual(calls.mock_calls, [
            mock.call.stream_stop(),
            mock.call.stream_close(),
            mock.call.restore(),
        ])
        self.assertFalse(instance._canceling_recording)

    def test_cancel_attempts_stream_close_when_stop_fails(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance._canceling_recording = True
        instance.icon = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        stream = mock.Mock()
        stream.stop.side_effect = RuntimeError("device already stopped")

        instance._cancel_recording_worker(stream, None)

        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()
        instance._restore_playback_after_recording.assert_called_once_with()
        self.assertFalse(instance._canceling_recording)

    def test_stopping_recording_claims_delivery_before_model_queue(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance.transcribing = False
        instance._rec_epoch = 7
        audio = __import__("numpy").ones(4800, dtype="float32")
        instance.buffer = [audio]
        instance.stream = None
        instance.icon = None
        instance.input_device = (0, 16000)
        paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_paste_target = paste_target
        instance._recording_scratchpad = None
        instance._recording_limit_timer = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._capture_benchmark_if_armed = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._model_executor = mock.Mock()

        self.assertTrue(instance.stop_recording(expected_epoch=7))

        self.assertTrue(instance.transcribing)
        instance._set_indicator.assert_called_once_with("transcribing")
        instance._model_executor.submit.assert_called_once()
        queued = instance._model_executor.submit.call_args.args
        self.assertEqual(queued[0], instance._transcribe_worker)
        __import__("numpy").testing.assert_array_equal(queued[1], audio)
        self.assertEqual(queued[2:], (paste_target, None))


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
    def test_model_timing_summary_exposes_vad_rejection_without_audio(self):
        summary = app._model_timing_summary({
            "backend": "whisper",
            "speech_seconds": 0.0,
            "lock_wait": 0.01,
            "inference": 0.2,
        })

        self.assertIn("backend=whisper", summary)
        self.assertIn("speech=0.000s", summary)
        self.assertIn("generate=0.200s", summary)

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
        instance.lock = __import__("threading").Lock()
        instance.transcribing = True
        instance._last_model_use = 0.0
        instance._transcribe_worker_inner = mock.Mock(return_value=None)
        instance._schedule_model_idle_unload = mock.Mock()
        instance._set_indicator = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=123.0):
            instance._transcribe_worker(
                [], app.PasteTarget("notepad.exe", 1234))

        self.assertEqual(instance._last_model_use, 123.0)
        instance._schedule_model_idle_unload.assert_called_once_with()
        instance._set_indicator.assert_called_once_with(None)
        self.assertFalse(instance.transcribing)


class StartupTests(unittest.TestCase):
    def test_fresh_non_cuda_install_selects_cpu_model(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance.transcriber = mock.Mock()
        instance.transcriber._device = "cpu"
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        with mock.patch.object(app.engine, "cuda_available", return_value=False), \
                mock.patch.object(app.cfg, "save") as save, \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker()

        self.assertEqual(instance.settings["model"], "base.en")
        save.assert_called_once_with(instance.settings)
        instance.transcriber.load.assert_called_once_with(
            "base.en", notify=instance.notify)
        self.assertIn("Whisper base.en on CPU", instance.model_status_detail)
        instance.notify.assert_any_call(
            "CPU speech model selected",
            "NVIDIA CUDA is unavailable; using Whisper base.en on CPU. "
            "You can choose another model in Settings.")

    def test_fresh_cuda_install_keeps_parakeet_default(self):
        settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        self.assertEqual(
            app._startup_model(settings, cuda_available=True),
            "parakeet-tdt-0.6b-v3")

    def test_explicit_first_run_choice_preserves_parakeet_without_cuda(self):
        settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "model_explicit": True,
            "setup_complete": False,
        }
        self.assertEqual(
            app._startup_model(settings, cuda_available=False),
            "parakeet-tdt-0.6b-v3")

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
